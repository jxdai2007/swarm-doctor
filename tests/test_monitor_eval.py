"""U15 monitor eval tests: deterministic strata sample, resumable labels,
hand-computed accuracy/calibration on a known fixture."""
import json

import pytest

from belowone.eval.monitor import (
    STRATA, LabelStore, analyze, next_unlabeled, sample_events,
    sample_from_recordings,
)


def _events(counts: dict[str, int]) -> list[dict]:
    events = []
    event_id = 0
    for stratum, n in counts.items():
        for _ in range(n):
            events.append({"event_id": event_id, "stratum": stratum,
                           "preview": f"event {event_id}"})
            event_id += 1
    return events


def test_sampler_deterministic_and_covers_all_strata():
    events = _events({"clean": 40, "drift": 10, "violation": 5})
    a = sample_events(events, per_stratum=5, seed=7)
    b = sample_events(events, per_stratum=5, seed=7)
    assert [e["event_id"] for e in a] == [e["event_id"] for e in b]
    strata = {e["stratum"] for e in a}
    assert strata == set(STRATA)
    from collections import Counter
    counts = Counter(e["stratum"] for e in a)
    assert counts == {"clean": 5, "drift": 5, "violation": 5}
    # different seed may differ but respects stratum caps
    c = sample_events(events, per_stratum=5, seed=8)
    assert len(c) == len(a)


def test_label_store_resumes_after_interruption(tmp_path):
    store = LabelStore(tmp_path / "labels" / "monitor_labels.jsonl")
    sample = _events({"clean": 3, "drift": 2, "violation": 1})
    assert next_unlabeled(sample, store)["event_id"] == 0
    store.add("0", "clean")
    store.add("1", "drift")
    assert next_unlabeled(sample, store)["event_id"] == 2
    # duplicate-store then reload: resume skips both
    reloaded = LabelStore(store.path)
    assert next_unlabeled(sample, reloaded)["event_id"] == 2
    with pytest.raises(ValueError):
        store.add("2", "obvious cheat")  # only the three strata labelable
    assert store.rows() == {"0": "clean", "1": "drift"}


def test_analyze_accuracy_calibration_hand_computed():
    # operator labels: e0 clean, e1 violation, e2 clean, e3 drift
    labels = {"e0": "clean", "e1": "violation", "e2": "clean", "e3": "drift"}
    jev = {
        "e0": {"label": "clean", "confidence": 0.95, "latency_s": 0.4,
               "cost_usd": 0.0001},
        "e1": {"label": "violation", "confidence": 0.97, "latency_s": 0.5,
               "cost_usd": 0.0002},
        "e2": {"label": "drift", "confidence": 0.80, "latency_s": 0.45,
               "cost_usd": 0.0001},
        "e3": {"label": "clean", "confidence": 0.60, "latency_s": 0.42,
               "cost_usd": 0.0001},
        "e9": {"label": "violation", "confidence": 0.99, "latency_s": 0.1,
               "cost_usd": 0.001},  # unlabeled: excluded from accuracy
    }
    report = analyze(labels, {eid: {"jev": check} for eid, check in jev.items()},
                     provenance={"source": "fixture"})
    r = report["jev"]
    assert report["synthetic"] is True
    assert report["provenance"]["source"] == "fixture"
    assert r["n_scored"] == 4
    assert r["accuracy"] == 2 / 4  # e2 and e3 mispredicted
    assert r["confusion"]["clean"]["drift"] == 1
    assert r["confusion"]["drift"]["clean"] == 1
    # hand-computed reliability: [.5,.75): e3 conf .6 wrong -> err 0.6
    # [.75,.9): e2 conf .8 wrong -> err 0.8 ; [.9,1]: both right -> ~0.04
    bins = {tuple(b["bin"]): b for b in r["reliability_bins"]}
    assert bins[(0.5, 0.75)]["n"] == 1
    assert bins[(0.5, 0.75)]["error"] == 0.6
    assert bins[(0.75, 0.9)]["error"] == 0.8
    assert bins[(0.9, 1.0)]["error"] == pytest.approx(0.04)
    expected_ece = (1 * 0.6 + 1 * 0.8 + 2 * 0.04) / 4
    assert r["calibration_error"] == pytest.approx(expected_ece)
    # latency/cost over the LABELED population only (e9 excluded)
    import statistics
    assert r["latency_median_s"] == statistics.median([0.4, 0.42, 0.45, 0.5])
    assert r["latency_p95_s"] == 0.5  # nearest-rank: ceil(.95*4)=4th
    assert r["cost_per_1000_checks_usd"] == pytest.approx(
        (0.0001 + 0.0002 + 0.0001 + 0.0001) / 4 * 1000)
    assert report["judge"]["unmeasured"].startswith("no recorded judge")
    assert "unmeasured" in report["comparison"]["judge_comparison"]


