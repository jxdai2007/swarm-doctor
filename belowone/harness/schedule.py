"""Whole-arm seed interleaving; pilot ladder records evidence, never fakes spread."""

from belowone.events import artifact_bytes
from .launcher import run
from .arms import LIVE_ARMS


async def schedule(seeds, arms, runner):
    if len(set(seeds)) != len(seeds) or not seeds or not arms:
        raise ValueError('Distinct nonempty seeds and arms required')
    results = []
    for seed in seeds:
        for arm in arms:
            results.append(await runner(seed, arm))
    return results


async def live_schedule(store, seeds, *, clients, workspace_root, commit,
                        scenario='outbreak', arms=LIVE_ARMS, prevention=(False, True), agent_count=5):
    """One router pin per seed across all live arms and prevention comparisons."""
    if any(arm not in LIVE_ARMS for arm in arms) or not prevention or any(type(value) is not bool for value in prevention):
        raise ValueError('Known live arms and boolean prevention settings required')
    if not clients.synthetic:
        from belowone.experiments import require_preregistration
        require_preregistration('live-harness')
    variants = [(arm, enabled) for arm in arms for enabled in prevention]
    async def runner(seed, variant):
        arm, enabled = variant
        return await run(store, f'{scenario}-{seed}-{arm}-prevention-{int(enabled)}',
                         seed=seed, arm=arm, scenario=scenario, clients=clients,
                         workspace_root=workspace_root, commit=commit,
                         prevention=enabled, agent_count=agent_count)
    return await schedule(seeds, variants, runner)


async def pilot(store, *, clients, workspace_root, commit):
    seeds = [0, 1, 2]
    results = []
    for seed in seeds:
        results.append(await run(store, f'pilot-{seed}', seed=seed, arm='no-defense',
                                 scenario='outbreak', clients=clients, workspace_root=workspace_root,
                                 commit=commit, agent_count=3))
    pressure = not any(result['secondary_infections'] for result in results)
    summary = {'seeds': seeds, 'runs': [f'pilot-{seed}' for seed in seeds],
               'chosen_scenario': 'outbreak-pressure' if pressure else 'outbreak',
               'reason': 'zero secondary infections across three seeds' if pressure else 'secondary spread observed',
               'synthetic': clients.synthetic,
               'source': 'synthetic-development' if clients.synthetic else 'live',
               'secondary_infections': [result['secondary_infections'] for result in results],
               'live_verification': 'blocked: live credentials required' if clients.synthetic else 'recorded'}
    (store.root / 'pilot-summary.json').write_bytes(artifact_bytes(summary))
    return summary
