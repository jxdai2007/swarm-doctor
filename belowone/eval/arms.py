"""Counterfactual policies over chronological U6 graph and cached U5 verdicts.

No manifest labels enter policy. Shadow checks are inert live, but their source
clock and recorded latency determine counterfactual replay enforcement.
"""
from __future__ import annotations

import heapq
import math

from belowone.graph.trust import TrustGraph
from belowone.policy.modes import effective_radius
from .decisions import bind_actions, decision_time, trace_key
from .replay import _elapsed, infection_survives, replay_freeze_schedule, replay_no_defense
from .graph import record

ARMS = ('no-defense', 'prompt-only', 'periodic-review', 'message-only',
        'blunt-khop', 'taint-without-checker', 'verify', 'strict')


def _neighbors(graph):
    files, neighbors = {}, {}
    for edge in graph.edges:
        files.setdefault(edge['path'], set()).add(edge['agent'])
    for agents in files.values():
        for agent in agents:
            neighbors.setdefault(agent, set()).update(agents - {agent})
    return neighbors


def replay_arm(events, decisions, *, spec_hash, arm='verify', radius=2,
               interval_s=15.0, added_latency=0.0):
    """Apply one policy; frozen windows prune only recorded events.

    Released agents resume retained recorded actions, never generated replacement
    turns. This is a counterfactual branch inference, not live validation.
    Trace reviews use trace:<agent>:<contact_read_seq>, trace_review=true.
    """
    if arm not in ARMS:
        raise ValueError(f'Unknown replay arm {arm}')
    if type(radius) is not int or radius < 0:
        raise ValueError('Invalid trace radius')
    if not math.isfinite(interval_s) or interval_s <= 0 or not math.isfinite(added_latency) or added_latency < 0:
        raise ValueError('Invalid replay timing')
    if arm in {'no-defense', 'prompt-only'}:
        result = replay_no_defense(events)
        result.arm = arm
        return result
    actions = bind_actions(events, decisions, spec_hash)
    graph, queue, controls = TrustGraph(), [], []
    states, freeze_started, poisoned, checked = {}, {}, set(), set()
    pruned, root_confidence = set(), {}
    order = 0

    def enqueue(at, priority, kind, value):
        nonlocal order
        order += 1
        heapq.heappush(queue, (float(at), priority, order, kind, value))

    def emit(agent, kind, at, **payload):
        controls.append({'agent_id': agent, 'kind': kind, 'elapsed': at,
                         'order': len(controls), **payload})
        if kind in {'freeze', 'kill', 'end'}:
            if states.get(agent) in {'killed', 'ended'}:
                return
            freeze_started.setdefault(agent, at)
            states[agent] = {'freeze': 'frozen', 'kill': 'killed', 'end': 'ended'}[kind]
        elif kind == 'release':
            states[agent] = 'active'
            freeze_started.pop(agent, None)
            graph.release(agent)

    def trace(source, at, confidence=1.0):
        limit = effective_radius(radius, confidence)
        if not limit:
            return
        ring = [contact for contact in graph.contacts(source)
                if (contact['agent'], contact['seq']) not in checked
                and states.get(contact['agent']) not in {'killed', 'ended'}
                and contact['agent'] != source]
        reviews = []
        for contact in ring:
            agent = contact['agent']
            checked.add((agent, contact['seq']))
            emit(agent, 'trace', at, source_agent=source, read_event_id=contact['seq'], ring=1)
            if arm == 'taint-without-checker':
                if states.get(agent) != 'frozen':
                    emit(agent, 'freeze', at, reason='Directional contact; checker disabled')
                if agent not in poisoned:
                    poisoned.add(agent)
                    graph.poison(agent, last_clean_seq=min(graph.last_clean[agent], contact['seq'] - 1))
                    enqueue(at, 2, 'trace', (agent, 1.0))
                continue
            key = trace_key(agent, contact['seq'])
            if key not in decisions:
                raise KeyError(f'Missing cached trace review {(spec_hash, key)}')
            review = decisions[key]
            if not review.get('trace_review') or review['agent_id'] != agent:
                raise ValueError(f'Invalid cached trace binding {key}')
            owned = arm == 'strict' and states.get(agent, 'active') != 'frozen'
            if owned:
                emit(agent, 'freeze', at, reason='Strict precautionary contact')
            reviews.append((contact, review, owned))
        # Parallel ring finishes after slowest review, matching U6 gather.
        finish = at + max((float(review['latency_s']) for _, review, _ in reviews), default=0.0) + added_latency
        for contact, review, owned in reviews:
            enqueue(finish, 1, 'review', (contact, review, owned))

    for event in events:
        enqueue(_elapsed(event), 0, 'event', event)
    for key, event in actions.items():
        decision = decisions[key]
        at = decision_time(decision, added_latency)
        if at < _elapsed(event):
            raise ValueError(f'Decision precedes recorded action {key}')
        if arm == 'periodic-review':
            at = math.ceil(at / interval_s) * interval_s
        if arm == 'message-only' and event.payload['action']['operation'] not in {'send', 'receive'}:
            continue
        enqueue(at, 1, 'decision', (event, decision))

    while queue:
        at, _, _, kind, value = heapq.heappop(queue)
        if kind == 'event':
            event = value
            if (event.agent_id in freeze_started and at > freeze_started[event.agent_id]
                    or 'provenance_paths' in event.payload and not infection_survives(event, pruned)):
                pruned.add(event.seq)
                continue
            record(graph, event)
            if event.kind == 'action_executed':
                for source in sorted(poisoned):
                    enqueue(at, 2, 'trace', (source, root_confidence.get(source, 1.0)))
        elif kind == 'trace':
            source, confidence = value
            if arm in {'verify', 'strict', 'taint-without-checker'}:
                trace(source, at, confidence)
        elif kind == 'review':
            contact, decision, owned = value
            agent = contact['agent']
            if states.get(agent) in {'killed', 'ended'}:
                continue
            if decision.get('uncertain', False):
                continue
            label = decision['label']
            if label == 'violation':
                if states.get(agent) != 'frozen':
                    emit(agent, 'freeze', at, reason=decision['reason'], confirmed=True)
                if agent not in poisoned:
                    poisoned.add(agent)
                    root_confidence[agent] = float(decision['confidence'])
                    graph.poison(agent, last_clean_seq=min(graph.last_clean[agent], contact['seq'] - 1))
                    enqueue(at, 2, 'trace', (agent, float(decision['confidence'])))
            elif label in {'clean', 'drift'}:
                if owned and states.get(agent) == 'frozen' and agent not in poisoned:
                    emit(agent, 'release', at, reason=decision['reason'])
                if agent not in poisoned and states.get(agent, 'active') != 'frozen':
                    graph.release(agent)
                if label == 'drift' and states.get(agent, 'active') != 'frozen':
                    emit(agent, 'steer', at, reason=decision['reason'], action_id=decision.get('reviewed_action_id', decision['action_id']))
        else:
            event, decision = value
            agent = event.agent_id
            if event.seq in pruned or agent in freeze_started and at > freeze_started[agent]:
                continue
            signal, label = decision['decision'], decision.get('label')
            if signal in {'kill', 'end'}:
                emit(agent, signal, at, reason=decision.get('reason', 'Budget exhausted'))
                continue
            if decision.get('uncertain', False) or signal == 'deny' and label != 'violation':
                emit(agent, 'deny', at, action_id=event.payload['action_id'], reason=decision['reason'])
                continue
            if label == 'clean':
                graph.last_clean[agent] = max(graph.last_clean[agent], event.seq)
                continue
            if label == 'drift':
                emit(agent, 'steer', at, action_id=event.payload['action_id'], reason=decision['reason'])
                continue
            if label != 'violation':
                raise ValueError('Cached decision lacks detector classification')
            if arm == 'blunt-khop':
                neighbors, frontier, seen = _neighbors(graph), [agent], {agent}
                for _ in range(radius):
                    frontier = sorted({other for source in frontier for other in neighbors.get(source, ())} - seen)
                    seen.update(frontier)
                for other in sorted(seen):
                    emit(other, 'kill', at, reason='Blind undirected K-hop kill')
            else:
                emit(agent, 'freeze', at, reason=decision['reason'], confirmed=True)
                poisoned.add(agent)
                root_confidence[agent] = float(decision.get('confidence', 1.0))
                graph.poison(agent, last_clean_seq=min(graph.last_clean[agent], event.seq - 1))
                enqueue(at, 2, 'trace', (agent, float(decision.get('confidence', 1.0))))
    return replay_freeze_schedule(events, controls=controls, arm=arm)
