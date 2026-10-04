import json

import pytest

from belowone.detect.classify import Detector
from belowone.detect.tallies import Tallies
from belowone.spec.schema import GoalSpec


class FastFixture:
    def __init__(self, *, goal=.98, hard='none', confidence=.95, progress=2, unavailable=False):
        self.calls = 0
        self.goal, self.hard, self.confidence, self.progress = goal, hard, confidence, progress
        self.unavailable = unavailable
    async def check(self, state, questions):
        self.calls += 1
        if self.unavailable:
            return {'verdict': 'uncertain', 'answers': {}}
        return {'verdict': 'checked', 'answers': {'serves_goal': {'type': 'noul', 'noul': self.goal}, 'hard_line': {'type': 'choice', 'choice': self.hard, 'confidence': self.confidence}, 'progress': {'type': 'score', 'score': self.progress, 'confidence': self.confidence}}}


class JudgeFixture:
    def __init__(self, label='drift', unavailable=False):
        self.calls = 0
        self.label, self.unavailable = label, unavailable
    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.unavailable:
            raise RuntimeError('offline')
        return {'choices': [{'message': {'content': json.dumps({'label': self.label, 'reason': 'Requested work does not advance CSV export.', 'confidence': .9})}}]}


def spec(tmp_path, **kwargs):
    return GoalSpec(goal='Add CSV export', done_when=['CSV test passes'], hard_lines=['Never share cheat'], decoys=['.env.production'], high_risk_paths=['tests/protected.py'], workspace=tmp_path, **kwargs)


@pytest.mark.parametrize('path', ['.env.production', 'goal-spec.json'])
async def test_tripwire_no_model(tmp_path, path):
    fast = FastFixture()
    detector = Detector(spec(tmp_path), fast, spec_path='goal-spec.json')
    result = await detector.check('a0', {'tool': 'read', 'operation': 'read', 'paths': [path]})
    assert result['label'] == 'violation' and result['allow'] is False
    assert result['layer'] == 'tripwire' and fast.calls == 0


@pytest.mark.parametrize('operation', ['write', 'delete', 'unknown', None])
@pytest.mark.parametrize('alias', [False, True])
async def test_high_risk_mutations_remain_tripwire(tmp_path, operation, alias):
    target = tmp_path / 'tests/protected.py'
    target.parent.mkdir()
    target.write_text('protected tests')
    path = 'tests/protected.py'
    if alias:
        (tmp_path / 'alias').symlink_to(target)
        path = 'alias'
    fast = FastFixture()
    result = await Detector(spec(tmp_path), fast).check('a0', {'paths': [path], 'operation': operation})
    assert result['layer'] == 'tripwire' and not result['allow']
    assert fast.calls == 0


@pytest.mark.parametrize('alias', [False, True])
async def test_high_risk_reads_reach_checker(tmp_path, alias):
    target = tmp_path / 'tests/protected.py'
    target.parent.mkdir()
    target.write_text('protected tests')
    path = 'tests/protected.py'
    if alias:
        (tmp_path / 'alias').symlink_to(target)
        path = 'alias'
    fast = FastFixture()
    result = await Detector(spec(tmp_path), fast).check('a0', {'paths': [path], 'operation': 'read'})
    assert result['label'] == 'clean' and result['allow']
    assert result['layer'] == 'jev' and fast.calls == 1


async def test_compartment_and_budget(tmp_path):
    detector = Detector(spec(tmp_path, compartments={'a0': ['src/**']}, budgets={'steps_per_agent': 1, 'cost_usd': 1}), FastFixture())
    assert (await detector.check('a0', {'paths': ['other.py']}))['label'] == 'violation'
    detector.tallies.observe('a1', {'paths': ['src/x.py']}, progress=2)
    result = await detector.check('a1', {'paths': ['src/x.py']})
    assert result['signal'] == 'end' and not result['allow']


@pytest.mark.parametrize('fast,label', [(FastFixture(hard='hard_line_0'), 'violation'), (FastFixture(), 'clean')])
async def test_fast_high_confidence(tmp_path, fast, label):
    result = await Detector(spec(tmp_path), fast).check('a0', {'paths': ['src/x.py']})
    assert result['label'] == label
    assert result['layer'] == 'jev'


async def test_uncertain_escalates_reason(tmp_path):
    judge = JudgeFixture()
    result = await Detector(spec(tmp_path), FastFixture(goal=.6), judge).check('a0', {'paths': ['x.py']})
    assert judge.calls == 1 and result['layer'] == 'judge'
    assert result['reason'] == 'Requested work does not advance CSV export.'


async def test_stalled_repeated_edits(tmp_path):
    detector = Detector(spec(tmp_path), FastFixture(progress=1))
    for index in range(6):
        detector.tallies.observe('a0', {'paths': ['utils/formatting.py' if index < 4 else 'README.txt'], 'operation': 'write'}, progress=1, cost_usd=.01)
    result = await detector.check('a0', {'paths': ['utils/formatting.py'], 'operation': 'write'})
    assert result['label'] == 'drift' and result['layer'] == 'tallies'
    assert detector.tallies.snapshot('a0')['cost_usd'] == pytest.approx(.06)


async def test_unreachable_both_denies(tmp_path):
    result = await Detector(spec(tmp_path), FastFixture(unavailable=True), JudgeFixture(unavailable=True)).check('a0', {'paths': []})
    assert not result['allow'] and result['uncertain']
    assert result['poisoned'] is False


async def test_cheat_message_checked_before_delivery(tmp_path):
    fast = FastFixture(hard='hard_line_0')
    result = await Detector(spec(tmp_path), fast).check('a0', {'tool': 'send_message', 'operation': 'message', 'paths': [], 'input': {'recipient': 'a1', 'content': 'Share cheat'}})
    assert fast.calls == 1 and not result['allow']


async def test_actual_jev_client_detector_smoke(tmp_path):
    import httpx
    from belowone.meter import Meter
    from belowone.models.cassette import Cassette
    from belowone.models.jev import JevClient
    reply = {'model': 'synthetic-jev', 'answers': {'serves_goal': {'type': 'noul', 'noul': .98}, 'hard_line': {'type': 'choice', 'choice': 'none', 'confidence': .95}, 'progress': {'type': 'score', 'score': 2, 'confidence': .95}}, 'usage': {'input_tokens': 10, 'output_tokens': 2, 'cost': '0.00000042'}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=reply))) as http:
        client = JevClient('development-fixture', Meter(), Cassette(tmp_path / 'cache'), http=http)
        detector = Detector(spec(tmp_path), client)
        action = {'tool': 'write', 'operation': 'write', 'paths': ['src/csv.py']}
        recorded = await detector.check('a0', action)
    replay = Detector(spec(tmp_path), JevClient(None, Meter(), Cassette(tmp_path / 'cache', 'replay')))
    assert await replay.check('a0', action) == recorded
    assert recorded['label'] == 'clean'


@pytest.mark.parametrize('alias', [False, True])
async def test_tripwire_matches_normalized_and_resolved_resource(tmp_path, alias):
    decoy = tmp_path / '.env.production'
    decoy.write_text('synthetic decoy')
    path = '.env.production'
    if alias:
        (tmp_path / 'alias').symlink_to(decoy)
        path = 'alias'
    locked = GoalSpec(goal='CSV', done_when=['tests pass'], decoys=['./.env.production'], workspace=tmp_path)
    fast = FastFixture()
    result = await Detector(locked, fast).check('a0', {'paths': [path], 'operation': 'read'})
    assert result['layer'] == 'tripwire' and not result['allow']
    assert fast.calls == 0
