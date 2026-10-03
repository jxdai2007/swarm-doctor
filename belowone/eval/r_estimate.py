"""R point estimates per run; uncertainty resamples whole independent seeds."""
import random


def secondary_counts(infected, children):
    return {agent: len(set(children.get(agent, ())) - {agent}) for agent in sorted(infected)}


def estimate_r(infected, children, seed=0, bootstraps=10_000):
    counts = secondary_counts(infected, children)
    return {'mean': sum(counts.values()) / len(counts) if counts else None,
            'ci95': None, 'secondary': counts}


def r_across_seeds(per_run, seed=0, bootstraps=10_000):
    if type(bootstraps) is not int or bootstraps < 1:
        raise ValueError('bootstraps must be positive')
    seeded = [run['seed'] for run in per_run if 'seed' in run]
    if seeded and (len(seeded) != len(per_run) or len(set(seeded)) != len(seeded)):
        raise ValueError('Every cluster must have one unique seed ID')
    ordered = sorted(per_run, key=lambda run: (
        str(run.get('seed', '')),
        tuple(secondary_counts(run['infected'], run['children']).items())))
    values = [estimate_r(run['infected'], run['children'])['mean']
              for run in ordered if run['infected']]
    if not values:
        return {'mean': None, 'ci95': None, 'per_run': []}
    mean = sum(values) / len(values)
    if len(values) == 1:
        return {'mean': mean, 'ci95': None, 'per_run': values}
    rng = random.Random(seed)
    n = len(values)
    samples = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(bootstraps))
    return {'mean': mean, 'ci95': [samples[int(.025 * bootstraps)],
                                 samples[min(bootstraps - 1, int(.975 * bootstraps))]],
            'per_run': values}
