"""U16 strict offline reproduction against actual sealed synthetic recordings."""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
from types import SimpleNamespace

import pytest

from belowone import experiments
from belowone.eval.ablation import ablate
from belowone.eval.arms import ARMS
from belowone.eval.delay import DAILY_REVIEW_SECONDS
from belowone.experiments import (
    QuotaExhausted, diff_named_key, diff_outputs, load_plan,
    require_preregistration, run_schedule, scan_secrets,
)
from belowone.meter import BudgetExceeded, Meter
from belowone.runstore import RunStore

ROOT = Path(__file__).resolve().parent.parent
CANONICAL_RUNS = ("pilot-0", "pilot-1", "pilot-2", "drift-demo")


@pytest.fixture
def sealed_runs(tmp_path):
    """Copy actual provenance and caches; never consume display-only duplicates."""
    source = ROOT / "experiments" / "committed" / "runs"
    destination = tmp_path / "runs"
    destination.mkdir()
    (destination / ".seals").mkdir()
    for name in CANONICAL_RUNS:
        assert (source / name).is_dir(), f"Missing canonical recording: {name}"
        seal = source / ".seals" / f"{name}.sha256"
        assert seal.is_file(), f"Missing canonical seal: {seal}"
        shutil.copytree(source / name, destination / name)
        shutil.copy2(seal, destination / ".seals" / seal.name)
        assert list((destination / name / "cache").glob("*.json")), name
    assert not destination.is_relative_to(ROOT)
    return destination


@pytest.fixture
def regenerated(sealed_runs, tmp_path):
    derived = tmp_path / "derived"
    analysis = experiments.regenerate(sealed_runs, derived)
    # Authored docs link sibling repo artifacts (clips, plans, generated
    # metrics); mirror the repo layout so link checks bind the fixture bytes.
    (derived / "authored" / "clips").symlink_to(ROOT / "clips")
    (derived / "authored" / "docs" / "plans").symlink_to(ROOT / "docs" / "plans")
    (derived / "authored" / "docs" / "generated").symlink_to(derived / "docs")
    (derived / "authored" / "generated").symlink_to(derived / "docs")
    return sealed_runs, derived, analysis


@pytest.fixture
def replay_revision_data(sealed_runs):
    analysis, inputs = experiments._analyze(sealed_runs)
    return sealed_runs, analysis, inputs


@pytest.fixture
def replay_revision_evaluator_root(tmp_path, monkeypatch):
    root = tmp_path / "evaluator"
    for name in experiments.REPLAY_EVALUATOR_SOURCES:
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    admission = root / "experiments/replay-revisions.json"
    admission.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(experiments.REPLAY_REVISIONS_PATH, admission)
    monkeypatch.setattr(experiments, "ROOT", root)
    monkeypatch.setattr(experiments, "REPLAY_REVISIONS_PATH", admission)
    return root


def _replay_revision_submission(derived, destination):
    """Use regenerated authored bytes and real linked repository artifacts."""
    shutil.copytree(derived / "authored", destination, symlinks=True)
    if not (destination / "docs/generated").exists():
        shutil.copytree(derived / "docs", destination / "docs/generated")
    for document in destination.rglob("*.md"):
        for target in re.findall(r"\]\(([^)]+)\)", document.read_text()):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            clean = target.split("#", 1)[0]
            if not clean:
                continue
            linked = (document.parent / clean).resolve()
            if linked.exists():
                continue
            source = ROOT / linked.relative_to(destination)
            linked.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, linked)
            else:
                shutil.copy2(source, linked)
    return destination


def _reseal_replay_revision_copy(run):
    assert not run.is_relative_to(ROOT)
    (run / "manifest.json").unlink()
    (run.parent / ".seals" / f"{run.name}.sha256").unlink()
    RunStore(run.parent).seal(run)


def test_replay_revision_admitted_regeneration_and_readonly_reproduction(sealed_runs, tmp_path):
    admission_before = experiments.REPLAY_REVISIONS_PATH.read_bytes()
    original = {str(path): path.read_bytes() for run in sealed_runs.iterdir() if run.is_dir() and run.name != ".seals"
                for path in (run / "metrics/all-arms.json", run / "manifest.json",
                             sealed_runs / ".seals" / f"{run.name}.sha256")}
    derived = tmp_path / "derived"
    analysis = experiments.regenerate(sealed_runs, derived)
    assert set(analysis["replay_revisions"]) == {"pilot-0", "pilot-1", "pilot-2"}
    for name, revision in analysis["replay_revisions"].items():
        entry = revision["entry"]
        assert revision["semantics_version"] == "started-effects-v2"
        assert entry["old_vector"]["infected"] == 1 and entry["old_vector"]["r_mean"] == 0
        assert entry["new_vector"]["infected"] == 2 and entry["new_vector"]["r_mean"] == .5
        assert analysis["runs"][name]["arms"]["message-only"] == entry["new_vector"]
        assert analysis["runs"][name]["metrics"] == json.loads((sealed_runs / name / "metrics.json").read_text())
        assert entry["timing_evidence"] and all(
            evidence["started_at_elapsed"] <= evidence["freeze_elapsed"] < evidence["settled_at_elapsed"]
            for evidence in entry["timing_evidence"])
    submission = _replay_revision_submission(derived, tmp_path / "submission")
    assert experiments.reproduce(sealed_runs, derived, submission_root=submission) == []
    assert experiments.REPLAY_REVISIONS_PATH.read_bytes() == admission_before
    assert all(Path(path).read_bytes() == content for path, content in original.items())
    changed = json.loads((derived / "runs/pilot-0/all-arms.json").read_text())
    changed["message-only"]["infected"] += 1
    (derived / "runs/pilot-0/all-arms.json").write_text(json.dumps(changed))
    diffs = experiments.reproduce(sealed_runs, derived, submission_root=submission)
    assert any("runs/pilot-0/all-arms.json" in diff and "infected" in diff for diff in diffs)


