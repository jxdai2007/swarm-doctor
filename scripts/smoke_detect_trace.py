"""Ops smoke (U6 tracer runtime ONLY): real event log -> nonempty trust
graph -> Tracer with injected synthetic checkers (development dependency; no
live-model claim, no real Detector/JevClient seam — that is U5's separate
cassette smoke). Covers fail-closed uncertainty AND the strict-confirmation
branch. TemporaryDirectory cleanup guaranteed."""
import asyncio
import tempfile
from pathlib import Path

from belowone.graph.trace import Tracer
from belowone.graph.trust import TrustGraph
from belowone.policy.lifecycle import Lifecycle
from belowone.runlog import EventLog

CHECKS_SEEN = []


async def fail_closed_check(agent_id):
    """Mimics Detector with unavailable fast checker + judge: uncertain deny."""
    CHECKS_SEEN.append(agent_id)
    return {"label": "violation", "allow": False, "confidence": 0.0,
            "uncertain": True, "poisoned": False,
            "reason": "Fast checker and escalation judge unavailable"}


async def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        log = EventLog(root / "events.jsonl")
        graph = TrustGraph()
        lifecycle = Lifecycle(["a0", "a1"])

        def record(agent, op, path, elapsed):
            event = log.append(agent, "action_executed", paths=[path], payload={
                "elapsed": elapsed,
                "action": {"operation": op, "paths": [path]}})
            graph.record(event)
            return event

        record("a0", "write", "x", 1)
        record("a1", "read", "x", 2)
        assert graph.edges, "smoke graph must not be empty"

        for mode, expect_a1 in (("strict", "frozen"), ("verify", "active")):
            g = TrustGraph()
            life = Lifecycle(["a0", "a1"])
            log2 = EventLog(root / f"{mode}.jsonl")

            def record2(agent, op, path, elapsed):
                e = log2.append(agent, "action_executed", paths=[path], payload={
                    "elapsed": elapsed,
                    "action": {"operation": op, "paths": [path]}})
                g.record(e)

            record2("a0", "write", "x", 1)
            record2("a1", "read", "x", 2)
            assert g.edges, f"{mode}: graph must not be empty"
            result = await Tracer(g, life, fail_closed_check, mode=mode).trace(
                "a0", last_clean_seq=0)
            assert CHECKS_SEEN[-1] == "a1", "checker never reached the contact"
            assert result["confirmed"] == [], "uncertain must never confirm"
            assert life.state("a1") == expect_a1, (mode, life.state("a1"))

        # U6 tracer runtime, injected synthetic checker (development
        # dependency; no live-model claim): strict-confirmation branch —
        # a1 precautionary-frozen, then confirmed, frontier reaches a2,
        # and a1's post-contact write is tainted.
        g = TrustGraph()
        life = Lifecycle(["a0", "a1", "a2"])
        log3 = EventLog(root / "strict-violation.jsonl")

        def record3(agent, op, path, elapsed):
            e = log3.append(agent, "action_executed", paths=[path], payload={
                "elapsed": elapsed,
                "action": {"operation": op, "paths": [path]}})
            g.record(e)

        record3("a0", "write", "x", 1)
        record3("a1", "read", "x", 2)
        record3("a1", "write", "y", 3)
        record3("a2", "read", "y", 4)

        async def violation_check(agent_id):
            CHECKS_SEEN.append(agent_id)
            return {"label": "violation", "allow": False, "confidence": 1.0,
                    "uncertain": False, "poisoned": True,
                    "reason": "Injected synthetic checker"}

        result = await Tracer(g, life, violation_check, mode="strict").trace(
            "a0", last_clean_seq=0)
        assert result["confirmed"] == ["a1", "a2"], result
        assert life.state("a1") == "frozen" and life.state("a2") == "frozen"
        assert g.tainted_writes, "confirmed taint must mark writes"
        print("SMOKE OK: nonempty graph; checker reached; uncertain -> strict "
              "keeps a1 frozen, verify leaves a1 active; strict violation "
              "confirms a1+a2 with tainted writes (U6 tracer runtime only)")


asyncio.run(main())
