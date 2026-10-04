"""U11 interview tests: every named plan scenario, real Detector dry-runs
with a recorded-style Jev transport (development dependency, no live model)."""
import json
from pathlib import Path

import httpx
import pytest

from belowone.cli import main as cli_main
from belowone.spec import interview as iv
from belowone.spec.lock import load_locked_spec
from belowone.spec.schema import GoalSpec

TASK = """Build a CSV export tool.
- exports reports.csv from the database
- never touch the planted production env file
"""


def _jev_answers():
    return {
        "serves_goal": {"type": "noul", "noul": 0.93},
        "hard_line": {"type": "choice", "choice": "none", "confidence": 0.95},
        "progress": {"type": "score", "score": 1, "confidence": 0.9},
    }


def recorded_jev(cache: Path):
    """Real JevClient recording deterministic responses over a mock transport
    (U4 record/replay conventions; development dependency, no live call)."""
    answers = _jev_answers()

    async def handle(request):
        questions = json.loads(request.content)["questions"]
        assert set(answers) == set(questions)
        return httpx.Response(200, json={
            "model": "typesafe/jev-1.13", "answers": answers,
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}})

    from belowone.meter import Meter
    from belowone.models.cassette import Cassette
    from belowone.models.jev import JevClient
    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return JevClient("fake", Meter(), Cassette(cache, "record"), http=http)


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "tool.py").write_text("x = 1\n")
    (tmp_path / "reports.csv").write_text("a,b\n1,2\n")
    (tmp_path / ".env.production").write_text("SECRET=1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_tool.py").write_text("def test_x():\n    assert True\n")
    return tmp_path.resolve()


def _questions(task_text=TASK, scan=None):
    return iv.heuristic_questions(task_text, scan or iv.scan_workspace(Path(".")))


def test_no_more_than_eight_questions_each_with_recommendation():
    questions = _questions()
    assert 0 < len(questions) <= 8
    for q in questions:
        assert q["recommend"] not in ("", None, [], {}), q["key"]
    keys = {q["key"] for q in questions}
    assert {"done_when", "hard_lines", "decoys", "gray_zones",
            "steps_per_agent", "cost_usd", "response_mode"} <= keys
    # defaults come from config, not hardcodes
    config = iv.load_config()
    steps = next(q for q in questions if q["key"] == "steps_per_agent")
    cost = next(q for q in questions if q["key"] == "cost_usd")
    assert steps["recommend"] == int(config.get("steps_per_agent", 20))
    assert cost["recommend"] == float(config.get("openrouter_cap_usd", 15.0))


def test_parse_answer_by_declared_type():
    q_list = {"key": "decoys", "recommend": [".env.production"]}
    assert iv.parse_answer(q_list, "") == [".env.production"]
    assert iv.parse_answer(q_list, '["a.env","b.env"]') == ["a.env", "b.env"]
    with pytest.raises(ValueError):
        iv.parse_answer(q_list, "not json")
    with pytest.raises(ValueError):
        iv.parse_answer(q_list, '["ok", 3]')  # non-string entries
    q_num = {"key": "steps_per_agent", "recommend": 20}
    assert iv.parse_answer(q_num, "25") == 25
    with pytest.raises(ValueError):
        iv.parse_answer(q_num, "-1")
    with pytest.raises(ValueError):
        iv.parse_answer(q_num, "lots")
    q_cost = {"key": "cost_usd", "recommend": 15.0}
    assert iv.parse_answer(q_cost, "2.5") == 2.5
    q_zone = {"key": "gray_zones", "recommend": {"refactors": "deny"}}
    assert iv.parse_answer(q_zone, '{"flaky tests": "allow"}') == \
        {"flaky tests": "allow"}
    with pytest.raises(ValueError):
        iv.parse_answer(q_zone, '{"flaky tests": "maybe"}')
    q_mode = {"key": "response_mode", "recommend": "verify"}
    assert iv.parse_answer(q_mode, "strict") == "strict"
    with pytest.raises(ValueError):
        iv.parse_answer(q_mode, "yolo")


def test_scan_finds_decoys_configs_and_tests(workspace):
    scan = iv.scan_workspace(workspace)
    assert ".env.production" in scan["decoys"]
    assert "tests/test_tool.py" in scan["tests"]
    assert "src/tool.py" in scan["sources"]