@pytest.mark.parametrize("damage", [
    "missing", "unknown_run", "unknown_arm", "old_artifact", "source_hash", "manifest_hash", "seal_hash",
    "old_vector", "old_vector_type", "new_vector", "differing_keys", "extra_key", "timing", "reason",
    "schema", "semantics", "revision", "evaluator", "evaluator_missing",
])
def test_replay_revision_admission_boundaries(replay_revision_data, replay_revision_evaluator_root, damage):
    runs, analysis, inputs = replay_revision_data
    manifest = json.loads(experiments.REPLAY_REVISIONS_PATH.read_text())
    entry = next(row for row in manifest["entries"] if row["run"] == "pilot-0")
    if damage == "unknown_run":
        entry["run"] = "unreviewed-run"
    elif damage == "unknown_arm":
        entry["arm"] = "verify"
    elif damage == "old_artifact":
        entry["old_artifact_sha256"] = "0" * 64
    elif damage == "source_hash":
        entry["source_sha256"]["events.jsonl"] = "0" * 64
    elif damage == "manifest_hash":
        entry["manifest_sha256"] = "0" * 64
    elif damage == "seal_hash":
        entry["seal_sha256"] = "0" * 64
    elif damage == "old_vector":
        entry["old_vector"]["infected"] += 1
    elif damage == "old_vector_type":
        entry["old_vector"]["infected"] = True
    elif damage == "new_vector":
        entry["new_vector"]["infected"] += 1
    elif damage == "differing_keys":
        entry["differing_keys"].remove("infected")
    elif damage == "extra_key":
        entry["differing_keys"].append("unreviewed")
    elif damage == "timing":
        entry["timing_evidence"][0]["freeze_elapsed"] += .001
    elif damage == "reason":
        entry["reason"] = "unreviewed explanation"
    elif damage == "schema":
        manifest["schema_version"] = 2
    elif damage == "semantics":
        manifest["semantics_version"] = "receipt-completion"
    elif damage == "revision":
        manifest["revision"] = 2
    elif damage == "evaluator":
        manifest["evaluator_sha256"]["belowone/eval/replay.py"] = "0" * 64
    elif damage == "evaluator_missing":
        del manifest["evaluator_sha256"]["belowone/experiments.py"]
    admission = experiments.REPLAY_REVISIONS_PATH
    if damage == "missing":
        admission.unlink()
    else:
        admission.write_text(json.dumps(manifest))
    with pytest.raises(experiments.MetricMismatch, match="pilot-0/metrics/all-arms.json/message-only/.*infected"):
        experiments._source_diffs(runs, analysis, inputs=inputs)


def test_replay_revision_runtime_evaluator_source_change(replay_revision_data, replay_revision_evaluator_root):
    runs, analysis, inputs = replay_revision_data
    evaluator_root = replay_revision_evaluator_root
    replay = evaluator_root / "belowone/eval/replay.py"
    replay.write_bytes(replay.read_bytes() + b"\n# changed evaluator source\n")
    with pytest.raises(experiments.MetricMismatch, match="evaluator identity mismatch"):
        experiments._source_diffs(runs, analysis, inputs=inputs)


def test_replay_revision_copied_scoped_admission_positive_control(replay_revision_data, replay_revision_evaluator_root):
    runs, analysis, inputs = replay_revision_data
    assert experiments._source_diffs(runs, analysis, inputs=inputs) == []
    assert set(analysis["replay_revisions"]) == {"pilot-0", "pilot-1", "pilot-2"}


@pytest.mark.parametrize("target", ["message-only", "verify", "unlisted-arm"])
def test_replay_revision_unlisted_current_metric_or_arm(replay_revision_data, target):
    runs, analysis, inputs = replay_revision_data
    arms = analysis["runs"]["pilot-0"]["arms"]
    if target == "unlisted-arm":
        arms[target] = {"infected": 900}
    else:
        arms[target]["infected"] += 1
    with pytest.raises(experiments.MetricMismatch, match=f"pilot-0/metrics/all-arms.json/.*{target}"):
        experiments._source_diffs(runs, analysis, inputs=inputs)


@pytest.mark.parametrize("target", ["event", "seal"])
def test_replay_revision_input_tamper_fails_before_admission(sealed_runs, tmp_path, target):
    path = (sealed_runs / "pilot-0/events.jsonl" if target == "event"
            else sealed_runs / ".seals/pilot-0.sha256")
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Seal verification failed for pilot-0"):
        experiments.regenerate(sealed_runs, tmp_path / "derived")
    assert not (tmp_path / "derived").exists()


@pytest.mark.parametrize("target", ["old_artifact", "source", "actual_metrics", "other_arm"])
def test_replay_revision_resealed_copy_does_not_authorize_changes(sealed_runs, tmp_path, target):
    run = sealed_runs / "pilot-0"
    if target == "source":
        path = run / "events.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        path.write_text("".join(json.dumps(event, sort_keys=True) + "\n" for event in events))
    else:
        path = run / ("metrics.json" if target == "actual_metrics" else "metrics/all-arms.json")
        data = json.loads(path.read_text())
        vector = data if target == "actual_metrics" else data["verify" if target == "other_arm" else "message-only"]
        vector["infected"] += 1
        path.write_text(json.dumps(data))
    _reseal_replay_revision_copy(run)
    match = ("pilot-0/metrics.json/infected" if target == "actual_metrics" else
             "pilot-0/metrics/all-arms.json/.*verify" if target == "other_arm" else
             "pilot-0/metrics/all-arms.json/message-only")
    with pytest.raises(experiments.MetricMismatch, match=match):
        experiments.regenerate(sealed_runs, tmp_path / "derived")
    assert not (tmp_path / "derived").exists()


def test_replay_revision_unknown_recording_fails(sealed_runs, tmp_path):
    (sealed_runs / "pilot-0").rename(sealed_runs / "unreviewed-run")
    (sealed_runs / ".seals/pilot-0.sha256").rename(sealed_runs / ".seals/unreviewed-run.sha256")
    run = sealed_runs / "unreviewed-run"
    checks = json.loads((run / "checks.json").read_text())
    (run / "checks.json").write_text(json.dumps(
        {key.replace("pilot-0:", "unreviewed-run:", 1): value for key, value in checks.items()}))
    _reseal_replay_revision_copy(run)
    with pytest.raises(experiments.MetricMismatch, match="unreviewed-run/metrics/all-arms.json/message-only"):
        experiments.regenerate(sealed_runs, tmp_path / "derived")


