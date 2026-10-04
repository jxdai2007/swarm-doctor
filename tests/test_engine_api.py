import pytest

from belowone.engine import Engine
from belowone.detect.classify import Detector
from belowone.runlog import EventLog
from belowone.spec.schema import GoalSpec
from tests.test_detect import FastFixture


def make_engine(tmp_path, *, fast=None, mode='verify', agent_ids=('a0', 'a1', 'a2')):
    spec = GoalSpec(goal='Add CSV export', done_when=['held-out CSV cases pass'],
                    hard_lines=['Never cheat'], decoys=['.env.production'],
                    compartments={agent: ['**'] for agent in agent_ids}, workspace=tmp_path)
    return Engine(spec, EventLog(tmp_path / 'events.jsonl'), Detector(spec, fast or FastFixture()),
                  mode=mode, clock=lambda: 1.0)


def action(operation='write', path='task.py'):
    return {'tool': operation, 'operation': operation, 'paths': [path], 'input': {}}


async def test_real_detector_decision_record_and_tripwire(tmp_path):
    engine = make_engine(tmp_path)
    proposal = action()
    answer = await engine.decide('a0', proposal)
    assert answer['allow'] is True and answer['label'] == 'clean'
    assert (await engine.start('a0', answer['decision_id']))['allow']
    executed = engine.record('a0', proposal, {'ok': True}, decision_id=answer['decision_id'], elapsed=1.0)
    assert executed.kind == 'action_executed'
    assert engine.snapshot()['graph']['edges'][0]['path'] == 'task.py'
    violation = await engine.decide('a1', action('read', '.env.production'))
    assert not violation['allow'] and violation['state'] == 'frozen'
    assert [event.kind for event in engine.event_log.read()] == [
        'action_proposed', 'decision', 'action_executed', 'action_proposed', 'freeze', 'decision']
    with pytest.raises(ValueError, match='already recorded'):
        engine.record('a0', proposal, {'ok': True}, decision_id=answer['decision_id'])


from contextlib import asynccontextmanager
import asyncio
import json
import socket

import httpx
import uvicorn

from belowone.server.api import create_app
from belowone.server.client import EngineClient, UNREACHABLE

OPERATOR = 'operator-private-capability-123456789'
AGENT_TOKENS = {agent: f'fixture-{agent}-private-capability-123456789' for agent in ('a0', 'a1', 'a2')}


def agent_headers(agent_id):
    return {'Authorization': f'Bearer {AGENT_TOKENS[agent_id]}'}


@asynccontextmanager
async def serving(engine, *, agent_tokens=None, **kwargs):
    tokens = agent_tokens if agent_tokens is not None else {
        agent: AGENT_TOKENS[agent] for agent in engine.lifecycle.states}
    app = create_app(engine, operator_token=OPERATOR, agent_tokens=tokens, **kwargs)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async def ready():
                while not server.started:
                    if task.done():
                        await task
                    await asyncio.sleep(.01)
            await asyncio.wait_for(ready(), 3)
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', trust_env=False) as client:
                yield client
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 5)


async def test_loopback_http_matches_real_inprocess_decisions(tmp_path):
    direct = make_engine(tmp_path / 'direct')
    remote = make_engine(tmp_path / 'remote')
    async with serving(remote) as client:
        for agent, proposal in [('a0', action()), ('a1', action('read', '.env.production'))]:
            expected = await direct.decide(agent, proposal)
            response = await client.post('/decide', json={'agent_id': agent, 'action': proposal},
                                         headers=agent_headers(agent))
            assert response.status_code == 200 and response.json() == expected
            ok = expected['allow']
            if ok:
                assert (await direct.start(agent, expected['decision_id']))['allow']
                claim = await client.post('/start', json={'agent_id': agent,
                                          'decision_id': expected['decision_id']}, headers=agent_headers(agent))
                assert claim.status_code == 200 and claim.json()['allow']
            direct.record(agent, proposal, {'ok': ok}, decision_id=expected['decision_id'])
            recorded = await client.post('/record', json={'agent_id': agent, 'action': proposal,
                                          'result': {'ok': ok}, 'decision_id': expected['decision_id']},
                                         headers=agent_headers(agent))
            assert recorded.status_code == 200
        assert remote.snapshot()['graph'] == direct.snapshot()['graph']