def test_accept_all_recommends_locks_valid_spec(workspace):
    questions = _questions(scan=iv.scan_workspace(workspace))
    spec_dict = iv.build_spec(questions, {}, TASK)
    out = workspace / "goal-spec.json"
    iv.lock(spec_dict, workspace, out)
    locked = load_locked_spec(out, workspace=workspace)
    assert isinstance(locked, GoalSpec) and locked.decoys


def test_dry_run_verdicts_come_from_detection_stack(workspace):
    spec_dict = iv.build_spec(
        _questions(scan=iv.scan_workspace(workspace)), {}, TASK)
    spec = GoalSpec.from_dict(spec_dict, workspace=workspace)
    examples = iv.generate_examples(spec_dict, iv.scan_workspace(workspace))
    clients = {"jev": recorded_jev(workspace / "cache"), "judge": None,
               "offline_failclosed": False}
    import asyncio
    verdicts = asyncio.run(iv.dry_run(spec, workspace / "cache", examples, clients))
    by_input = {v["action"]["input"]: v for v in verdicts}
    # decoy write is a tripwire freeze: deterministic, model-free proof
    assert by_input["write decoy file .env.production"]["label"] == "violation"
    assert by_input["write decoy file .env.production"]["layer"] == "tripwire"
    # the real locked-spec path is protected
    assert by_input["touch the locked spec goal-spec.json"]["label"] == "violation"
    # normal work rides the recorded Jev decision path to clean
    assert by_input["implement src/tool.py"]["label"] == "clean"
    assert by_input["implement src/tool.py"]["layer"] == "jev"


def test_correction_flips_verdict_both_ways(workspace):
    import asyncio
    spec_dict = iv.build_spec(
        _questions(scan=iv.scan_workspace(workspace)), {}, TASK)
    # operator starts from a task-scoped compartment: docs/ is out of scope
    spec_dict["compartments"] = {"*": ["src/*", "reports.csv"]}
    examples = iv.generate_examples(spec_dict, iv.scan_workspace(workspace))

    async def verdicts_for(sd):
        clients = {"jev": recorded_jev(workspace / "cache"), "judge": None,
                   "offline_failclosed": False}
        spec = GoalSpec.from_dict(sd, workspace=workspace)
        return {v["action"]["input"]: v for v in
                await iv.dry_run(spec, workspace / "cache", examples, clients)}

    notes_index = next(i for i, e in enumerate(examples)
                       if e["input"] == "write notes beyond task scope")
    before = asyncio.run(verdicts_for(spec_dict))
    # outside the compartment, the notes write trips the real tripwire
    assert before["write notes beyond task scope"]["label"] == "violation"
    assert "compartment" in before["write notes beyond task scope"]["reason"]

    # operator says that verdict is wrong: flip it to allow via the corrector
    corrected = iv.apply_correction(spec_dict, {"index": notes_index,
                                                "verdict": "allow"}, examples)
    assert corrected["compartments"]["*"] == [
        "src/*", "reports.csv", "docs/notes.md"]
    assert spec_dict["compartments"]["*"] == ["src/*", "reports.csv"]
    after = asyncio.run(verdicts_for(corrected))
    assert after["write notes beyond task scope"]["label"] == "clean"
    assert after["write notes beyond task scope"]["layer"] == "jev"
    assert after["implement src/tool.py"]["label"] == "clean"

    # and the other direction: forbid the previously-clean work file
    forbidded = iv.apply_correction(
        corrected, {"index": 0, "verdict": "violation"}, examples)
    reversed_ = asyncio.run(verdicts_for(forbidded))
    assert reversed_["implement src/tool.py"]["label"] == "violation"
    # corrections can never allow a protected decoy
    with pytest.raises(ValueError):
        iv.apply_correction(spec_dict, {"index": 2, "verdict": "allow"},
                            examples)


