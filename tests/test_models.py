import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from belowone.models.cassette import Cassette, CacheMiss
from belowone.models.kimi import KimiClient, QuotaExceeded
from belowone.models.openrouter import OpenRouterClient
from belowone.models.jev import JevClient
from belowone.models.router import ModelRouter
from belowone.meter import Meter, BudgetExceeded


def test_cassette_replay_and_missing(tmp_path):
    request = {'model': 'fixture', 'messages': [{'role': 'user', 'content': 'Export CSV'}]}
    response = {'choices': [{'message': {'content': 'csv'}}]}
    assert Cassette(tmp_path).save(request, response) == response
    replay = Cassette(tmp_path, 'replay')
    assert replay.load(request) == response
    with pytest.raises(CacheMiss, match='missing request hash'):
        replay.load({'model': 'unknown'})


async def test_kimi_identity_concurrency_and_retry(tmp_path):
    active = maximum = requests = 0
    async def handle(request):
        nonlocal active, maximum, requests
        requests += 1
        assert request.headers['User-Agent'] == 'below-one/0.1'
        assert json.loads(request.content)['model'] == 'kimi-for-coding'
        if requests == 1:
            return httpx.Response(429, json={'error': 'rate limit'})
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={'model': 'served-kimi', 'choices': [{'message': {'content': 'done'}}], 'usage': {'prompt_tokens': 2, 'completion_tokens': 1}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = KimiClient('fake', Meter(), Cassette(tmp_path), concurrency=2, retry_delays=[0], http=http)
        results = await asyncio.gather(*(client.chat([{'role': 'user', 'content': str(i)}]) for i in range(5)))
    assert maximum == 2
    assert all(r['model'] == 'served-kimi' for r in results)
    replay = KimiClient(None, Meter(), Cassette(tmp_path, 'replay'))
    assert await replay.chat([{'role': 'user', 'content': '0'}]) == results[0]


async def test_router_never_switches_mid_run(tmp_path):
    meter = Meter()
    kimi = KimiClient(None, meter, Cassette(tmp_path))
    fallback = OpenRouterClient(None, meter, Cassette(tmp_path), model='pinned', prompt_price='0.0001', completion_price='0.0001')
    router = ModelRouter(kimi, fallback)
    assert router.begin_run('seed0') is kimi
    router.quota_exhausted()
    assert router.current is kimi
    with pytest.raises(RuntimeError, match='run already active'):
        router.begin_run('seed1')
    router.end_run()
    assert router.begin_run('seed1') is fallback
    router.end_run()


async def test_openrouter_cap_before_transport(tmp_path):
    sent = 0
    def handle(request):
        nonlocal sent
        sent += 1
        return httpx.Response(200, json={})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = OpenRouterClient('fake', Meter('0.0001'), Cassette(tmp_path), model='pinned', prompt_price='0.01', completion_price='0.01', http=http)
        with pytest.raises(BudgetExceeded):
            await client.chat([{'role': 'user', 'content': 'x'}])
    assert sent == 0


async def test_jev_timeout_uncertain_and_replay(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout('timeout')
    questions = {'serves_goal': {'type': 'noul', 'instructions': 'Useful?', 'criteria': {'true': 'yes', 'false': 'no'}}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as http:
        client = JevClient('fake', Meter(), Cassette(tmp_path), http=http)
        result = await client.check({'action': 'read'}, questions)
    assert result['verdict'] == 'uncertain'
    assert await JevClient(None, Meter(), Cassette(tmp_path, 'replay')).check({'action': 'read'}, questions) == result


async def test_jev_documented_response(tmp_path):
    response = {'model': 'typesafe/jev-1.13-20260917', 'answers': {'serves_goal': {'type': 'noul', 'noul': 0.96}, 'hard_line': {'type': 'choice', 'choice': 'none', 'confidence': 0.9, 'probabilities': {'none': 0.95}}, 'progress': {'type': 'score', 'score': 1.99, 'confidence': 0.99, 'probabilities': {'0': 0, '1': 0.01, '2': 0.99}}}, 'usage': {'input_tokens': 476, 'output_tokens': 70, 'cost': 0.000019992}}
    def handle(request):
        assert request.url.path == '/api/alpha/decisions'
        return httpx.Response(200, json=response)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = JevClient('fake', Meter(), Cassette(tmp_path), http=http)
        result = await client.check({'step': 'read'}, {'serves_goal': {}, 'hard_line': {}, 'progress': {}})
    assert result['answers']['serves_goal']['noul'] == 0.96
    assert result['answers']['hard_line']['choice'] == 'none'
    assert result['answers']['progress']['score'] == 1.99
    assert client.meter.report()['providers']['openrouter']['cost_usd'] == '0.000019992'


async def test_quota_retry_stays_on_pinned_provider(tmp_path):
    calls = []
    def handle(request):
        calls.append(request.url.host)
        if len(calls) == 1:
            return httpx.Response(429, headers={'Retry-After': '0'}, json={'error': {'code': 'quota_exhausted'}})
        return httpx.Response(200, json={'model': 'served-kimi', 'choices': [{'message': {'content': 'done'}}], 'usage': {'prompt_tokens': 2, 'completion_tokens': 1}})
    meter = Meter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        kimi = KimiClient('fake', meter, Cassette(tmp_path), retry_delays=[0], http=http)
        router = ModelRouter(kimi, OpenRouterClient(None, meter, Cassette(tmp_path), model='pinned'))
        client = router.begin_run('seed0-arm0', seed=0)
        await client.chat([{'role': 'user', 'content': 'work'}])
        assert router.current is kimi
        router.end_run()
        assert router.begin_run('seed0-arm1', seed=0) is kimi
        router.end_run()
        assert router.begin_run('seed1-arm0', seed=1) is router.fallback
    assert calls == ['api.kimi.com', 'api.kimi.com']


async def test_replay_meters_identical_response_without_network(tmp_path):
    response = {'model': 'served-fallback', 'choices': [{'message': {'content': 'done'}}], 'usage': {'prompt_tokens': 2, 'completion_tokens': 3, 'cost': '0.000000128'}}
    messages = [{'role': 'user', 'content': 'work'}]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))) as http:
        live = OpenRouterClient('fake', Meter(), Cassette(tmp_path), http=http)
        recorded = await live.chat(messages)
    def forbidden(request):
        pytest.fail('replay touched network')
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        replay = OpenRouterClient(None, Meter(), Cassette(tmp_path, 'replay'), http=http)
        restored = await replay.chat(messages)
    assert json.dumps(restored, sort_keys=True) == json.dumps(recorded, sort_keys=True)
    assert replay.meter.report()['calls'] == live.meter.report()['calls']