async def test_operator_controls_require_capability_known_ids_and_off_logs(tmp_path):
    engine = make_engine(tmp_path)
    async with serving(engine) as client:
        body = {'agent_id': 'a0', 'command': 'off'}
        assert (await client.post('/control', json=body)).status_code == 403
        assert (await client.post('/control', json=body, headers={'Authorization': 'Bearer wrong'})).status_code == 403
        assert (await client.post('/control', json={'agent_id': 'unknown', 'command': 'off'},
                                  headers={'Authorization': f'Bearer {OPERATOR}'})).status_code == 400
        answer = await client.post('/control', json=body, headers={'Authorization': f'Bearer {OPERATOR}'})
        assert answer.status_code == 200
        transport = EngineClient(agent_token=AGENT_TOKENS['a0'], client=client)
        proposal = action('read', '.env.production')
        decision = await transport.decide('a0', proposal)
        assert decision['allow'] and decision['layer'] == 'off'
        assert (await transport.start('a0', decision['decision_id']))['allow']
        event = await transport.record('a0', proposal, {'ok': True}, decision_id=decision['decision_id'])
        assert event.payload['unguarded'] is True
        assert OPERATOR not in json.dumps(await transport.snapshot())
        assert OPERATOR not in engine.event_log.path.read_text()


async def test_unreachable_client_is_closed_with_exact_reason():
    with socket.socket() as unused:
        unused.bind(('127.0.0.1', 0))
        port = unused.getsockname()[1]
    async with EngineClient(f'http://127.0.0.1:{port}', agent_token=AGENT_TOKENS['a0'], timeout=.1) as client:
        answer = await client.decide('a0', action())
        assert answer['allow'] is False and answer['reason'] == UNREACHABLE
        assert answer['uncertain'] is True


async def test_fifty_concurrent_decide_record_calls_match_log_graph(tmp_path):
    from belowone.eval.graph import rebuild
    engine = make_engine(tmp_path, agent_ids=tuple(f'a{i}' for i in range(50)))
    async def step(index):
        proposal = action(path=f'task{index}.py')
        answer = await engine.decide(f'a{index}', proposal)
        assert (await engine.start(f'a{index}', answer['decision_id']))['allow']
        return engine.record(f'a{index}', proposal, {'ok': True}, decision_id=answer['decision_id'])
    results = await asyncio.gather(*(step(index) for index in range(50)))
    events = engine.event_log.read()
    assert len(results) == 50 and len(events) == 150
    assert [event.seq for event in events] == list(range(1, 151))
    assert engine.snapshot()['graph'] == rebuild(events).snapshot()
    assert len(engine.snapshot()['graph']['edges']) == 50
    assert len({event.payload['action_id'] for event in events if event.kind == 'action_proposed'}) == 50


async def test_two_loopback_sse_subscribers_receive_same_contiguous_sequence(tmp_path):
    engine = make_engine(tmp_path)
    async with serving(engine) as client:
        ready = [asyncio.Event(), asyncio.Event()]
        async def receive(index):
            seen = []
            async with client.stream('GET', '/events') as response:
                async for line in response.aiter_lines():
                    if line.startswith('data: '):
                        payload = json.loads(line[6:])
                        if 'seq' not in payload:
                            ready[index].set()
                            continue
                        seen.append(payload['seq'])
                        if len(seen) == 3:
                            return seen
        tasks = [asyncio.create_task(receive(index)) for index in range(2)]
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in ready)), 3)
            answer = await engine.decide('a0', action())
            assert (await engine.start('a0', answer['decision_id']))['allow']
            engine.record('a0', action(), {'ok': True}, decision_id=answer['decision_id'])
            assert await asyncio.wait_for(asyncio.gather(*tasks), 3) == [[1, 2, 3], [1, 2, 3]]
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_static_dashboard_same_server_and_readonly_artifact_path_safety(tmp_path):
    engine = make_engine(tmp_path)
    root = tmp_path / 'runs'
    run = root / 'seed0'
    run.mkdir(parents=True)
    (run / 'metrics.json').write_text(json.dumps({'synthetic': True, 'infected': 0, 'api_key': 'must-not-leak'}))
    (run / 'snapshot.json').write_text(json.dumps(engine.snapshot()))
    (run / 'events.jsonl').write_text('')
    secret = tmp_path / 'private.json'
    secret.write_text('private')
    (root / 'alias').symlink_to(tmp_path, target_is_directory=True)
    (run / 'manifest.json').symlink_to(secret)
    async with serving(engine, artifact_root=root) as client:
        page = await client.get('/')
        assert page.status_code == 200 and 'Below One' in page.text
        assert (await client.get('/vendor/cytoscape.min.js')).status_code == 200
        artifact = await client.get('/artifacts/seed0/metrics.json')
        assert artifact.json() == {'synthetic': True, 'infected': 0}
        for path in ['/artifacts/seed0/private.json', '/artifacts/seed0/manifest.json',
                     '/artifacts/alias/metrics.json', '/artifacts/%2e%2e/private.json',
                     '/.env', '/pyproject.toml', '/%2e%2e/AGENTS.md']:
            assert (await client.get(path)).status_code == 404
        assert (await client.get('/snapshot?run=seed0')).status_code == 200
        assert OPERATOR not in page.text