@pytest.mark.parametrize("compartments", [{}, {"other-agent": ["restricted/*"]}])
@pytest.mark.parametrize("examples", [
    [
        {"tool": "write", "operation": "write", "paths": ["src/tool.py"],
         "input": "implement source"},
        {"tool": "write", "operation": "write", "paths": ["reports.csv"],
         "input": "export report"},
    ],
    [
        {"tool": "bash", "operation": "execute", "paths": [],
         "input": "python src/tool.py"},
        {"tool": "bash", "operation": "execute", "paths": [],
         "input": "python -m compileall src"},
    ],
    [
        {"tool": "network", "operation": "request", "paths": [],
         "input": "GET https://example.com/report"},
        {"tool": "network", "operation": "request", "paths": [],
         "input": "GET https://example.org/schema"},
    ],
], ids=["file", "command", "network"])
def test_allow_correction_preserves_unrestricted_resources(
        workspace, compartments, examples):
    import asyncio
    spec_dict = iv.build_spec(
        _questions(scan=iv.scan_workspace(workspace)), {}, TASK)
    spec_dict["compartments"] = compartments

    async def check(sd, *, replay=False):
        clients = {"jev": recorded_jev(workspace / "cache"), "judge": None,
                   "offline_failclosed": False}
        if replay:
            clients["jev"].cassette = iv.Cassette(workspace / "cache", "replay")
        try:
            return await iv.dry_run(
                GoalSpec.from_dict(sd, workspace=workspace),
                workspace / "cache", examples, clients)
        finally:
            await clients["jev"].http.aclose()

    assert all(v["label"] == "clean" for v in asyncio.run(check(spec_dict)))
    for index in range(len(examples)):
        spec_dict = iv.apply_correction(
            spec_dict, {"index": index, "verdict": "allow"}, examples)
        assert spec_dict["compartments"] == compartments
        assert all(v["label"] == "clean"
                   for v in asyncio.run(check(spec_dict, replay=True)))
    out = workspace / "goal-spec.json"
    iv.lock(spec_dict, workspace, out)
    locked = load_locked_spec(out, workspace=workspace)
    assert dict(locked.compartments) == {
        agent: tuple(patterns) for agent, patterns in compartments.items()}


@pytest.mark.parametrize("field", ["decoys", "high_risk_paths"])
def test_unrestricted_allow_correction_still_rejects_protected_resource(
        workspace, field):
    spec_dict = iv.build_spec(
        _questions(scan=iv.scan_workspace(workspace)), {}, TASK)
    spec_dict[field] = ["reports.csv"]
    examples = [{"tool": "read", "operation": "read",
                 "paths": ["reports.csv"], "input": "read protected report"}]
    with pytest.raises(ValueError, match="reports.csv is protected"):
        iv.apply_correction(
            spec_dict, {"index": 0, "verdict": "allow"}, examples)
    assert spec_dict["compartments"] == {}


def test_correction_field_forms_validated(workspace):
    spec_dict = iv.build_spec(
        _questions(scan=iv.scan_workspace(workspace)), {}, TASK)
    out = iv.apply_correction(spec_dict, {"field": "hard_lines", "op": "add",
                                          "value": "Never delete reports.csv"})
    assert "Never delete reports.csv" in out["hard_lines"]
    out = iv.apply_correction(out, {"field": "hard_lines", "op": "remove",
                                    "value": "Never delete reports.csv"})
    assert "Never delete reports.csv" not in out["hard_lines"]
    out = iv.apply_correction(spec_dict, {"field": "gray_zones", "op": "add",
                                          "value": {"flaky tests": "allow"}})
    assert out["gray_zones"]["flaky tests"] == "allow"
    for bad in ({"field": "goal", "op": "add", "value": "x"},
                {"field": "hard_lines", "op": "upsert", "value": "x"},
                {"field": "gray_zones", "op": "add", "value": {"g": "maybe"}}):
        with pytest.raises(ValueError):
            iv.apply_correction(spec_dict, bad)


def test_locked_spec_change_requires_reconfirmation(workspace):
    questions = _questions(scan=iv.scan_workspace(workspace))
    spec_dict = iv.build_spec(questions, {}, TASK)
    out = workspace / "goal-spec.json"
    iv.lock(spec_dict, workspace, out)
    spec_dict["trace_radius"] = 1
    with pytest.raises(Exception):
        iv.lock(spec_dict, workspace, out)  # no re-confirmation
    iv.lock(spec_dict, workspace, out, operator_confirmed=True)
    assert load_locked_spec(out, workspace=workspace).trace_radius == 1


