"""One blocking decision/record/control seam for harnesses and local HTTP."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import math
import threading
from types import SimpleNamespace
import time

from belowone.events import scrub
from belowone.eval.graph import record as record_graph
from belowone.graph.trust import TrustGraph
from belowone.graph.trace import Tracer
from belowone.policy.lifecycle import Lifecycle
from belowone.policy.modes import validate_mode
from belowone.spec.lock import spec_hash


class Engine:
    def __init__(self, spec, event_log, detector, mode='verify', clock=time.monotonic, *, agent_ids=None,
                 synthetic=False):
        self.spec, self.event_log, self.detector = spec, event_log, detector
        self.mode, self.clock, self.synthetic = validate_mode(mode), clock, synthetic
        self.spec_hash = spec_hash(spec)
        if spec_hash(detector.spec) != self.spec_hash:
            raise ValueError('Detector must use engine locked spec')
        ids = tuple(spec.compartments) if agent_ids is None else tuple(agent_ids)
        if not ids or len(set(ids)) != len(ids) or not all(isinstance(agent, str) and agent for agent in ids):
            raise ValueError('Engine requires distinct known agent IDs')
        self.graph, self.lifecycle = TrustGraph(), Lifecycle(ids)
        self._lock, self._decisions_lock = threading.RLock(), asyncio.Lock()
        self._changed, self._pending, self._latest = asyncio.Event(), {}, {}
        self._inboxes = {agent: [] for agent in ids}
        self._off = False
        self._start, self._last_elapsed = clock(), 0.0
        self._trace_checks, self._trace_roots = set(), set()
        self._trace_contact = {}
        self._action_ids = set()
        self._trace_tasks = []
        self._root_bounds, self._trace_results = {}, {}
        for event in event_log.read():
            self.lifecycle.state(event.agent_id)
            self._last_elapsed = max(self._last_elapsed, float(event.payload.get('elapsed', 0)))
            if event.kind == 'action_proposed':
                self._action_ids.add(event.payload['action_id'])
            record_graph(self.graph, event)
            if event.kind in {'freeze', 'release', 'kill'}:
                self.lifecycle.transition(event.agent_id, event.kind)
            elif (event.kind == 'decision' and event.payload.get('label') == 'clean'
                  and event.payload.get('layer') not in {'off', 'lifecycle'}):
                self.graph.last_clean[event.agent_id] = event.seq

    def _elapsed(self, elapsed=None):
        value = self.clock() - self._start if elapsed is None else elapsed
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('elapsed must be finite and nonnegative')
        self._last_elapsed = max(self._last_elapsed, float(value))
        return self._last_elapsed if elapsed is None else float(value)

    def _emit(self, agent, kind, *, paths=(), elapsed=None, **payload):
        event = self.event_log.append(agent, kind, paths=paths,
                                      payload={'elapsed': self._elapsed(elapsed), **payload})
        self._changed.set()
        return event

    def _action(self, action):
        if not isinstance(action, dict) or set(action) - {'tool', 'operation', 'paths', 'input', 'action_id'}:
            raise ValueError('Action must be a normalized object')
        result = deepcopy(action)
        result.pop('action_id', None)
        if not isinstance(result.get('tool'), str) or not result['tool'] or '\x00' in result['tool']:
            raise ValueError('Action tool must be a nonempty string')
        if result.get('operation') not in {'read', 'write', 'run', 'list', 'send', 'receive', 'finish', 'spawn', 'stop', 'unknown'}:
            raise ValueError('Unknown normalized action operation')
        if not isinstance(result.get('paths'), list) or not all(isinstance(path, str) and '\x00' not in path for path in result['paths']):
            raise ValueError('Action paths must be strings without NUL')
        if not isinstance(result.get('input'), dict):
            raise ValueError('Action input must be an object')
        if result['operation'] == 'send':
            recipient = result['input'].get('recipient')
            if not isinstance(recipient, str):
                raise ValueError('Message recipient must be a known agent ID')
            self.lifecycle.state(recipient)
            if not isinstance(result['input'].get('content'), str):
                raise ValueError('Message content must be a string')
        return result

    def _answer(self, agent, decision_id, action_id, check, *, allow=None, steer=None):
        return {'decision_id': decision_id, 'action_id': action_id,
                'allow': bool(check.get('allow', False) if allow is None else allow),
                'label': check.get('label', 'clean'), 'reason': check.get('reason', ''),
                'state': self.lifecycle.state(agent), 'steer': steer,
                'confidence': float(check.get('confidence', 0)), 'elapsed': self._elapsed(),
                'signal': check.get('signal', 'allow'), 'layer': check.get('layer', 'lifecycle'),
                'uncertain': bool(check.get('uncertain', False))}

    async def wait_pending_traces(self):
        """Finish observed-contact reviews before a harness starts another model turn."""
        while self._trace_tasks:
            queued = list(self._trace_tasks)
            await asyncio.gather(*queued)
            self._trace_tasks = [task for task in self._trace_tasks if task not in queued]

    async def decide(self, agent_id, action):
        self.lifecycle.state(agent_id)
        normalized = self._action(action)
        await self.wait_pending_traces()
        async with self._decisions_lock:
            with self._lock:
                action_id = action.get('action_id', f'{agent_id}:{len(self.event_log.read()) + 1}')
                if not isinstance(action_id, str) or not action_id or action_id in self._action_ids:
                    raise ValueError('action_id must be a unique nonempty string')
                proposal = self._emit(agent_id, 'action_proposed', paths=normalized['paths'],
                                      action=normalized, action_id=action_id)
                self._action_ids.add(action_id)
                decision_id = f'd{proposal.seq}'
                state = self.lifecycle.state(agent_id)
                self._latest[agent_id] = normalized
                if not self.lifecycle.allowed(agent_id):
                    check = {'allow': False, 'label': 'clean', 'signal': 'kill' if state == 'killed' else 'deny',
                             'reason': f'Agent {state}', 'confidence': 1.0}
                elif self._off:
                    check = {'allow': True, 'label': 'clean', 'signal': 'allow', 'reason': 'Operator disabled Below One',
                             'confidence': 1.0, 'layer': 'off'}
                else:
                    check = None
            if check is None:
                check = await self.detector.check(agent_id, deepcopy(normalized))
            with self._lock:
                steer = None
                if not self.lifecycle.allowed(agent_id):
                    check = {**check, 'allow': False, 'signal': 'kill' if self.lifecycle.state(agent_id) == 'killed' else 'deny',
                             'reason': f'Agent {self.lifecycle.state(agent_id)}'}
                elif check.get('signal') == 'end':
                    self.lifecycle.transition(agent_id, 'end')
                    self._emit(agent_id, 'kill', reason=check['reason'], budget_ended=True,
                               action_id=action_id, decision_id=decision_id)
                elif check.get('uncertain'):
                    check = {**check, 'allow': False, 'signal': 'deny'}
                elif check['label'] == 'violation':
                    self.lifecycle.transition(agent_id, 'freeze')
                    self._emit(agent_id, 'freeze', reason=check['reason'], action_id=action_id,
                               decision_id=decision_id, confirmed=True)
                elif check['label'] == 'drift':
                    self.lifecycle.transition(agent_id, 'steer')
                    steer = f'Goal: {self.spec.goal}. Return to task: {check["reason"]}'
                    self._emit(agent_id, 'steer', reason=check['reason'], message=steer,
                               action_id=action_id, decision_id=decision_id,
                               operator_alert=self.lifecycle.state(agent_id) == 'escalated')
                else:
                    self.lifecycle.transition(agent_id, 'clean')
            if check.get('poisoned') and self.lifecycle.state(agent_id) == 'frozen':
                await self._trace(agent_id, float(check['confidence']), proposal.seq - 1)
            with self._lock:
                answer = self._answer(agent_id, decision_id, action_id, check, steer=steer)
                if normalized['operation'] == 'receive' and answer['allow']:
                    answer['messages'] = deepcopy(self._inboxes[agent_id])
                self._emit(agent_id, 'decision', paths=normalized['paths'], action=normalized,
                           action_id=action_id, decision_id=decision_id, detection=check, **{
                               name: value for name, value in answer.items() if name not in {'action_id', 'decision_id', 'elapsed', 'messages'}})
                self._pending[decision_id] = {'agent': agent_id, 'action': normalized, 'answer': answer,
                                             'progress': check.get('progress', 1), 'recorded': False,
                                             'messages': deepcopy(answer.get('messages', []))}
                return scrub(answer)

    def start(self, agent_id, decision_id):
        """Claim ticket immediately before tool begins; controls revoke unclaimed tickets.

        A kill after a successful claim aborts in-flight work best effort. Record
        actual completion even if its post-hook arrives after operator control.
        """
        with self._lock:
            self.lifecycle.state(agent_id)
            pending = self._pending.get(decision_id)
            if pending is None or pending['agent'] != agent_id or pending['recorded']:
                raise ValueError('Unknown or completed action ticket')
            state = self.lifecycle.state(agent_id)
            allow = pending['answer']['allow'] and self.lifecycle.allowed(agent_id) and 'started_at' not in pending
            answer = {**pending['answer'], 'allow': bool(allow), 'state': state, 'elapsed': self._elapsed()}
            if allow:
                pending['started_at'] = answer['elapsed']
            else:
                answer.pop('messages', None)
                answer['reason'] = f'Agent {state}' if not self.lifecycle.allowed(agent_id) else 'Action ticket unavailable'
                answer['signal'] = 'kill' if state == 'killed' else 'deny'
            return scrub(answer)

    def _contacts(self, root):
        with self._lock:
            return [contact for contact in self.graph.contacts(root)
                    if (contact['agent'], contact['seq']) not in self._trace_checks
                    and contact['agent'] not in self._trace_roots
                    and self.lifecycle.state(contact['agent']) not in {'killed', 'ended'}]

    def _poison(self, agent, **kwargs):
        with self._lock:
            self.graph.poison(agent, **kwargs)

    def _release(self, agent):
        with self._lock:
            self.graph.release(agent)

    async def _trace(self, root, confidence, clean_bound):
        queue = [(root, confidence, min(self.graph.last_clean[root], clean_bound))]
        visited = set()
        while queue:
            agent, certainty, clean = queue.pop(0)
            if agent in visited:
                continue
            visited.add(agent)
            self._trace_roots.add(agent)
            self._root_bounds.setdefault(agent, (clean, certainty))
            view = SimpleNamespace(poison=self._poison, release=self._release,
                                   last_clean=self.graph.last_clean, contacts=self._contacts)
            tracer = Tracer(view, self.lifecycle, self._check_contact, mode=self.mode,
                            radius=self.spec.trace_radius, emit=self._trace_emit)
            result = await tracer.trace(agent, last_clean_seq=clean, confidence=certainty)
            queue.extend((confirmed, self._trace_results[confirmed]['confidence'], self.graph.last_clean[confirmed])
                         for confirmed in result['confirmed'])

    def _trace_emit(self, agent, kind, payload):
        with self._lock:
            if kind == 'trace':
                self._trace_contact[agent] = payload
            message = None
            if kind == 'steer':
                message = f'Goal: {self.spec.goal}. Return to task: {payload.get("reason", "Drift at contact")}'
            self._emit(agent, kind, **{key: value for key, value in payload.items() if key not in {'agent', 'elapsed'}},
                       **({'contact_elapsed': payload['elapsed']} if 'elapsed' in payload else {}),
                       **({'message': message} if message else {}))

    async def _check_contact(self, agent):
        with self._lock:
            contact = self._trace_contact[agent]
            key = (agent, contact['seq'])
            if self.lifecycle.state(agent) in {'killed', 'ended'}:
                return {'label': 'clean', 'confidence': 0., 'uncertain': True, 'reason': 'Terminal contact'}
            self._trace_checks.add(key)
            action = deepcopy(self._latest.get(agent, {'tool': 'trace_review', 'operation': 'unknown', 'paths': [], 'input': {}}))
        result = await self.detector.check(agent, action, trace=True)
        with self._lock:
            if self.lifecycle.state(agent) in {'killed', 'ended'}:
                result = {**result, 'uncertain': True}
            self._trace_results[agent] = result
            self._emit(agent, 'decision', action=action, action_id=f'trace:{agent}:{contact["seq"]}',
                       decision_id=f'trace:{agent}:{contact["seq"]}', detection=result, trace_review=True,
                       label=result['label'], confidence=result['confidence'], reason=result['reason'])
        return result

    def record(self, agent_id, action, result, *, decision_id, elapsed=None):
        self.lifecycle.state(agent_id)
        normalized = self._action(action)
        if not isinstance(result, dict) or type(result.get('ok')) is not bool:
            raise ValueError('Tool result requires explicit boolean ok')
        cost = result.get('cost_usd', 0)
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            raise ValueError('Tool cost must be a finite nonnegative USD amount')
        with self._lock:
            pending = self._pending.get(decision_id)
            if pending is None or pending['agent'] != agent_id or pending['action'] != normalized:
                raise ValueError('Record must match agent, action and issued decision')
            if pending['recorded']:
                raise ValueError('Decision already recorded')
            answer = pending['answer']
            if not answer['allow'] and result['ok']:
                raise ValueError('Denied action cannot report successful execution')
            kind = 'action_executed' if answer['allow'] and result['ok'] else 'action_denied'
            paths = list(normalized['paths'])
            if kind == 'action_executed' and normalized['operation'] == 'send':
                paths = [f'__messages__/{normalized["input"]["recipient"]}/{pending["answer"]["action_id"]}']
            elif kind == 'action_executed' and normalized['operation'] == 'receive':
                paths = [message['path'] for message in pending['messages']]
            event = self._emit(agent_id, kind, paths=paths, elapsed=elapsed, action=normalized,
                               result=result, decision_id=decision_id, action_id=answer['action_id'],
                               unguarded=answer['layer'] == 'off', started_at_elapsed=pending.get('started_at'))
            pending['recorded'] = True
            if kind == 'action_executed':
                if normalized['operation'] != 'receive' or paths:
                    record_graph(self.graph, event)
                self.detector.tallies.observe(agent_id, normalized, progress=pending['progress'],
                                             cost_usd=result.get('cost_usd', 0))
                if answer['label'] == 'clean' and answer['layer'] not in {'off', 'lifecycle'}:
                    self.graph.last_clean[agent_id] = event.seq
                self._latest[agent_id] = {**normalized, 'input': {**normalized['input'], 'observed_result': deepcopy(result)}}
                if normalized['operation'] == 'send':
                    recipient = normalized['input']['recipient']
                    message = {'sender': agent_id, 'recipient': recipient, 'content': normalized['input']['content'],
                               'path': paths[0], 'write_event_id': event.seq}
                    self._inboxes[recipient].append(message)
                    self._emit(agent_id, 'message_delivered', paths=paths, recipient=recipient,
                               action_id=answer['action_id'], decision_id=decision_id, write_event_id=event.seq)
                elif normalized['operation'] == 'receive':
                    read_paths = set(paths)
                    self._inboxes[agent_id] = [message for message in self._inboxes[agent_id] if message['path'] not in read_paths]
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is not None:
                    for root in sorted(self._trace_roots):
                        if self._contacts(root):
                            self._trace_tasks.append(loop.create_task(self._trace_after_record(root)))
            return event

    async def _trace_after_record(self, root):
        async with self._decisions_lock:
            clean, confidence = self._root_bounds[root]
            await self._trace(root, confidence, clean)

    async def control(self, agent_id, command, reason='Operator action'):
        self.lifecycle.state(agent_id)
        if command not in {'freeze', 'release', 'kill', 'off', 'on'}:
            raise ValueError('Unknown operator command')
        # Kill bypasses the model queue: active adapters observe the event now.
        if command == 'kill':
            with self._lock:
                self._emit(agent_id, 'operator_action', command=command, reason=reason)
                self.lifecycle.transition(agent_id, 'kill')
                event = self._emit(agent_id, 'kill', reason=reason, operator=True)
                return {'agent_id': agent_id, 'state': 'killed', 'seq': event.seq, 'off': self._off}
        async with self._decisions_lock:
            with self._lock:
                self._emit(agent_id, 'operator_action', command=command, reason=reason)
                if command in {'off', 'on'}:
                    self._off = command == 'off'
                else:
                    self.lifecycle.transition(agent_id, command)
                    if command == 'release':
                        self.graph.release(agent_id)
                        self._trace_roots.discard(agent_id)
                    self._emit(agent_id, command, reason=reason, operator=True)
                return {'agent_id': agent_id, 'state': self.lifecycle.state(agent_id), 'off': self._off}

    def snapshot(self):
        with self._lock:
            events = self.event_log.read()
            infected = {event.agent_id for event in events if event.kind == 'infection'}
            agents = {agent: ('frozen' if state == 'frozen' else state if state in {'killed', 'ended'}
                              else 'infected' if agent in infected else 'clean')
                      for agent, state in self.lifecycle.states.items()}
            return scrub({'spec_hash': self.spec_hash, 'mode': self.mode, 'off': self._off,
                          'synthetic': self.synthetic, 'states': dict(self.lifecycle.states), 'agents': agents,
                          'graph': self.graph.snapshot(), 'last_seq': events[-1].seq if events else 0,
                          'counters': [{'label': 'Frozen agents', 'value': sum(state == 'frozen' for state in self.lifecycle.states.values()), 'source': 'events.freeze/release'},
                                       {'label': 'Released agents', 'value': len({event.agent_id for event in events if event.kind == 'release'}), 'source': 'events.release'},
                                       {'label': 'Infected agents', 'value': len(infected), 'source': 'manifest infection events'}]})

    async def events(self, after=0):
        if type(after) is not int or after < 0:
            raise ValueError('Event cursor must be a nonnegative integer')
        while True:
            for event in self.event_log.read():
                if event.seq > after:
                    after = event.seq
                    yield event
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=.05)
            except TimeoutError:
                pass
