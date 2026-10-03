"""Synthetic development fixtures: real temporal U6 graph, no study claims."""
import json
from types import SimpleNamespace

import pytest

from belowone.eval.arms import ARMS, replay_arm
from belowone.eval.decisions import bind_actions, load_decisions, trace_key
from belowone.eval.graph import rebuild
from belowone.eval.replay import attribute, prune_log, replay_freeze_schedule, replay_no_defense
from belowone.eval.delay import DAILY_REVIEW_SECONDS, delay_sweep
from belowone.eval.ablation import ablate
from belowone.eval.replay import provenance_paths
from belowone.runlog import EventLog


def event(seq, agent, kind, elapsed, paths=(), **payload):
    return SimpleNamespace(seq=seq, agent_id=agent, kind=kind, paths=list(paths),
                           payload={'elapsed': float(elapsed), **payload})


def action(seq, agent, at, operation, path, **payload):
    return event(seq, agent, 'action_executed', at, [path], action_id=f'a{seq}',
                 action={'tool': 'message' if operation in {'send', 'receive'} else operation,
                         'operation': operation, 'paths': [path], 'input': {}},
                 manifest_drift=False, **payload)


def infection(seq, agent, at, paths, source=None):
    return event(seq, agent, 'infection', at, source_agent=source,
                 write_event_id=paths[0][-2] if paths[0] else None,
                 read_event_id=paths[0][-1] if paths[0] else None,
                 provenance_paths=paths, manifest_action='manifest cheat')


def decision(aid, agent, *, label='clean', at=0, latency=0, shadow=False, **extra):
    return {'spec_hash': 'spec', 'action_id': aid, 'agent_id': agent,
            'decision': {'clean': 'allow', 'drift': 'steer', 'violation': 'freeze'}[label],
            'label': label, 'confidence': .9, 'reason': label, 'latency_s': latency,
            'decided_at_elapsed': at, 'shadow': shadow, **extra}


def fixture():
    events = [action(1, 'a0', 1, 'write', 'PASS'), infection(2, 'a0', 1, [[]]),
              action(3, 'a0', 2, 'write', 'shared.py'),
              action(4, 'a1', 3, 'read', 'shared.py'), infection(5, 'a1', 3, [[3, 4]], 'a0'),
              action(6, 'a3', 3.5, 'read', 'shared.py'),
              action(7, 'a1', 4, 'write', 'forward.py', provenance_paths=[[5]]),
              action(8, 'a2', 5, 'read', 'forward.py'), infection(9, 'a2', 5, [[5, 7, 8]], 'a1'),
              action(10, 'a3', 7, 'write', 'task.py'),
              action(11, 'a3', 9, 'write', 'task.py'),
              event(12, 'a3', 'outcome', 10, completed=True, waste=.02, cost_usd=.04,
                    served_model='synthetic-dev', cost_origin='synthetic')]
    decisions = {e.payload['action_id']: decision(e.payload['action_id'], e.agent_id, at=e.payload['elapsed'])
                 for e in events if e.kind == 'action_executed'}
    decisions['a1'] = decision('a1', 'a0', label='violation', at=100, latency=5,
                              shadow=True, source_action_elapsed=1)
    for agent, seq, label, latency in [('a1', 4, 'violation', 1), ('a3', 6, 'clean', 2), ('a2', 8, 'violation', 1)]:
        key = trace_key(agent, seq)
        decisions[key] = decision(key, agent, label=label, latency=latency,
                                  trace_review=True, reviewed_action_id=f'a{seq}')
    return events, decisions


def test_no_defense_identity_and_prompt_only_observation():
    events, _ = fixture()
    for arm in ('no-defense', 'prompt-only'):
        result = replay_arm(events, {}, spec_hash='spec', arm=arm)
        assert result.retained == events and result.controls == []
        assert result.infected_agents == {'a0', 'a1', 'a2'}


