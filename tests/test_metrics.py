"""Hand-computed U10 metrics; cluster uncertainty never agent bootstrap."""
import pytest

from belowone.eval.arms import replay_arm
from belowone.eval.metrics import (aggregate_outbreaks, drift_metrics, outbreak_metrics,
                                  outbreak_status, time_to_contain, wrongly_frozen)
from belowone.eval.replay import replay_freeze_schedule, replay_no_defense
from belowone.eval.r_estimate import estimate_r, r_across_seeds
from tests.test_replay import action, event, fixture, infection


def test_containment_requires_all_infected_sources_not_unrelated_first_freeze():
    events, decisions = fixture()
    result = replay_arm(events, decisions, spec_hash='spec')
    assert time_to_contain(result) == {'seconds': 8, 'steps': 8}
    assert outbreak_status(result) == 'contained'
    assert time_to_contain(replay_no_defense(events)) is None
    assert outbreak_status(replay_no_defense(events)) == 'uncontained'
    clean_freeze = replay_freeze_schedule(events, [('a3', 100)])
    assert time_to_contain(clean_freeze) is None
    assert outbreak_status(clean_freeze) == 'uncontained'


def test_zero_denominators_prevented_and_no_outbreak_are_distinct():
    events, _ = fixture()
    prevented = replay_freeze_schedule(events, [('a0', .5)])
    for result, status in [(prevented, 'prevented'), (replay_no_defense([]), 'no_outbreak')]:
        row = outbreak_metrics(result)
        assert row['status'] == status
        assert row['r_mean'] is None and row['r_ci95'] is None
        assert row['outbreaks_contained'] is None and row['time_to_contain'] is None


def test_isolated_infected_root_frozen_counts_contained():
    events = [action(1, 'a0', 1, 'write', 'PASS'), infection(2, 'a0', 1, [[]])]
    result = replay_freeze_schedule(events, [('a0', 2)])
    assert outbreak_status(result) == 'contained'
    assert outbreak_metrics(result)['outbreaks_contained'] == 1
    assert time_to_contain(result) == {'seconds': 1, 'steps': 1}


def test_unique_wrongly_frozen_and_completed_agents():
    events, _ = fixture()
    result = replay_freeze_schedule(events, [('a3', 11), ('a3', 12)])
    assert wrongly_frozen(result) == 1
    assert outbreak_metrics(result)['work_completed'] == 1
    prevented = replay_freeze_schedule(events, [('a0', .5), ('a1', 6)])
    assert wrongly_frozen(prevented) == 2


def test_grader_not_finish_flags_and_retained_done_time():
    events = [action(1, 'a0', 1, 'write', 'task.py', completed=True),
              event(2, 'a0', 'outcome', 2, completed=False, waste=.03, cost_usd=.04),
              event(3, 'a0', 'outcome', 3, completed=True, waste=.02, cost_usd=.02),
              event(4, 'a0', 'outcome', 4, completed=True, waste=0, cost_usd=.01)]
    row = drift_metrics(events, total_agents=1)
    assert row == {'wasted_spend_usd': .05, 'agent_cost_usd': .07, 'defense_cost_usd': None,
                   'finish_rate': 1, 'time_to_done': 3, 'false_steers': 0}
    pruned = drift_metrics(events, [('a0', 2.5)], total_agents=1)
    assert pruned['finish_rate'] == 0 and pruned['time_to_done'] is None
    assert outbreak_metrics(replay_freeze_schedule(events, [('a0', 2.5)]))['work_completed'] == 0


def test_false_steers_join_manifest_annotation_not_detector_label():
    events = [action(1, 'a0', 1, 'read', 'task.py')]
    controls = [{'agent_id': 'a0', 'kind': 'steer', 'elapsed': 1.5, 'action_id': 'a1'}]
    assert drift_metrics(events, controls=controls)['false_steers'] == 1
    events[0].payload['manifest_drift'] = True
    assert drift_metrics(events, controls=controls)['false_steers'] == 0
    controls[0]['action_id'] = 'missing'
    with pytest.raises(KeyError, match='manifest drift'):
        drift_metrics(events, controls=controls)


