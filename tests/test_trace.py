import asyncio

from belowone.graph.trust import TrustGraph
from belowone.graph.trace import Tracer
from belowone.policy.lifecycle import Lifecycle
from belowone.runlog import EventLog


def add(log, graph, agent, operation, path, elapsed):
    event = log.append(agent, 'action_executed', paths=[path], payload={'elapsed': elapsed, 'action': {'operation': operation, 'paths': [path]}})
    graph.record(event)
    return event


async def test_old_writes_not_reached(tmp_path):
    graph = TrustGraph()
    log = EventLog(tmp_path / 'events.jsonl')
    add(log, graph, 'a0', 'write', 'utils.py', 0)
    read = add(log, graph, 'a2', 'read', 'utils.py', 5)
    graph.poison('a0', last_clean_seq=read.seq)
    assert graph.contacts('a0') == []


async def test_verify_clean_and_strict_release(tmp_path):
    for mode in ['verify', 'strict']:
        graph = TrustGraph()
        lifecycle = Lifecycle(['a0', 'a1'])
        log = EventLog(tmp_path / f'{mode}.jsonl')
        add(log, graph, 'a0', 'write', 'x', 1)
        add(log, graph, 'a1', 'read', 'x', 2)
        observed = []
        async def check(agent):
            observed.append(lifecycle.state(agent))
            return {'label': 'clean', 'confidence': .99, 'uncertain': False}
        result = await Tracer(graph, lifecycle, check, mode=mode).trace('a0', last_clean_seq=0)
        assert result['reached'] == ['a1'] and result['confirmed'] == []
        assert observed == (['frozen'] if mode == 'strict' else ['active'])
        assert lifecycle.state('a1') == 'active'
        assert graph.trust['agent:a1'] == (1 if mode == 'strict' else 0)


async def test_confirmed_trace_continues_and_radius_scales(tmp_path):
    for confidence, expected in [(1, ['a1', 'a2']), (.5, ['a1'])]:
        graph = TrustGraph()
        lifecycle = Lifecycle(['a0', 'a1', 'a2', 'a3'])
        log = EventLog(tmp_path / f'{confidence}.jsonl')
        for index in range(3):
            add(log, graph, f'a{index}', 'write', f'f{index}', index * 2)
            add(log, graph, f'a{index+1}', 'read', f'f{index}', index * 2 + 1)
        async def check(agent):
            return {'label': 'violation', 'confidence': 1, 'uncertain': False}
        result = await Tracer(graph, lifecycle, check, radius=2).trace('a0', last_clean_seq=0, confidence=confidence)
        assert result['reached'] == expected
        assert all(lifecycle.state(agent) == 'frozen' for agent in expected)
        assert lifecycle.state('a3') == 'active'


async def test_ring_checks_parallel(tmp_path):
    graph = TrustGraph()
    lifecycle = Lifecycle(['a0', 'a1', 'a2'])
    log = EventLog(tmp_path / 'events.jsonl')
    add(log, graph, 'a0', 'write', 'x', 1)
    add(log, graph, 'a1', 'read', 'x', 2)
    add(log, graph, 'a2', 'read', 'x', 3)
    active = maximum = 0
    async def check(agent):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(.01)
        active -= 1
        return {'label': 'clean', 'uncertain': False}
    await Tracer(graph, lifecycle, check).trace('a0', last_clean_seq=0)
    assert maximum == 2


def test_version_direction_and_low_water(tmp_path):
    graph = TrustGraph()
    log = EventLog(tmp_path / 'events.jsonl')
    add(log, graph, 'a1', 'read', 'x', 0)
    add(log, graph, 'a0', 'write', 'x', 1)
    graph.poison('a0', last_clean_seq=0)
    assert graph.contacts('a0') == []
    add(log, graph, 'a1', 'read', 'x', 2)
    assert graph.trust['agent:a1'] == 0
    add(log, graph, 'a2', 'write', 'clean', 3)
    add(log, graph, 'a1', 'read', 'clean', 4)
    assert graph.trust['agent:a1'] == 0
    graph.release('a1')
    assert graph.trust['agent:a1'] == 1


