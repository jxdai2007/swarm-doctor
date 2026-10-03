"""U13 dashboard data tests: counters equal U10 metrics for the fixture run."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.make_fixture_runs import ARMS, outbreak_fixture, write_run  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from belowone.eval.replay import replay_freeze_schedule  # noqa: E402
from belowone.eval.metrics import outbreak_metrics, drift_metrics  # noqa: E402
from belowone.dashboard_data import snapshot, counters  # noqa: E402


def _fixture_result():
    events = outbreak_fixture()
    return events, replay_freeze_schedule(events, ARMS["below-one-verify"])


def test_counters_equal_u10_metrics_for_fixture_run():
    events, result = _fixture_result()
    outbreak = outbreak_metrics(result, events, seed=0)
    drift = drift_metrics(events, ARMS["below-one-verify"], total_agents=4)
    rows = {r["source"]: r["value"] for r in counters(outbreak, drift)}
    # canonical U10: a0's pre-cheat freeze removes a0/a1, but a1's retained
    # independent write still reaches a2 -> exactly one infection survives
    assert rows["metrics.infected"] == outbreak["infected"] == 1
    assert rows["metrics.r_mean"] == (outbreak["r_mean"]
                                      if outbreak["r_mean"] is not None
                                      else "unmeasured (no infections)")
    if outbreak["time_to_contain"]:
        assert rows["metrics.time_to_contain.seconds"] == \
            outbreak["time_to_contain"]["seconds"]
    assert rows["metrics.wasted_spend_usd"] == drift["wasted_spend_usd"]
    assert rows["metrics.false_steers"] == drift["false_steers"]
    # canonical provenance semantics: even a pre-cheat freeze leaves a2
    # reached through a1's retained independent write — R measured, not 0
    empty = replay_freeze_schedule(events, [("a0", 0.0)])
    z = outbreak_metrics(empty, events, seed=0)
    zrows = {r["source"]: r["value"] for r in counters(z)}
    assert zrows["metrics.infected"] == 1
    assert zrows["metrics.r_mean"] == 0.0
    # a genuinely zero-infection recording surfaces the unmeasured row
    from types import SimpleNamespace
    bare = [SimpleNamespace(seq=0, agent_id="a3", kind="outcome", paths=[],
                            payload={"elapsed": 9.0, "completed": True})]
    bresult = replay_freeze_schedule(bare, [])
    bz = outbreak_metrics(bresult, bare, seed=0)
    assert any("unmeasured" in str(row) for row in counters(bz))


def test_snapshot_labels_synthetic_and_carries_event_stream():
    events = outbreak_fixture()
    result = replay_freeze_schedule(events, ARMS["no-defense"])
    snap = snapshot(result.events, ARMS["no-defense"],
                    outbreak_metrics(result, events, seed=0))
    assert snap["synthetic"] is True
    assert set(snap["agents"]) == {"a0", "a1", "a2", "a3"}
    assert any(e["kind"] == "infection" for e in snap["events"])


def _state_at(snap_events, t):
    inf, frozen = set(), set()
    for e in snap_events:
        if (e.get("elapsed") or 0) > t:
            break
        if e["kind"] == "infection":
            inf.add(e["agent_id"])
        elif e["kind"] == "freeze":
            frozen.add(e["agent_id"])
    return inf, frozen


def test_fixture_runs_written_and_split_panels_share_relative_time(tmp_path):
    for arm in ARMS:
        write_run(tmp_path, arm)
    panels = {}
    for arm in ARMS:
        d = tmp_path / f"{arm}-fixture-outbreak"
        snap = json.loads((d / "snapshot.json").read_text())
        panels[arm] = snap
        assert snap["synthetic"] is True
    # all panels share one relative axis: the max event time across panels
    max_t = max((e.get("elapsed") or 0)
                for s in panels.values() for e in s["events"])
    for t in (5.0, 26.0, 45.0, max_t):
        states = {arm: _state_at(p["events"], t) for arm, p in panels.items()}
        # canonical U10: a0 frozen from 9.5; a1 prevented; a2 still reached
        inf_v, frozen_v = states["below-one-verify"]
        assert inf_v == ({"a2"} if t >= 40.0 else set())
        assert frozen_v == ({"a0"} if t >= 9.5 else set())
        # no-defense never freezes
        assert states["no-defense"][1] == set()


def test_strict_control_records_release_restores_active_and_retains_next_legit():
    """Actual U10 strict control records: freeze@1, release@2 -> the next
    recorded legit action at t=3 is RETAINED, agent state clean, released
    counter 1, frozen counter 0."""
    from belowone.eval.replay import replay_freeze_schedule
    from belowone.viz.cards import cites_metrics

    def ev(seq, agent, kind, elapsed, **payload):
        from types import SimpleNamespace
        paths = payload.pop("paths", [])
        payload.setdefault("action", {"operation": "write",
                                      "paths": paths})
        return SimpleNamespace(seq=seq, agent_id=agent, kind=kind,
                               paths=paths, payload={"elapsed": elapsed,
                                                     **payload})
    events = [
        ev(0, "a0", "action_executed", 0.5, completed=True, tool="write",
           paths=["reports/export.py"], action_id="x1"),
        ev(1, "a0", "action_executed", 1.0, completed=True, tool="write",
           paths=["utils/formatting.py"], action_id="x2"),
        ev(2, "a0", "action_executed", 3.0, completed=True, tool="write",
           paths=["reports/export.py"], action_id="x3"),
        ev(3, "a0", "outcome", 4.0, completed=True, waste=0.0, cost_usd=0.01),
    ]
    controls = [
        {"agent_id": "a0", "kind": "freeze", "elapsed": 1.0, "order": 1},
        {"agent_id": "a0", "kind": "release", "elapsed": 2.0, "order": 2},
    ]
    result = replay_freeze_schedule(events, controls=controls)
    snap = snapshot(result.events, result.controls,
                    {"infected": 0, "r_mean": None}, synthetic=True)
    assert snap["agents"]["a0"] == "clean"          # released -> active
    assert [c["kind"] for c in snap["controls"]] == ["freeze", "release"]
    assert sum(c["kind"] == "release" for c in snap["controls"]) == 1
    # the post-release legit action survives pruning, full payload retained
    kept_x3 = next(e for e in snap["events"] if e["seq"] == 2)
    assert kept_x3["payload"].get("action_id") == "x3"
    assert kept_x3["paths"] == ["reports/export.py"]
    assert snap["counters"][0]["value"] == 0  # sourced row, no fake metrics