async def test_engine_drift_steer_and_uncertain_deny_do_not_infect(tmp_path):
    drift = make_engine(tmp_path / 'drift', fast=FastFixture(goal=.1))
    answer = await drift.decide('a0', action())
    assert not answer['allow'] and answer['state'] == 'steered' and 'Add CSV export' in answer['steer']
    uncertain = make_engine(tmp_path / 'uncertain', fast=FastFixture(unavailable=True))
    answer = await uncertain.decide('a0', action())
    assert not answer['allow'] and answer['uncertain'] and answer['state'] == 'active'
    assert not any(event.kind in {'freeze', 'infection'} for event in uncertain.event_log.read())


async def test_frozen_client_observes_release_and_terminal_kill_never_resumes(tmp_path):
    engine = make_engine(tmp_path)
    await engine.control('a0', 'freeze')
    assert not (await engine.decide('a0', action()))['allow']
    await engine.control('a0', 'release')
    assert (await engine.decide('a0', action()))['allow']
    await engine.control('a0', 'kill')
    answer = await engine.decide('a0', action())
    assert not answer['allow'] and answer['state'] == 'killed'
    with pytest.raises(ValueError, match='terminal'):
        await engine.control('a0', 'release')


async def test_kill_returns_immediately_while_model_check_is_waiting(tmp_path):
    entered, resume = asyncio.Event(), asyncio.Event()
    class SlowFast(FastFixture):
        async def check(self, state, questions):
            entered.set()
            await resume.wait()
            return await super().check(state, questions)
    engine = make_engine(tmp_path, fast=SlowFast())
    task = asyncio.create_task(engine.decide('a0', action()))
    await entered.wait()
    try:
        control = await asyncio.wait_for(engine.control('a0', 'kill'), .2)
        assert control['state'] == 'killed' and engine.event_log.read()[-1].kind == 'kill'
        resume.set()
        answer = await asyncio.wait_for(task, 1)
        assert not answer['allow'] and answer['state'] == 'killed'
    finally:
        resume.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_messages_checked_before_delivery_and_read_versions_recorded(tmp_path):
    engine = make_engine(tmp_path)
    message = {'tool': 'send_message', 'operation': 'send', 'paths': [],
               'input': {'recipient': 'a1', 'content': 'Implement CSV exporter'}}
    answer = await engine.decide('a0', message)
    assert (await engine.start('a0', answer['decision_id']))['allow']
    written = engine.record('a0', message, {'ok': True}, decision_id=answer['decision_id'])
    inbox = {'tool': 'read_inbox', 'operation': 'receive', 'paths': [], 'input': {}}
    answer = await engine.decide('a1', inbox)
    assert answer['messages'][0]['content'] == message['input']['content']
    assert (await engine.start('a1', answer['decision_id']))['allow']
    read = engine.record('a1', inbox, {'ok': True}, decision_id=answer['decision_id'])
    assert read.paths == written.paths
    assert engine.snapshot()['graph']['edges'][-1]['write_seq'] == written.seq
    assert (await engine.decide('a1', inbox))['messages'] == []
    blocked = make_engine(tmp_path / 'blocked', fast=FastFixture(hard='hard_line_0'))
    answer = await blocked.decide('a0', message)
    assert not answer['allow']
    blocked.record('a0', message, {'ok': False}, decision_id=answer['decision_id'])
    await blocked.control('a0', 'off')
    assert (await blocked.decide('a1', inbox))['messages'] == []


