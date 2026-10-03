"""Executed-step history, progress, and spend; denied proposals do not burn steps."""
from collections import defaultdict, deque
import math


class Tallies:
    def __init__(self):
        self.history = defaultdict(lambda: deque(maxlen=6))
        self.steps = defaultdict(int)
        self.cost = defaultdict(float)

    def observe(self, agent_id, action, *, progress=1, cost_usd=0):
        if type(progress) not in (int, float) or not math.isfinite(progress):
            raise ValueError('progress must be finite')
        if type(cost_usd) not in (int, float) or not math.isfinite(cost_usd) or cost_usd < 0:
            raise ValueError('cost must be finite nonnegative')
        self.steps[agent_id] += 1
        self.cost[agent_id] += cost_usd
        self.history[agent_id].append({'paths': tuple(action.get('paths', [])),
                                      'operation': action.get('operation'), 'progress': progress})

    def snapshot(self, agent_id):
        return {'steps': self.steps[agent_id], 'cost_usd': self.cost[agent_id],
                'recent': list(self.history[agent_id])}

    def stalled(self, agent_id, action):
        history = self.history[agent_id]
        if len(history) < 6 or any(item['progress'] > 1 for item in history):
            return False
        if action.get('operation') != 'write':
            return False
        return any(sum(item['operation'] == 'write' and path in item['paths'] for item in history) >= 4
                   for path in action.get('paths', []))
