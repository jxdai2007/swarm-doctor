"""Timed counterfactual pruning; scenario truth is measurement, never policy."""
from __future__ import annotations

from dataclasses import dataclass, field


def _elapsed(event):
    return float(event.payload['elapsed'])


def freeze_windows(freezes=(), controls=()):
    windows = [(agent, float(at), None) for agent, at in freezes]
    active = {}
    for control in sorted(controls, key=lambda item: (item['elapsed'], item.get('order', 0))):
        agent, at, kind = control['agent_id'], float(control['elapsed']), control['kind']
        if kind in {'freeze', 'kill', 'end'}:
            start, terminal = active.get(agent, (at, False))
            active[agent] = (start, terminal or kind in {'kill', 'end'})
        elif kind == 'release' and agent in active and not active[agent][1]:
            start, _ = active.pop(agent)
            windows.append((agent, start, at))
    windows.extend((agent, start, None) for agent, (start, _) in active.items())
    return windows


def provenance_paths(event):
    """Canonical causal contract for truth events and executed tool results.

    A tool result's dependencies are observed source versions, not policy
    labels. Independent actions have no provenance field; only exposure and
    first-source infection may carry an unconditional empty path.
    """
    top = event.payload.get('provenance_paths')
    result = event.payload.get('result', {})
    nested = result.get('provenance_paths') if event.kind == 'action_executed' and isinstance(result, dict) else None
    if top is not None and nested is not None and top != nested:
        raise ValueError(f'Conflicting provenance at seq {event.seq}')
    paths = top if top is not None else nested
    if paths is None:
        if event.kind in {'infection', 'exposure'}:
            raise ValueError(f'Missing infection provenance at seq {event.seq}')
        return None
    if (not isinstance(paths, list) or not paths
            or not all(isinstance(path, list) for path in paths)
            or any(not path for path in paths) and event.kind not in {'infection', 'exposure'}
            or any(type(seq) is not int or seq < 1 or seq >= event.seq for path in paths for seq in path)):
        raise ValueError(f'Invalid provenance at seq {event.seq}')
    return paths


def infection_survives(infection, pruned):
    paths = provenance_paths(infection)
    if paths is None:
        raise ValueError(f'Missing causal provenance at seq {infection.seq}')
    return any(not (set(path) & pruned) for path in paths)


def pruned_seqs(events, freezes=(), controls=()):
    """Freeze windows remove later events; causal alternatives form a fixpoint.

    Preventing infection does not invent a replacement trajectory, nor remove
    independent legitimate work. Only explicitly causal events are pruned.
    """
    windows = freeze_windows(freezes, controls)
    pruned = {event.seq for event in events
              if any(event.agent_id == agent and _elapsed(event) > start
                     and (end is None or _elapsed(event) < end)
                     for agent, start, end in windows)}
    changed = True
    while changed:
        changed = False
        for event in events:
            if event.seq not in pruned and provenance_paths(event) is not None:
                if not infection_survives(event, pruned):
                    pruned.add(event.seq)
                    changed = True
    return pruned


def prune_log(events, freezes=(), controls=()):
    pruned = pruned_seqs(events, freezes, controls)
    return [event for event in events if event.seq not in pruned]


@dataclass
class ReplayResult:
    events: list
    freezes: list = field(default_factory=list)
    infections: list = field(default_factory=list)
    prevented: list = field(default_factory=list)
    controls: list = field(default_factory=list)
    arm: str = 'no-defense'

    @property
    def infected_agents(self):
        return {event.agent_id for event in self.infections}

    @property
    def retained(self):
        return prune_log(self.events, self.freezes, self.controls)


def attribute(infection, events, freezes=(), controls=()):
    """Immediate source from first surviving write/read alternative.

    Chains run ancestor-to-recipient: last executed write before the recipient's
    read is the direct source, not an earlier ancestor or arbitrary event author.
    """
    pruned = pruned_seqs(events, freezes, controls)
    index = {event.seq: event for event in events}
    for path in provenance_paths(infection):
        if set(path) & pruned:
            continue
        if not path:
            return None
        for seq in reversed(path):
            event = index.get(seq)
            if event is None:
                raise ValueError(f'Unknown provenance event {seq}')
            if event.kind == 'action_executed' and event.payload['action']['operation'] in {'write', 'send'}:
                return event.agent_id
        raise ValueError('Infection path contains no executed source write')
    return None


def replay_no_defense(events):
    return ReplayResult(events=events, infections=[event for event in events if event.kind == 'infection'])


def replay_freeze_schedule(events, freezes=(), *, controls=(), arm='schedule'):
    pruned = pruned_seqs(events, freezes, controls)
    infections = [event for event in events if event.kind == 'infection']
    return ReplayResult(events=events, freezes=list(freezes), controls=list(controls), arm=arm,
                        infections=[event for event in infections if event.seq not in pruned],
                        prevented=[event for event in infections if event.seq in pruned])