async def test_contacted_drift_released_then_steered(tmp_path):
    graph = TrustGraph()
    lifecycle = Lifecycle(['a0', 'a1'])
    log = EventLog(tmp_path / 'events.jsonl')
    add(log, graph, 'a0', 'write', 'x', 1)
    add(log, graph, 'a1', 'read', 'x', 2)

    async def check(agent):
        return {'label': 'drift', 'confidence': .9, 'uncertain': False,
                'reason': 'Off-scope refactor'}

    for mode in ['verify', 'strict']:
        result = await Tracer(graph, lifecycle, check, mode=mode).trace('a0', last_clean_seq=0)
        assert result['confirmed'] == []
        # a1 released out of any strict freeze, then steered, still reachable
        assert lifecycle.state('a1') == 'steered'
        assert result['reached'] == ['a1']


async def test_terminal_contacts_never_frozen_or_released(tmp_path):
    graph = TrustGraph()
    lifecycle = Lifecycle(['a0', 'a1'])
    log = EventLog(tmp_path / 'events.jsonl')
    add(log, graph, 'a0', 'write', 'x', 1)
    add(log, graph, 'a1', 'read', 'x', 2)
    lifecycle.transition('a1', 'end')

    async def check(agent):
        return {'label': 'violation', 'confidence': 1, 'uncertain': False}

    result = await Tracer(graph, lifecycle, check, mode='strict').trace('a0', last_clean_seq=0)
    assert lifecycle.state('a1') == 'ended'
    assert result['confirmed'] == []


async def test_strict_precautionary_freeze_still_confirms_and_continues(tmp_path):
    graph = TrustGraph()
    lifecycle = Lifecycle(['a0', 'a1', 'a2'])
    log = EventLog(tmp_path / 'events.jsonl')
    add(log, graph, 'a0', 'write', 'x', 1)
    add(log, graph, 'a1', 'read', 'x', 2)
    add(log, graph, 'a1', 'write', 'y', 3)
    add(log, graph, 'a2', 'read', 'y', 4)
    observed = []

    async def check(agent):
        observed.append((agent, lifecycle.state(agent)))
        return {'label': 'violation', 'confidence': 1, 'uncertain': False}

    result = await Tracer(graph, lifecycle, check, mode='strict').trace('a0', last_clean_seq=0)
    # a1 was precautionary-frozen by the strict ring, then CONFIRMED anyway:
    # poison recorded, frontier extended to a2, also frozen+confirmed
    assert observed == [('a1', 'frozen'), ('a2', 'frozen')]
    assert result['confirmed'] == ['a1', 'a2']
    assert lifecycle.state('a1') == 'frozen' and lifecycle.state('a2') == 'frozen'


async def test_operator_frozen_agents_not_autoreleased(tmp_path):
    for label in ['clean', 'drift']:
        graph = TrustGraph()
        lifecycle = Lifecycle(['a0', 'a1'])
        log = EventLog(tmp_path / f'{label}.jsonl')
        add(log, graph, 'a0', 'write', 'x', 1)
        add(log, graph, 'a1', 'read', 'x', 2)
        # operator froze a1 before the trace ran: this trace must not release it
        lifecycle.transition('a1', 'freeze')

        async def check(agent):
            return {'label': label, 'confidence': .95, 'uncertain': False}

        await Tracer(graph, lifecycle, check, mode='strict').trace('a0', last_clean_seq=0)
        assert lifecycle.state('a1') == 'frozen', label


async def test_killed_contacts_skipped_like_ended(tmp_path):
    graph = TrustGraph()
    lifecycle = Lifecycle(['a0', 'a1'])
    log = EventLog(tmp_path / 'events.jsonl')
    add(log, graph, 'a0', 'write', 'x', 1)
    add(log, graph, 'a1', 'read', 'x', 2)
    lifecycle.transition('a1', 'kill')

    async def check(agent):
        return {'label': 'violation', 'confidence': 1, 'uncertain': False}

    result = await Tracer(graph, lifecycle, check, mode='strict').trace('a0', last_clean_seq=0)
    assert lifecycle.state('a1') == 'killed'
    assert result['confirmed'] == []
