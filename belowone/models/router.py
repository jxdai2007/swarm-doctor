"""Quota switches occur between seeds, never between arms or inside runs."""
from belowone.meter import BudgetExceeded


class ModelRouter:
    def __init__(self, kimi, fallback):
        if kimi.meter is not fallback.meter:
            raise ValueError('router providers must share one spend meter')
        self.kimi, self.fallback = kimi, fallback
        self.current = None
        self.run_id = None
        self.seed = None
        self._seed_provider = None
        self._quota_exhausted = False
        kimi.on_quota_exhausted = self.quota_exhausted

    def quota_exhausted(self):
        self._quota_exhausted = True
        if self.kimi.max_quota_wait_seconds == 0:
            self.kimi.meter.stop_reason = self.kimi.meter.stop_reason or 'Kimi provider quota exhausted; campaign stopped without retry or fallback'

    def begin_run(self, run_id, *, seed=None):
        if self.run_id is not None:
            raise RuntimeError('run already active')
        if self.kimi.meter.stop_reason:
            from belowone.models.kimi import QuotaPending
            raise QuotaPending(self.kimi.meter.stop_reason)
        report = self.kimi.meter.report()
        from decimal import Decimal
        if report['budget_breached'] or Decimal(report['spent_usd']) + Decimal(report['reserved_usd']) >= Decimal(report['cap_usd']):
            raise BudgetExceeded('OpenRouter cap reached; refusing new run')
        seed = run_id if seed is None else seed
        if self._seed_provider is None or seed != self.seed:
            self._seed_provider = self.fallback if self._quota_exhausted else self.kimi
            self.seed = seed
        self.current = self._seed_provider
        self.run_id = run_id
        return self.current

    def end_run(self):
        if self.run_id is None:
            raise RuntimeError('no active run')
        self.run_id = None
        self.current = None