@pytest.mark.parametrize("value", [False, 0.0])
@pytest.mark.parametrize("artifact", ["metrics.json", "metrics/all-arms.json"])
def test_replay_revision_nonadmitted_recording_rejects_equal_python_metric_types(sealed_runs, tmp_path, artifact, value):
    run = sealed_runs / "drift-demo"
    path = run / artifact
    data = json.loads(path.read_text())
    vector = data if artifact == "metrics.json" else data["verify"]
    assert type(vector["infected"]) is int and vector["infected"] == 0
    vector["infected"] = value
    path.write_text(json.dumps(data))
    _reseal_replay_revision_copy(run)
    match = "drift-demo/metrics.json/infected" if artifact == "metrics.json" else "drift-demo/metrics/all-arms.json/verify"
    with pytest.raises(experiments.MetricMismatch, match=match):
        experiments.regenerate(sealed_runs, tmp_path / "derived")
    assert not (tmp_path / "derived").exists()


def _guard_replay_revision_private_reads(monkeypatch, private):
    reads = []
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path.resolve() == private.resolve():
            reads.append(path)
            raise AssertionError("Private sentinel must not be read")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    return reads


@pytest.mark.parametrize("metadata", [
    "source_traversal", "source_absolute", "evaluator_traversal", "evaluator_absolute", "evaluator_unknown",
])
def test_replay_revision_metadata_paths_have_no_read_authority(replay_revision_data, replay_revision_evaluator_root, tmp_path, monkeypatch, metadata):
    runs, analysis, inputs = replay_revision_data
    private = tmp_path / "private-sentinel"
    private.write_text("PRIVATE_SENTINEL_NOT_A_CREDENTIAL")
    private.chmod(0o600)
    manifest = json.loads(experiments.REPLAY_REVISIONS_PATH.read_text())
    entry = next(item for item in manifest["entries"] if item["run"] == "pilot-0")
    base = runs / "pilot-0" if metadata.startswith("source") else experiments.ROOT
    declared = str(private) if metadata.endswith("absolute") else os.path.relpath(private, base)
    if metadata == "evaluator_unknown":
        declared = "belowone/not-a-reviewed-evaluator.py"
    mapping = entry["source_sha256"] if metadata.startswith("source") else manifest["evaluator_sha256"]
    mapping[declared] = "0" * 64
    experiments.REPLAY_REVISIONS_PATH.write_text(json.dumps(manifest))
    reads = _guard_replay_revision_private_reads(monkeypatch, private)
    with pytest.raises(experiments.MetricMismatch, match="pilot-0/metrics/all-arms.json/message-only"):
        experiments._source_diffs(runs, analysis, inputs=inputs)
    assert reads == []


def test_replay_revision_evaluator_symlink_escape_refused_before_read(replay_revision_data, replay_revision_evaluator_root, tmp_path, monkeypatch):
    runs, analysis, inputs = replay_revision_data
    evaluator_root = replay_revision_evaluator_root
    private = tmp_path / "private-sentinel"
    private.write_text("PRIVATE_SENTINEL_NOT_A_CREDENTIAL")
    private.chmod(0o600)
    replay = evaluator_root / "belowone/eval/replay.py"
    replay.unlink()
    replay.symlink_to(private)
    reads = _guard_replay_revision_private_reads(monkeypatch, private)
    with pytest.raises(experiments.MetricMismatch, match="evaluator source escapes repository: belowone/eval/replay.py"):
        experiments._source_diffs(runs, analysis, inputs=inputs)
    assert reads == []


def test_replay_revision_sealed_input_symlink_refused_before_read(sealed_runs, tmp_path, monkeypatch):
    private = tmp_path / "private-sentinel"
    private.write_text("PRIVATE_SENTINEL_NOT_A_CREDENTIAL")
    private.chmod(0o600)
    events = sealed_runs / "pilot-0/events.jsonl"
    events.unlink()
    events.symlink_to(private)
    reads = _guard_replay_revision_private_reads(monkeypatch, private)
    with pytest.raises(ValueError, match="Seal verification failed for pilot-0"):
        experiments.regenerate(sealed_runs, tmp_path / "derived")
    assert reads == []
    assert not (tmp_path / "derived").exists()


@pytest.mark.parametrize("link", ["file", "directory"])
def test_replay_revision_admission_symlink_escape_refused_before_read(
        replay_revision_data, replay_revision_evaluator_root, tmp_path, monkeypatch, link):
    runs, analysis, inputs = replay_revision_data
    outside = tmp_path / "private-admissions"
    outside.mkdir()
    private = outside / "replay-revisions.json"
    private.write_text("PRIVATE_SENTINEL_NOT_A_CREDENTIAL")
    private.chmod(0o600)
    if link == "file":
        experiments.REPLAY_REVISIONS_PATH.unlink()
        experiments.REPLAY_REVISIONS_PATH.symlink_to(private)
    else:
        shutil.rmtree(replay_revision_evaluator_root / "experiments")
        (replay_revision_evaluator_root / "experiments").symlink_to(outside, target_is_directory=True)
    reads = _guard_replay_revision_private_reads(monkeypatch, private)
    with pytest.raises(experiments.MetricMismatch, match="admission path must be the fixed nonsymlink repository-local"):
        experiments._source_diffs(runs, analysis, inputs=inputs)
    assert reads == []


def test_plan_orders_never_cut_first_then_reverse_cut_list():
    plan = load_plan()
    ids = [e["id"] for e in plan["priority"]]
    never_cut = {"split-screen-race", "prompt-vs-below-one",
                 "delay-vs-damage", "wasted-spend-card", "replay-results"}
    assert set(ids[:5]) == never_cut
    cut = [e for e in plan["priority"] if "cut_order" in e]
    orders = [e["cut_order"] for e in cut]
    assert orders == sorted(orders, reverse=True)  # last-cut runs first


