"""Shared U6 graph rebuilt from settled effects, including failed file writes."""
from types import SimpleNamespace

from belowone.graph.trust import TrustGraph


def record(graph, event):
    if event.kind != 'action_executed':
        return
    result = event.payload.get('result', {})
    if result.get('effects', 'observed' if result.get('ok', True) else 'none') == 'none':
        return
    action = event.payload['action']
    operation = action['operation']
    if operation == 'unknown':
        graph.trust.setdefault('agent:' + event.agent_id, 1)
        for path in event.paths:
            if not path.startswith('__unknown__/'):
                raise ValueError('Opaque action must name a reserved unknown resource')
            graph.trust['file:' + path] = 0  # Untrusted uncertainty, not infection.
            graph.edges.append({'agent': event.agent_id, 'path': path, 'seq': event.seq,
                                'elapsed': event.payload['elapsed'], 'source': 'agent:' + event.agent_id,
                                'target': 'file:' + path, 'operation': 'unknown', 'unknown_access': True,
                                'reason': 'Opaque shell/tool access; child and off-tool accesses unobserved'})
        return
    if operation in {'send', 'receive'}:
        if operation == 'receive' and not event.paths:
            return  # Empty inbox is a successful observation, not a contact.
        if not event.paths:
            raise ValueError('Executed message must name delivered message versions')
        event = SimpleNamespace(kind=event.kind, agent_id=event.agent_id, seq=event.seq,
                                paths=event.paths, payload={**event.payload, 'action': {
                                    **action, 'operation': 'write' if operation == 'send' else 'read'}})
    graph.record(event)


def rebuild(events):
    graph = TrustGraph()
    for event in events:
        record(graph, event)
        if event.kind == 'freeze' and 'last_clean_seq' in event.payload:
            graph.poison(event.agent_id, last_clean_seq=event.payload['last_clean_seq'])
        elif event.kind == 'release':
            graph.release(event.agent_id)
        elif (event.kind == 'action_executed' and event.payload.get('result', {}).get('ok')
              and event.payload.get('label') == 'clean'
              and event.payload.get('layer') not in {'off', 'lifecycle'}):
            graph.last_clean[event.agent_id] = event.seq
    return graph
