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
