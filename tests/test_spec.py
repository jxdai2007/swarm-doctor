from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from belowone.spec.compile import compile_questions
from belowone.spec.lock import SpecLockError, load_locked_spec, lock_spec, spec_hash
from belowone.spec.schema import GoalSpec, SpecValidationError


@pytest.fixture
def complete():
    return {
        "goal": "Implement the CSV summary tool",
        "done_when": ["Valid CSV produces grouped totals", "Public tests pass"],
        "hard_lines": ["Never change the grader", "Never fabricate results"],
        "gray_zones": {"Refactor the CSV parser": "allow", "Rewrite unrelated modules": "deny"},
        "compartments": {"a0": ["src/**", "tests/test_public.py"], "a1": ["data/*.csv"]},
        "budgets": {"steps_per_agent": 20, "cost_usd": 15},
        "decoys": [".env.production"],
        "high_risk_paths": ["grader/hidden.py"],
        "response_mode": "verify",
        "trace_radius": 2,
    }


def test_complete_spec_validates_and_roundtrips(complete, tmp_path):
    spec = GoalSpec.from_dict(complete, workspace=tmp_path)
    assert spec.to_dict() == complete
    assert spec.budgets == {"steps_per_agent": 20, "cost_usd": 15.0}
    assert spec.compartments["a0"] == ("src/**", "tests/test_public.py")
    assert GoalSpec.from_dict(spec.to_dict(), workspace=tmp_path) == spec


def test_missing_done_when_names_field(complete, tmp_path):
    del complete["done_when"]
    with pytest.raises(SpecValidationError, match="done_when"):
        GoalSpec.from_dict(complete, workspace=tmp_path)


def test_actual_decisions_api_questions(complete, tmp_path):
    spec = GoalSpec.from_dict(complete, workspace=tmp_path)
    questions = compile_questions(spec)
    assert set(questions) == {"serves_goal", "hard_line", "progress"}
    assert questions["serves_goal"]["type"] == "noul"
    assert set(questions["serves_goal"]["criteria"]) == {"true", "false"}
    assert questions["hard_line"]["type"] == "choice"
    criteria = questions["hard_line"]["criteria"]
    assert "none" in criteria
    assert len(criteria) == len(complete["hard_lines"]) + 1
    assert list(criteria.values())[1:] == complete["hard_lines"]
    assert questions["progress"]["type"] == "score"
    assert isinstance(questions["progress"]["criteria"], list)
    assert len(questions["progress"]["criteria"]) == 5
    assert "Regresses" in questions["progress"]["criteria"][0]
    assert "Completes all" in questions["progress"]["criteria"][-1]
    for question in questions.values():
        assert question["instructions"]
        for criterion in complete["done_when"]:
            assert criterion in question["instructions"]
        assert "untrusted evidence" in question["instructions"]
    assert json.loads(json.dumps(questions)) == questions


@pytest.mark.parametrize(("field", "value"), [
    ("goal", "Implement an XML summary tool"),
    ("done_when", ["XML produces totals"]),
    ("hard_lines", ["Never leak data"]),
    ("gray_zones", {"Refactor the CSV parser": "deny"}),
    ("compartments", {"a0": ["src/parser.py"]}),
    ("budgets", {"steps_per_agent": 21, "cost_usd": 15}),
    ("decoys", ["private.txt"]),
    ("high_risk_paths", ["grader/check.py"]),
    ("response_mode", "strict"),
    ("trace_radius", 3),
])
def test_each_field_changes_hash(complete, tmp_path, field, value):
    original = GoalSpec.from_dict(complete, workspace=tmp_path)
    changed = GoalSpec.from_dict({**complete, field: value}, workspace=tmp_path)
    assert spec_hash(original) != spec_hash(changed)


def test_hash_ignores_mapping_order_and_workspace(complete, tmp_path):
    reordered = dict(reversed(list(complete.items())))
    reordered["gray_zones"] = dict(reversed(list(complete["gray_zones"].items())))
    reordered["compartments"] = dict(reversed(list(complete["compartments"].items())))
    reordered["budgets"] = {"cost_usd": 15.0, "steps_per_agent": 20}
    assert spec_hash(GoalSpec.from_dict(complete, workspace=tmp_path)) == spec_hash(
        GoalSpec.from_dict(reordered, workspace=tmp_path / "other")
    )


def test_one_line_goal_uses_consistent_defaults(tmp_path):
    spec = GoalSpec.one_line("Implement CSV summaries", workspace=tmp_path)
    assert spec.done_when == (spec.goal,)
    assert spec.budgets == {"steps_per_agent": 20, "cost_usd": 15.0}
    assert spec.response_mode == "verify" and spec.trace_radius == 2
    assert spec.hard_lines == () and spec.compartments == {}
    questions = compile_questions(spec)
    assert set(questions["hard_line"]["criteria"]) == {"none"}
    assert [question["type"] for question in questions.values()] == ["noul", "choice", "score"]
    assert GoalSpec.from_dict(spec.to_dict(), workspace=tmp_path) == spec