def test_non_pilot_preregistration_requires_head_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(experiments, "ROOT", tmp_path)
    prereg = tmp_path / "preregistration.md"
    prereg.write_bytes(b"Frozen protocol\n")
    plan = {"preregistration": prereg.name, "pilot_ids": ["pilot"]}
    calls = []

    def git_show(command, **kwargs):
        calls.append(command)
        assert command[:2] == ["git", "show"]
        assert command[-1] == "HEAD:preregistration.md"
        return subprocess.CompletedProcess(command, 0, b"Frozen protocol\n", b"")

    monkeypatch.setattr(experiments.subprocess, "run", git_show)
    require_preregistration("pilot", plan)
    assert calls == []
    require_preregistration("study", plan)
    assert len(calls) == 1
    prereg.write_bytes(b"Edited protocol\n")
    with pytest.raises(RuntimeError, match="HEAD|committed"):
        require_preregistration("study", plan)
    prereg.unlink()
    with pytest.raises(RuntimeError, match="HEAD|committed"):
        require_preregistration("study", plan)


def test_staged_only_preregistration_missing_from_head_rejects(tmp_path, monkeypatch):
    monkeypatch.setattr(experiments, "ROOT", tmp_path)
    (tmp_path / "preregistration.md").write_bytes(b"Staged but not committed\n")

    def git_show(command, **kwargs):
        assert command[:2] == ["git", "show"]
        assert command[-1] == "HEAD:preregistration.md"
        return subprocess.CompletedProcess(command, 128, b"", b"path absent in HEAD")

    monkeypatch.setattr(experiments.subprocess, "run", git_show)
    with pytest.raises(RuntimeError, match="HEAD|committed"):
        require_preregistration("study", {
            "preregistration": "preregistration.md", "pilot_ids": []})


def test_quota_exhaustion_finishes_current_seed_before_stopping():
    arms = ["no-defense", "prompt-only", "verify"]
    plan = {"priority": [{"id": "study", "arms": arms}]}
    calls = []

    def start(seed, arm):
        calls.append((seed, arm))

    with pytest.raises(QuotaExhausted) as exc:
        run_schedule(plan, "study", [0, 1], start, lambda: bool(calls))
    payload = json.loads(str(exc.value))
    assert calls == [(0, arm) for arm in arms]
    assert payload["stopped_after_seed"] == 0
    assert payload["reached"] == {"0": len(arms)}
    assert payload["pending_arms"] == arms
    assert payload["status"] == "stopped"
    assert payload["pending_seed"] == 1


def test_hard_budget_abort_reports_only_successful_arms():
    arms = ["no-defense", "prompt-only", "verify"]
    plan = {"priority": [{"id": "study", "arms": arms}]}
    meter = Meter(cap_usd="1")
    attempted, completed = [], []

    def start(seed, arm):
        attempted.append((seed, arm))
        hold = meter.reserve("openrouter", "1")
        meter.record("openrouter", "recorded-model", 1, 1, "1", 0,
                     reservation=hold)
        completed.append((seed, arm))

    with pytest.raises(QuotaExhausted) as exc:
        run_schedule(plan, "study", [0, 1], start, lambda: False)
    assert isinstance(exc.value.__cause__, BudgetExceeded)
    payload = json.loads(str(exc.value))
    assert attempted == [(0, arms[0]), (0, arms[1])]
    assert completed == [(0, arms[0])]
    assert payload["reached"] == {"0": 1}
    assert payload["pending_arms"] == arms[1:]
    assert payload["status"] == "incomplete"
    assert payload["stopped_after_seed"] == 0
    assert payload["pending_seed"] == 0


def test_regeneration_diff_names_differing_files_and_keys(tmp_path):
    committed = tmp_path / "committed"
    rebuilt = tmp_path / "rebuilt"
    (committed / "figs").mkdir(parents=True)
    (rebuilt / "figs").mkdir(parents=True)
    (committed / "figs" / "same.json").write_text('{"a": 1}')
    (rebuilt / "figs" / "same.json").write_text('{"a": 1}')
    (committed / "metrics.json").write_text('{"infected": 1, "r_mean": 0.5}')
    (rebuilt / "metrics.json").write_text('{"infected": 2, "r_mean": 0.5}')
    (rebuilt / "extra.txt").write_text("x")
    assert diff_outputs(committed, rebuilt) == ["extra.txt", "metrics.json"]
    assert diff_named_key(committed / "metrics.json",
                          rebuilt / "metrics.json") == \
        "infected: committed 1 != rebuilt 2"


@pytest.mark.parametrize("key", [
    "sk-" + "a" * 24,
    "AKIA" + "A" * 16,
    "ghp_" + "a" * 24,
    "KIMI_API_KEY=" + "a" * 24,
    "OPENROUTER_API_KEY=" + "a" * 24,
])
def test_secret_scan_flags_shaped_keys_even_in_example_files(tmp_path, key):
    path = tmp_path / ".env.example"
    path.write_text(key)
    hits = scan_secrets([path])
    assert hits and all(key not in hit for hit in hits)


def test_secret_scan_accepts_placeholders_without_callsite_filtering(tmp_path):
    example = tmp_path / ".env.example"
    example.write_text(
        "KIMI_API_KEY=<your-key-here>\n"
        "OPENROUTER_API_KEY=changeme\n")
    clean = tmp_path / "metrics.json"
    clean.write_text('{"infected": 1}')
    assert scan_secrets([clean, example]) == []


@pytest.mark.parametrize("content", [
    "OPENROUTER_API_KEY=\nKIMI_API_KEY=\n",
    'TOKEN = ""\nSECRET = \'\'\n',
    'KIMI_API_KEY="<your-key-here>"\nOPENROUTER_API_KEY=\'changeme\'\n',
    "TOKEN = re.compile(r'not-a-credential')\n",
    "TOKEN = os.environ['TOKEN']\n",
    'TOKEN = "prefix" + value\n',
    'TOKEN = r"pattern"\nSECRET = f"{value}"\n',
    '(work / ".env.production").write_text("SECRET=1\\n")\n',
    "TOKEN=FAKE_SCENARIO_DECOY_NOT_A_CREDENTIAL\n",
    'TOKEN="FAKE_SCENARIO_DECOY_NOT_A_CREDENTIAL"\n',
    "OPENROUTER_API_KEY=\t\r\nKIMI_API_KEY=\r\n",
])
def test_secret_scan_rejects_expressions_and_normalizes_clean_literals(tmp_path, content):
    path = tmp_path / ".env.example"
    path.write_text(content)
    assert scan_secrets([path]) == []


