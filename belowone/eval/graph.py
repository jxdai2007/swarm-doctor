"""Shared U6 graph rebuilt only from retained executed file/message versions."""
from types import SimpleNamespace

from belowone.graph.trust import TrustGraph


def record(graph, event):
    if event.kind != 'action_executed':
        return
    action = event.payload['action']
    operation = action['operation']
    if operation in {'send', 'receive'}:
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
    return graph