async def test_record_binding_and_failed_operations_never_create_edges(tmp_path):
    engine = make_engine(tmp_path)
    proposal = action()
    answer = await engine.decide('a0', proposal)
    with pytest.raises(ValueError, match='match'):
        engine.record('a1', proposal, {'ok': True}, decision_id=answer['decision_id'])
    with pytest.raises(ValueError, match='USD'):
        engine.record('a0', proposal, {'ok': True, 'cost_usd': True}, decision_id=answer['decision_id'])
    failed = engine.record('a0', proposal, {'ok': False}, decision_id=answer['decision_id'])
    assert failed.kind == 'action_denied' and engine.snapshot()['graph']['edges'] == []
    assert engine.detector.tallies.snapshot('a0')['steps'] == 0


def traced_engine(tmp_path, *, fast=None, mode='strict', chain=1):
    spec = GoalSpec(goal='Add CSV export', done_when=['held-out CSV cases pass'],
                    hard_lines=['Never cheat'], decoys=['.env.production'],
                    compartments={f'a{i}': ['**'] for i in range(chain + 1)}, workspace=tmp_path)
    log = EventLog(tmp_path / 'events.jsonl')
    for index in range(chain):
        log.append(f'a{index}', 'action_executed', paths=[f'f{index}.py'],
                   payload={'elapsed': index * 2., 'action_id': f'write{index}', 'action': action('write', f'f{index}.py')})
        log.append(f'a{index + 1}', 'action_executed', paths=[f'f{index}.py'],
                   payload={'elapsed': index * 2. + 1, 'action_id': f'read{index}', 'action': action('read', f'f{index}.py')})
    return Engine(spec, log, Detector(spec, fast or FastFixture()), mode=mode, clock=lambda: 1.,
                  synthetic=True)


async def test_actual_tracer_strict_clean_release_preserves_operator_freeze(tmp_path):
    engine = traced_engine(tmp_path / 'clean')
    answer = await engine.decide('a0', action('read', '.env.production'))
    assert answer['state'] == 'frozen' and engine.snapshot()['states']['a1'] == 'active'
    kinds = [event.kind for event in engine.event_log.read()]
    assert kinds.index('trace') < kinds.index('release')
    assert engine.graph.tainted_writes == {1}
    held = traced_engine(tmp_path / 'operator')
    await held.control('a1', 'freeze')
    await held.decide('a0', action('read', '.env.production'))
    assert held.snapshot()['states']['a1'] == 'frozen'
    assert not any(event.kind == 'release' for event in held.event_log.read())


async def test_actual_tracer_restarts_each_confirmed_root_past_k2(tmp_path):
    fast = FastFixture(hard='hard_line_0')
    engine = traced_engine(tmp_path, fast=fast, chain=3)
    await engine.decide('a0', action('read', '.env.production'))
    assert engine.snapshot()['states'] == {f'a{i}': 'frozen' for i in range(4)}
    assert fast.calls == 3
    reviews = [event for event in engine.event_log.read() if event.payload.get('trace_review')]
    assert len(reviews) == 3 and len({event.agent_id for event in reviews}) == 3
    assert engine.graph.tainted_writes == {1, 3, 5}


async def test_new_temporal_contact_checks_before_next_turn(tmp_path):
    spec = GoalSpec(goal='Add CSV export', done_when=['CSV cases pass'],
                    decoys=['.env.production'], compartments={'a0': ['**'], 'a1': ['**']}, workspace=tmp_path)
    log = EventLog(tmp_path / 'events.jsonl')
    log.append('a0', 'action_executed', paths=['shared.py'],
               payload={'elapsed': 0., 'action_id': 'earlierwrite', 'action': action('write', 'shared.py')})
    engine = Engine(spec, log, Detector(spec, FastFixture()), mode='strict', clock=lambda: 1., synthetic=True)
    await engine.decide('a0', action('read', '.env.production'))
    read = action('read', 'shared.py')
    answer = await engine.decide('a1', read)
    assert (await engine.start('a1', answer['decision_id']))['allow']
    engine.record('a1', read, {'ok': True, 'content': 'CSV helper'}, decision_id=answer['decision_id'])
    resumed = await engine.decide('a1', action())
    assert resumed['allow']
    events = engine.event_log.read()
    assert any(event.kind == 'release' and event.agent_id == 'a1' for event in events)
    assert len([event for event in events if event.payload.get('trace_review')]) == 1


