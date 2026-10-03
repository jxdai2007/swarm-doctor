"""H1 latency sweep replays complete cached policy, not a shifted stop list."""
import math

from .arms import replay_arm
from .metrics import outbreak_metrics

DAILY_REVIEW_SECONDS = 86_400.0


def delay_sweep(events, decisions, *, spec_hash, measured_jev_latency, grid=None, arm='verify'):
    if not math.isfinite(measured_jev_latency) or measured_jev_latency < 0:
        raise ValueError('Measured Jev latency must be finite and nonnegative')
    points = sorted(set([0.0, float(measured_jev_latency), DAILY_REVIEW_SECONDS] if grid is None else grid))
    if measured_jev_latency not in points or DAILY_REVIEW_SECONDS not in points or 0.0 not in points:
        raise ValueError('Delay grid must include zero, measured Jev latency, and daily review')
    if any(not math.isfinite(point) or point < 0 for point in points):
        raise ValueError('Invalid delay grid')
    rows = []
    for delta in points:
        result = replay_arm(events, decisions, spec_hash=spec_hash, arm=arm, added_latency=delta)
        rows.append({'delta': delta, 'damage': len(result.infected_agents),
                     'metrics': outbreak_metrics(result)})
    return rows
