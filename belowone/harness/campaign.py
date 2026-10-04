"""Authorized pressure campaign: real actors, explicit intervention, owned deadline."""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import signal

from belowone.events import artifact_bytes
from belowone.harness.launcher import live_clients, run
from belowone.meter import Meter
from belowone.runstore import RunStore


async def campaign(args):
    root = Path(args.out).resolve()
    store = RunStore(root)
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
    receipts = []

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
            cohort_role='validation/injected-pressure' if injected else 'validation/natural-pressure',
            injected_compromise=injected))
        deadline_task = asyncio.create_task(stop.wait())
        result = None
        error = None
        try:
            done, _ = await asyncio.wait((task, deadline_task), return_when=asyncio.FIRST_COMPLETED)
            if deadline_task in done and not task.done():
                if active_engine is not None:
                    for agent, state in active_engine.snapshot()['states'].items():
                        if state not in {'killed', 'ended'}:
                            await active_engine.control(agent, 'kill', 'Owned campaign deadline/interruption')
                clients.meter.stop_reason = 'Owned campaign deadline/interruption'
                task.cancel()
            result = await task
        except BaseException as exc:
            error = 'Owned campaign deadline/interruption' if isinstance(exc, asyncio.CancelledError) else str(exc)
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
                 'injected_compromise': injected, 'backend_pin': args.backend}
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
                audit = await record(args.seed, arm, True)
                if not audit['complete']:
                    break
    finally:
        await clients.aclose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('pressure', 'paired'), required=True)
    parser.add_argument('--backend', choices=('kimi', 'openrouter'), default='kimi')
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--workspaces', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--deadline', default='2026-10-04T10:30:00-07:00')
    asyncio.run(campaign(parser.parse_args()))