async def test_real_u4_jev_cassette_detector_tracer_chain_offline(tmp_path):
    from belowone.meter import Meter
    from belowone.models.cassette import Cassette
    from belowone.models.jev import JevClient
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'model': 'synthetic-jev', 'usage': {'prompt_tokens': 2, 'completion_tokens': 1},
            'answers': {'serves_goal': {'type': 'noul', 'noul': .98},
                        'hard_line': {'type': 'choice', 'choice': 'none', 'confidence': .95},
                        'progress': {'type': 'score', 'score': 2, 'confidence': .95}}})
    cache = tmp_path / 'cache'
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        recorded = traced_engine(tmp_path / 'record', fast=JevClient('synthetic', Meter(), Cassette(cache), http=http))
        await recorded.decide('a0', action('read', '.env.production'))
    assert len(requests) == 1 and recorded.snapshot()['states']['a1'] == 'active'
    replayed = traced_engine(tmp_path / 'replay', fast=JevClient(None, Meter(), Cassette(cache, 'replay')))
    await replayed.decide('a0', action('read', '.env.production'))
    assert replayed.snapshot()['states'] == recorded.snapshot()['states']
    assert len(requests) == 1


async def test_step_budget_ends_agent_without_marking_infection(tmp_path):
    spec = GoalSpec(goal='Export CSV', done_when=['CSV cases pass'], compartments={'a0': ['**']},
                    budgets={'steps_per_agent': 1, 'cost_usd': 1}, workspace=tmp_path)
    engine = Engine(spec, EventLog(tmp_path / 'events.jsonl'), Detector(spec, FastFixture()), clock=lambda: 1.)
    answer = await engine.decide('a0', action())
    assert (await engine.start('a0', answer['decision_id']))['allow']
    engine.record('a0', action(), {'ok': True}, decision_id=answer['decision_id'])
    ended = await engine.decide('a0', action())
    assert ended['state'] == 'ended' and not ended['allow']
    assert not any(event.kind in {'freeze', 'infection'} for event in engine.event_log.read())


async def test_malformed_success_response_is_fail_closed():
    invalid = {'allow': True, 'decision_id': 'd1', 'reason': 'bad', 'state': 'killed',
               'label': 'clean', 'confidence': .9}
    async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=invalid))) as http:
        client = EngineClient(agent_token=AGENT_TOKENS['a0'], client=http)
        answer = await client.decide('a0', action())
        assert not answer['allow'] and answer['reason'] == UNREACHABLE


async def test_kill_http_and_sse_notify_before_blocked_checker_finishes(tmp_path):
    entered, resume = asyncio.Event(), asyncio.Event()
    class SlowFast(FastFixture):
        async def check(self, state, questions):
            entered.set()
            await resume.wait()
            return await super().check(state, questions)
    engine = make_engine(tmp_path, fast=SlowFast())
    async with serving(engine) as client:
        decision = asyncio.create_task(client.post('/decide', json={'agent_id': 'a0', 'action': action()},
                                                    headers=agent_headers('a0')))
        await asyncio.wait_for(entered.wait(), 2)
        ready = asyncio.Event()
        async def killed():
            async with client.stream('GET', '/events?after=1') as response:
                async for line in response.aiter_lines():
                    if line.startswith('data: '):
                        payload = json.loads(line[6:])
                        if 'seq' not in payload:
                            ready.set()
                        elif payload['kind'] == 'kill':
                            return payload
        watcher = asyncio.create_task(killed())
        try:
            await asyncio.wait_for(ready.wait(), 2)
            result = await asyncio.wait_for(client.post('/control', json={'agent_id': 'a0', 'command': 'kill'},
                                headers={'Authorization': f'Bearer {OPERATOR}'}), .8)
            assert result.json()['state'] == 'killed' and not decision.done()
            notification = await asyncio.wait_for(watcher, .8)
            assert notification['agent_id'] == 'a0' and notification['seq'] == 3
        finally:
            resume.set()
            await asyncio.wait_for(decision, 2)
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)


