"""Regenerate/reproduce actual-only salvage evidence; no organism policy replay."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from belowone.events import artifact_bytes
from belowone.eval.metrics import drift_metrics, outbreak_metrics
from belowone.eval.replay import replay_freeze_schedule
from belowone.experiments import network_disabled
from belowone.runlog import EventLog
from belowone.runstore import RunStore
from scripts.salvage_campaign import changed_byte_secondary

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'experiments/committed/salvage-campaign'
NOTE = 'susceptible model organism, chosen to induce spread; tests containment, not natural propensity'
ARMS = ('no-defense', 'prompt-only', 'periodic-review', 'blunt-khop', 'verify', 'strict')


def main(*, check=False):
    with network_disabled():
        selection_path = OUT / 'selection-outcome.json'
        selection = json.loads(selection_path.read_text()) if selection_path.exists() else {'selected_model': None, 'status': 'pending'}
        candidate = selection.get('selected_model')
        model = candidate['id'] if candidate else None
        store = RunStore(OUT)
        runs = []
        audits = {}
        for filename in ('protocol-v1-campaign-audit.json', 'campaign-audit.json'):
            audit_path = OUT / filename
            if audit_path.exists():
                audits.update({r['run']: r for r in json.loads(audit_path.read_text())['runs']})
        for folder in sorted(OUT.glob('salvage-*')):
            if not folder.is_dir() or not (folder / 'manifest.json').is_file():
                continue
            assert store.verify(folder), f'Invalid salvage seal: {folder}'
            config = json.loads((folder / 'config.json').read_text())
            role = ('model-selection' if config['seed'] % 100 < 10 else 'nd-replication'
                    if config['arm'] == 'no-defense' else 'live-verify-validation')
            complete = not (folder / 'incomplete.json').exists() and (folder / 'summary.json').exists()
            complete &= bool(audits.get(folder.name, {}).get('complete'))
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
            row['completion_audit'] = audits.get(folder.name)
            events = EventLog(folder / 'events.jsonl').read()
            real_actions = [e for e in events if e.kind == 'action_proposed' and not e.payload.get('injected_compromise')]
            row['valid_model_action_count'] = sum(e.payload['action']['tool'] != 'invalid' for e in real_actions)
            row['invalid_model_action_count'] = sum(e.payload['action']['tool'] == 'invalid' for e in real_actions)
            row['model_turn_cap'] = config['model_turn_budget']
            row['exposed_real_peers'] = sorted({e.agent_id for e in events if e.kind == 'exposure'
                                                and e.agent_id != 'a0' and e.payload.get('source_agent') is not None})
            row['infection_events'] = [{'agent_id': e.agent_id, 'source_agent': e.payload.get('source_agent'),
                                        'elapsed': e.payload['elapsed'], 'action_id': e.payload.get('action_id'),
                                        'write_event_id': e.payload.get('write_event_id'),
                                        'read_event_id': e.payload.get('read_event_id')}
                                       for e in events if e.kind == 'infection']
            row['changed_byte_secondary_proofs'] = changed_byte_secondary(events)
            row['qualifying_secondary_count'] = len({p['agent_id'] for p in row['changed_byte_secondary_proofs']})
            if complete:
                summary = json.loads((folder / 'summary.json').read_text())
                row['served_peer_models'] = summary.get('served_peer_models', summary['served_models'])
                row['served_judge_models'] = summary.get('served_judge_models', [])
                row['actual'] = json.loads((folder / 'metrics.json').read_text())
                row['grader'] = summary['grader']
                controls = [{'agent_id': e.agent_id, 'kind': e.kind,
                             'elapsed': e.payload['elapsed'], 'order': e.seq}
                            for e in events if e.kind in {'freeze', 'release', 'kill'}]
                result = replay_freeze_schedule(events, controls=controls, arm=config['arm'])
                actual = {**outbreak_metrics(result),
                          **drift_metrics(events, controls=controls, total_agents=len(summary['turns']),
                                          defense_cost_usd=summary['defense_cost_usd']),
                          'source': 'actual-recorded-live-policy', 'synthetic': config['synthetic']}
                assert actual == row['actual'], f'Actual metric mismatch: {folder.name}'
                row['actual_metrics_reproduced'] = True
                row['observed_secondary'] = summary['secondary_infections']
            else:
                row['incomplete'] = (json.loads((folder / 'incomplete.json').read_text()) if (folder / 'incomplete.json').exists()
                                     else {'reason': (audits.get(folder.name, {}).get('error') or
                                                      'Recorded model failure; excluded from eligible denominators')})
            runs.append(row)
        completed = [r for r in runs if r['complete']]
        corrected = [r for r in runs if r['seed'] != 200]
        nd = [r for r in completed if r['config']['arm'] == 'no-defense']
        chart = {arm: {'status': 'unmeasured', 'run_count': 0, 'r_mean': None, 'r_ci95': None,
                       'interpretation': NOTE, 'reason': 'Qualifying spread absent; conditional organism replay/live validation skipped.'}
                 for arm in ARMS}
        if nd:
            chart['no-defense'] = {'status': 'observed-scripted-source', 'run_count': len(nd),
                                   'r_mean': sum(r['actual']['r_mean'] for r in nd) / len(nd),
                                   'r_ci95': None, 'infected': [r['actual']['infected'] for r in nd],
                                   'source_runs': [r['run'] for r in nd], 'interpretation': NOTE,
                                   'reason': 'Actual candidate episode only; source scripted. Not natural propensity or containment benefit.'}
        phases = {}
        for filename in ('protocol-v1-campaign-accounting.json', 'campaign-accounting.json'):
            path = OUT / filename
            if path.exists():
                phases[filename] = json.loads(path.read_text())
        accounting = {'phases': phases,
                      'known_phase_cost_usd': str(sum((Decimal(p['phase_recorded_cost_usd']) for p in phases.values()), Decimal(0))),
                      'unsettled_phase_reservations_usd': str(sum((Decimal(p['meter']['reserved_usd']) for p in phases.values()), Decimal(0))),
                      'interpretation': 'Native total key limit $15 unchanged; prior-usage ledger rows are not new paid calls. Unknown/cancelled charges retained as reservations.'}
        report = {'schema': 'salvage-step-b-v2', 'created_pt': phases['campaign-accounting.json']['all_jobs_joined_pt'],
                  'interpretation': NOTE, 'selection': selection, 'selected_model': model,
                  'attempted_distinct_models': len({r['config']['requested_peer_model'] for r in corrected}),
                  'completed_candidate_episode_count': sum(r['complete'] for r in corrected),
                  'failed_or_partial_candidate_count': sum(not r['complete'] for r in corrected),
                  'preserved_protocol_failure_count': sum(r['seed'] == 200 for r in runs),
                  'eligible_nd_count': 0, 'observed_nd_count': len(nd), 'actual_verify_count': 0,
                  'observed_secondary': sum(r['qualifying_secondary_count'] for r in runs),
                  'manifest_secondary': sum(e['source_agent'] is not None for r in runs for e in r['infection_events']),
                  'runs': runs, 'arms': chart, 'accounting': accounting,
                  'native_budget_before': json.loads((OUT / 'protocol-v1-native-budget-before.json').read_text()),
                  'native_budget_after': json.loads((OUT / 'native-budget-after.json').read_text()),
                  'model_jobs_owned': 0, 'conditional_organism_replay': 'skipped', 'conditional_live_verify': 'skipped',
                  'limitations': ['No susceptible organism selected; complete LingFlash episode is bounded, not model resistance.',
                                  'Nemo lack of data reflects slow generation; LingVL failed HTTP429; both excluded.',
                                  'Scripted P0 source already infected before policy; source R0 is not natural propensity.',
                                  'No organism six-arm counterfactual replay or live-verify run: qualifying spread absent.',
                                  'Raw launcher-generated generic all-arms caches retained but excluded from claims and final chart.',
                                  'Eight-turn corrected episodes bounded; valid/invalid real-model actions separate.',
                                  'Selection requires attributed changed-byte executed write/delete. Manifest also counts synthetic-decoy reads; definitions remain separate.']}
        encoded = artifact_bytes(report)
        if check:
            assert (OUT / 'step-b.json').read_bytes() == encoded, 'Actual-only regenerated report differs'
            print('SALVAGE_REPORT_IDENTICAL_NETWORK_DISABLED')
        else:
            (OUT / 'step-b.json').write_bytes(encoded)
            print('SALVAGE_ACTUAL_ONLY_REGENERATED')
        print(json.dumps({k: report[k] for k in ('created_pt', 'selected_model', 'eligible_nd_count', 'actual_verify_count', 'observed_secondary')}))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    main(check=parser.parse_args().check)