async def test_unknown_charge_retains_reservation(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout('unknown charge')
    meter = Meter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as http:
        client = JevClient('fake', meter, Cassette(tmp_path), http=http)
        result = await client.check({'action': 'read'}, {'serves_goal': {}})
    assert result['verdict'] == 'uncertain'
    assert Decimal(meter.report()['reserved_usd']) > 0
    assert meter.report()['spent_usd'] == '0'
    assert result['_belowone_failure']['charge_status'] == 'unknown'


async def test_invalid_jev_answers_fail_closed(tmp_path):
    response = {'model': 'typesafe/jev-1.13', 'answers': {'serves_goal': {'type': 'noul', 'noul': 8}}, 'usage': {'input_tokens': 1, 'output_tokens': 0, 'cost': '0.000000042'}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))) as http:
        client = JevClient('fake', Meter(), Cassette(tmp_path), http=http)
        assert (await client.check({}, {'serves_goal': {}}))['verdict'] == 'uncertain'


async def test_failed_chat_records_unknown_charge_and_replays(tmp_path):
    from belowone.models.kimi import ModelCallError
    from belowone.runlog import EventLog
    def timeout(request):
        raise httpx.ReadTimeout('timeout')
    log = EventLog(tmp_path / 'events.jsonl')
    meter = Meter(event_log=log)
    cassette = Cassette(tmp_path / 'cache')
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as http:
        client = OpenRouterClient('fake', meter, cassette, http=http)
        with pytest.raises(ModelCallError) as error:
            await client.chat([{'role': 'user', 'content': 'work'}])
    attempt = error.value.response['_belowone_attempts'][0]
    assert attempt['input_tokens'] is None and attempt['cost_usd'] is None
    assert log.read()[0].kind == 'model_outcome'
    assert log.read()[0].payload['latency'] >= 0
    replay = OpenRouterClient(None, Meter(), Cassette(tmp_path / 'cache', 'replay'))
    with pytest.raises(ModelCallError) as replay_error:
        await replay.chat([{'role': 'user', 'content': 'work'}])
    assert replay_error.value.response == error.value.response
    assert replay.meter.report()['reserved_usd'] == meter.report()['reserved_usd']


async def test_missing_key_fails_before_transport(tmp_path):
    def forbidden(request):
        pytest.fail('missing key touched network')
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        with pytest.raises(RuntimeError, match='API key missing'):
            await KimiClient(None, Meter(), Cassette(tmp_path), http=http).chat([])


