"""BFS checks each ring concurrently; exposure is never infection evidence."""
import asyncio

from belowone.policy.modes import effective_radius, validate_mode


class Tracer:
    def __init__(self, graph, lifecycle, check, *, mode='verify', radius=2,
                 confidence_threshold=.6, emit=None):
        self.graph, self.lifecycle, self.check = graph, lifecycle, check
        self.mode, self.radius = validate_mode(mode), radius
        self.confidence_threshold = confidence_threshold
        self.emit = emit

    def _emit(self, agent, kind, payload):
        if self.emit is not None:
            self.emit(agent, kind, payload)

    async def trace(self, initial, *, last_clean_seq, confidence=1):
        radius = effective_radius(self.radius, confidence, threshold=self.confidence_threshold)
        self.graph.poison(initial, last_clean_seq=last_clean_seq)
        seen = {initial}
        frontier = [initial]
        reached, confirmed, rings = [], [], []
        precaution = set()  # frozen by THIS trace, safe to release on review
        for depth in range(1, radius + 1):
            contacts = {}
            for source in frontier:
                for contact in self.graph.contacts(source):
                    if contact['agent'] not in seen:
                        contacts.setdefault(contact['agent'], {**contact, 'source_agent': source})
            ring = sorted(contacts, key=lambda agent: (contacts[agent]['seq'], agent))
            if not ring:
                break
            seen.update(ring)
            reached.extend(ring)
            rings.append(ring)
            for agent in ring:
                self._emit(agent, 'trace', {'ring': depth, **contacts[agent]})
                if self.mode == 'strict' and self.lifecycle.allowed(agent):
                    self.lifecycle.transition(agent, 'freeze')
                    precaution.add(agent)
                    self._emit(agent, 'freeze', {'reason': 'Strict-mode trace contact'})
            results = await asyncio.gather(*(self.check(agent) for agent in ring), return_exceptions=True)
            frontier = []
            for agent, result in zip(ring, results):
                if isinstance(result, BaseException) or result.get('uncertain', False):
                    continue
                label = result.get('label')
                terminal = self.lifecycle.state(agent) in {'killed', 'ended'}
                if label == 'violation' and not terminal:
                    self.lifecycle.transition(agent, 'freeze')
                    # Taint from first causal contact, not clean activity before exposure.
                    clean_point = min(self.graph.last_clean[agent], contacts[agent]['seq'] - 1)
                    self.graph.poison(agent, last_clean_seq=clean_point)
                    self._emit(agent, 'freeze', {'reason': result.get('reason', 'Trace-confirmed violation'), 'confirmed': True})
                    confirmed.append(agent)
                    frontier.append(agent)
                elif label == 'drift':
                    # Contacted drift is steered, never contained: release only
                    # THIS trace's precautionary freeze, then steer.
                    if agent in precaution:
                        self.lifecycle.transition(agent, 'release')
                        self.graph.release(agent)
                        precaution.discard(agent)
                        self._emit(agent, 'release', {'reason': 'Drift is not a violation'})
                    if self.lifecycle.allowed(agent):
                        self.lifecycle.transition(agent, 'steer')
                        self._emit(agent, 'steer', {'reason': result.get('reason', 'Drift at trace contact')})
                elif label == 'clean' and agent in precaution:
                    self.lifecycle.transition(agent, 'release')
                    self.graph.release(agent)
                    precaution.discard(agent)
                    self._emit(agent, 'release', {'reason': 'Clean trace review'})
            if not frontier:
                break
        return {'reached': reached, 'confirmed': confirmed, 'rings': rings, 'radius': radius}