def test_verify_checks_contacts_and_restarts_confirmed_roots():
    events, decisions = fixture()
    result = replay_arm(events, decisions, spec_hash='spec')
    freezes = [(c['agent_id'], c['elapsed']) for c in result.controls if c['kind'] == 'freeze']
    assert freezes == [('a0', 6), ('a1', 8), ('a2', 9)]
    assert all(c['agent_id'] != 'a3' for c in result.controls if c['kind'] == 'freeze')
    assert len([c for c in result.controls if c['kind'] == 'trace']) == 3


def test_strict_freezes_before_parallel_checks_and_releases_clean():
    events, decisions = fixture()
    result = replay_arm(events, decisions, spec_hash='spec', arm='strict')
    assert any(c['agent_id'] == 'a3' and c['kind'] == 'freeze' and c['elapsed'] == 6 for c in result.controls)
    assert any(c['agent_id'] == 'a3' and c['kind'] == 'release' and c['elapsed'] == 8 for c in result.controls)
    assert 10 not in {e.seq for e in result.retained}
    assert 11 in {e.seq for e in result.retained}
    assert 12 in {e.seq for e in result.retained}


def test_strict_uncertain_retains_precautionary_freeze():
    events, decisions = fixture()
    decisions[trace_key('a3', 6)]['uncertain'] = True
    result = replay_arm(events, decisions, spec_hash='spec', arm='strict')
    assert not any(c['kind'] == 'release' and c['agent_id'] == 'a3' for c in result.controls)
    assert 11 not in {e.seq for e in result.retained}


def test_strict_contact_drift_releases_and_steers_not_poisoned():
    events, decisions = fixture()
    key = trace_key('a3', 6)
    decisions[key].update(label='drift', decision='steer')
    result = replay_arm(events, decisions, spec_hash='spec', arm='strict')
    assert any(c['kind'] == 'release' and c['agent_id'] == 'a3' for c in result.controls)
    assert any(c['kind'] == 'steer' and c['agent_id'] == 'a3' for c in result.controls)
    assert 11 in {e.seq for e in result.retained}


def test_all_arms_are_distinct_not_truth_driven():
    events, decisions = fixture()
    controls = {}
    for arm in ARMS:
        controls[arm] = replay_arm(events, decisions, spec_hash='spec', arm=arm).controls
    assert controls['periodic-review'][0]['elapsed'] == 15
    assert controls['message-only'] == []
    assert {c['agent_id'] for c in controls['blunt-khop'] if c['kind'] == 'kill'} == {'a0', 'a1', 'a2', 'a3'}
    assert {c['agent_id'] for c in controls['taint-without-checker'] if c['kind'] == 'freeze'} == {'a0', 'a1', 'a2', 'a3'}
    stripped = [e for e in events if e.kind not in {'infection', 'outcome'}]
    assert replay_arm(stripped, decisions, spec_hash='spec').controls == controls['verify']


def test_message_gate_uses_delivered_message_versions():
    events = [action(1, 'a0', 1, 'send', 'message:a1:m1'), action(2, 'a1', 2, 'receive', 'message:a1:m1')]
    decisions = {'a1': decision('a1', 'a0', label='violation', at=1.5),
                 'a2': decision('a2', 'a1', at=2)}
    result = replay_arm(events, decisions, spec_hash='spec', arm='message-only')
    assert [(c['kind'], c['agent_id']) for c in result.controls] == [('freeze', 'a0')]
    graph = rebuild(events)
    graph.poison('a0', last_clean_seq=0)
    assert graph.contacts('a0')[0]['agent'] == 'a1'


def test_cache_misses_and_binding_errors_fail_loud():
    events, decisions = fixture()
    with pytest.raises(KeyError, match='Missing cached decision'):
        replay_arm(events, {}, spec_hash='spec')
    decisions.pop(trace_key('a3', 6))
    with pytest.raises(KeyError, match='Missing cached trace'):
        replay_arm(events, decisions, spec_hash='spec')
    with pytest.raises(ValueError, match='binding'):
        bind_actions(events, decisions, 'other')