@pytest.mark.parametrize("content", [
    'TOKEN = "actual-private-credential"\n',
    "SECRET='actual-private-credential'\n",
    'KIMI_API_KEY="actual-private-credential"\n',
    "TOKEN=x\n",
    'TOKEN = ' + '"actual-private-credential"; # literal code assignment\n',
    "TOKEN=FAKE_SCENARIO_DECOY_NOT_A_CREDENTIAL_SUFFIX\n",
    "TOKEN=FAKE_SCENARIO_DECOY_NOT_A_CREDENTIAl\n",
    "TOKEN=FAKE_OTHER_CREDENTIAL\n",
])
@pytest.mark.parametrize("filename", [".env.example", "credentials.py"])
def test_secret_scan_flags_real_literals_without_length_or_filename_exemptions(tmp_path, content, filename):
    path = tmp_path / filename
    path.write_text(content)
    assert scan_secrets([path])


@pytest.mark.parametrize("credential", [
    "sk-" + "a" * 24,
    "AKIA" + "A" * 16,
    *("gh" + kind + "_" + "a" * 24 for kind in "pousr"),
    *("xox" + kind + "-" + "a" * 16 for kind in "baprs"),
    "glpat-" + "a" * 24,
    "Bearer " + "a" * 24,
])
@pytest.mark.parametrize("prefix", ["your_", "changeme", "FAKE_SCENARIO_DECOY_NOT_A_CREDENTIAL"])
def test_secret_scan_provider_patterns_override_placeholder_prefixes(tmp_path, credential, prefix):
    path = tmp_path / ".env.example"
    path.write_text('TOKEN="' + prefix + credential + '"\n')
    assert scan_secrets([path])


def test_doc_checker_fails_on_edited_number_and_passes_clean(regenerated):
    from scripts.check_doc_numbers import main as check_main

    _, derived, _ = regenerated
    path = derived / "analysis.json"
    args = ["--metrics-glob", str(path), "--docs", str(derived / "docs"),
            "--root", str(derived / "authored")]
    assert check_main(args) == 0
    edited = json.loads(path.read_text())
    edited["doc_metrics"]["no-defense"]["metrics.infected"] += 1
    path.write_text(json.dumps(edited))
    assert check_main(args) == 1


def test_actual_recordings_regenerate_all_artifacts_and_reproduce(regenerated, capsys, tmp_path):
    runs, derived, analysis = regenerated
    submission = _replay_revision_submission(derived, tmp_path / "submission")
    assert set(analysis["runs"]) == set(CANONICAL_RUNS)
    assert json.loads((derived / "analysis.json").read_text()) == analysis
    assert analysis["monitor"] == {
        "status": "blocked", "reason": "Human labels absent"}
    for name, run in analysis["runs"].items():
        source_metrics = json.loads((runs / name / "metrics.json").read_text())
        assert run["metrics"] == source_metrics
        assert run["config"]["synthetic"] is True
        assert run["config"]["source"] == "synthetic-development"
        assert set(run["arms"]) == set(ARMS)
        assert run["cache_count"] > 0
        assert set(run["ablation"]["arms"]) == {"interviewed", "one_line"}
        tokens = analysis["doc_metrics"][name]
        for key, value in source_metrics.items():
            if not isinstance(value, (dict, list)):
                assert tokens[f"metrics.{key}"] == value
        interviewed = [
            json.loads(line) for line in
            (runs / name / "decisions.jsonl").read_text().splitlines() if line]
        one_line = [
            json.loads(line) for line in
            (runs / name / "decisions-one-line.jsonl").read_text().splitlines() if line]
        assert interviewed and one_line
        assert {row["spec_hash"] for row in interviewed}.isdisjoint(
            {row["spec_hash"] for row in one_line})
        events = [
            SimpleNamespace(**json.loads(line)) for line in
            (runs / name / "events.jsonl").read_text().splitlines() if line]
        expected_ablation = ablate(
            events,
            {"interviewed": runs / name / "decisions.jsonl",
             "one_line": runs / name / "decisions-one-line.jsonl"},
            {"interviewed": interviewed[0]["spec_hash"],
             "one_line": one_line[0]["spec_hash"]},
            total_agents=run["config"]["agent_count"],
        )
        assert run["ablation"]["arms"] == expected_ablation["arms"]
        for filename, key in (
            ("metrics.json", "metrics"), ("all-arms.json", "arms"),
            ("delay.json", "delay"), ("ablation.json", "ablation"),
        ):
            assert json.loads((derived / "runs" / name / filename).read_text()) == run[key]
    for artifact_type in ("figures", "cards", "receipts", "docs"):
        assert any(path.is_file() for path in (derived / artifact_type).rglob("*")), artifact_type
    for stem in ("epidemic", "r-bar", "delay-damage"):
        assert (derived / "figures" / f"{stem}.svg").is_file()
        data = json.loads((derived / "figures" / f"{stem}.json").read_text())
        assert data["synthetic"] is True
        assert data["source"] == "synthetic-development"
    assert experiments.reproduce(runs, derived, submission_root=submission) == []
    assert "SKIP" not in capsys.readouterr().out


def test_aggregation_uses_three_unique_outbreak_seeds_and_no_single_seed_ci(regenerated):
    _, _, analysis = regenerated
    assert len(analysis["groups"]) == 2
    groups = {group["scenario"]: group for group in analysis["groups"].values()}
    assert set(groups) == {"outbreak", "drift"}
    for scenario, group in groups.items():
        assert group["served_models"] == ["synthetic-dev-kimi"]
        assert group["synthetic"] is True
        assert group["source"] == "synthetic-development"
        assert set(group["arms"]) == set(group["curves"]) == set(ARMS)
        expected_seeds = [0, 1, 2] if scenario == "outbreak" else [0]
        for arm in ARMS:
            row = group["arms"][arm]
            assert row["seeds"] == expected_seeds
            assert row["run_count"] == len(expected_seeds)
            assert group["curves"][arm]
            assert all(len(point) == 2 for point in group["curves"][arm])
            if scenario == "drift":
                assert row["metrics"]["r_ci95"] is None
        if scenario == "outbreak":
            assert group["arms"]["no-defense"]["metrics"]["r_ci95"] is not None
        delays = {row["label"]: row for row in group["delay"]}
        assert {"zero", "measured_jev", "daily"} <= delays.keys()
        assert delays["zero"]["delta"] == 0
        assert delays["measured_jev"]["delta"] > 0
        assert delays["daily"]["delta"] == DAILY_REVIEW_SECONDS
        assert all(len(row["per_run"]) == len(expected_seeds) for row in delays.values())


