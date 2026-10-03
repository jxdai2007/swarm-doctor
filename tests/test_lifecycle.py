import pytest

from belowone.policy.lifecycle import Lifecycle
from belowone.graph.trust import TrustGraph


def test_freeze_kill_release_and_drift():
    life = Lifecycle(['a0'])
    life.transition('a0', 'steer')
    assert life.state('a0') == 'steered'
    life.transition('a0', 'clean')
    assert life.state('a0') == 'active'
    life.transition('a0', 'freeze')
    assert not life.allowed('a0')
    life.transition('a0', 'kill')
    assert life.state('a0') == 'killed'
    with pytest.raises(ValueError):
        life.transition('a0', 'release')


def test_budget_replacement_clean_files_only():
    life = Lifecycle(['a0'])
    graph = TrustGraph()
    graph.trust.update({'file:clean.py': 1, 'file:tainted.py': 0})
    life.transition('a0', 'end')
    replacement = life.replace('a0', 'a1', graph)
    assert replacement['visible_files'] == ['clean.py']
    assert replacement['reset_context'] is True
    assert life.allowed('a1')
