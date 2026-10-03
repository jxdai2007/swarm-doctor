"""R estimation: mean secondary infections per infected agent, with bootstrap.

R is None (unmeasured) when no agent is infected — a prevented or
zero-infection run never reports R=0. Seed comes from the scenario/run seed
already pinned in config, so the same seed reproduces the same interval.
"""
from __future__ import annotations

import random


def secondary_counts(infected: set, children: dict) -> dict:
    """Per infected source: number of agents it actually infected."""
    out = {}
    for agent in infected:
        out[agent] = len(children.get(agent, ()))
    return out


def estimate_r(infected: set, children: dict, seed: int = 0,
               bootstraps: int = 10_000) -> dict:
    if not infected:
        return {"mean": None, "ci95": None, "secondary": {}}
    counts = secondary_counts(infected, children)
    values = list(counts.values())
    mean = sum(values) / len(values)
    rng = random.Random(seed)
    n = len(values)
    means = sorted(
        sum(values[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(bootstraps)
    )
    lo = means[int(0.025 * bootstraps)]
    hi = means[min(bootstraps - 1, int(0.975 * bootstraps))]
    return {"mean": mean, "ci95": [lo, hi], "secondary": counts}
