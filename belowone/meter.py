"""Worst-case preflight reservations and honest per-call usage accounting."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
import math
from threading import RLock
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from belowone.events import scrub

if TYPE_CHECKING:
    from belowone.runlog import EventLog


class BudgetExceeded(RuntimeError):
    """No request may be sent when its worst-case cost exceeds remaining credit."""


def _amount(value: Decimal | str | int | float) -> Decimal:
    try:
        if isinstance(value, bool):
            raise ValueError('amount must be a finite nonnegative decimal')
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError('amount must be a finite nonnegative decimal')
        return amount
    except (InvalidOperation, TypeError) as exc:
        raise ValueError('amount must be a finite nonnegative decimal') from exc


def _sum(values) -> Decimal:
    """Exact addition independent of caller's Decimal context/rounding precision."""
    values = list(values)
    if not values:
        return Decimal(0)
    precision = max(item.adjusted() for item in values) - min(item.as_tuple().exponent for item in values)
    with localcontext() as context:
        context.prec = max(1, precision + len(str(len(values))) + 2)
        return sum(values, Decimal(0))


def _provider(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('provider must be a nonempty string')
    return scrub(value.strip().lower())


@dataclass(frozen=True)
class Reservation:
    token: str
    provider: str
    maximum_cost: Decimal


class Meter:
    """Share one instance across calls/runs governed by the same spend cap.

    OpenRouter calls require reserve() before network access. record() settles
    actual charges, including charged failures; cancel() only releases requests
    known not to have incurred charges. The caller supplies a true upper bound
    including input, maximum output, tools, and provider fees.
    """

    def __init__(self, cap_usd: Decimal | str | int | float = '15', *,
                 event_log: 'EventLog | None' = None, agent_id: str = 'meter'):
        self.cap_usd = _amount(cap_usd)
        self.event_log = event_log
        self.agent_id = agent_id
        self._lock = RLock()
        self._pending: dict[str, Reservation] = {}
        self._calls: list[dict[str, Any]] = []
        self._breached = False
        self.stop_reason = None

    def _spent(self) -> Decimal:
        return _sum(Decimal(call['cost_usd']) for call in self._calls if call['provider'] == 'openrouter')

    def _reserved(self) -> Decimal:
        return _sum(hold.maximum_cost for hold in self._pending.values() if hold.provider == 'openrouter')

    def reserve(self, provider: str, maximum_cost: Decimal | str | int | float) -> Reservation:
        provider = _provider(provider)
        maximum_cost = _amount(maximum_cost)
        with self._lock:
            if provider == 'openrouter' and (self._breached or
                    _sum((self._spent(), self._reserved(), maximum_cost)) > self.cap_usd):
                self.stop_reason = 'OpenRouter worst-case reservation exceeds remaining cap; campaign stopped'
                raise BudgetExceeded('OpenRouter worst-case request would exceed spend cap')
            hold = Reservation(uuid4().hex, provider, maximum_cost)
            self._pending[hold.token] = hold
            return hold

    def _hold(self, reservation: Reservation, provider: str | None = None) -> Reservation:
        if not isinstance(reservation, Reservation) or self._pending.get(reservation.token) is not reservation:
            raise ValueError('unknown, foreign, or already settled reservation')
        if provider is not None and reservation.provider != provider:
            raise ValueError('reservation provider does not match call')
        return reservation

    def cancel(self, reservation: Reservation) -> None:
        """Release an unsent/uncharged request; charged failures must use record()."""
        with self._lock:
            hold = self._hold(reservation)
            del self._pending[hold.token]

    def record(self, provider: str, model: str, input_tokens: int, output_tokens: int,
               cost: Decimal | str | int | float, latency: float,
               reservation: Reservation | None = None) -> dict[str, Any]:
        provider = _provider(provider)
        if not isinstance(model, str) or not model:
            raise ValueError('served model must be a nonempty string')
        if any(type(count) is not int or count < 0 for count in (input_tokens, output_tokens)):
            raise ValueError('token counts must be nonnegative integers')
        if type(latency) not in (float, int) or not math.isfinite(latency) or latency < 0:
            raise ValueError('latency must be finite nonnegative seconds')
        cost = _amount(cost)
        call = scrub({'provider': provider, 'model': model, 'input_tokens': input_tokens,
                      'output_tokens': output_tokens, 'cost_usd': str(cost), 'latency': float(latency)})
        with self._lock:
            hold = self._hold(reservation, provider) if reservation is not None else None
            if provider == 'openrouter' and hold is None:
                raise ValueError('OpenRouter call requires a preflight reservation')
            underestimated = hold is not None and cost > hold.maximum_cost
            if hold is not None:
                del self._pending[hold.token]
            # Even a provider charge above its bound is real spend. Never hide it.
            self._calls.append(call)
            if provider == 'openrouter' and (underestimated or self._spent() > self.cap_usd):
                self._breached = True
            if self.event_log is not None:
                self.event_log.append(self.agent_id, 'model_call', payload=call)
            if underestimated:
                raise BudgetExceeded('actual charge exceeded reservation; recorded honestly')
            return dict(call)

    def report(self) -> dict[str, Any]:
        with self._lock:
            providers = {}
            for provider in sorted({call['provider'] for call in self._calls}):
                calls = [call for call in self._calls if call['provider'] == provider]
                providers[provider] = {
                    'calls': len(calls), 'input_tokens': sum(call['input_tokens'] for call in calls),
                    'output_tokens': sum(call['output_tokens'] for call in calls),
                    'cost_usd': str(_sum(Decimal(call['cost_usd']) for call in calls)),
                    'latency': math.fsum(call['latency'] for call in calls),
                }
            return scrub({'cap_usd': str(self.cap_usd), 'spent_usd': str(self._spent()),
                          'reserved_usd': str(self._reserved()), 'budget_breached': self._breached,
                          'stop_reason': self.stop_reason,
                          'providers': providers, 'calls': [dict(call) for call in self._calls]})
