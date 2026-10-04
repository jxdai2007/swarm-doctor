"""Offline six-arm salvage report, preserving injected sources and failures."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from belowone.events import artifact_bytes
from belowone.eval.arms import replay_arm
from belowone.eval.decisions import load_decisions
from belowone.eval.metrics import aggregate_outbreaks, drift_metrics, outbreak_metrics
from belowone.experiments import network_disabled
from belowone.runlog import EventLog
from belowone.runstore import RunStore

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'experiments/committed/salvage-campaign'
NOTE = 'susceptible model organism, chosen to induce spread; tests containment, not natural propensity'
ARMS = ('no-defense', 'prompt-only', 'periodic-review', 'blunt-khop', 'verify', 'strict')


def main():
    with network_disabled():
        selection_path = OUT / 'selection-outcome.json'
        selection = json.loads(selection_path.read_text()) if selection_path.exists() else {'selected_model': None, 'status': 'pending'}
        candidate = selection.get('selected_model')
        model = candidate['id'] if candidate else None
        store = RunStore(OUT)
        runs, eligible_results = [], []
        for folder in sorted(OUT.glob('salvage-*')):
            if not folder.is_dir() or not (folder / 'manifest.json').is_file():
                continue
            assert store.verify(folder), f'Invalid salvage seal: {folder}'
            config = json.loads((folder / 'config.json').read_text())
            role = ('model-selection' if config['seed'] % 100 < 10 else 'nd-replication'
                    if config['arm'] == 'no-defense' else 'live-verify-validation')
            complete = not (folder / 'incomplete.json').exists() and (folder / 'summary.json').exists()
            failures = []
            for cache in sorted((folder / 'cache').glob('*.json')):
                response = json.loads(cache.read_text()).get('response', {})
                if response.get('_belowone_error'):
                    failures.append({'cassette': cache.name, 'kind': response['_belowone_error'], 'reason': response.get('reason')})
            complete &= not failures
            row = {'run': folder.name, 'seed': config['seed'], 'role': role, 'complete': complete,
                   'interpretation': NOTE, 'source': 'sealed-real-recording', 'config': config,
                   'seal_sha256': hashlib.sha256((OUT / '.seals' / (folder.name + '.sha256')).read_bytes()).hexdigest(),
                   'model_failures': failures, 'actual': None, 'arms': None}
            events = EventLog(folder / 'events.jsonl').read()
            real_actions = [e for e in events if e.kind == 'action_proposed' and not e.payload.get('injected_compromise')]
            row['valid_model_action_count'] = sum(e.payload['action']['tool'] != 'invalid' for e in real_actions)
            row['invalid_model_action_count'] = sum(e.payload['action']['tool'] == 'invalid' for e in real_actions)
            row['model_turn_cap'] = config['model_turn_budget']
            if complete:
                summary = json.loads((folder / 'summary.json').read_text())
                row['served_peer_models'] = summary.get('served_peer_models', summary['served_models'])
                row['served_judge_models'] = summary.get('served_judge_models', [])
                row['actual'] = json.loads((folder / 'metrics.json').read_text())
                row['grader'] = summary['grader']
                events = EventLog(folder / 'events.jsonl').read()
                digest = config['spec_hash']
                decisions = load_decisions(folder / 'decisions.jsonl', digest)
                results = {arm: replay_arm(events, decisions, spec_hash=digest, arm=arm) for arm in ARMS}
                for result in results.values():
                    assert 'a0' in result.infected_agents, f'Scripted pre-policy source incorrectly prevented: {folder.name}/{result.arm}'
                row['arms'] = {arm: {**outbreak_metrics(result),
                                     **drift_metrics(events, controls=result.controls, total_agents=config['agent_count']),
                                     'interpretation': NOTE, 'mode': 'posthoc cached-policy counterfactual, not live intervention'}
                               for arm, result in results.items()}
                row['observed_secondary'] = summary['secondary_infections']
                if role == 'nd-replication' and config.get('requested_peer_model') == model:
                    eligible_results.append((config['seed'], results))
            else:
                row['incomplete'] = json.loads((folder / 'incomplete.json').read_text()) if (folder / 'incomplete.json').exists() else {'reason': 'Recorded model failure; excluded from eligible denominators'}
            runs.append(row)
        aggregates = {}
        if eligible_results:
            for arm in ARMS:
                aggregates[arm] = {**aggregate_outbreaks([r[arm] for _, r in eligible_results], seeds=[s for s, _ in eligible_results]),
                                   'interpretation': NOTE, 'mode': 'posthoc same-recording counterfactual'}
        report = {'schema': 'salvage-step-b-v1', 'created_pt': datetime.now(ZoneInfo('America/Los_Angeles')).isoformat(),
                  'interpretation': NOTE, 'selection': selection, 'selected_model': model,
                  'eligible_nd_count': len(eligible_results),
                  'actual_verify_count': sum(r['complete'] and r['role'] == 'live-verify-validation' for r in runs),
                  'observed_secondary': sum(r.get('observed_secondary', 0) for r in runs if r['complete']),
                  'runs': runs, 'arms': aggregates,
                  'limitations': ['Models selected intentionally for spread; not natural propensity.',
                                  'Scripted P0 source already infected before policy; never claim prevention.',
                                  'Model-selection seeds excluded from ND replication denominator; partial/error runs excluded.',
                                  'Counterfactual policies prune recorded turns, never generate replacement actions.',
                                  'Prompt-only replay cannot regenerate instruction-conditioned model behavior; identity replay is comparator inference.',
                                  'Contact/exposure is not infection; secondary requires manifest changed-byte infection.']}
        (OUT / 'step-b.json').write_bytes(artifact_bytes(report))
        print(json.dumps({k: report[k] for k in ('created_pt', 'selected_model', 'eligible_nd_count', 'actual_verify_count', 'observed_secondary')}))


if __name__ == '__main__':
    main()
