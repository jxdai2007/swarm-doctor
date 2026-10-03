"""Explicit lifecycle: freeze preserves evidence; killed/ended states terminal."""


class Lifecycle:
    def __init__(self, agent_ids=()):
        self.states = {agent: 'active' for agent in agent_ids}
        self.drifts = {agent: 0 for agent in agent_ids}

    def state(self, agent):
        if agent not in self.states:
            raise ValueError(f'Unknown agent ID: {agent}')
        return self.states[agent]

    def allowed(self, agent):
        return self.state(agent) in {'active', 'steered', 'escalated'}

    def transition(self, agent, command):
        current = self.state(agent)
        if current in {'killed', 'ended'}:
            if command == current.rstrip('d'):
                return current
            raise ValueError(f'Cannot {command} terminal agent {agent}')
        if command == 'clean':
            if current != 'frozen':
                self.states[agent] = 'active'
        elif command == 'steer':
            if current == 'frozen':
                raise ValueError('Cannot steer frozen agent')
            self.drifts[agent] += 1
            self.states[agent] = 'escalated' if self.drifts[agent] >= 3 else 'steered'
        elif command == 'release':
            if current != 'frozen':
                raise ValueError('Only frozen agents may be released')
            self.states[agent] = 'active'
        elif command in {'freeze', 'kill', 'end'}:
            self.states[agent] = {'freeze': 'frozen', 'kill': 'killed', 'end': 'ended'}[command]
        elif command == 'continue':
            self.states[agent] = 'active'
        else:
            raise ValueError(f'Unknown lifecycle command: {command}')
        return self.states[agent]

    def replace(self, old_agent, new_agent, graph):
        if self.state(old_agent) != 'ended' or new_agent in self.states:
            raise ValueError('Replacement requires ended agent and fresh ID')
        self.states[new_agent] = 'active'
        self.drifts[new_agent] = 0
        return {'agent_id': new_agent, 'visible_files': graph.clean_files(), 'reset_context': True}
