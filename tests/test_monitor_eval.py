"""U15 monitor eval tests: deterministic strata sample, resumable labels,
hand-computed accuracy/calibration on a known fixture."""
import json

import pytest

from belowone.eval.monitor import (
    STRATA, LabelStore, analyze, next_unlabeled, sample_events,
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
    checks = {
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
    report = analyze(labels, checks)
    assert report["n_scored"] == 4
    assert report["accuracy"] == 2 / 4  # e2 and e3 mispredicted
    assert report["confusion"]["clean"]["drift"] == 1
    assert report["confusion"]["drift"]["clean"] == 1
    # hand-computed reliability: [.5,.75): e3 conf .6 wrong -> err 0.6
    # [.75,.9): e2 conf .8 wrong -> err 0.8 ; [.9,1]: both right -> 0
    bins = {tuple(b["bin"]): b for b in report["reliability_bins"]}
    assert bins[(0.5, 0.75)]["n"] == 1
    assert bins[(0.5, 0.75)]["error"] == 0.6
    assert bins[(0.75, 0.9)]["error"] == 0.8
    assert bins[(0.9, 1.0)]["error"] == pytest.approx(0.04)  # (0.96 mean conf vs 1.0)
    expected_ece = (1 * 0.6 + 1 * 0.8 + 2 * 0.04) / 4  # = 0.37; mean-of-errors differs from n-weighted ECE
    assert report["calibration_error"] == pytest.approx(expected_ece)
    assert report["latency_median_s"] == 0.45
    assert report["latency_p95_s"] == 0.5
    assert report["cost_per_1000_checks_usd"] == pytest.approx(
        (0.0001 + 0.0002 + 0.0001 + 0.0001) / 4 * 1000)
    assert report["synthetic"] is True


def test_proposed_band_shifts_for_poor_mid_confidence():
    labels = {f"e{i}": "clean" for i in range(4)}
    checks = {f"e{i}": {"label": "clean", "confidence": c, "latency_s": 0.1}
              for i, c in enumerate([0.55, 0.6, 0.7, 0.95])}
    report = analyze(labels, checks)
    # mid-confidence bins are overconfident-but-fine here (all correct):
    # no shift proposed when calibration is good
    assert report["proposed_band"] is None
    checks["e0"]["label"] = "violation"
    checks["e1"]["label"] = "violation"  # bin [0.5,0.75) now 2/3 wrong
    report = analyze(labels, checks)
    assert report["proposed_band"] == [0.45, 0.75]
