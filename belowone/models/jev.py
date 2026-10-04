"""Decisions API parsing; unavailable or malformed decisions never allow."""
import math
from belowone.models.openrouter import OpenRouterClient
from belowone.models.kimi import ModelCallError


def _number(value, low=None, high=None):
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError('decision answer must be finite number')
    if low is not None and value < low or high is not None and value > high:
        raise ValueError('decision probability outside [0,1]')
    return value


class JevClient(OpenRouterClient):
    def __init__(self, api_key, meter, cassette, *, model='typesafe/jev-1.13',
                 base_url='https://openrouter.ai/api/alpha', prompt_price='0.000000042',
                 completion_price='0', context_limit=32000, timeout=10, **kwargs):
        super().__init__(api_key, meter, cassette, model=model, base_url=base_url,
                         prompt_price=prompt_price, completion_price=completion_price,
                         context_limit=context_limit, timeout=timeout, **kwargs)

    def _normalize(self, response, payload):
        response = super()._normalize(response, payload)
        # Meter malformed model answers as real paid calls too.
        try:
            answers = response.get('answers')
            if not isinstance(answers, dict) or set(answers) != set(payload['questions']):
                raise ValueError('decision answers do not match compiled questions')
            for name, answer in answers.items():
                kind = answer.get('type')
                expected = payload['questions'][name].get('type', kind)
                if kind != expected:
                    raise ValueError('decision answer has wrong type')
                if kind == 'noul':
                    _number(answer['noul'], 0, 1)
                elif kind in {'choice', 'score'}:
                    _number(answer['confidence'], 0, 1)
                    criteria = payload['questions'][name].get('criteria')
                    if kind == 'score':
                        _number(answer['score'])
                        if criteria is not None:
                            if not isinstance(criteria, list) or not criteria:
                                raise ValueError('score criteria must be nonempty ordered list')
                            _number(answer['score'], 0, len(criteria) - 1)
                    else:
                        if not isinstance(answer['choice'], str):
                            raise ValueError('decision choice must be string')
                        if criteria is not None and (not isinstance(criteria, dict) or answer['choice'] not in criteria):
                            raise ValueError('decision choice outside supplied criteria')
                    probabilities = answer.get('probabilities', {})
                    if not isinstance(probabilities, dict):
                        raise ValueError('decision probabilities must be object')
                    for probability in probabilities.values():
                        _number(probability, 0, 1)
                else:
                    raise ValueError('unknown decision answer type')
            return {**response, 'verdict': 'checked'}
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            return {**response, 'answers': {}, 'verdict': 'uncertain', 'reason': str(exc)}

    def _failure_response(self, reason, attempts, *, quota=False):
        return {'verdict': 'uncertain', 'answers': {}, 'reason': 'Jev unavailable',
                '_belowone_failure': attempts[-1], '_belowone_attempts': attempts}

    async def check(self, state, questions):
        payload = {'model': self.model, 'state': state, 'questions': questions}
        try:
            return await self._request(payload, endpoint='/decisions')
        except ModelCallError as exc:
            if self.meter.stop_reason:
                raise
            return exc.response
