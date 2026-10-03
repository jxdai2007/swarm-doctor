"""Ops smoke: Detector fail-closed chain into Tracer (U5->U6, offline)."""
import asyncio
import pathlib
import tempfile

from belowone.detect.classify import Detector
from belowone.graph.trace import Tracer
from belowone.graph.trust import TrustGraph
from belowone.meter import Meter
from belowone.models.cassette import Cassette
from belowone.models.jev import JevClient
from belowone.policy.lifecycle import Lifecycle
from belowone.spec.schema import GoalSpec

SPEC = {
    "goal": "build csv tool", "done_when": ["tool passes"],
    "hard_lines": ["no .env.production writes"],
    "gray_zones": {"refactor helpers": "deny"},
    "compartments": {}, "budgets": {"steps_per_agent": 20, "cost_usd": 1.0},
    "decoys": [".env.production"],
    "high_risk_paths": [], "response_mode": "verify", "trace_radius": 2,
}


async def main():
    spec = GoalSpec.from_dict(SPEC)
    # empty replay cassette: every Jev call misses -> Detector must fail closed
    d = Detector(spec, JevClient(None, Meter(),
                                 Cassette(pathlib.Path(tempfile.mkdtemp()), "replay")))
    r = await d.check("a0", {"tool": "write", "operation": "write",
                             "paths": ["ok.py"]}, trace=True)
    assert r["uncertain"] and r["allow"] is False and r["poisoned"] is False, r
    g, life = TrustGraph(), Lifecycle(["a0", "a1"])
    res = await Tracer(
        g, life,
        lambda a: d.check(a, {"tool": "read", "operation": "read",
                              "paths": ["x"]}, trace=True),
    ).trace("a0", last_clean_seq=0)
    assert res["confirmed"] == []
    assert life.state("a1") in ("active", "steered")
    print("SMOKE OK: detector fail-closed -> tracer skip, no invented infection")


asyncio.run(main())
