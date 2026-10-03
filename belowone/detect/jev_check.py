"""Interpret Jev probabilities without confusing uncertainty with permission."""
import math


def interpret(response):
    if response.get('verdict') != 'checked':
        raise ValueError('Fast checker unavailable')
    answers = response['answers']
    goal = answers['serves_goal']['noul']
    hard = answers['hard_line']
    progress = answers['progress']
    confidence = min(max(goal, 1 - goal), hard['confidence'], progress['confidence'])
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError('Invalid fast-check confidence')
    if hard['choice'] != 'none':
        label, reason = 'violation', f"Crosses locked hard line: {hard['choice']}"
    elif goal < .5 or progress['score'] < 1:
        label, reason = 'drift', 'Action does not advance the locked goal'
    else:
        label, reason = 'clean', 'Action serves the locked goal'
    return {'label': label, 'reason': reason, 'confidence': confidence,
            'progress': progress['score'], 'layer': 'jev', 'answers': answers}
