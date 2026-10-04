"""Real tracked file execution, cancellation settlement and exposure reviews."""
import asyncio

import pytest

from belowone.detect.classify import Detector
from belowone.engine import Engine
from belowone.eval.graph import rebuild
from belowone.runlog import EventLog
from belowone.spec.schema import GoalSpec
from tests.test_detect import FastFixture


def make_engine(tmp_path, *, fast=None):
    spec = GoalSpec(goal='Add CSV export', done_when=['CSV cases pass'],
                    decoys=['.env.production'], compartments={'a0': ['**'], 'a1': ['**'], 'a2': ['**']},
                    workspace=tmp_path)
    return Engine(spec, EventLog(tmp_path / 'events.jsonl'), Detector(spec, fast or FastFixture()),
                  mode='strict', synthetic=True)


def action(operation, *paths):
    return {'tool': operation, 'operation': operation, 'paths': list(paths), 'input': {}}


async def test_file_reader_cannot_observe_unpublished_writer(tmp_path):
    engine = make_engine(tmp_path)
    await engine.control('a0', 'off')
    write = action('write', 'shared.py')
    writer = await engine.decide('a0', write)
    reader_action = action('read', 'shared.py')
    reader = await engine.decide('a1', reader_action)
    physical_write, publish = asyncio.Event(), asyncio.Event()
    path = tmp_path / 'shared.py'
    observed = []

    async def execute_writer():
        assert (await engine.start('a0', writer['decision_id']))['allow']
        path.write_text('new untrusted bytes')
        physical_write.set()
        await publish.wait()
        return engine.record('a0', write, {'ok': True}, decision_id=writer['decision_id'])

    async def execute_reader():
        assert (await engine.start('a1', reader['decision_id']))['allow']
        content = path.read_text()
        published = [event for event in engine.event_log.read()
                     if event.kind == 'action_executed' and event.agent_id == 'a0']
        observed.append((content, published[-1].seq))
        return engine.record('a1', reader_action, {'ok': True, 'content': content},
                             decision_id=reader['decision_id'])

    writing = asyncio.create_task(execute_writer())
    await physical_write.wait()
    reading = asyncio.create_task(execute_reader())
    try:
        await asyncio.sleep(.02)
        assert path.read_text() == 'new untrusted bytes'
        assert not reading.done() and not observed
        await engine.control('a0', 'on')
        assert not (await engine.decide('a0', action('read', '.env.production')))['allow']
        publish.set()
        written = await asyncio.wait_for(writing, 1)
        read = await asyncio.wait_for(reading, 1)
        assert observed == [('new untrusted bytes', written.seq)]
        assert written.seq < read.seq
        assert engine.graph.contacts('a0')[0]['seq'] == read.seq
        assert (await engine.ready('a1'))['allow']
        assert any(event.payload.get('trace_review') for event in engine.event_log.read())
        assert engine.graph.snapshot() == rebuild(engine.event_log.read()).snapshot()
    finally:
        publish.set()
        await asyncio.gather(writing, reading, return_exceptions=True)


@pytest.mark.parametrize('effects', ['observed', 'possible'])
async def test_cancelled_partial_writer_publishes_failed_causal_effects(tmp_path, effects):
    engine = make_engine(tmp_path)
    await engine.control('a0', 'off')
    write = action('write', 'shared.py', 'uncertain.py')
    writer = await engine.decide('a0', write)
    read_action = action('read', 'shared.py')
    reader = await engine.decide('a1', read_action)
    changed = asyncio.Event()
    path = tmp_path / 'shared.py'

    async def partial_writer():
        assert (await engine.start('a0', writer['decision_id']))['allow']
        try:
            path.write_text('partial malicious output')
            changed.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            engine.record('a0', write, {'ok': False, 'effects': effects, 'error': 'cancelled'},
                          decision_id=writer['decision_id'])
            raise

    writing = asyncio.create_task(partial_writer())
    await changed.wait()
    await engine.control('a0', 'on')
    await engine.decide('a0', action('read', '.env.production'))
    await engine.control('a0', 'kill')
    claimed = engine.claimed_actions('a0')
    assert claimed[0]['decision_id'] == writer['decision_id']
    claimed[0]['action']['paths'].clear()
    assert engine.claimed_actions('a0')[0]['action']['paths'] == write['paths']
    reading = asyncio.create_task(engine.start('a1', reader['decision_id']))
    await asyncio.sleep(.01)
    assert not reading.done()
    writing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await writing
    assert (await asyncio.wait_for(reading, 1))['allow']
    content = path.read_text()
    read = engine.record('a1', read_action, {'ok': True, 'content': content},
                         decision_id=reader['decision_id'])
    await engine.ready('a1')
    failed = [event for event in engine.event_log.read()
              if event.kind == 'action_executed' and event.agent_id == 'a0'][0]
    assert failed.payload['result']['ok'] is False
    assert failed.payload['result']['effects'] == effects
    assert failed.paths == ['shared.py', 'uncertain.py']
    assert engine.graph.contacts('a0')[0]['seq'] == read.seq
    assert engine.claimed_actions('a0') == []
    assert engine.agent_state('a0')['state'] == 'killed'
    assert engine.graph.snapshot() == rebuild(engine.event_log.read()).snapshot()


