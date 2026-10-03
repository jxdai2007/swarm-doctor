"""H4 ablation: identical recording, independently cached locked specs."""
from .arms import replay_arm
from .decisions import load_decisions
from .metrics import drift_metrics, outbreak_metrics


def ablate(events, cache_paths, spec_hashes, *, arm='verify', total_agents=0, seed=0):
    results = {}
    for name in ('interviewed', 'one_line'):
        decisions = load_decisions(cache_paths[name], spec_hashes[name])
        result = replay_arm(events, decisions, spec_hash=spec_hashes[name], arm=arm)
        results[name] = {'outbreak': outbreak_metrics(result),
                         'drift': drift_metrics(events, controls=result.controls, total_agents=total_agents)}
    return {'arms': results,
            'false_alarm_delta': results['one_line']['outbreak']['clean_wrongly_frozen'] - results['interviewed']['outbreak']['clean_wrongly_frozen'],
            'false_steer_delta': results['one_line']['drift']['false_steers'] - results['interviewed']['drift']['false_steers']}
