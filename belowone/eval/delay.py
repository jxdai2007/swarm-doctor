"""Delay sweep (U10): damage versus detection delay on a fixed latency grid.

Shifting each freeze decision later by delta re-prunes the log; damage is the
surviving infection count. The grid includes measured Jev latency and a
daily-review point; actual measured values are injected by the caller from run
artifacts, never hand-typed here.
"""
from __future__ import annotations

from .replay import ReplayResult, replay_freeze_schedule

DAILY_REVIEW_SECONDS = 86_400.0  # fixed grid point; Jev latency is measured


def shift(freezes, delta: float) -> list:
    return [(agent, at + delta) for agent, at in freezes]


def damage_at(events, freezes, delta: float) -> int:
    return len(replay_freeze_schedule(events, shift(freezes, delta)).infections)


def delay_sweep(events, freezes, grid) -> list[dict]:
    """[{delta, damage}] ordered by delta; caller supplies the grid values."""
    return [{"delta": d, "damage": damage_at(events, freezes, d)} for d in grid]