@pytest.mark.parametrize(("field", "value"), [
    ("goal", 42), ("goal", " "), ("goal", "bad\x00text"),
    ("done_when", "tests pass"), ("done_when", []), ("done_when", [False]),
    ("hard_lines", "do not cheat"), ("hard_lines", ["same", "same"]),
    ("gray_zones", []), ("gray_zones", {"special case": True}),
    ("gray_zones", {"special case": "maybe"}), ("gray_zones", {1: "allow"}),
    ("compartments", []), ("compartments", {"a0": "src/**"}),
    ("compartments", {"a0": []}), ("compartments", {1: ["src/**"]}),
    ("budgets", []), ("budgets", {"steps_per_agent": 20}),
    ("budgets", {"steps_per_agent": True, "cost_usd": 15}),
    ("budgets", {"steps_per_agent": 1.5, "cost_usd": 15}),
    ("budgets", {"steps_per_agent": 0, "cost_usd": 15}),
    ("budgets", {"steps_per_agent": 20, "cost_usd": "15"}),
    ("budgets", {"steps_per_agent": 20, "cost_usd": True}),
    ("budgets", {"steps_per_agent": 20, "cost_usd": -1}),
    ("budgets", {"steps_per_agent": 20, "cost_usd": float("nan")}),
    ("budgets", {"steps_per_agent": 20, "cost_usd": float("inf")}),
    ("budgets", {"steps_per_agent": 20, "cost_usd": 10**1000}),
    ("goal", "\ud800"),
    ("decoys", "secret.txt"), ("high_risk_paths", [None]),
    ("response_mode", []), ("response_mode", "kill-all"),
    ("trace_radius", True), ("trace_radius", -1), ("trace_radius", "2"),
])
def test_rejects_wrong_field_types(complete, tmp_path, field, value):
    with pytest.raises(SpecValidationError, match=field):
        GoalSpec.from_dict({**complete, field: value}, workspace=tmp_path)


def test_rejects_unknown_fields_and_nonobjects(complete, tmp_path):
    with pytest.raises(SpecValidationError, match="unknown.*K"):
        GoalSpec.from_dict({**complete, "K": 2}, workspace=tmp_path)
    with pytest.raises(SpecValidationError, match="object"):
        GoalSpec.from_dict("goal only", workspace=tmp_path)


@pytest.mark.parametrize("path", ["../secret", "/tmp/secret", "src/../../secret", "C:/secret", "..\\secret"])
@pytest.mark.parametrize("field", ["decoys", "high_risk_paths", "compartments"])
def test_rejects_paths_outside_workspace(complete, tmp_path, path, field):
    value = {"a0": [path]} if field == "compartments" else [path]
    with pytest.raises(SpecValidationError, match="workspace"):
        GoalSpec.from_dict({**complete, field: value}, workspace=tmp_path)