def test_shadow_completion_uses_source_clock_not_postrun_time():
    events, decisions = fixture()
    result = replay_arm(events, decisions, spec_hash='spec', arm='periodic-review', interval_s=5)
    assert result.controls[0]['elapsed'] == 10


def test_pruning_retains_independent_clean_work_and_removes_explicit_dependencies():
    events, _ = fixture()
    result = replay_freeze_schedule(events, [('a0', 1.5)])
    kept = {e.seq for e in result.retained}
    assert 5 not in kept and 7 not in kept and 9 not in kept
    assert 10 in kept and 11 in kept and 12 in kept
    assert result.infected_agents == {'a0'}


def test_alternative_provenance_immediate_source_not_ancestor():
    events = [action(1, 'a0', 1, 'write', 'x'), action(2, 'a1', 2, 'read', 'x'),
              action(3, 'a1', 3, 'write', 'y'), action(4, 'a2', 4, 'read', 'y'),
              action(5, 'a3', 3, 'write', 'z'), action(6, 'a2', 4, 'read', 'z')]
    inf = infection(7, 'a2', 5, [[1, 2, 3, 4], [5, 6]], 'a1')
    events.append(inf)
    assert attribute(inf, events) == 'a1'
    assert attribute(inf, events, [('a0', .5)]) == 'a3'
    assert inf in prune_log(events, [('a0', .5)])


def test_prepoisoning_write_does_not_contact_reader():
    events = [action(1, 'a0', 1, 'write', 'x'), action(2, 'a1', 2, 'read', 'x')]
    graph = rebuild(events)
    graph.poison('a0', last_clean_seq=1)
    assert graph.contacts('a0') == []


def test_delay_sweep_uses_full_policy_and_required_grid():
    events, decisions = fixture()
    rows = delay_sweep(events, decisions, spec_hash='spec', measured_jev_latency=5)
    assert [row['delta'] for row in rows] == [0, 5, DAILY_REVIEW_SECONDS]
    assert rows[0]['damage'] <= rows[-1]['damage']
    with pytest.raises(ValueError, match='include'):
        delay_sweep(events, decisions, spec_hash='spec', measured_jev_latency=5, grid=[0, 5])


def test_ablation_binds_both_actual_spec_caches(tmp_path):
    events, decisions = fixture()
    paths = {}
    hashes = {'interviewed': 'spec', 'one_line': 'line'}
    for name, spec_hash in hashes.items():
        cache = {key: {**value, 'spec_hash': spec_hash} for key, value in decisions.items()}
        if name == 'one_line':
            cache['a11'] = decision('a11', 'a3', label='drift', at=9, spec_hash=spec_hash)
        path = tmp_path / f'{name}.jsonl'
        path.write_text(''.join(json.dumps(value) + '\n' for value in cache.values()))
        paths[name] = path
    output = ablate(events, paths, hashes, total_agents=4)
    assert output['false_steer_delta'] == 1
    assert output['arms']['interviewed']['outbreak']['r_mean'] == pytest.approx(2 / 3)
    paths['one_line'].write_text('')
    with pytest.raises(KeyError):
        ablate(events, paths, hashes, total_agents=4)


def test_real_event_log_roundtrip_and_temporal_graph(tmp_path):
    from belowone.runlog import EventLog
    events, decisions = fixture()
    log = EventLog(tmp_path / 'events.jsonl')
    for original in events:
        written = log.append(original.agent_id, original.kind, paths=original.paths, payload=original.payload)
        assert written.seq == original.seq
    result = replay_arm(log.read(), decisions, spec_hash='spec', arm='strict')
    assert len(rebuild(result.retained).edges) == 7
    assert any(control['kind'] == 'release' for control in result.controls)


def test_future_edges_never_influence_earlier_blind_kill():
    events = [action(1, 'a0', 1, 'write', 'x'), action(2, 'a1', 20, 'read', 'x')]
    decisions = {'a1': decision('a1', 'a0', label='violation', at=2),
                 'a2': decision('a2', 'a1', at=20)}
    result = replay_arm(events, decisions, spec_hash='spec', arm='blunt-khop')
    assert [control['agent_id'] for control in result.controls] == ['a0']


