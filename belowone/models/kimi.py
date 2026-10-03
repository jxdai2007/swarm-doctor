"""Honest identity, bounded requests, auditable failures, network-free replay."""
import asyncio
from collections import deque
from decimal import Decimal
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import time

import httpx


class ModelCallError(RuntimeError):
    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response


class QuotaExceeded(ModelCallError):
    pass

class QuotaPending(QuotaExceeded):
    """Run remains pinned but quota reset did not arrive within outage bound."""


class RateLimitExceeded(ModelCallError):
    """Transient short retries exhausted; not evidence of quota exhaustion."""



class KimiClient:
    provider = 'kimi'

    def __init__(self, api_key, meter, cassette, *, model='kimi-for-coding',
                 base_url='https://api.kimi.com/coding/v1', concurrency=8,
                 timeout=30, retry_delays=None, requests_per_window=300,
                 window_seconds=18000, max_quota_wait_seconds=None, http=None):
        if type(concurrency) is not int or concurrency < 1 or requests_per_window < 1 or window_seconds <= 0:
            raise ValueError('request limits must be positive')
        self.api_key, self.meter, self.cassette = api_key, meter, cassette
        self.model, self.base_url = model, base_url.rstrip('/')
        self.semaphore = asyncio.Semaphore(concurrency)
        self.timeout = timeout
        self.retry_delays = [1, 2, 4] if retry_delays is None else list(retry_delays)
        if any(delay < 0 for delay in self.retry_delays):
            raise ValueError('retry delays must be nonnegative')
        self.requests_per_window, self.window_seconds = requests_per_window, window_seconds
        self.max_quota_wait_seconds = window_seconds + timeout if max_quota_wait_seconds is None else max_quota_wait_seconds
        if type(self.max_quota_wait_seconds) not in (int, float) or not math.isfinite(self.max_quota_wait_seconds) or self.max_quota_wait_seconds < 0:
            raise ValueError('quota outage bound must be finite nonnegative seconds')
        self.window = deque()
        self.http = http
        self.on_quota_exhausted = None

    def _reserve(self, payload):
        return None

    def _normalize(self, response, payload):
        if not isinstance(response, dict) or not isinstance(response.get('model'), str) or not response['model']:
            raise ValueError('model response lacks served model identity')
        return response

    def _record(self, response, latency, reservation):
        usage = response.get('usage')
        if not isinstance(usage, dict):
            raise ValueError('model response lacks token usage')
        input_tokens = usage.get('prompt_tokens', usage.get('input_tokens'))
        output_tokens = usage.get('completion_tokens', usage.get('output_tokens'))
        if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
            raise ValueError('model token usage must be nonnegative integers')
        if self.provider == 'openrouter':
            cost = usage.get('cost', input_tokens * self.prompt_price + output_tokens * self.completion_price)
        else:
            cost = '0'
        return self.meter.record(self.provider, response['model'], input_tokens,
                                 output_tokens, cost, latency, reservation=reservation)

    def _log_attempt(self, attempt):
        if self.meter.event_log is not None:
            self.meter.event_log.append(self.meter.agent_id, 'model_outcome', payload=attempt)

    def _record_replay(self, response):
        attempts = response.get('_belowone_attempts')
        if not isinstance(attempts, list) or not attempts:
            raise ValueError('recorded response lacks metering provenance')
        for attempt in attempts:
            call = attempt.get('call')
            if call:
                hold = self.meter.reserve(call['provider'], call['cost_usd']) if call['provider'] == 'openrouter' else None
                self.meter.record(call['provider'], call['model'], call['input_tokens'],
                                  call['output_tokens'], call['cost_usd'], call['latency'], reservation=hold)
            elif attempt['charge_status'] == 'unknown' and self.provider == 'openrouter':
                self.meter.reserve(self.provider, attempt['reserved_usd'])
            self._log_attempt(attempt)
        if response.get('_belowone_error'):
            kind = {'quota_pending': QuotaPending, 'rate_limit': RateLimitExceeded}.get(response['_belowone_error'], ModelCallError)
            raise kind(response['reason'], response)
        return response

    def _failure_response(self, reason, attempts, *, quota=False):
        kind = 'quota_pending' if quota else 'rate_limit' if reason == 'rate_limit' else 'request'
        return {'_belowone_error': kind, 'status': 'incomplete' if quota else 'failed',
                'reason': reason, '_belowone_attempts': attempts}

    def _quota_delay(self, response):
        retry_after = response.headers.get('Retry-After')
        if retry_after is not None:
            try:
                delay = float(retry_after)
                if math.isfinite(delay) and delay >= 0:
                    return delay
            except ValueError:
                try:
                    reset = parsedate_to_datetime(retry_after)
                    if reset.tzinfo is not None:
                        return max(0, (reset - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
        reset = self.window[0] + self.window_seconds
        return max(0, reset - time.monotonic())

    async def _request(self, payload, *, endpoint='/chat/completions'):
        request = {'provider': self.provider, 'endpoint': self.base_url + endpoint, 'body': payload}
        if self.cassette.mode == 'replay':
            return self._record_replay(self.cassette.load(request))
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise RuntimeError(f'operator action needed: {self.provider} API key missing')
        attempts = []
        pending_deadline = None
        number = 0
        async with self.semaphore:
            while True:
                while True:
                    now = time.monotonic()
                    while self.window and now - self.window[0] >= self.window_seconds:
                        self.window.popleft()
                    if len(self.window) < self.requests_per_window:
                        break
                    if self.on_quota_exhausted is not None:
                        self.on_quota_exhausted()
                    await asyncio.sleep(max(0, self.window[0] + self.window_seconds - now))
                reservation = self._reserve(payload)
                self.window.append(time.monotonic())
                started = time.perf_counter()
                owned = self.http is None
                client = self.http or httpx.AsyncClient(timeout=self.timeout)
                attempt = {'provider': self.provider, 'model': self.model,
                           'model_identity': 'requested', 'input_tokens': None,
                           'output_tokens': None, 'cost_usd': None,
                           'charge_status': 'unknown', 'status': 'failed',
                           'reserved_usd': str(reservation.maximum_cost) if reservation else '0'}
                try:
                    response = await client.post(request['endpoint'], json=payload,
                        headers={'Authorization': 'Bearer ' + self.api_key,
                                 'User-Agent': 'below-one/0.1', 'X-Title': 'below-one'}, timeout=self.timeout)
                    if response.status_code == 429:
                        try:
                            error = response.json().get('error', {})
                        except (ValueError, AttributeError):
                            error = {}
                        exhausted = 'quota' in str(error).lower()
                        pending = exhausted and self.provider == 'kimi'
                        if exhausted and self.on_quota_exhausted is not None:
                            self.on_quota_exhausted()
                        if pending:
                            attempt['retry_after_seconds'] = self._quota_delay(response)
                            attempt['quota_pending'] = True
                            if pending_deadline is None:
                                pending_deadline = time.monotonic() + self.max_quota_wait_seconds
                        if reservation is not None:
                            self.meter.cancel(reservation)
                        attempt.update(charge_status='uncharged', cost_usd='0',
                                       input_tokens=0, output_tokens=0, reason='rate_limit')
                    else:
                        if response.is_error:
                            try:
                                charged = self._normalize(response.json(), payload)
                                call = self._record(charged, time.perf_counter() - started, reservation)
                                attempt.update(call=call, model=call['model'], model_identity='served',
                                               input_tokens=call['input_tokens'], output_tokens=call['output_tokens'],
                                               cost_usd=call['cost_usd'], charge_status='known')
                            except (ValueError, TypeError, KeyError):
                                pass
                            response.raise_for_status()
                        data = self._normalize(response.json(), payload)
                        call = self._record(data, time.perf_counter() - started, reservation)
                        attempt.update(call=call, model=call['model'], model_identity='served',
                                       input_tokens=call['input_tokens'], output_tokens=call['output_tokens'],
                                       cost_usd=call['cost_usd'], charge_status='known', status='success')
                        attempt['latency'] = call['latency']
                        attempts.append(attempt)
                        self._log_attempt(attempt)
                        return self.cassette.save(request, {**data, '_belowone': call,
                                                           '_belowone_attempts': attempts})
                except (httpx.HTTPError, ValueError) as exc:
                    attempt['reason'] = type(exc).__name__
                finally:
                    if owned:
                        await client.aclose()
                attempt['latency'] = time.perf_counter() - started
                attempts.append(attempt)
                self._log_attempt(attempt)
                if attempt.get('quota_pending'):
                    delay = attempt['retry_after_seconds']
                    remaining = max(0, pending_deadline - time.monotonic())
                    if remaining > 0 and delay <= remaining:
                        await asyncio.sleep(delay)
                        continue
                    attempt['reason'] = 'Quota remains pending; active run incomplete and provider unchanged'
                    failed = self.cassette.save(request, self._failure_response(attempt['reason'], attempts, quota=True))
                    raise QuotaPending(attempt['reason'], failed)
                if attempt.get('reason') == 'rate_limit' and number < len(self.retry_delays):
                    await asyncio.sleep(self.retry_delays[number])
                    number += 1
                    continue
                failed = self.cassette.save(request, self._failure_response(attempt['reason'], attempts))
                kind = RateLimitExceeded if attempt.get('reason') == 'rate_limit' else ModelCallError
                raise kind(attempt['reason'], failed)

    async def chat(self, messages, *, tools=None, max_tokens=1024):
        if type(max_tokens) is not int or max_tokens < 1:
            raise ValueError('max_tokens must be positive integer')
        payload = {'model': self.model, 'messages': messages, 'max_tokens': max_tokens}
        if tools:
            payload['tools'] = tools
        data = await self._request(payload)
        if not isinstance(data.get('choices'), list) or not data['choices']:
            raise ValueError('chat response has no choices')
        return data