def test_analyze_judge_comparison_when_both_measured():
    labels = {"e0": "clean", "e1": "clean", "e2": "clean", "e3": "clean"}
    checks = {
        "e0": {"jev": {"label": "clean", "confidence": 0.9, "latency_s": 0.4,
                       "cost_usd": 0.0001},
               "judge": {"label": "clean", "confidence": 0.99,
                         "latency_s": 2.0, "cost_usd": 0.01}},
        "e1": {"jev": {"label": "violation", "confidence": 0.9,
                       "latency_s": 0.6, "cost_usd": 0.0001},
               "judge": {"label": "clean", "confidence": 0.99,
                         "latency_s": 2.2, "cost_usd": 0.01}},
        "e2": {"jev": {"label": "clean", "confidence": 0.9, "latency_s": 0.4,
                       "cost_usd": 0.0001},
               "judge": {"label": "clean", "confidence": 0.99,
                         "latency_s": 2.0, "cost_usd": 0.01}},
        "e3": {"jev": {"label": "clean", "confidence": 0.9, "latency_s": 0.4,
                       "cost_usd": 0.0001}},
    }
    report = analyze(labels, checks, provenance={"source": "fixture"})
    comp = report["comparison"]
    assert comp["n_both"] == 3  # e3 has no judge check
    assert comp["jev_accuracy"] == pytest.approx(3 / 3 - 1 / 3)
    assert comp["judge_accuracy"] == 1.0
    assert comp["jev_cost_usd"] == pytest.approx(0.0003)
    assert report["judge"]["accuracy"] == 1.0


def test_recording_adapter_stratifies_by_recorded_labels(tmp_path):
    run = tmp_path / "pilot-seed0"
    run.mkdir()
    events = [
        {"seq": 0, "kind": "action_executed", "agent_id": "a0",
         "action_id": "x1", "payload": {"elapsed": 1.0}},
        {"seq": 1, "kind": "action_executed", "agent_id": "a1",
         "action_id": "x2", "payload": {"elapsed": 2.0}},
        {"seq": 2, "kind": "action_executed", "agent_id": "a2",
         "payload": {"elapsed": 3.0}},  # no recorded decision: excluded
        {"seq": 3, "kind": "outcome", "agent_id": "a0",
         "payload": {"elapsed": 4.0}},
    ]
    (run / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events))
    (run / "decisions.jsonl").write_text(
        json.dumps({"action_id": "x1", "label": "drift"}) + "\n"
        + json.dumps({"action_id": "x2", "label": "violation"}) + "\n")
    sample = sample_from_recordings([run], per_stratum=10, seed=0)
    by_id = {e["event_id"]: e for e in sample}
    assert by_id["pilot-seed0:0"]["stratum"] == "drift"
    assert by_id["pilot-seed0:1"]["stratum"] == "violation"
    assert len(sample) == 2  # unlabeled event never invented a stratum
    # deterministic
    again = sample_from_recordings([run], per_stratum=10, seed=0)
    assert [e["event_id"] for e in sample] == [e["event_id"] for e in again]


def test_cli_label_resumes_after_quit(tmp_path, monkeypatch, capsys):
    import statistics  # noqa: F401
    from belowone.eval.monitor import main as monitor_main
    sample = tmp_path / "sample.jsonl"
    events = _events({"clean": 2, "drift": 1, "violation": 1})
    sample.write_text("".join(json.dumps(e) + "\n" for e in events))
    labels = tmp_path / "labels.jsonl"
    inputs = iter(["clean", "drift"])  # label two, then quit via EOF

    def fake_input(prompt=""):
        try:
            return next(inputs)
        except StopIteration:
            raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", fake_input)
    rc = monitor_main(["label", "--sample", str(sample),
                       "--labels", str(labels)])
    assert rc == 0
    store = LabelStore(labels)
    assert store.rows() == {"0": "clean", "1": "drift"}
    assert "progress saved" in capsys.readouterr().out
    # resume: next unlabeled is event 2; label the rest
    inputs = iter(["clean", "violation", ""])
    monkeypatch.setattr("builtins.input", fake_input)
    rc = monitor_main(["label", "--sample", str(sample),
                       "--labels", str(labels)])
    assert rc == 0
    assert store.rows() == {"0": "clean", "1": "drift", "2": "clean",
                            "3": "violation"}


def test_proposed_band_exploratory_min_samples():
    labels = {f"e{i}": "clean" for i in range(8)}
    confs = [0.55, 0.6, 0.7, 0.55, 0.6, 0.7, 0.95, 0.96]
    checks = {f"e{i}": {"jev": {"label": "clean", "confidence": c,
                                "latency_s": 0.1}}
              for i, c in enumerate(confs)}
    report = analyze(labels, checks, provenance={"source": "fixture"})
    # all-correct bins: accurate-but-under-confident never moves the band
    assert report["jev"]["proposed_band"] is None
    # make bin [0.5,0.75) mostly WRONG with enough samples -> exploratory
    for i in (0, 1, 3, 4, 5):
        checks[f"e{i}"]["jev"]["label"] = "violation"
    report = analyze(labels, checks, provenance={"source": "fixture"})
    band = report["jev"]["proposed_band"]
    assert band["band"] == [0.45, 0.75]
    assert band["exploratory"] is True
    assert "accuracy" in band["basis"]
    # too few samples never moves the band
    small = {f"e{i}": checks[f"e{i}"] for i in (0, 1)}
    small_labels = {k: "clean" for k in small}
    report = analyze(small_labels, small, provenance={"source": "fixture"})
    assert report["jev"]["proposed_band"] is None
