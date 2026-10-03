"""Recorded detector decisions keyed by locked spec and immutable action ID."""
from __future__ import annotations

import json
import math

ENFORCING_STOP = {'deny', 'end', 'kill'}


def load_decisions(path, spec_hash):
    out = {}
    with open(path) as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            decision = json.loads(line)
            if decision.get('spec_hash') != spec_hash:
                raise KeyError(f'{path}:{number}: spec_hash mismatch')
            for name in ('action_id', 'agent_id', 'decision', 'decided_at_elapsed', 'latency_s'):
                if name not in decision:
                    raise KeyError(f'{path}:{number}: missing {name}')
            key = decision['action_id']
            if key in out:
                raise KeyError(f'{path}:{number}: duplicate action_id {key}')
            if decision['decision'] not in {'allow', 'deny', 'steer', 'freeze', 'kill', 'end'}:
                raise ValueError('Invalid decision taxonomy')
            for name in ('decided_at_elapsed', 'latency_s'):
                value = decision[name]
                if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                    raise ValueError(f'Invalid decision {name}')
            if decision.get('shadow') and 'source_action_elapsed' not in decision:
                raise KeyError('Shadow decision missing source_action_elapsed')
            out[key] = decision
    return out


def decision_time(decision, added_latency=0):
    """Live completion is recorded; shadow completion uses source-time clock."""
    if decision.get('shadow'):
        return float(decision['source_action_elapsed']) + float(decision['latency_s']) + added_latency
    return float(decision['decided_at_elapsed']) + added_latency


def bind_actions(events, decisions, spec_hash):
    actions = {}
    for event in events:
        if event.kind not in {'action_proposed', 'action_executed', 'action_denied'}:
            continue
        key = event.payload.get('action_id')
        if not isinstance(key, str) or not key:
            raise KeyError(f'Missing action_id at seq {event.seq}')
        previous = actions.get(key)
        if previous is not None and (previous.agent_id != event.agent_id
                                     or previous.payload['action'] != event.payload['action']):
            raise ValueError(f'Conflicting recorded action {key}')
        if previous is None or event.kind == 'action_proposed':
            actions[key] = event
    for key, event in actions.items():
        if key not in decisions:
            raise KeyError(f'Missing cached decision {(spec_hash, key)}')
        decision = decisions[key]
        if decision.get('spec_hash') != spec_hash or decision['agent_id'] != event.agent_id:
            raise ValueError(f'Decision/action binding mismatch for {key}')
    for key, decision in decisions.items():
        if decision.get('spec_hash') != spec_hash:
            raise ValueError(f'Decision/spec binding mismatch for {key}')
        if not decision.get('trace_review') and key not in actions:
            raise KeyError(f'Cached decision has no recorded action {key}')
    return actions


def trace_key(agent, read_seq):
    return f'trace:{agent}:{read_seq}'


def freeze_schedule(decisions, include_steers=False):
    """Live-only legacy schedule utility; replay policies consume shadows too.

    Steer never changes lifecycle to frozen. include_steers remains only for
    explicit caller diagnostics, not defense replay.
    """
    return [(decision['agent_id'], decision_time(decision))
            for decision in decisions.values() if not decision.get('shadow')
            and (decision['decision'] in ENFORCING_STOP | {'freeze'}
                 or include_steers and decision['decision'] == 'steer')]


def shadow_checks(decisions):
    return [{name: decision.get(name) for name in
             ('action_id', 'agent_id', 'decision', 'label', 'source_action_elapsed', 'latency_s', 'shadow')}
            for decision in decisions.values() if decision.get('shadow')]
