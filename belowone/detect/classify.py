"""One classification path for tool actions, messages, and trace reviews."""
from belowone.detect.jev_check import interpret
from belowone.detect.judge import judge
from belowone.detect.tallies import Tallies
from belowone.detect.tripwires import tripwire
from belowone.spec.compile import compile_questions


class Detector:
    def __init__(self, spec, jev, judge_client=None, *, spec_path='goal-spec.json',
                 escalation_band=(.35, .75), tallies=None):
        self.spec, self.jev, self.judge_client = spec, jev, judge_client
        self.spec_path = spec_path
        low, high = escalation_band
        if not 0 <= low <= high <= 1:
            raise ValueError('Invalid escalation band')
        self.escalation_band = (low, high)
        self.questions = compile_questions(spec)
        self.tallies = tallies or Tallies()

    async def check(self, agent_id, action, *, before_kill=False, trace=False):
        history = self.tallies.snapshot(agent_id)
        hit = None if trace else tripwire(self.spec, agent_id, action, history, spec_path=self.spec_path)
        if hit is not None and not before_kill:
            return {'label': 'violation', 'allow': False, 'confidence': 1.,
                    'progress': 1, 'layer': 'tripwire', 'uncertain': False,
                    'poisoned': hit['signal'] != 'end', **hit}
        state = {'agent_id': agent_id, 'action': action, 'recent_executed_steps': history['recent'], 'trace_review': trace}
        try:
            fast = interpret(await self.jev.check(state, self.questions))
            uncertain = False
        except Exception:
            fast = {'label': 'violation', 'confidence': 0., 'progress': 1,
                    'reason': 'Fast checker unavailable', 'layer': 'jev'}
            uncertain = True
        low, high = self.escalation_band
        if uncertain or low <= fast['confidence'] <= high or before_kill:
            try:
                result = await judge(self.judge_client, self.spec, action, fast, history['recent'], before_kill=before_kill)
                uncertain = False
            except Exception:
                return {**fast, 'allow': False, 'signal': 'deny', 'uncertain': True,
                        'poisoned': False, 'reason': 'Fast checker and escalation judge unavailable'}
        else:
            result = fast
        if result['label'] == 'clean' and self.tallies.stalled(agent_id, action):
            result = {**result, 'label': 'drift', 'layer': 'tallies',
                      'reason': 'Repeated edits with no progress across six executed steps'}
        label = result['label']
        return {**result, 'allow': label == 'clean', 'uncertain': uncertain,
                'poisoned': label == 'violation',
                'signal': {'clean': 'allow', 'drift': 'steer', 'violation': 'freeze'}[label]}