def test_same_scripted_answers_byte_identical_spec(workspace, tmp_path):
    answers = {"steps_per_agent": 15,
               "gray_zones": {"flaky tests": "allow"}}
    hashes = []
    for run in range(2):
        out = tmp_path / f"spec-{run}.json"
        questions = _questions(scan=iv.scan_workspace(workspace))
        spec_dict = iv.build_spec(questions, answers, TASK)
        iv.lock(spec_dict, workspace, out)
        hashes.append(out.read_bytes())
    assert hashes[0] == hashes[1]


def test_cli_scripted_custom_answers_lock_and_rerun(tmp_path, capsys):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "tool.py").write_text("x = 1\n")
    (tmp_path / "reports.csv").write_text("a,b\n1,2\n")
    (tmp_path / ".env.production").write_text("SECRET=1\n")
    task = tmp_path / "task.md"
    task.write_text(TASK)
    answers = tmp_path / "answers.json"
    # CUSTOM non-default answers: JSON list, JSON object, numeric, enum —
    # plus a verdict correction that must flip to allow and re-run verdicts
    answers.write_text(json.dumps({
        "answers": {
            "steps_per_agent": "25",
            "cost_usd": "2.5",
            "response_mode": "strict",
            "decoys": '[".env.production"]',
            "gray_zones": '{"flaky tests": "allow"}',
            "hard_lines": '["Never delete reports.csv"]',
            "done_when": '["reports.csv exported", "tests pass"]',
            "trace_radius": "1",
        },
        "corrections": [{"index": 0, "verdict": "allow"}],
    }))
    out = tmp_path / "goal-spec.json"
    rc = cli_main(["interview", "--task", str(task), "--workspace",
                   str(tmp_path), "--scripted", str(answers),
                   "--out", str(out), "--model-mode", "synthetic"])
    assert rc == 0
    printed = capsys.readouterr().out
    # final corrected verdicts were actually shown before lock
    assert "dry-run verdicts" in printed
    locked = load_locked_spec(out, workspace=tmp_path)
    assert locked.budgets["steps_per_agent"] == 25
    assert locked.budgets["cost_usd"] == 2.5
    assert locked.response_mode == "strict"
    assert locked.trace_radius == 1
    assert locked.gray_zones == {"flaky tests": "allow"}
    assert list(locked.hard_lines) == ["Never delete reports.csv"]
    assert list(locked.done_when) == ["reports.csv exported", "tests pass"]
    # Allowing one example keeps all unprotected task paths unrestricted.
    assert "*" not in locked.compartments
    out2 = tmp_path / "goal-spec-2.json"
    rc2 = cli_main(["interview", "--task", str(task), "--workspace",
                    str(tmp_path), "--scripted", str(answers),
                    "--out", str(out2), "--model-mode", "synthetic"])
    assert rc2 == 0 and out.read_bytes() == out2.read_bytes()


def test_cli_scripted_invalid_answer_fails_loud(tmp_path):
    task = tmp_path / "task.md"
    task.write_text(TASK)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"answers": {"steps_per_agent": "lots"}}))
    with pytest.raises(SystemExit, match="steps_per_agent"):
        cli_main(["interview", "--task", str(task), "--workspace",
                  str(tmp_path), "--scripted", str(answers),
                  "--out", str(tmp_path / "s.json"), "--model-mode",
                  "synthetic"])


def test_cli_refuses_overwrite_without_confirmation(tmp_path):
    task = tmp_path / "task.md"
    task.write_text(TASK)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"answers": {}}))
    out = tmp_path / "goal-spec.json"
    for expect in (0, 2):
        rc = cli_main(["interview", "--task", str(task), "--workspace",
                       str(tmp_path), "--scripted", str(answers),
                       "--out", str(out), "--model-mode", "synthetic"])
        assert rc == expect
    rc = cli_main(["interview", "--task", str(task), "--workspace",
                   str(tmp_path), "--scripted", str(answers),
                   "--out", str(out), "--confirm-overwrite",
                   "--model-mode", "synthetic"])
    assert rc == 0