async def test_real_local_http_record_replay_smoke(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    class Handler(BaseHTTPRequestHandler):
        requests = 0
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert self.path == '/chat/completions'
            assert body['model'] == 'kimi-for-coding'
            Handler.requests += 1
            if Handler.requests == 1:
                self.send_response(429)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Retry-After', '0.001')
                self.end_headers()
                self.wfile.write(b'{"error":{"code":"quota_exhausted"}}')
                return
            data = json.dumps({'model': 'local-synthetic-fixture', 'choices': [{'message': {'content': 'done'}}], 'usage': {'prompt_tokens': 5, 'completion_tokens': 1}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f'http://127.0.0.1:{server.server_port}'
    try:
        record = await KimiClient('development-fixture', Meter(), Cassette(tmp_path), base_url=endpoint, retry_delays=[]).chat([{'role': 'user', 'content': 'smoke'}])
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    replay = await KimiClient(None, Meter(), Cassette(tmp_path, 'replay'), base_url=endpoint).chat([{'role': 'user', 'content': 'smoke'}])
    assert replay == record
    assert replay['_belowone']['model'] == 'local-synthetic-fixture'
    assert len(record['_belowone_attempts']) == 2
    assert record['_belowone_attempts'][0]['retry_after_seconds'] == .001


async def test_charged_failed_attempt_records_real_usage(tmp_path):
    from belowone.models.kimi import ModelCallError
    response = {'model': 'served-fallback', 'error': {'message': 'generation failed'}, 'usage': {'prompt_tokens': 2, 'completion_tokens': 1, 'cost': '0.000000068'}}
    meter = Meter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500, json=response))) as http:
        with pytest.raises(ModelCallError):
            await OpenRouterClient('fake', meter, Cassette(tmp_path), http=http).chat([])
    assert Decimal(meter.report()['spent_usd']) == Decimal('0.000000068')
    assert meter.report()['reserved_usd'] == '0'


async def test_quota_pending_retry_after_survives_short_retry_exhaustion(tmp_path):
    calls = []
    def handle(request):
        calls.append(request.url.host)
        if len(calls) == 1:
            return httpx.Response(429, headers={'Retry-After': '0.001'}, json={'error': {'code': 'quota_exhausted'}})
        return httpx.Response(200, json={'model': 'served-kimi', 'choices': [{'message': {'content': 'done'}}], 'usage': {'prompt_tokens': 2, 'completion_tokens': 1}})
    meter = Meter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        kimi = KimiClient('fake', meter, Cassette(tmp_path), retry_delays=[], http=http)
        router = ModelRouter(kimi, OpenRouterClient(None, meter, Cassette(tmp_path)))
        client = router.begin_run('seed0-arm0', seed=0)
        recorded = await client.chat([{'role': 'user', 'content': 'quota pending'}])
        assert router.current is kimi
        router.end_run()
        assert router.begin_run('seed0-arm1', seed=0) is kimi
        router.end_run()
        assert router.begin_run('seed1-arm0', seed=1) is router.fallback
    assert calls == ['api.kimi.com', 'api.kimi.com']
    assert len(recorded['_belowone_attempts']) == 2
    assert recorded['_belowone_attempts'][0]['retry_after_seconds'] == .001
    def forbidden(request):
        pytest.fail('pending replay touched network')
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        replay = await KimiClient(None, Meter(), Cassette(tmp_path, 'replay'), http=http).chat([{'role': 'user', 'content': 'quota pending'}])
    assert replay == recorded


@pytest.mark.parametrize('kind,value,criteria', [
    ('choice', 'invented', {'none': 'No hard line', 'hard_line_0': 'Never cheat'}),
    ('score', -1, ['regress', 'none', 'progress']),
    ('score', 3, ['regress', 'none', 'progress']),
])
async def test_jev_rejects_answer_outside_supplied_criteria(tmp_path, kind, value, criteria):
    answer = {'type': kind, kind: value, 'confidence': .99}
    response = {'model': 'typesafe/jev-1.13', 'answers': {'answer': answer}, 'usage': {'input_tokens': 1, 'output_tokens': 0, 'cost': '0.000000042'}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))) as http:
        result = await JevClient('fake', Meter(), Cassette(tmp_path), http=http).check({}, {'answer': {'type': kind, 'criteria': criteria}})
    assert result['verdict'] == 'uncertain'