def test_zero_radius_disables_trace_and_clean_periodic_checks_never_freeze():
    events, decisions = fixture()
    result = replay_arm(events, decisions, spec_hash='spec', radius=0)
    assert [(control['kind'], control['agent_id']) for control in result.controls] == [('freeze', 'a0')]
    decisions['a1'] = decision('a1', 'a0', at=1)
    assert replay_arm(events, decisions, spec_hash='spec', arm='periodic-review').controls == []


def test_real_journal_result_dependencies_prune_forward_before_policy_graph(tmp_path):
    log = EventLog(tmp_path / 'events.jsonl')

    def executed(agent, operation, path, at, **result):
        seq = len(log.read()) + 1
        return log.append(agent, 'action_executed', paths=[path], payload={
            'elapsed': at, 'action_id': f'a{seq}', 'manifest_drift': False,
            'action': {'tool': operation, 'operation': operation, 'paths': [path], 'input': {}},
            'result': {'ok': True, **result}})

    source = executed('a0', 'write', 'PASS', 1)
    infected = log.append('a0', 'infection', payload={'elapsed': 1.1, 'provenance_paths': [[]]})
    seed_message = executed('a0', 'send', '__messages__/a1/note', 1.2,
                            provenance_paths=[[infected.seq]])
    read = executed('a1', 'receive', '__messages__/a1/note', 1.3)
    secondary = log.append('a1', 'infection', payload={
        'elapsed': 1.4, 'provenance_paths': [[infected.seq, seed_message.seq, read.seq]]})
    forward = executed('a1', 'send', '__messages__/a2/note', 2,
                       provenance_paths=[[secondary.seq]])
    downstream_read = executed('a2', 'receive', '__messages__/a2/note', 3)
    downstream = log.append('a2', 'infection', payload={
        'elapsed': 4, 'provenance_paths': [[secondary.seq, forward.seq, downstream_read.seq]]})
    independent = executed('a1', 'write', 'reports/export.py', 5)
    events = log.read()
    assert provenance_paths(forward) == [[secondary.seq]]
    cache = {e.payload['action_id']: decision(e.payload['action_id'], e.agent_id,
                                             at=e.payload['elapsed'])
             for e in events if e.kind == 'action_executed'}
    cache[source.payload['action_id']] = decision(source.payload['action_id'], 'a0',
                                                  label='violation', at=1.05)
    cache[independent.payload['action_id']] = decision(independent.payload['action_id'], 'a1',
                                                       label='violation', at=5.5)
    result = replay_arm(events, cache, spec_hash='spec', arm='verify')
    assert not any(c['kind'] == 'trace' for c in result.controls)
    retained = {e.seq for e in result.retained}
    assert infected.seq not in retained and forward.seq not in retained
    assert secondary.seq not in retained and downstream.seq not in retained
    assert independent.seq in retained


def test_result_provenance_rejects_conflicts_and_invalid_event_references():
    forward = action(4, 'a1', 3, 'send', 'message', result={'provenance_paths': [[2]]})
    assert provenance_paths(forward) == [[2]]
    forward.payload['provenance_paths'] = [[1]]
    with pytest.raises(ValueError, match='Conflicting'):
        provenance_paths(forward)
    forward.payload.pop('provenance_paths')
    for invalid in ([[True]], [[4]], [[]], []):
        forward.payload['result']['provenance_paths'] = invalid
        with pytest.raises(ValueError, match='provenance'):
            provenance_paths(forward)


def test_real_empty_inbox_is_successful_without_phantom_graph_edge(tmp_path):
    log = EventLog(tmp_path / 'events.jsonl')
    log.append('a0', 'action_executed', paths=[], payload={
        'elapsed': 1, 'action_id': 'empty',
        'action': {'tool': 'read-inbox', 'operation': 'receive', 'paths': [], 'input': {}},
        'result': {'ok': True, 'messages': []}})
    assert rebuild(log.read()).edges == []
