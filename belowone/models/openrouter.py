"""Pinned, capped OpenRouter chat client using shared record/replay transport."""
from decimal import Decimal

from belowone.models.kimi import KimiClient


class OpenRouterClient(KimiClient):
    provider = 'openrouter'

    def __init__(self, api_key, meter, cassette, *, model='mistralai/mistral-nemo',
                 prompt_price='0.000000019', completion_price='0.00000003',
                 context_limit=131072, base_url='https://openrouter.ai/api/v1',
                 reasoning_enabled=None, json_object=False, **kwargs):
        self.prompt_price = Decimal(str(prompt_price))
        if reasoning_enabled is not None and type(reasoning_enabled) is not bool:
            raise ValueError('reasoning_enabled must be boolean or None')
        self.reasoning_enabled = reasoning_enabled
        if type(json_object) is not bool:
            raise ValueError('json_object must be boolean')
        self.json_object = json_object
        self.completion_price = Decimal(str(completion_price))
        if any(not price.is_finite() or price < 0 for price in (self.prompt_price, self.completion_price)):
            raise ValueError('token prices must be finite and nonnegative')
        if type(context_limit) is not int or context_limit < 1:
            raise ValueError('context limit must be positive integer')
        self.context_limit = context_limit
        super().__init__(api_key, meter, cassette, model=model, base_url=base_url, **kwargs)

    async def _request(self, payload, *, endpoint='/chat/completions'):
        if self.json_object and endpoint == '/chat/completions':
            payload['response_format'] = {'type': 'json_object'}
        if self.reasoning_enabled is not None:
            payload['reasoning'] = {'enabled': self.reasoning_enabled}
        return await super()._request(payload, endpoint=endpoint)

    def _reserve(self, payload):
        # Reserve full provider context: byte/token estimates cannot guarantee caps.
        bound = self.context_limit * self.prompt_price + payload.get('max_tokens', 0) * self.completion_price
        return self.meter.reserve(self.provider, bound)
