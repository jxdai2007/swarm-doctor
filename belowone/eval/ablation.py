"""Interview ablation (U10): replay with decision caches for the interviewed
spec versus the one-line goal (H4). Decisions arrive as a map
agent_id -> freeze elapsed (or None); the arm difference shows up as
wrongly-frozen and false-steer counts, not re-judging by the judge.
"""
from __future__ import annotations

from .replay import replay_freeze_schedule
from .metrics import outbreak_metrics


def ablate(events, decision_caches: dict[str, list], seed: int = 0) -> dict:
    """decision_caches: {"interviewed": freezes, "one_line": freezes}.
    Returns per-arm metrics plus the deltas H4 cares about."""
    arms = {}
    for name, freezes in decision_caches.items():
        result = replay_freeze_schedule(events, freezes)
        arms[name] = outbreak_metrics(result, events, seed=seed)
    return {
        "arms": arms,
        "false_alarm_delta": (
            arms["one_line"]["clean_wrongly_frozen"]
            - arms["interviewed"]["clean_wrongly_frozen"]
        ),
    }