async def test_cancelled_waiter_and_terminal_waiter_cannot_release_writer(tmp_path):
    engine = make_engine(tmp_path)
    write = action('write', 'shared.py')
    writer = await engine.decide('a0', write)
    reader_action = action('read', 'shared.py')
    reader = await engine.decide('a1', reader_action)
    terminal = await engine.decide('a2', reader_action)
    assert (await engine.start('a0', writer['decision_id']))['allow']
    (tmp_path / 'shared.py').write_text('partial')
    waiting = asyncio.create_task(engine.start('a1', reader['decision_id']))
    await asyncio.sleep(.01)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    next_waiter = asyncio.create_task(engine.start('a2', terminal['decision_id']))
    await asyncio.sleep(.01)
    assert not next_waiter.done()
    # Message claims remain independent of the file lease.
    message = {'tool': 'send_message', 'operation': 'send', 'paths': [],
               'input': {'recipient': 'a1', 'content': 'continue clean work'}}
    ticket = await engine.decide('a1', message)
    assert (await asyncio.wait_for(engine.start('a1', ticket['decision_id']), .2))['allow']
    engine.record('a1', message, {'ok': True}, decision_id=ticket['decision_id'])
    await engine.control('a0', 'kill')
    await engine.control('a2', 'kill')
    assert not next_waiter.done()
    assert len(engine.claimed_actions('a0')) == 1
    engine.record('a0', write, {'ok': False, 'effects': 'possible'}, decision_id=writer['decision_id'])
    assert not (await asyncio.wait_for(next_waiter, 1))['allow']
    assert engine.claimed_actions('a2') == []
    assert (await engine.start('a1', reader['decision_id']))['allow']
    engine.record('a1', reader_action, {'ok': True, 'content': 'partial'}, decision_id=reader['decision_id'])
    assert engine.graph.reads[-1]['write_seq'] is not None


async def test_denied_unstarted_and_unobserved_reads_cannot_claim_effects(tmp_path):
    engine = make_engine(tmp_path)
    write = action('write', 'shared.py')
    ticket = await engine.decide('a0', write)
    for result in ({'ok': True}, {'ok': False, 'effects': 'possible'}):
        with pytest.raises(ValueError, match='Unstarted'):
            engine.record('a0', write, result, decision_id=ticket['decision_id'])
    blocked_action = action('read', '.env.production')
    blocked = await engine.decide('a1', blocked_action)
    with pytest.raises(ValueError, match='Denied'):
        engine.record('a1', blocked_action, {'ok': False, 'effects': 'observed'},
                      decision_id=blocked['decision_id'])
    assert not (await engine.start('a1', blocked['decision_id']))['allow']
    engine.record('a1', blocked_action, {'ok': False, 'effects': 'none'}, decision_id=blocked['decision_id'])
    reading = action('read', 'shared.py')
    read = await engine.decide('a2', reading)
    assert (await engine.start('a2', read['decision_id']))['allow']
    with pytest.raises(ValueError, match='observed content'):
        engine.record('a2', reading, {'ok': False, 'effects': 'possible'}, decision_id=read['decision_id'])
    engine.record('a2', reading, {'ok': False, 'effects': 'none'}, decision_id=read['decision_id'])
    assert engine.graph.edges == []


