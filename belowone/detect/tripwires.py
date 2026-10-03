"""Cheap locked-spec checks precede all paid model calls."""
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from belowone.spec.schema import workspace_path, SpecValidationError


def tripwire(spec, agent_id, action, tally, *, spec_path='goal-spec.json'):
    if tally['steps'] >= spec.budgets['steps_per_agent']:
        return {'reason': 'Step budget exhausted', 'signal': 'end'}
    if tally['cost_usd'] >= spec.budgets['cost_usd']:
        return {'reason': 'Cost budget exhausted', 'signal': 'end'}
    paths = action.get('paths', [])
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        return {'reason': 'Invalid resource paths', 'signal': 'freeze'}
    patterns = spec.compartments.get(agent_id, spec.compartments.get('*'))
    root = spec._workspace
    protected = {PurePosixPath(path).as_posix() for path in (spec_path, *spec.decoys, *spec.high_risk_paths)}
    try:
        protected.update((root / path).resolve().relative_to(root).as_posix() for path in tuple(protected))
    except (ValueError, OSError, RuntimeError):
        return {'reason': 'Protected resource resolves outside workspace', 'signal': 'freeze'}
    for path in paths:
        try:
            workspace_path(path, spec._workspace, 'action path')
        except SpecValidationError:
            return {'reason': f'Unsafe resource path: {path}', 'signal': 'freeze'}
        normalized = PurePosixPath(path).as_posix()
        resolved = (root / path).resolve().relative_to(root).as_posix()
        for resource in sorted({normalized, resolved}):
            if any(resource == risk or resource.startswith(risk.rstrip('/') + '/') for risk in protected):
                return {'reason': f'Protected resource: {resource}', 'signal': 'freeze'}
            if patterns is not None and not any(fnmatchcase(resource, PurePosixPath(pattern).as_posix()) for pattern in patterns):
                return {'reason': f'Outside compartment: {resource}', 'signal': 'freeze'}
    return None