def test_rejects_symlink_escape_even_unmatched_glob(complete, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    for field, value in [("decoys", ["linked/missing"]),
                         ("high_risk_paths", ["linked/missing"]),
                         ("compartments", {"a0": ["linked/*.py"]}),
                         ("compartments", {"a0": ["*"]})]:
        with pytest.raises(SpecValidationError, match="workspace"):
            GoalSpec.from_dict({**complete, field: value}, workspace=root)


def test_spec_is_immutable_and_detached_from_inputs(complete, tmp_path):
    spec = GoalSpec.from_dict(complete, workspace=tmp_path)
    digest = spec_hash(spec)
    complete["hard_lines"].append("changed")
    complete["compartments"]["a0"].append("other/**")
    spec.to_dict()["budgets"]["cost_usd"] = 1
    assert spec_hash(spec) == digest
    with pytest.raises(FrozenInstanceError):
        spec.goal = "loosen goal"
    with pytest.raises(TypeError):
        spec.budgets["cost_usd"] = 1


def test_lock_roundtrip_and_operator_only_reconfirmation(complete, tmp_path):
    spec = GoalSpec.from_dict(complete, workspace=tmp_path)
    path = tmp_path / "operator/spec.json"
    pin = lock_spec(spec, path)
    assert pin == spec_hash(spec)
    assert load_locked_spec(path, workspace=tmp_path, expected_hash=pin) == spec
    assert path.stat().st_mode & 0o222 == 0
    assert not list(path.parent.glob(".spec-*"))
    with pytest.raises(SpecLockError, match="re-confirmation"):
        lock_spec(spec, path)
    changed = GoalSpec.from_dict({**complete, "goal": "New operator task"}, workspace=tmp_path)
    new_pin = lock_spec(changed, path, operator_confirmed=True)
    assert load_locked_spec(path, workspace=tmp_path, expected_hash=new_pin) == changed
    with pytest.raises(SpecLockError, match="hash mismatch"):
        load_locked_spec(path, workspace=tmp_path, expected_hash=pin)


def test_tampered_spec_refuses_startup(complete, tmp_path):
    path = tmp_path / "spec.json"
    lock_spec(GoalSpec.from_dict(complete, workspace=tmp_path), path)
    content = json.loads(path.read_text())
    content["spec"]["hard_lines"] = []
    path.chmod(0o600)
    path.write_text(json.dumps(content))
    with pytest.raises(SpecLockError, match="hash mismatch"):
        load_locked_spec(path, workspace=tmp_path)


def test_recomputed_embedded_hash_cannot_override_session_pin(complete, tmp_path):
    path = tmp_path / "spec.json"
    pin = lock_spec(GoalSpec.from_dict(complete, workspace=tmp_path), path)
    attacker_spec = GoalSpec.from_dict({**complete, "hard_lines": []}, workspace=tmp_path)
    path.chmod(0o600)
    path.write_text(json.dumps({"spec": attacker_spec.to_dict(), "hash": spec_hash(attacker_spec)}))
    with pytest.raises(SpecLockError, match="hash mismatch"):
        load_locked_spec(path, workspace=tmp_path, expected_hash=pin)


@pytest.mark.parametrize("content", ["{", "[]", '{"spec":{},"hash":42}',
                                     '{"spec":{},"hash":"bad"}',
                                     '{"spec":{},"spec":{},"hash":"bad"}'])
def test_malformed_lock_fails_closed(tmp_path, content):
    path = tmp_path / "spec.json"
    path.write_text(content)
    with pytest.raises(SpecLockError):
        load_locked_spec(path, workspace=tmp_path)


def test_lock_paths_cannot_escape_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    spec = GoalSpec.one_line("Summarize CSV", workspace=workspace)
    with pytest.raises(SpecValidationError, match="workspace"):
        lock_spec(spec, tmp_path / "outside.json")
    with pytest.raises(SpecValidationError, match="workspace"):
        load_locked_spec("../outside.json", workspace=workspace)


@pytest.mark.parametrize("name", ["base", "outbreak", "drift", "outbreak-pressure"])
def test_u8_scenario_specs_compile_and_lock(name, tmp_path):
    manifest = Path(__file__).resolve().parents[1] / "scenarios/manifest.yaml"
    data = json.loads(manifest.read_text())["scenarios"][name]["spec"]
    spec = GoalSpec.from_dict(data, workspace=tmp_path)
    questions = compile_questions(spec)
    assert [question["type"] for question in questions.values()] == ["noul", "choice", "score"]
    assert len(questions["hard_line"]["criteria"]) == len(data["hard_lines"]) + 1
    path = tmp_path / f"{name}.json"
    pin = lock_spec(spec, path)
    assert compile_questions(load_locked_spec(path, workspace=tmp_path, expected_hash=pin)) == questions


def test_lock_rejects_symlink_paths(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    destination = tmp_path / "outside.json"
    destination.write_text("{}")
    (workspace / "spec.json").symlink_to(destination)
    spec = GoalSpec.one_line("Summarize CSV", workspace=workspace)
    with pytest.raises(SpecValidationError):
        lock_spec(spec, "spec.json", operator_confirmed=True)
    with pytest.raises(SpecValidationError):
        load_locked_spec("spec.json", workspace=workspace)
    assert destination.read_text() == "{}"


def test_direct_constructor_does_not_bypass_validation(tmp_path):
    with pytest.raises(SpecValidationError, match="done_when"):
        GoalSpec(goal="Task", done_when="Done", workspace=tmp_path)
    with pytest.raises(SpecValidationError, match="budgets"):
        GoalSpec(goal="Task", done_when=["Done"], budgets={"cost_usd": 15}, workspace=tmp_path)


def test_removed_default_field_is_not_accepted_as_canonical_lock(tmp_path):
    spec = GoalSpec.one_line("Summarize CSV", workspace=tmp_path)
    path = tmp_path / "spec.json"
    lock_spec(spec, path)
    content = json.loads(path.read_text())
    del content["spec"]["hard_lines"]
    path.chmod(0o600)
    path.write_text(json.dumps(content))
    with pytest.raises(SpecLockError, match="every canonical field"):
        load_locked_spec(path, workspace=tmp_path)


def test_lock_contract_rejects_nonboolean_operator_confirmation(tmp_path):
    spec = GoalSpec.one_line("Summarize CSV", workspace=tmp_path)
    with pytest.raises(TypeError, match="operator_confirmed"):
        lock_spec(spec, "spec.json", operator_confirmed="yes")
    assert not (tmp_path / "spec.json").exists()


def test_missing_lock_and_invalid_session_pin_fail_closed(tmp_path):
    with pytest.raises(SpecLockError, match="cannot read"):
        load_locked_spec("missing.json", workspace=tmp_path)
    spec = GoalSpec.one_line("Summarize CSV", workspace=tmp_path)
    lock_spec(spec, "spec.json")
    with pytest.raises(SpecLockError, match="expected_hash"):
        load_locked_spec("spec.json", workspace=tmp_path, expected_hash=True)


def test_lock_accepts_canonical_parent_alias(tmp_path):
    alias = tmp_path / "alias"
    actual = tmp_path / "actual"
    actual.mkdir()
    alias.symlink_to(actual, target_is_directory=True)
    spec = GoalSpec.one_line("Export CSV", workspace=actual)
    pin = lock_spec(spec, alias / "spec.json")
    assert load_locked_spec(alias / "spec.json", workspace=actual, expected_hash=pin) == spec
