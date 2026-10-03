"""Decision cache adapter tests: strict keyed lookup, taxonomy mapping."""
import json
import pytest

from belowone.eval.decisions import (
    ENFORCING_STOP, freeze_schedule, load_decisions, shadow_checks,
)


def line(**kw):
    base = {"spec_hash": "abc123", "agent_id": "a0", "label": "violation",
            "confidence": 0.9, "reason": "cheat"}
    return {**base, **kw}


def test_strict_lookup_by_spec_and_action(tmp_path):
    p = tmp_path / "decisions.jsonl"
    p.write_text(json.dumps(line(action_id="x1", decision="freeze",
                                 decided_at_elapsed=9.5, latency_s=0.4)) + "\n"
                 + json.dumps(line(action_id="x2", decision="allow",
                                   decided_at_elapsed=12.0, latency_s=0.4)) + "\n")
    d = load_decisions(p, "abc123")
    assert set(d) == {"x1", "x2"}
    with pytest.raises(KeyError, match="spec_hash"):
        load_decisions(p, "other")
    dup = tmp_path / "dup.jsonl"
    dup.write_text((json.dumps(line(action_id="x1", decision="allow",
                                    decided_at_elapsed=1.0, latency_s=0.1)) + "\n") * 2)
    with pytest.raises(KeyError, match="duplicate"):
        load_decisions(dup, "abc123")
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps(line(action_id="x9", spec_hash="abc123",
                                   decision="allow")) + "\n")  # no timing
    with pytest.raises(KeyError, match="missing"):
        load_decisions(bad, "abc123")


def test_taxonomy_maps_enforcing_to_stops_and_shadow_skipped(tmp_path):
    p = tmp_path / "decisions.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in [
        line(action_id="f", decision="freeze", decided_at_elapsed=9.5, latency_s=0.3),
        line(action_id="k", decision="kill", agent_id="a1",
             decided_at_elapsed=20.0, latency_s=0.3),
        line(action_id="d", decision="deny", agent_id="a2",
             decided_at_elapsed=25.0, latency_s=0.3),
        line(action_id="s", decision="steer", agent_id="a3",
             decided_at_elapsed=30.0, latency_s=0.3),
        line(action_id="sh", decision="freeze", agent_id="a4", shadow=True,
             decided_at_elapsed=40.0, latency_s=0.3, source_action_elapsed=39.7),
        line(action_id="ok", decision="allow", agent_id="a5",
             decided_at_elapsed=41.0, latency_s=0.3),
    ]) + "\n")
    d = load_decisions(p, "abc123")
    stops = sorted(freeze_schedule(d))
    assert stops == [("a0", 9.5), ("a1", 20.0), ("a2", 25.0)]
    assert ("a3", 30.0) in sorted(freeze_schedule(d, include_steers=True))
    assert all(a != "a4" for a, _ in stops)
    sh = shadow_checks(d)
    assert sh == [{"action_id": "sh", "agent_id": "a4", "decision": "freeze",
                   "label": "violation", "source_action_elapsed": 39.7,
                   "latency_s": 0.3, "shadow": True}]
    assert ENFORCING_STOP == {"deny", "end", "kill"}
