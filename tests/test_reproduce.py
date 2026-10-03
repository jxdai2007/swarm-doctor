"""U16 reproducibility tests: preregistration gate, quota stop, regeneration
diff, key scan — all fixture-driven, offline."""
import json
import os
import sys

import pytest

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent

from belowone.experiments import (
    QuotaExhausted, diff_named_key, diff_outputs, load_plan,
    require_preregistration, run_schedule, scan_secrets,
)


def test_plan_orders_never_cut_first_then_reverse_cut_list():
    plan = load_plan()
    ids = [e["id"] for e in plan["priority"]]
    never_cut = {"split-screen-race", "prompt-vs-below-one",
                 "delay-vs-damage", "wasted-spend-card", "replay-results"}
    assert set(ids[:5]) == never_cut
    cut = [e for e in plan["priority"] if "cut_order" in e]
    orders = [e["cut_order"] for e in cut]
    assert orders == sorted(orders, reverse=True)  # last-cut runs first


def test_non_pilot_run_refuses_without_committed_preregistration(tmp_path):
    plan = {"priority": [{"id": "scale-hf-size", "arms": ["verify"],
                          "seeds": 1}],
            "preregistration": "experiments/preregistration.md",
            "pilot_ids": ["split-screen-race"]}
    require_preregistration("split-screen-race", plan)  # pilot: exempt
    # repo preregistration is committed, so the real gate passes for real ids
    require_preregistration("scale-hf-size")
    missing = tmp_path / "prereg.md"
    plan_missing = {"priority": plan["priority"],
                    "preregistration": str(missing), "pilot_ids": []}
    with pytest.raises(RuntimeError, match="committed"):
        require_preregistration("scale-hf-size", plan_missing)
    missing.write_text("draft")
    with pytest.raises(RuntimeError, match="committed"):
        require_preregistration("scale-hf-size", plan_missing)  # untracked


def test_quota_exhaustion_stops_after_current_seed():
    plan = load_plan()
    calls = []

    def start(seed, arm):
        calls.append((seed, arm))

    state = {"calls_left": 5}
    def exhausted():
        state["calls_left"] -= 1
        return state["calls_left"] <= 0

    with pytest.raises(QuotaExhausted) as exc:
        run_schedule(plan, "split-screen-race", [0, 1], start, exhausted)
    payload = json.loads(str(exc.value))
    assert payload["stopped_after_seed"] == 1
    assert payload["reached"]["0"] == 3  # full first seed: all arms ran
    assert payload["pending_arms"]  # second seed did not finish


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


def test_secret_scan_flags_key_shaped_strings(tmp_path):
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({"infected": 1}))
    dirty = tmp_path / "run" / "notes.txt"
    dirty.parent.mkdir()
    dirty.write_text("used sk-abcdefghijklmnop123456 today")
    assert scan_secrets([clean]) == []
    hits = scan_secrets([dirty])
    assert hits and "sk-" in hits[0]


def test_doc_checker_fails_on_edited_number_and_passes_clean(tmp_path):
    import shutil
    import subprocess
    sys.path.insert(0, str(ROOT))
    from scripts.check_doc_numbers import main as check_main
    committed = tmp_path / "committed"
    committed.mkdir()
    run = committed / "no-defense-fixture-outbreak"
    shutil.copytree(ROOT / "experiments/committed/no-defense-fixture-outbreak", run)
    shutil.copytree(ROOT / "experiments/committed/prompt-only-fixture-outbreak",
                    committed / "prompt-only-fixture-outbreak")
    shutil.copytree(ROOT / "experiments/committed/below-one-verify-fixture-outbreak",
                    committed / "below-one-verify-fixture-outbreak")
    docs = tmp_path / "docs" / "generated"
    metrics = sorted(committed.glob("*/snapshot.json"))
    from belowone.viz.doc_templates import build_docs, load_metrics
    build_docs(load_metrics(metrics), docs)
    args = ["--metrics-glob", str(committed / "*" / "snapshot.json"),
            "--docs", str(docs)]
    assert check_main(args) == 0  # clean pass
    # edit one committed number -> checker fails naming the file
    snap = run / "snapshot.json"
    edited = json.loads(snap.read_text())
    for row in edited["counters"]:
        if row["source"] == "metrics.infected":
            row["value"] = 42
    snap.write_text(json.dumps(edited))
    assert check_main(args) == 1


def test_reproduce_cycle_byte_consistent_on_committed_artifacts():
    from belowone.experiments import reproduce
    assert reproduce(ROOT / "experiments/committed") == []


def test_live_commands_fail_loud_without_keys_or_harness():
    from belowone.spec.interview import load_config
    from belowone.experiments import main as experiments_main
    # missing keys -> loud operator blocker, never a fake run
    saved = {k: os.environ.pop(k, None)
             for k in ("KIMI_API_KEY", "OPENROUTER_API_KEY")}
    try:
        with pytest.raises(RuntimeError, match="operator TODO"):
            experiments_main(["experiments", "--only", "split-screen-race",
                              "--seeds", "0"])
    finally:
        os.environ.update({k: v for k, v in saved.items() if v})