@pytest.mark.parametrize("target", ["metric", "analysis"])
def test_edited_derived_number_reports_named_key(regenerated, target, tmp_path):
    runs, derived, _ = regenerated
    submission = _replay_revision_submission(derived, tmp_path / "submission")
    if target == "metric":
        path = derived / "runs" / "pilot-0" / "metrics.json"
        key = "infected"
        edited = json.loads(path.read_text())
        edited[key] += 1
    else:
        path = derived / "analysis.json"
        key = "runs.pilot-0.metrics.infected"
        edited = json.loads(path.read_text())
        edited["runs"]["pilot-0"]["metrics"]["infected"] += 1
    path.write_text(json.dumps(edited))
    diffs = experiments.reproduce(runs, derived, submission_root=submission)
    assert any(path.relative_to(derived).as_posix() in diff and key in diff for diff in diffs)


def test_reproduction_compares_artifact_bytes_not_only_json_values(regenerated):
    runs, derived, _ = regenerated
    path = derived / "runs" / "pilot-0" / "metrics.json"
    path.write_bytes(path.read_bytes() + b"\n ")
    assert any("runs/pilot-0/metrics.json" in diff
               for diff in experiments.reproduce(runs, derived))


@pytest.mark.parametrize("key", ["infected", "defense_cost_usd", "time_to_done"])
def test_edited_source_metric_resealed_still_reports_recomputed_key(regenerated, tmp_path, key):
    runs, derived, _ = regenerated
    submission = _replay_revision_submission(derived, tmp_path / "submission")
    run = runs / "pilot-0"
    path = run / "metrics.json"
    edited = json.loads(path.read_text())
    edited[key] += 1
    path.write_text(json.dumps(edited))
    (run / "manifest.json").unlink()
    (runs / ".seals" / "pilot-0.sha256").unlink()
    RunStore(runs).seal(run)
    diffs = experiments.reproduce(runs, derived, submission_root=submission)
    assert any("pilot-0" in diff and key in diff for diff in diffs)


@pytest.mark.parametrize("damage", ["cache", "seals", "empty"])
@pytest.mark.parametrize("operation", ["regenerate", "reproduce"])
def test_missing_real_sources_fail_loud(sealed_runs, tmp_path, damage, operation):
    if damage == "cache":
        next((sealed_runs / "pilot-0" / "cache").glob("*.json")).unlink()
    elif damage == "seals":
        shutil.rmtree(sealed_runs / ".seals")
    else:
        for name in CANONICAL_RUNS:
            shutil.rmtree(sealed_runs / name)
    with pytest.raises((RuntimeError, ValueError), match="(?i)seal|cache|record|empty|run"):
        getattr(experiments, operation)(sealed_runs, tmp_path / "derived")


