"""Timestamped versions, reconciled late receipts and low-water integrity."""
from collections import defaultdict


class TrustGraph:
    def __init__(self):
        self.trust = {}
        self.writes = defaultdict(list)
        self.reads = []
        self.edges = []
        self.last_clean = defaultdict(int)
        self.tainted_writes = set()

    def _bind_read(self, read):
        previous = read.get('write_seq')
        new_read = 'write_seq' not in read
        eligible = [write for write in self.writes[read['path']]
                    if (write['elapsed'], write['seq']) < (read['elapsed'], read['seq'])]
        write = max(eligible, key=lambda item: (item['elapsed'], item['seq'])) if eligible else None
        read['write_seq'] = write['seq'] if write else None
        if (new_read or previous != read['write_seq']) and read['write_seq'] in self.tainted_writes:
            self.trust['agent:' + read['agent']] = 0

    def record(self, event):
        if event.kind != 'action_executed':
            return
        result = event.payload.get('result', {})
        if result.get('effects', 'observed' if result.get('ok', True) else 'none') == 'none':
            return
        action = event.payload.get('action', {})
        operation = action.get('operation')
        elapsed = event.payload['elapsed']
        agent = event.agent_id
        self.trust.setdefault('agent:' + agent, 1)
        for path in event.paths:
            self.trust.setdefault('file:' + path, 1)
            entry = {'agent': agent, 'path': path, 'seq': event.seq, 'elapsed': elapsed}
            if operation == 'write':
                self.writes[path].append(entry)
                self.trust['file:' + path] = min(self.trust['file:' + path], self.trust['agent:' + agent])
                if self.trust['agent:' + agent] == 0:
                    self.tainted_writes.add(event.seq)
                self.edges.append({**entry, 'source': 'agent:' + agent, 'target': 'file:' + path, 'operation': 'write'})
                # Delayed receipts may predate reads already in the journal.
                for read in self.reads:
                    if read['path'] == path:
                        self._bind_read(read)
            elif operation == 'read':
                entry.update(source='file:' + path, target='agent:' + agent, operation='read')
                self._bind_read(entry)
                self.reads.append(entry)
                self.trust['agent:' + agent] = min(self.trust['agent:' + agent], self.trust['file:' + path])
                self.edges.append(entry)

    def poison(self, agent, *, last_clean_seq):
        previous = set(self.tainted_writes)
        self.trust['agent:' + agent] = 0
        for path, writes in self.writes.items():
            for write in writes:
                if write['agent'] == agent and write['seq'] > last_clean_seq:
                    self.tainted_writes.add(write['seq'])
                    self.trust['file:' + path] = 0
        newly_tainted = self.tainted_writes - previous
        for read in self.reads:
            if read['write_seq'] in newly_tainted:
                self.trust['agent:' + read['agent']] = 0

    def contacts(self, agent):
        tainted = {write['seq'] for writes in self.writes.values() for write in writes
                   if write['agent'] == agent and write['seq'] in self.tainted_writes}
        contacts = [read for read in self.reads
                    if read['write_seq'] in tainted and read['agent'] != agent]
        # Callers filter checked exposures before choosing one contact per agent.
        return sorted(contacts, key=lambda item: (item['seq'], item['agent'], item['path']))

    def release(self, agent):
        self.trust['agent:' + agent] = 1

    def clean_files(self):
        return sorted(key[5:] for key, trust in self.trust.items() if key.startswith('file:') and trust == 1)

    def snapshot(self):
        return {'nodes': [{'id': key, 'trust': value} for key, value in sorted(self.trust.items())],
                'edges': list(self.edges)}