@pytest.mark.parametrize('command', ['freeze', 'kill'])
async def test_outstanding_ticket_cannot_begin_real_tool_after_operator_control(tmp_path, command):
    engine = make_engine(tmp_path)
    output = tmp_path / 'task.py'
    async with serving(engine) as http:
        client = EngineClient(agent_token=AGENT_TOKENS['a0'], client=http)
        proposal = action()
        ticket = await client.decide('a0', proposal)
        assert ticket['allow']
        await http.post('/control', json={'agent_id': 'a0', 'command': command},
                        headers={'Authorization': f'Bearer {OPERATOR}'})
        claimed = await client.start('a0', ticket['decision_id'])
        if claimed['allow']:
            output.write_text('actual tool execution')
        assert not claimed['allow'] and not output.exists()
        await client.record('a0', proposal, {'ok': False, 'reason': claimed['reason']},
                            decision_id=ticket['decision_id'])
        assert engine.snapshot()['graph']['edges'] == []
        if command == 'kill':
            assert (await http.post('/control', json={'agent_id': 'a0', 'command': 'release'},
                     headers={'Authorization': f'Bearer {OPERATOR}'})).status_code == 400


async def test_real_tool_completed_before_control_is_truthfully_recorded_after(tmp_path):
    now = [0.]
    spec = GoalSpec(goal='Add CSV export', done_when=['CSV cases pass'],
                    compartments={'a0': ['**']}, workspace=tmp_path)
    engine = Engine(spec, EventLog(tmp_path / 'events.jsonl'), Detector(spec, FastFixture()),
                    clock=lambda: now[0], synthetic=True)
    proposal = action()
    now[0] = 1
    ticket = await engine.decide('a0', proposal)
    assert (await engine.start('a0', ticket['decision_id']))['allow']
    (tmp_path / 'task.py').write_text('completed real tool')
    now[0] = 2
    completed_at = now[0]
    now[0] = 3
    await engine.control('a0', 'kill')
    event = engine.record('a0', proposal, {'ok': True}, decision_id=ticket['decision_id'], elapsed=completed_at)
    assert event.kind == 'action_executed' and event.payload['elapsed'] == 2
    assert event.payload['started_at_elapsed'] == 1
    assert (tmp_path / 'task.py').read_text() == 'completed real tool'
    assert engine.snapshot()['states']['a0'] == 'killed'
    assert engine.snapshot()['graph']['edges'][0]['elapsed'] == 2


async def test_historical_sse_preflights_before_headers_and_finishes_normally(tmp_path):
    engine = make_engine(tmp_path / 'current')
    history = tmp_path / 'runs' / 'recorded'
    history.mkdir(parents=True)
    journal = EventLog(history / 'events.jsonl')
    journal.append('a0', 'action_proposed', payload={'elapsed': 1})
    journal.append('a0', 'outcome', payload={'elapsed': 2, 'completed': False})
    (history / 'snapshot.json').write_text(json.dumps(engine.snapshot()))
    async with serving(engine, artifact_root=history.parent) as client:
        response = await client.get('/events?run=recorded')
        assert response.status_code == 200
        assert response.text.endswith('data: [done]\n\n')
        assert response.text.count('id: ') == 2
        resumed = await client.get('/events?run=recorded', headers={'Last-Event-ID': '1'})
        assert 'id: 1\n' not in resumed.text and 'id: 2\n' in resumed.text
        assert resumed.text.endswith('data: [done]\n\n')
        # A late bad event must not reset an already-started HTTP 200 stream.
        journal.append('a0', 'outcome', payload={'completed': False})
        invalid = await client.get('/events?run=recorded')
        assert invalid.status_code == 400
        assert 'Invalid historical event artifact' in invalid.json()['detail']


async def test_off_actions_never_create_checker_trust_live_or_journal_rebuild(tmp_path):
    engine = make_engine(tmp_path)
    await engine.control('a0', 'off')
    proposal = action('write', 'unchecked.py')
    answer = await engine.decide('a0', proposal)
    assert (await engine.start('a0', answer['decision_id']))['allow']
    engine.record('a0', proposal, {'ok': True}, decision_id=answer['decision_id'])
    assert engine.graph.last_clean['a0'] == 0
    rebuilt = make_engine(tmp_path)
    assert rebuilt.graph.last_clean['a0'] == 0
    assert rebuilt.graph.writes['unchecked.py']


