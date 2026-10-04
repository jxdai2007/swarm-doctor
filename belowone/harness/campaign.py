"""Authorized pressure campaign: real actors, explicit intervention, owned deadline."""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import signal

from belowone.events import artifact_bytes
from belowone.harness.launcher import live_clients, run
from belowone.meter import BudgetExceeded, Meter
from belowone.models.kimi import QuotaPending, RateLimitExceeded
from belowone.runstore import RunStore


def credit_stopped(report):
    from decimal import Decimal
    reason = report.get('stop_reason')
    return (report['budget_breached'] or
            Decimal(report['spent_usd']) + Decimal(report['reserved_usd']) >= Decimal(report['cap_usd']) or
            bool(reason and not reason.startswith('Kimi provider quota exhausted')))


def overflow_backend(backend, failure, meter):
    """A failed whole Kimi set may restart on OR; never erase a credit stop."""
    if backend == 'kimi' and isinstance(failure, (QuotaPending, RateLimitExceeded)) and not credit_stopped(meter.report()):
        return 'openrouter'
    return None


async def campaign(args):
    root = Path(args.out).resolve()
    store = RunStore(root)
    audit_path = root / 'campaign-audit.json'
    prior = json.loads(audit_path.read_text()) if audit_path.is_file() else {}
    if prior.get('meter') and credit_stopped(prior['meter']):
        raise BudgetExceeded(prior['meter'].get('stop_reason') or 'Prior campaign spend cap reached')
    clients = live_clients(root / '_cache', Meter(cap_usd='15'))
    if args.backend == 'openrouter':
        clients.router._quota_exhausted = True
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    deadline = datetime.fromisoformat(args.deadline).timestamp()
    loop.call_later(max(0, deadline - datetime.now().timestamp()), stop.set)
    active_engine = None
    receipts = prior.get('runs', [])

    async def ready(engine):
        nonlocal active_engine
        active_engine = engine

    async def record(seed, arm, injected):
        nonlocal active_engine
        active_engine = None
        name = f'{"injected" if injected else "natural"}-{args.backend}-{seed}-{arm}'
        task = asyncio.create_task(run(store, name, seed=seed, arm=arm,
            scenario='outbreak-pressure', clients=clients, workspace_root=args.workspaces,
            commit=args.commit, agent_count=5, steps=20, engine_ready=ready,
            cohort_role=('validation/injected-pressure' if injected else
                         'validation/natural-paired-pressure' if args.phase == 'paired' else 'validation/natural-pressure'),
            injected_compromise=injected))
        deadline_task = asyncio.create_task(stop.wait())
        result = None
        error = None
        failure = None
        try:
            done, _ = await asyncio.wait((task, deadline_task), return_when=asyncio.FIRST_COMPLETED)
            if deadline_task in done and not task.done():
                if active_engine is not None:
                    for agent, state in active_engine.snapshot()['states'].items():
                        if state not in {'killed', 'ended'}:
                            await active_engine.control(agent, 'kill', 'Owned campaign deadline/interruption')
                clients.meter.stop_reason = 'Owned campaign deadline/interruption'
                if active_engine is not None:
                    traces = list(active_engine._trace_tasks)
                    for trace in traces:
                        trace.cancel()
                    await asyncio.gather(*traces, return_exceptions=True)
                    active_engine._trace_tasks.clear()
                task.cancel()
            result = await task
        except BaseException as exc:
            error = 'Owned campaign deadline/interruption' if isinstance(exc, asyncio.CancelledError) else str(exc)
            failure = exc
            folder = root / name
            if active_engine is not None:
                for agent, state in active_engine.snapshot()['states'].items():
                    if state not in {'killed', 'ended'}:
                        await active_engine.control(agent, 'kill', error)
                await active_engine.wait_pending_traces()
            if folder.is_dir() and not (folder / 'manifest.json').exists():
                if active_engine is not None:
                    store.write_json(folder, 'snapshot.json', active_engine.snapshot())
                store.write_json(folder, 'incomplete.json', {'status': 'incomplete', 'reason': error,
                    'seed': seed, 'arm': arm, 'injected_compromise': injected,
                    'backend_pin': args.backend, 'meter': clients.meter.report()})
                store.seal(folder)
        finally:
            deadline_task.cancel()
        folder = root / name
        # Independent audit: a normal seal is insufficient under known terminal swallowing.
        complete = result is not None and not clients.meter.stop_reason
        audit = {'run': name, 'complete': complete, 'error': error or clients.meter.stop_reason,
                 'injected_compromise': injected, 'backend_pin': args.backend,
                 'error_class': type(failure).__name__ if failure else None}
        if folder.is_dir() and (folder / 'manifest.json').is_file():
            import hashlib
            audit['seal_sha256'] = hashlib.sha256((root / '.seals' / f'{name}.sha256').read_bytes()).hexdigest()
        if result is not None:
            required = ('summary.json', 'snapshots.json', 'checks.json', 'decisions.jsonl',
                        'decisions-one-line.jsonl', 'spec-interviewed.json', 'spec-one-line.json')
            audit['missing'] = [item for item in required if not (folder / item).is_file()]
            audit['complete'] &= not audit['missing']
            audit['grader'] = result['grader']
            audit['infections'] = result['infections']
            audit['secondary_infections'] = result['secondary_infections']
            audit['served_models'] = result['served_models']
            events = [json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines()]
            audit['freeze_events'] = sum(event['kind'] == 'freeze' for event in events)
            failures = []
            for path in (folder / 'cache').glob('*.json'):
                response = json.loads(path.read_text())['response']
                if response.get('_belowone_error'):
                    failures.append({'cassette': path.name, 'kind': response['_belowone_error'],
                                     'reason': response.get('reason'),
                                     'provider': response.get('_belowone_attempts', [{}])[0].get('provider')})
            audit['recorded_model_failures'] = failures
            if any(row['kind'] in {'quota_pending', 'rate_limit'} for row in failures):
                audit['complete'] = False
                audit['error'] = audit['error'] or 'Recorded provider quota/rate-limit failure; paired arm excluded'
                terminal = next((row for row in failures if row['provider'] == 'kimi' and
                                 row['kind'] in {'quota_pending', 'rate_limit'}), None)
                if terminal and failure is None:
                    failure = (QuotaPending if terminal['kind'] == 'quota_pending' else RateLimitExceeded)(terminal['reason'])
                    audit['error_class'] = type(failure).__name__
        audit['overflow_backend'] = overflow_backend(args.backend, failure, clients.meter)
        receipts.append(audit)
        (root / 'campaign-audit.json').write_bytes(artifact_bytes({'runs': receipts, 'meter': clients.meter.report()}))
        print(json.dumps(audit, sort_keys=True), flush=True)
        return audit

    try:
        if args.phase == 'pressure':
            await record(args.seed, 'no-defense', False)
        else:
            for arm in ('no-defense', 'prompt-only', 'verify'):
                if stop.is_set() or clients.meter.stop_reason:
                    break
                audit = await record(args.seed, arm, args.injected_compromise)
                if not audit['complete']:
                    break
    finally:
        await clients.aclose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('pressure', 'paired'), required=True)
    parser.add_argument('--backend', choices=('kimi', 'openrouter'), default='kimi')
    parser.add_argument('--injected-compromise', action='store_true')
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--workspaces', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--deadline', default='2026-10-04T10:30:00-07:00')
    asyncio.run(campaign(parser.parse_args()))
