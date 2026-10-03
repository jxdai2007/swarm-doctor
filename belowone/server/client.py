"""Agent transport has no operator capability and always fails closed."""
from __future__ import annotations

import math
import httpx

from belowone.events import Event

UNREACHABLE = 'Below One engine unreachable'


class EngineClient:
    def __init__(self, base_url='http://127.0.0.1:8765', *, timeout=2.0, client=None):
        url = httpx.URL(base_url)
        if url.scheme != 'http' or url.host not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('Engine transport must be local HTTP')
        self._owned = client is None
        self.client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout, trust_env=False)

    async def decide(self, agent_id, action):
        return await self._ask('/decide', {'agent_id': agent_id, 'action': action})

    async def start(self, agent_id, decision_id):
        return await self._ask('/start', {'agent_id': agent_id, 'decision_id': decision_id})

    async def _ask(self, endpoint, payload):
        try:
            response = await self.client.post(endpoint, json=payload)
            response.raise_for_status()
            answer = response.json()
            if (not isinstance(answer, dict) or type(answer.get('allow')) is not bool
                    or not isinstance(answer.get('reason'), str) or not isinstance(answer.get('decision_id'), str)
                    or answer.get('label') not in {'clean', 'drift', 'violation'}
                    or answer.get('state') not in {'active', 'steered', 'escalated', 'frozen', 'killed', 'ended'}
                    or type(answer.get('confidence')) not in (int, float)
                    or not math.isfinite(answer['confidence']) or not 0 <= answer['confidence'] <= 1
                    or answer['allow'] and answer['state'] in {'frozen', 'killed', 'ended'}):
                raise ValueError('Malformed engine decision')
            return answer
        except (httpx.HTTPError, ValueError, TypeError):
            return {'decision_id': None, 'allow': False, 'label': 'clean', 'reason': UNREACHABLE,
                    'state': 'unreachable', 'steer': None, 'confidence': 0.0, 'elapsed': None,
                    'signal': 'deny', 'layer': 'transport', 'uncertain': True}

    async def record(self, agent_id, action, result, *, decision_id, elapsed=None):
        response = await self.client.post('/record', json={'agent_id': agent_id, 'action': action,
            'result': result, 'decision_id': decision_id, 'elapsed': elapsed})
        response.raise_for_status()
        return Event.from_dict(response.json())

    async def snapshot(self):
        response = await self.client.get('/snapshot')
        response.raise_for_status()
        return response.json()

    async def aclose(self):
        if self._owned:
            await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()