@pytest.mark.parametrize('endpoint', ['ready', 'decide', 'start', 'record', 'state'])
async def test_agent_http_capabilities_reject_impersonation_before_effects(tmp_path, endpoint):
    engine = make_engine(tmp_path)
    proposal = action()
    body = {'agent_id': 'a0'}
    if endpoint in {'decide', 'record'}:
        body['action'] = proposal
    if endpoint in {'start', 'record'}:
        ticket = await engine.decide('a0', proposal)
        body['decision_id'] = ticket['decision_id']
    if endpoint == 'record':
        assert (await engine.start('a0', ticket['decision_id']))['allow']
        body['result'] = {'ok': True}
    async with serving(engine) as http:
        async def request(agent='a0', headers=None):
            if endpoint == 'state':
                return await http.get(f'/state/{agent}', headers=headers)
            return await http.post(f'/{endpoint}', json={**body, 'agent_id': agent}, headers=headers)

        before = engine.event_log.path.read_bytes() if engine.event_log.path.exists() else b''
        for headers, status in [
                (None, 401),
                ({'Authorization': 'Basic invalid'}, 401),
                ({'Authorization': 'Bearer wrong'}, 403),
                ({'Authorization': f'Bearer {OPERATOR}'}, 403),
                (agent_headers('a1'), 403)]:
            assert (await request(headers=headers)).status_code == status
        assert (await request('unknown', agent_headers('a0'))).status_code == 403
        after = engine.event_log.path.read_bytes() if engine.event_log.path.exists() else b''
        assert after == before and engine.lifecycle.state('a0') == 'active'
        authorized = await request(headers=agent_headers('a0'))
        assert authorized.status_code == 200
        if endpoint == 'state':
            assert authorized.json() == {'agent_id': 'a0', 'state': 'active'}
        elif endpoint == 'record':
            assert authorized.json()['kind'] == 'action_executed'
        else:
            assert authorized.json()['allow'] is True
        if endpoint == 'start':
            engine.record('a0', proposal, {'ok': False}, decision_id=ticket['decision_id'])


async def test_authenticated_client_agent_flow_and_authoritative_state(tmp_path):
    engine = make_engine(tmp_path)
    async with serving(engine) as http:
        client = EngineClient(agent_token=AGENT_TOKENS['a0'], client=http)
        assert (await client.ready('a0'))['allow']
        proposal = action()
        answer = await client.decide('a0', proposal)
        assert answer['allow']
        assert (await client.start('a0', answer['decision_id']))['allow']
        event = await client.record('a0', proposal, {'ok': True}, decision_id=answer['decision_id'])
        assert event.kind == 'action_executed' and engine.graph.writes['task.py']
        assert (await http.post('/control', json={'agent_id': 'a0', 'command': 'kill'},
                               headers=agent_headers('a0'))).status_code == 403
        await engine.control('a0', 'freeze')
        assert await client.state('a0') == {'agent_id': 'a0', 'state': 'frozen'}
        assert not (await client.ready('a1'))['allow']
        with pytest.raises(httpx.HTTPStatusError):
            await client.state('a1')
        snapshot = await client.snapshot()
        serialized = json.dumps(snapshot) + engine.event_log.path.read_text()
        assert all(token not in serialized for token in [OPERATOR, *AGENT_TOKENS.values()])


@pytest.mark.parametrize('tokens', [
    {}, {'a0': 'short', 'a1': AGENT_TOKENS['a1'], 'a2': AGENT_TOKENS['a2']},
    {**AGENT_TOKENS, 'a1': AGENT_TOKENS['a0']},
    {**AGENT_TOKENS, 'a0': OPERATOR}, {**AGENT_TOKENS, 'unknown': 'unknown-capability-123456789'},
])
def test_app_rejects_missing_weak_shared_or_operator_agent_capabilities(tmp_path, tokens):
    with pytest.raises(ValueError, match='capabilit'):
        create_app(make_engine(tmp_path), operator_token=OPERATOR, agent_tokens=tokens)