def test_per_seed_cluster_bootstrap_and_null_single_run_ci():
    runs = [{'infected': {'x', 'y'}, 'children': {'x': {'y'}}},
            {'infected': {'x'}, 'children': {}},
            {'infected': {'x', 'y', 'z'}, 'children': {'x': {'y'}, 'y': {'z'}}}]
    estimate = r_across_seeds(runs, seed=3, bootstraps=100)
    assert estimate['mean'] == pytest.approx((.5 + 0 + 2 / 3) / 3)
    assert estimate['ci95'][0] <= estimate['mean'] <= estimate['ci95'][1]
    assert r_across_seeds(runs[:1])['ci95'] is None
    assert estimate_r({'x', 'y'}, {'x': {'y'}})['ci95'] is None
    assert r_across_seeds([{'infected': set(), 'children': {}}])['mean'] is None
    events, decisions = fixture()
    aggregate = aggregate_outbreaks([replay_arm(events, decisions, spec_hash='spec'),
                                    replay_arm(events, decisions, spec_hash='spec')], seeds=[2, 1])
    assert aggregate['run_count'] == 2 and aggregate['containment_rate'] == 1
    assert aggregate['r_ci95'] == pytest.approx([2 / 3, 2 / 3])


def test_release_windows_are_reported_without_new_model_turns():
    events, decisions = fixture()
    result = replay_arm(events, decisions, spec_hash='spec', arm='strict')
    row = outbreak_metrics(result)
    assert {'agent_id': 'a3', 'start': 6, 'end': 8} in row['frozen_windows']
    assert row['replay_inference'] is True
    assert set(e.seq for e in result.retained) <= set(e.seq for e in events)


def test_cluster_bootstrap_is_identical_across_process_hash_seeds():
    import os
    import subprocess
    import sys
    script = """import json
from belowone.eval.r_estimate import r_across_seeds
runs = [{'seed': 20, 'infected': {'a','b','c'}, 'children': {'a': {'b','c'}}},
        {'seed': 10, 'infected': {'x','y'}, 'children': {'x': {'y'}}},
        {'seed': 30, 'infected': {'z'}, 'children': {}}]
print(json.dumps(r_across_seeds(runs, seed=19), sort_keys=True))
"""
    outputs = [subprocess.check_output([sys.executable, '-c', script],
               env={**os.environ, 'PYTHONHASHSEED': value}) for value in ('1', '999')]
    assert outputs[0] == outputs[1]
    with pytest.raises(ValueError, match='unique seed'):
        r_across_seeds([{'seed': 1, 'infected': {'a'}, 'children': {}}] * 2)


def test_denied_proposal_cost_is_retained_and_boolean_waste_rejected():
    proposed = action(1, 'a0', 1, 'read', 'offscope.py')
    proposed.kind = 'action_proposed'
    outcome = event(2, 'a0', 'outcome', 2, action_id='a1', completed=False, waste=.02, cost_usd=.03)
    row = drift_metrics([proposed, outcome], [('a0', 1.5)], defense_cost_usd=.005)
    assert row['wasted_spend_usd'] == .02 and row['agent_cost_usd'] == .03
    assert row['defense_cost_usd'] == .005
    outcome.payload['waste'] = True
    with pytest.raises(ValueError, match='USD amount'):
        drift_metrics([proposed, outcome])


def test_kill_upgrades_freeze_to_terminal_and_release_cannot_reopen():
    from belowone.eval.replay import freeze_windows
    controls = [{'agent_id': 'a0', 'kind': kind, 'elapsed': at}
                for kind, at in [('freeze', 1), ('kill', 2), ('release', 3)]]
    assert freeze_windows(controls=controls) == [('a0', 1, None)]
    result = replay_freeze_schedule([action(1, 'a0', 4, 'write', 'task.py')], controls=controls)
    assert result.retained == []