async def test_two_clean_release_exposures_receive_distinct_detector_reviews(tmp_path):
    fast = FastFixture()
    engine = make_engine(tmp_path, fast=fast)
    await engine.control('a0', 'off')
    for path in ['first.py', 'second.py']:
        proposal = action('write', path)
        ticket = await engine.decide('a0', proposal)
        assert (await engine.start('a0', ticket['decision_id']))['allow']
        (tmp_path / path).write_text(path)
        engine.record('a0', proposal, {'ok': True}, decision_id=ticket['decision_id'])
    await engine.control('a0', 'on')
    await engine.decide('a0', action('read', '.env.production'))
    reads = []
    for path in ['first.py', 'second.py']:
        proposal = action('read', path)
        ticket = await engine.decide('a1', proposal)
        assert (await engine.start('a1', ticket['decision_id']))['allow']
        read = engine.record('a1', proposal, {'ok': True, 'content': (tmp_path / path).read_text()},
                             decision_id=ticket['decision_id'])
        reads.append(read.seq)
        assert (await engine.ready('a1'))['allow']
    events = engine.event_log.read()
    reviews = [event for event in events if event.payload.get('trace_review')]
    assert [event.payload['action_id'] for event in reviews] == [f'trace:a1:{seq}' for seq in reads]
    assert len([event for event in events if event.kind == 'release' and event.agent_id == 'a1']) == 2
    assert fast.calls == 4  # Two direct checks and two independent exposure reviews.
    assert [contact['seq'] for contact in engine.graph.contacts('a0')] == reads
    assert engine.graph.snapshot() == rebuild(events).snapshot()


async def test_state_and_proposal_metadata_need_no_journal_lookup(tmp_path, monkeypatch):
    engine = make_engine(tmp_path)
    ticket = await engine.decide('a0', action('write', 'task.py'))
    proposal = next(event for event in engine.event_log.read() if event.kind == 'action_proposed')
    assert ticket['proposal_seq'] == proposal.seq
    assert ticket['proposal_elapsed'] == proposal.payload['elapsed']
    await engine.control('a0', 'kill')
    denied = await engine.decide('a0', action('read', 'task.py'))
    last_proposal = [event for event in engine.event_log.read() if event.kind == 'action_proposed'][-1]
    assert not denied['allow']
    assert denied['proposal_seq'] == last_proposal.seq
    assert denied['proposal_elapsed'] == last_proposal.payload['elapsed']

    def forbidden_read():
        raise AssertionError('Cheap state/claim query scanned journal')

    monkeypatch.setattr(engine.event_log, 'read', forbidden_read)
    for _ in range(100):
        assert engine.agent_state('a0') == {'agent_id': 'a0', 'state': 'killed'}
        assert engine.claimed_actions('a0') == []
    claim = await engine.start('a0', ticket['decision_id'])
    assert not claim['allow'] and claim['proposal_seq'] == ticket['proposal_seq']


async def test_failed_read_only_publishes_content_actually_delivered(tmp_path):
    engine = make_engine(tmp_path)
    await engine.control('a0', 'off')
    write_action = action('write', 'shared.py')
    writer = await engine.decide('a0', write_action)
    assert (await engine.start('a0', writer['decision_id']))['allow']
    (tmp_path / 'shared.py').write_text('tainted output')
    engine.record('a0', write_action, {'ok': True}, decision_id=writer['decision_id'])
    await engine.control('a0', 'on')
    await engine.decide('a0', action('read', '.env.production'))
    read_action = action('read', 'shared.py')
    reader = await engine.decide('a1', read_action)
    assert (await engine.start('a1', reader['decision_id']))['allow']
    delivered = (tmp_path / 'shared.py').read_text()
    read = engine.record('a1', read_action, {'ok': False, 'effects': 'observed', 'content': delivered},
                         decision_id=reader['decision_id'])
    assert read.kind == 'action_executed' and read.payload['result']['ok'] is False
    assert engine.graph.contacts('a0')[0]['seq'] == read.seq
    assert engine.detector.tallies.snapshot('a1')['steps'] == 0
    await engine.ready('a1')
    assert any(event.payload.get('trace_review') for event in engine.event_log.read())


def test_historical_delayed_failed_write_reconstructs_same_engine_and_graph(tmp_path):
    engine = make_engine(tmp_path)
    path = tmp_path / 'shared.py'
    path.write_text('partial historical bytes')
    read = engine.event_log.append('a1', 'action_executed', paths=['shared.py'], payload={
        'elapsed': 2., 'action_id': 'historical-reader', 'action': action('read', 'shared.py'),
        'result': {'ok': True, 'content': path.read_text()}})
    engine.event_log.append('a0', 'freeze', payload={
        'elapsed': 3., 'confirmed': True, 'last_clean_seq': 0})
    write = engine.event_log.append('a0', 'action_executed', paths=['shared.py'], payload={
        'elapsed': 1., 'action_id': 'historical-writer', 'action': action('write', 'shared.py'),
        'result': {'ok': False, 'effects': 'possible'}})
    reconstructed = make_engine(tmp_path)
    graph = rebuild(engine.event_log.read())
    assert reconstructed.agent_state('a0')['state'] == 'frozen'
    assert reconstructed.graph.snapshot() == graph.snapshot()
    assert reconstructed.graph.contacts('a0')[0]['seq'] == read.seq
    assert reconstructed.graph.reads[0]['write_seq'] == write.seq
