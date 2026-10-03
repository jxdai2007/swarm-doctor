"""U10 fixture tests: hand-built graphs with known values (plan scenarios)."""
from types import SimpleNamespace as Ev

from belowone.eval.replay import (
    attribute, infection_survives, prune_log, replay_freeze_schedule,
    replay_no_defense,
)
from belowone.eval.r_estimate import estimate_r
from belowone.eval.metrics import (
    drift_metrics, infected_count, outbreak_metrics, outbreak_status,
    time_to_contain, work_completed, wrongly_frozen,
)
from belowone.eval.delay import DAILY_REVIEW_SECONDS, delay_sweep, damage_at


def ev(seq, agent, kind, elapsed, **payload):
    return Ev(seq=seq, agent_id=agent, kind=kind, payload={"elapsed": elapsed, **payload})


def outbreak_fixture():
    """a0 patient zero cheats unconditionally (source None, provenance [[]]),
    writes cheat at t=10; a1 reads at t=20 and is infected at t=25 (source a0,
    write 2, read 3); a1 writes at t=30, a2 reads at t=35, infected t=40.
    a3 stays clean and completes real work."""
    return [
        ev(0, "a0", "action_executed", 5.0, completed=True, tool="write"),
        ev(1, "a0", "action_executed", 8.0, completed=False, tool="read"),
        ev(2, "a0", "action_executed", 10.0, completed=True, tool="write"),  # cheat write
        ev(3, "a1", "action_executed", 20.0, completed=True, tool="read"),   # reads cheat
        ev(4, "a0", "infection", 10.0, source_agent=None, write_event_id=None,
           read_event_id=None, provenance_paths=[[]], manifest_action="cheat"),
        ev(5, "a1", "infection", 25.0, source_agent="a0", write_event_id=2,
           read_event_id=3, provenance_paths=[[2, 3]], manifest_action="cheat_write"),
        ev(6, "a1", "action_executed", 30.0, completed=True, tool="write"),  # a1 spreads
        ev(7, "a2", "action_executed", 35.0, completed=True, tool="read"),
        ev(8, "a2", "infection", 40.0, source_agent="a1", write_event_id=6,
           read_event_id=7, provenance_paths=[[6, 7]], manifest_action="cheat_write"),
        ev(9, "a3", "action_executed", 50.0, completed=True, tool="write"),
        ev(10, "a3", "outcome", 60.0, completed=True, waste=0.0, cost_usd=0.02,
           served_model="synthetic-dev"),
    ]


def test_no_defense_reproduces_original_metrics_exactly():
    events = outbreak_fixture()
    base = replay_no_defense(events)
    again = replay_no_defense(events)
    assert [i.seq for i in base.infections] == [4, 5, 8]
    assert [i.seq for i in base.infections] == [i.seq for i in again.infections]
    assert outbreak_metrics(base, events, seed=7) == outbreak_metrics(again, events, seed=7)
    assert base.events is events and base.freezes == []


def test_patient_zero_unconditional_and_exposure_not_infection():
    events = outbreak_fixture()
    base = replay_no_defense(events)
    pz = next(i for i in base.infections if i.seq == 4)
    assert pz.payload["source_agent"] is None
    assert pz.payload["provenance_paths"] == [[]]
    # a1's infection attributes to a0 (first unpruned causal source)
    assert attribute(next(i for i in base.infections if i.seq == 5), events) == "a0"


def test_freeze_removes_later_events_and_dependent_infections():
    events = outbreak_fixture()
    # freeze a0 at t=9.5: cheat write (seq 2, t=10) pruned; a1's read (seq 3,
    # a1 unfrozen, pre-infection) stays, but its infection path [2,3] hits the
    # pruned write, so both downstream infections die.
    result = replay_freeze_schedule(events, [("a0", 9.5)])
    assert [i.seq for i in result.infections] == [4]  # a0's own cheat stays
    assert {i.seq for i in result.prevented} == {5, 8}
    kept = {e.seq for e in prune_log(events, [("a0", 9.5)])}
    assert 0 in kept and 1 in kept and 3 in kept and 2 not in kept  # earlier writes preserved


