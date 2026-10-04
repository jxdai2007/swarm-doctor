"""Posthoc fixed-policy replay of immutable sources; never calls a model."""
import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

from belowone.detect.classify import Detector
from belowone.events import artifact_bytes
from belowone.eval.arms import replay_arm
from belowone.eval.decisions import bind_actions, load_decisions
from belowone.eval.metrics import outbreak_metrics
from belowone.runlog import EventLog
from belowone.runstore import RunStore
from belowone.spec.schema import GoalSpec

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / 'experiments/committed/pressure-campaign'
OUT = ROOT / 'experiments/committed/salvage-campaign'


class Unavailable:
    async def check(self, *args, **kwargs):
        raise RuntimeError('No matching checker response recorded; offline replay only')


async def main():
    source_store = RunStore(SOURCES)
    seals = {}
    for seal in sorted((SOURCES / '.seals').glob('*.sha256')):
        run = SOURCES / seal.stem
        assert source_store.verify(run), f'Invalid immutable source: {run}'
        seals[run.name] = {'seal_sha256': hashlib.sha256(seal.read_bytes()).hexdigest(),
                           'manifest_sha256': hashlib.sha256((run / 'manifest.json').read_bytes()).hexdigest()}
    rows = []
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='below-one-salvage-replay-') as workspace:
        for seed in (101, 102):
            name = f'injected-kimi-{seed}-verify'
            run = SOURCES / name
            envelope = json.loads((run / 'spec-interviewed.json').read_text())
            digest = envelope['spec_hash']
            spec = GoalSpec.from_dict(envelope['spec'], workspace=workspace)
            events = EventLog(run / 'events.jsonl').read()
            original = load_decisions(run / 'decisions.jsonl', digest)
            fixed = deepcopy(original)
            actions = bind_actions(events, original, digest)
            changes = []
            detector = Detector(spec, Unavailable())
            for key, event in actions.items():
                old = original[key]
                action = event.payload['action']
                if old.get('layer') != 'tripwire' or action.get('operation') != 'read':
                    continue
                if not old.get('reason', '').startswith('Protected resource: '):
                    continue
                updated = await detector.check(event.agent_id, action)
                if updated.get('layer') == 'tripwire':
                    continue
                fixed[key].update(updated, decision=updated['signal'])
                changes.append({'action_id': key, 'agent_id': event.agent_id,
                                'paths': action['paths'], 'old': old, 'new': fixed[key],
                                'checker_status': 'missing matching recorded response; fail-closed deny, no freeze; not a clean model verdict'})
            before = replay_arm(events, original, spec_hash=digest, arm='verify')
            after = replay_arm(events, fixed, spec_hash=digest, arm='verify')
            before_metrics, after_metrics = outbreak_metrics(before), outbreak_metrics(after)
            actual = json.loads((run / 'metrics.json').read_text())
            actual_freezes = [{'agent_id': e.agent_id, 'elapsed': e.payload['elapsed'],
                               'timestamp': e.timestamp, 'reason': e.payload.get('reason'), 'seq': e.seq}
                              for e in events if e.kind == 'freeze']
            source_times = lambda result: [c['elapsed'] for c in result.controls
                                           if c['agent_id'] == 'a0' and c['kind'] == 'freeze']
            row = {'run': name, 'seed': seed, 'source_path': run.relative_to(ROOT).as_posix(),
                   **seals[name], 'mode': 'posthoc same-recording fixed-policy replay; not live before/after',
                   'actual_recorded': actual, 'source_freezes_actual': actual_freezes,
                   'source_freeze_before': source_times(before), 'source_freeze_after': source_times(after),
                   'clean_freezes_before': before_metrics['clean_wrongly_frozen'],
                   'clean_freezes_after': after_metrics['clean_wrongly_frozen'],
                   'before': before_metrics, 'after': after_metrics, 'changed_decisions': changes,
                   'before_controls': before.controls, 'after_controls': after.controls,
                   'limitations': ['No replacement turns generated for frozen agents.',
                                   'Removed READ tripwires have no matching old checker response; fixed Detector fails closed with uncertain deny, not assumed clean.',
                                   'Scripted pre-policy P0 setup preserved in both replays; source is already infected, not prevention.']}
            assert before_metrics['infected'] >= 1 and after_metrics['infected'] >= 1
            rows.append(row)
            (OUT / f'step-a-{seed}-fixed-decisions.jsonl').write_bytes(b''.join(artifact_bytes(d) + b'\n' for d in fixed.values()))
    for name in seals:
        assert source_store.verify(SOURCES / name), f'Source changed: {name}'
    report = {'schema': 'salvage-step-a-v1', 'created_pt': datetime.now(ZoneInfo('America/Los_Angeles')).isoformat(),
              'source_commit': '8fe548878ec74a32984aa8bc344e690302d91ebe',
              'interpretation': 'Posthoc same sealed recordings; policy correction only. Not a live intervention difference.',
              'model_calls': 0, 'preserved_source_seals': seals, 'preserved_source_count': len(seals),
              'runs': rows, 'clean_freezes_before': sum(r['clean_freezes_before'] for r in rows),
              'clean_freezes_after': sum(r['clean_freezes_after'] for r in rows),
              'regression': {'detector': '22 passed', 'runtime': 'see focused runtime check receipt'}}
    (OUT / 'step-a.json').write_bytes(artifact_bytes(report))
    print(json.dumps({k: report[k] for k in ('created_pt', 'preserved_source_count', 'clean_freezes_before', 'clean_freezes_after')}))


if __name__ == '__main__':
    asyncio.run(main())