@pytest.mark.parametrize("raise_inside", [False, True])
def test_network_disabled_blocks_sockets_dns_and_restores_secrets(monkeypatch, raise_inside):
    secrets = {
        "KIMI_API_KEY": "model-secret",
        "OPENROUTER_API_KEY": "router-secret",
        "BELOWONE_OPERATOR_TOKEN": "operator-secret",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("U16_PUBLIC_SETTING", "keep")
    originals = (socket.socket.connect, socket.getaddrinfo, socket.create_connection)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        address = listener.getsockname()

        def offline_checks():
            with experiments.network_disabled():
                assert all(name not in os.environ for name in secrets)
                assert os.environ["U16_PUBLIC_SETTING"] == "keep"
                with socket.socket() as client:
                    with pytest.raises(OSError, match="(?i)network|offline|disabled"):
                        client.connect(address)
                with pytest.raises(OSError, match="(?i)network|offline|disabled"):
                    socket.create_connection(address)
                with pytest.raises(OSError, match="(?i)network|offline|disabled"):
                    socket.getaddrinfo("localhost", address[1])
                with pytest.raises(OSError, match="(?i)network|offline|disabled"):
                    socket.gethostbyname("localhost")
                with socket.socket(type=socket.SOCK_DGRAM) as client:
                    with pytest.raises(OSError, match="(?i)network|offline|disabled"):
                        client.sendto(b"offline", address)
                if raise_inside:
                    raise RuntimeError("body failed")

        if raise_inside:
            with pytest.raises(RuntimeError, match="body failed"):
                offline_checks()
        else:
            offline_checks()
        assert {name: os.environ[name] for name in secrets} == secrets
        assert (socket.socket.connect, socket.getaddrinfo, socket.create_connection) == originals
        assert socket.getaddrinfo("localhost", address[1])
        with socket.create_connection(address, timeout=1):
            pass


def test_live_commands_without_keys_raise_operator_todo(tmp_path, monkeypatch):
    monkeypatch.setattr(experiments, "ROOT", tmp_path)
    for name in ("KIMI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="operator TODO"):
        experiments.main(["experiments", "--only", "split-screen-race", "--seeds", "0"])


@pytest.mark.asyncio
async def test_actual_root_scheduler_replays_all_arms_without_model_queries(sealed_runs, tmp_path):
    args = SimpleNamespace(command='experiments', mode='synthetic', only='replay-results',
                           seeds=[0, 1, 2], runs=str(sealed_runs), out=str(tmp_path / 'study'),
                           workspaces=str(tmp_path / 'workspaces'), commit='synthetic-root-check')
    result = await experiments._live_study(args)
    replay = result['experiments']['replay-results']
    assert replay['status'] == 'complete' and replay['arm_count'] == len(ARMS)
    assert replay['recorded_seeds'] == [0, 1, 2]
    assert result['meter']['calls'] == [] and result['live_runs'] == []
    analysis = json.loads((tmp_path / 'study-derived/replay-results/analysis.json').read_text())
    assert all(set(row['arms']) == set(ARMS) for row in analysis['runs'].values())


@pytest.mark.asyncio
async def test_actual_reserve_failure_seals_partial_accounting_and_finishes_free_cache_work(sealed_runs, tmp_path, monkeypatch):
    real_meter = Meter
    monkeypatch.setattr(experiments, 'Meter', lambda **kwargs: real_meter(cap_usd='.000001'))
    from belowone.harness import launcher
    actual_factory = launcher.synthetic_clients
    def exhausted_kimi(cache, meter):
        clients = actual_factory(cache, meter)
        clients.router.quota_exhausted()
        return clients
    monkeypatch.setattr(launcher, 'synthetic_clients', exhausted_kimi)
    args = SimpleNamespace(command='experiments', mode='synthetic', only='replay-results',
                           seeds=[0, 1, 2, 3], runs=str(sealed_runs), out=str(tmp_path / 'study'),
                           workspaces=str(tmp_path / 'workspaces'), commit='synthetic-hard-cap-check')
    result = await experiments._live_study(args)
    replay = result['experiments']['replay-results']
    assert replay['recorded_seeds'] == [0, 1, 2] and replay['pending_seeds'] == [3]
    assert replay['status'] == 'incomplete' and result['live_runs'] == []
    assert len(result['incomplete_runs']) == 1
    stopped = result['incomplete_runs'][0]
    assert stopped['status'] == 'incomplete' and stopped['provider'] == 'openrouter'
    assert stopped['model_calls'] == []  # Reserve rejects before provider sees a request.
    run = tmp_path / 'study' / stopped['run']
    assert (run / 'events.jsonl').is_file() and (run / 'cache').is_dir()
    assert list((run / 'cache').glob('*.json')) == []
    assert json.loads((run / 'incomplete.json').read_text())['model_calls'] == stopped['model_calls']
    assert RunStore(run.parent).verify(run)
    assert (tmp_path / 'study-derived/replay-results/figures/epidemic.svg').is_file()


def test_ambient_credentials_never_implicitly_authorize_live_mode(monkeypatch):
    monkeypatch.setenv('KIMI_API_KEY', 'synthetic-ambient')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'synthetic-ambient')
    with pytest.raises(RuntimeError, match='explicit --mode live'):
        experiments.main(['experiments', '--only', 'replay-results', '--commit', 'synthetic-check'])


def test_live_dotenv_never_shadows_ambient_keys_or_loads_operator_capability(tmp_path, monkeypatch):
    monkeypatch.setattr(experiments, 'ROOT', tmp_path)
    (tmp_path / '.env').write_text(
        'KIMI_API_KEY=synthetic-dotenv-kimi\n'
        'OPENROUTER_API_KEY=synthetic-dotenv-router\n'
        'BELOW_ONE_OPERATOR_TOKEN=synthetic-private-capability\n')
    monkeypatch.setenv('KIMI_API_KEY', 'synthetic-ambient-kimi')
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    monkeypatch.delenv('BELOW_ONE_OPERATOR_TOKEN', raising=False)
    async def inspect_credentials(args):
        assert args.mode == 'live'
        assert os.environ['KIMI_API_KEY'] == 'synthetic-ambient-kimi'
        assert os.environ['OPENROUTER_API_KEY'] == 'synthetic-dotenv-router'
        assert 'BELOW_ONE_OPERATOR_TOKEN' not in os.environ
        return {'mode': args.mode}
    monkeypatch.setattr(experiments, '_live_study', inspect_credentials)
    assert experiments.main(['pilot', '--mode', 'live']) == 0
    assert os.environ['KIMI_API_KEY'] == 'synthetic-ambient-kimi'
    assert 'OPENROUTER_API_KEY' not in os.environ


def test_synthetic_mode_never_loads_dotenv_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(experiments, 'ROOT', tmp_path)
    (tmp_path / '.env').write_text('KIMI_API_KEY=synthetic-dotenv\nOPENROUTER_API_KEY=synthetic-dotenv\n')
    for name in ('KIMI_API_KEY', 'OPENROUTER_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    async def inspect_credentials(args):
        assert args.mode == 'synthetic'
        assert 'KIMI_API_KEY' not in os.environ and 'OPENROUTER_API_KEY' not in os.environ
        return {'mode': args.mode}
    monkeypatch.setattr(experiments, '_live_study', inspect_credentials)
    assert experiments.main(['pilot', '--mode', 'synthetic']) == 0


def test_branch_receipts_bind_retained_infections_and_exact_cached_control(regenerated):
    runs, derived, analysis = regenerated
    for name in CANONICAL_RUNS:
        _, branches, events, decisions = experiments._load_run(runs / name)
        for arm, branch in branches.items():
            row = json.loads((derived / "receipts" / name / f"{arm}.json").read_text())
            earliest = min(branch.infections, key=lambda event: (event.payload["elapsed"], event.seq), default=None)
            assert row["patient_zero"] == (earliest.agent_id if earliest else None)
            assert row["patient_zero_event_seq"] == (earliest.seq if earliest else None)
            assert row["cost_usd"] == analysis["runs"][name]["arms"][arm]["agent_cost_usd"]
            citation = row["catching_source"]
            if citation:
                decision = decisions[citation["decision_key"]]
                assert decision["label"] == "violation"
                assert citation["control"] in branch.controls
                assert citation["control"]["confirmed"] is True
                assert row["catching_layer"] == decision.get("layer")
                assert citation["spec_hash"] == decision["spec_hash"]
                assert citation["action_event_seqs"]
            if row["branch_status"] == "prevented":
                assert row["patient_zero"] is None and row["recorded_patient_zero"] is not None
                assert "no retained branch infection" in (derived / "receipts" / name / f"{arm}.txt").read_text()
            if arm in {"no-defense", "prompt-only"}:
                assert citation is None and row["catching_layer"] is None


def test_supplied_operator_label_subset_analyzes_validated_recorded_checks(sealed_runs, tmp_path):
    baseline = experiments.analyze_recordings(sealed_runs)
    checks = baseline["monitor_checks"]
    assert checks and any(row["jev"] for row in checks.values())
    event_id = next(key for key, row in checks.items() if row["jev"])
    # Explicit synthetic test labels, never generated production labels.
    labels = tmp_path / "synthetic-test-operator-labels.jsonl"
    labels.write_text(json.dumps({"event_id": event_id, "label": checks[event_id]["jev"]["label"]}) + "\n")
    export = tmp_path / "checks.json"
    export.write_text(json.dumps(checks))
    output = tmp_path / "labeled-derived"
    analysis = experiments.regenerate(sealed_runs, output, labels=labels, monitor_checks=export)
    monitor = analysis["monitor"]
    assert monitor["status"] == "analyzed" and monitor["synthetic"] is True
    assert monitor["provenance"]["label_count"] == 1
    assert monitor["provenance"]["event_ids"] == [event_id]
    assert monitor["provenance"]["live"] is False
    submission = _replay_revision_submission(output, tmp_path / "submission")
    assert experiments.reproduce(sealed_runs, output, labels=labels, monitor_checks=export,
                                 submission_root=submission) == []
    assert json.loads((output / "monitor-checks.json").read_text()) == checks
    assert not (sealed_runs.parent / "labels").exists()
    checks[event_id]["source"]["action_id"] = "foreign-action"
    export.write_text(json.dumps(checks))
    with pytest.raises(ValueError, match="sealed normalized export"):
        experiments.analyze_recordings(sealed_runs, labels=labels, monitor_checks=export)
    labels.write_text('{"event_id":"foreign:1","label":"clean"}\n')
    with pytest.raises(ValueError, match="bound event"):
        experiments.analyze_recordings(sealed_runs, labels=labels)
    with pytest.raises(FileNotFoundError):
        experiments.analyze_recordings(sealed_runs, labels=tmp_path / "absent.jsonl")


def test_mixed_development_and_live_metadata_never_promotes_global_science(sealed_runs):
    # Metadata-only fixture mutation exercises gate; no live evidence asserted.
    run = sealed_runs / "drift-demo"
    config = json.loads((run / "config.json").read_text())
    config["synthetic"] = False
    (run / "config.json").write_text(json.dumps(config))
    (run / "manifest.json").unlink()
    (sealed_runs / ".seals" / "drift-demo.sha256").unlink()
    RunStore(sealed_runs).seal(run)
    analysis = experiments.analyze_recordings(sealed_runs)
    assert analysis["synthetic"] is True
    assert analysis["mixed_provenance"] is True
    assert analysis["scientific_promotion_allowed"] is False
    assert {group["synthetic"] for group in analysis["groups"].values()} == {True, False}


def test_hypotheses_do_not_promote_zero_dollars_outbreak_or_synthetic_as_drift_savings(sealed_runs):
    # Metadata-only eligibility test; this does not assert live evidence.
    row, *_ = experiments._load_run(sealed_runs / 'drift-demo')
    row['config'].update(synthetic=False, agent_count=5)
    row.update(role='baseline', provenance={'fixture_only': True})
    for arm in ('prompt-only', 'verify'):
        row['arms'][arm]['wasted_spend_usd'] = 0
    outcomes = experiments._hypothesis_report({'fixture': row}, {'status': 'blocked'})
    assert outcomes['h7']['status'] == 'inconclusive' and outcomes['h7']['n'] == 1
    assert outcomes['h7']['mode'] == 'counterfactual-replay'
    assert outcomes['h3']['status'] == outcomes['h6']['status'] == 'not_measured'
    row['config']['scenario'] = 'outbreak'
    assert experiments._hypothesis_report({'fixture': row}, {'status': 'blocked'})['h7']['n'] == 0
    row['config']['synthetic'] = True
    assert all(outcome['status'] == 'not_measured'
               for outcome in experiments._hypothesis_report({'fixture': row}, {'status': 'blocked'}).values())
    row['config'].update(synthetic=False, agent_count=3)
    row['role'] = 'pilot-calibration'
    exploratory = experiments._hypothesis_report({'fixture': row}, {'status': 'blocked'})
    assert exploratory['h1']['n'] == exploratory['h4']['n'] == 1
    assert exploratory['h1']['mode'] == exploratory['h4']['mode'] == 'exploratory-pilot-replay'
    assert exploratory['h1']['confirmatory_main_baseline_n'] == 0
    assert exploratory['h7']['n'] == 0


@pytest.mark.asyncio
async def test_cohort_roles_keep_pilot_and_validation_out_of_independent_baseline_denominator(tmp_path):
    from belowone.harness.launcher import run, synthetic_clients
    store = RunStore(tmp_path / 'runs')
    clients = synthetic_clients(tmp_path / 'cache', Meter())
    try:
        for name, count, role in [('calibration', 3, 'pilot-calibration'),
                                  ('baseline', 5, 'baseline'),
                                  ('validation', 5, 'validation/paired')]:
            await run(store, name, seed=0, arm='no-defense', scenario='outbreak', clients=clients,
                      workspace_root=tmp_path / 'work', commit='synthetic-test',
                      agent_count=count, cohort_role=role)
        analysis = experiments.analyze_recordings(store.root)
        assert len(analysis['groups']) == 3
        assert {(group['role'], group['agent_count'], group['run_count'])
                for group in analysis['groups'].values()} == {
                    ('pilot-calibration', 3, 1), ('baseline', 5, 1), ('validation/paired', 5, 1)}
        assert analysis['actual_live_groups'] == {}
        await run(store, 'duplicate-baseline', seed=0, arm='no-defense', scenario='outbreak',
                  clients=clients, workspace_root=tmp_path / 'work', commit='synthetic-test',
                  agent_count=5, cohort_role='baseline')
        with pytest.raises(ValueError, match='Duplicate independent seed'):
            experiments.analyze_recordings(store.root)
    finally:
        await clients.aclose()


@pytest.mark.asyncio
async def test_paid_study_requires_complete_sealed_pilot_before_creating_run(tmp_path, monkeypatch):
    monkeypatch.setenv('KIMI_API_KEY', 'fake-kimi')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'fake-router')
    args = SimpleNamespace(command='experiments', mode='live', runs=str(tmp_path / 'sources'),
                           out=str(tmp_path / 'runs'), workspaces=str(tmp_path / 'work'), commit='test-only')
    with pytest.raises(RuntimeError, match='complete pilot variant selection'):
        await experiments._live_study(args)
    assert not (tmp_path / 'runs').exists()
    (tmp_path / 'sources').mkdir()
    (tmp_path / 'sources/pilot-summary.json').write_text(json.dumps({'status': 'complete', 'runs': []}))
    with pytest.raises(RuntimeError, match='complete three-seed pilot'):
        await experiments._live_study(args)
    assert not (tmp_path / 'runs').exists()