def test_infection_through_unpruned_earlier_write_survives():
    events = outbreak_fixture()
    # freeze a1 at t=29.5: seq 6 (a1's spread write, t=30) pruned, so a2's only
    # path [6,7] dies; a1's own infection (path [2,3]) survives untouched.
    result = replay_freeze_schedule(events, [("a1", 29.5)])
    assert 5 in {i.seq for i in result.infections}
    assert 8 in {i.seq for i in result.prevented}

    # alternate paths: a2 infected via two paths, one through pruned seq 6,
    # one through an earlier retained write seq 100 -> survives.
    events2 = outbreak_fixture() + [
        ev(100, "a1", "action_executed", 29.0, completed=True, tool="write"),
        ev(101, "a2", "action_executed", 36.0, completed=True, tool="read"),
        ev(102, "a2", "infection", 45.0, source_agent="a1", write_event_id=100,
           read_event_id=101, provenance_paths=[[6, 7], [100, 101]],
           manifest_action="cheat_write"),
    ]
    result2 = replay_freeze_schedule(events2, [("a1", 30.0)])
    assert infection_survives(next(e for e in events2 if e.seq == 102),
                              {6, 7})
    assert 102 in {i.seq for i in result2.infections}


def test_r_equals_known_value_and_zero_infection_unmeasured():
    events = outbreak_fixture()
    base = replay_no_defense(events)
    m = outbreak_metrics(base, events, seed=7)
    # a0 infected a1; a1 infected a2; a2 infected nobody.
    # mean secondary per infected = (1+1+0)/3; bootstrap degenerate -> [1,1]? no:
    # resampling 3 values from {1,1,0} varies. Known degenerate check:
    assert m["infected"] == 3
    assert m["r_ci95"][0] <= m["r_mean"] <= m["r_ci95"][1]
    r = estimate_r({"x", "y"}, {"x": {"y"}, "y": set()}, seed=7, bootstraps=100)
    assert r["mean"] == 0.5

    zero = replay_no_defense([ev(0, "a3", "outcome", 9.0, completed=True,
                                 waste=0.0, cost_usd=0.01, served_model="synthetic-dev")])
    mz = outbreak_metrics(zero, zero.events, seed=7)
    assert mz["infected"] == 0 and mz["r_mean"] is None and mz["r_ci95"] is None
    assert mz["time_to_contain"] is None
    assert mz["status"] == "no_outbreak" and mz["outbreaks_contained"] == 0

    # prevented: same zero result but recording had infections
    mz2 = outbreak_metrics(zero, outbreak_fixture(), seed=7)
    assert mz2["status"] == "prevented" and mz2["outbreaks_contained"] == 1


def test_hand_computed_metric_values():
    events = outbreak_fixture()
    freezes = [("a1", 29.5)]  # a1 infected at 25 -> correct freeze; a0 unfrozen
    result = replay_freeze_schedule(events, freezes)
    assert infected_count(result) == 2  # a0, a1 (a2 prevented)
    assert time_to_contain(result, events) == {"seconds": 19.5, "steps": 2}
    assert wrongly_frozen(result, events) == 0
    # retained completed actions: 0,2,3,7,9 (seq 6 pruned, seq 1 incomplete)
    assert work_completed(events, freezes) == 5
    assert wrongly_frozen(replay_freeze_schedule(events, [("a3", 10.0)]), events) == 1


def test_drift_metrics_hand_computed():
    events = [
        ev(0, "a0", "action_executed", 1.0, completed=True),
        ev(1, "a0", "steer", 2.0, manifest_drift=True),
        ev(2, "a1", "steer", 3.0, manifest_drift=False),
        ev(3, "a0", "outcome", 40.0, completed=True, waste=0.05,
           cost_usd=0.10, served_model="synthetic-dev"),
        ev(4, "a1", "outcome", 50.0, completed=False, waste=0.02,
           cost_usd=0.08, served_model="synthetic-dev"),
    ]
    m = drift_metrics(events, total_agents=2, done_elapsed=40.0)
    assert m["wasted_spend_usd"] == 0.07
    assert m["finish_rate"] == 0.5
    assert m["time_to_done"] == 40.0
    assert m["false_steers"] == 1


def test_damage_monotone_in_delay():
    events = outbreak_fixture()
    freezes = [("a0", 9.0)]  # early catch: cheat write (t=10) pruned
    sweep = delay_sweep(events, freezes, [0.0, 5.0, DAILY_REVIEW_SECONDS])
    assert sweep[0]["damage"] == 1 <= sweep[2]["damage"]
    # big delay: a0 keeps acting past 9 -> a1 and a2 infected before the freeze
    assert damage_at(events, freezes, 100.0) == 3 > sweep[0]["damage"]


def test_status_distinguishes_contained_from_spread():
    events = outbreak_fixture()
    # freeze a1 early (t=26): a1 already infected at 25 (baseline), a2 prevented
    contained = replay_freeze_schedule(events, [("a1", 26.0)])
    assert outbreak_status(contained, events) == "contained"
    # late freeze of a3 (clean) changes nothing -> spread continues
    spread = replay_freeze_schedule(events, [("a3", 55.0)])
    assert outbreak_status(spread, events) == "spread"