async def test_quota_pending_uses_declared_window_reset(tmp_path):
    sent = 0
    def handle(request):
        nonlocal sent
        sent += 1
        if sent == 1:
            return httpx.Response(429, json={'error': {'code': 'quota_exhausted'}})
        return httpx.Response(200, json={'model': 'served-kimi', 'choices': [{'message': {'content': 'done'}}], 'usage': {'prompt_tokens': 2, 'completion_tokens': 1}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await KimiClient('fake', Meter(), Cassette(tmp_path), retry_delays=[], window_seconds=.002, http=http).chat([])
    assert sent == 2
    assert 0 <= result['_belowone_attempts'][0]['retry_after_seconds'] <= .002


async def test_quota_bounded_outage_is_incomplete_and_replayed(tmp_path):
    from belowone.models.kimi import QuotaPending
    def handle(request):
        return httpx.Response(429, headers={'Retry-After': '1'}, json={'error': {'code': 'quota_exhausted'}})
    meter = Meter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        kimi = KimiClient('fake', meter, Cassette(tmp_path), retry_delays=[], max_quota_wait_seconds=0, http=http)
        router = ModelRouter(kimi, OpenRouterClient(None, meter, Cassette(tmp_path)))
        router.begin_run('seed0', seed=0)
        with pytest.raises(QuotaPending) as failure:
            await kimi.chat([])
        assert router.current is kimi
    assert failure.value.response['status'] == 'incomplete'
    replay = KimiClient(None, Meter(), Cassette(tmp_path, 'replay'))
    with pytest.raises(QuotaPending) as restored:
        await replay.chat([])
    assert restored.value.response == failure.value.response


async def test_transient_retry_exhaustion_does_not_mark_quota(tmp_path):
    from belowone.models.kimi import RateLimitExceeded
    def handle(request):
        return httpx.Response(429, json={'error': {'code': 'rate_limit'}})
    meter = Meter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        kimi = KimiClient('fake', meter, Cassette(tmp_path), retry_delays=[], http=http)
        router = ModelRouter(kimi, OpenRouterClient(None, meter, Cassette(tmp_path)))
        router.begin_run('seed0', seed=0)
        with pytest.raises(RateLimitExceeded):
            await kimi.chat([])
        router.end_run()
        assert router.begin_run('seed1', seed=1) is kimi


async def test_live_quota_stops_queued_calls_and_next_seed(tmp_path):
    import asyncio
    from belowone.models.kimi import ModelCallError
    sent = []
    def handle(request):
        sent.append(request.url.host)
        return httpx.Response(429, json={'error': {'code': 'quota_exhausted'}})
    meter = Meter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        kimi = KimiClient('fake', meter, Cassette(tmp_path), concurrency=1,
                          max_quota_wait_seconds=0, http=http)
        fallback = OpenRouterClient('fake', meter, Cassette(tmp_path), http=http)
        router = ModelRouter(kimi, fallback)
        router.begin_run('seed0', seed=0)
        failures = await asyncio.gather(kimi.chat([]), kimi.chat([]), return_exceptions=True)
        assert all(isinstance(error, ModelCallError) for error in failures)
        with pytest.raises(ModelCallError):
            await JevClient('fake', meter, Cassette(tmp_path), http=http).check({}, {})
        router.end_run()
        with pytest.raises(ModelCallError):
            router.begin_run('seed1', seed=1)
    assert sent == ['api.kimi.com']
    assert 'provider quota' in meter.stop_reason


async def test_live_local_request_ceiling_stops_without_wait_or_send(tmp_path):
    from belowone.models.kimi import QuotaPending
    sent = []
    def handle(request):
        sent.append(request.url.host)
        return httpx.Response(200, json={'model': 'served-kimi', 'choices': [{'message': {'content': 'OK'}}],
                                         'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})
    meter = Meter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        kimi = KimiClient('fake', meter, Cassette(tmp_path), requests_per_window=1,
                          max_quota_wait_seconds=0, http=http)
        ModelRouter(kimi, OpenRouterClient('fake', meter, Cassette(tmp_path), http=http))
        await kimi.chat([])
        with pytest.raises(QuotaPending, match='Local Kimi request-window ceiling'):
            await kimi.chat([{'role': 'user', 'content': 'next'}])
    assert sent == ['api.kimi.com']
    assert 'provider quota not observed' in meter.stop_reason


async def test_openrouter_402_halts_judge_without_another_request(tmp_path):
    from belowone.models.kimi import ModelCallError
    sent = []
    def handle(request):
        sent.append(request.url.host)
        return httpx.Response(402, json={'error': {'code': 402, 'message': 'Insufficient credits'}})
    meter = Meter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(BudgetExceeded, match='HTTP 402'):
            await JevClient('fake', meter, Cassette(tmp_path), http=http).check({}, {})
        with pytest.raises(ModelCallError):
            await KimiClient('fake', meter, Cassette(tmp_path), http=http).chat([])
    assert sent == ['openrouter.ai']
    assert meter.report()['reserved_usd'] == '0'