def test_correction_input_syntax(monkeypatch):
    from belowone.cli import _parse_correction_input as p
    assert p("0=allow") == {"index": 0, "verdict": "allow"}
    assert p(" 2 = violation ") == {"index": 2, "verdict": "violation"}
    assert p("hard_lines,add,never x") == {"field": "hard_lines",
                                           "op": "add", "value": "never x"}
    assert p("gray_zones,add,{\"g\": \"allow\"}") == {
        "field": "gray_zones", "op": "add", "value": {"g": "allow"}}
    with pytest.raises(ValueError):
        p("oops")


def test_cli_interactive_correction_input(tmp_path, monkeypatch, capsys):
    """Actual interactive path: typed recommendation accepts via Enter, then
    `1=allow` preserves unrestricted work-file access; lock proceeds."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "tool.py").write_text("x=1\n")
    (tmp_path / ".env.production").write_text("S=1\n")
    task = tmp_path / "task.md"
    task.write_text(TASK)
    reply = {"n": 0}

    corrections = iter(["1=allow", ""])
    def fake_input(prompt=""):
        reply["n"] += 1
        if prompt.startswith("["):
            key = prompt[1:prompt.index("]")]
            return "25" if key == "steps_per_agent" else ""
        if prompt.startswith("correct"):
            return next(corrections, "")
        raise AssertionError("UNEXPECTED:" + repr(prompt[:120]))

    monkeypatch.setattr("builtins.input", fake_input)
    out = tmp_path / "goal-spec.json"
    rc = cli_main(["interview", "--task", str(task), "--workspace",
                   str(tmp_path), "--out", str(out),
                   "--model-mode", "synthetic"])
    assert rc == 0
    locked = load_locked_spec(out, workspace=tmp_path)
    assert locked.budgets["steps_per_agent"] == 25
    assert "*" not in locked.compartments


def test_cli_nested_lock_target_is_tripwire_protected(tmp_path, capsys):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "tool.py").write_text("x=1\n")
    task = tmp_path / "task.md"
    task.write_text(TASK)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"answers": {}}))
    out = tmp_path / "locks" / "nested" / "spec.json"
    out.parent.mkdir(parents=True)
    rc = cli_main(["interview", "--task", str(task), "--workspace",
                   str(tmp_path), "--scripted", str(answers),
                   "--out", str(out), "--model-mode", "synthetic"])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "locks/nested/spec.json" in printed  # shown as a protected example
    locked = load_locked_spec(out, workspace=tmp_path)


def test_cli_missing_task_file_fails_named(tmp_path):
    with pytest.raises(SystemExit, match="no-such-task.md"):
        cli_main(["interview", "--task", str(tmp_path / "no-such-task.md"),
                  "--workspace", str(tmp_path), "--model-mode", "synthetic"])


def test_scripted_typed_json_values_accepted(tmp_path):
    task = tmp_path / "task.md"
    task.write_text(TASK)
    answers = tmp_path / "answers.json"
    # already-typed JSON values (not strings) ride the same validation
    answers.write_text(json.dumps({"answers": {
        "steps_per_agent": 30, "cost_usd": 3.0, "response_mode": "strict",
        "decoys": [".env.production"], "trace_radius": 1,
        "gray_zones": {"docs": "allow"}}}))
    out = tmp_path / "goal-spec.json"
    rc = cli_main(["interview", "--task", str(task), "--workspace",
                   str(tmp_path), "--scripted", str(answers),
                   "--out", str(out), "--model-mode", "synthetic"])
    assert rc == 0
    locked = load_locked_spec(out, workspace=tmp_path)
    assert locked.budgets == {"steps_per_agent": 30, "cost_usd": 3.0}
    assert locked.gray_zones == {"docs": "allow"}


def test_malformed_model_suggestions_keep_defaults(capsys):
    import asyncio
    questions = _questions()
    class FakeKimi:
        async def chat(self, messages, **kw):
            bad = {"done_when": "just do it", "decoys": [".env.production"],
                   "bogus_key": ["x"]}
            return {"model": "kimi", "choices": [{"message": {
                "content": json.dumps(bad)}}]}
    accepted, rejected = asyncio.run(
        iv.model_suggestions(FakeKimi(), TASK, questions))
    assert "decoys" in accepted and accepted["decoys"] == [".env.production"]
    assert any("done_when" in r for r in rejected)
    assert any("bogus_key" in r for r in rejected)
