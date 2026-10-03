"""Monitor evaluation (U15): stratified sample, resumable operator labeling
terminal tool, and fast-checker accuracy/calibration/latency/cost analysis.

Ground rules: labels come ONLY from the operator (labels/monitor_labels.jsonl);
no agent labels. Analysis never claims live-model properties without live
recordings — fixture inputs are labeled synthetic.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

STRATA = ("clean", "drift", "violation")


def sample_events(events: list[dict], per_stratum: int, seed: int) -> list[dict]:
    """Deterministic stratified sample; same seed + events => same sample.
    Stratum key: each event dict carries "stratum" from recorded provenance
    (see sample_from_recordings); events without a recorded stratum are never
    invented into one."""
    by_stratum: dict[str, list[int]] = {s: [] for s in STRATA}
    for index, event in enumerate(events):
        stratum = event.get("stratum")
        if stratum in by_stratum:
            by_stratum[stratum].append(index)
    rng = random.Random(seed)
    picked = []
    for stratum in STRATA:
        pool = by_stratum[stratum][:]
        rng.shuffle(pool)
        for index in pool[:per_stratum]:
            picked.append(events[index])
    return picked


def sample_from_recordings(run_dirs: list[Path], per_stratum: int = 50,
                           seed: int = 0, *,
                           spec_hash: str | None = None) -> list[dict]:
    """U9-recording adapter: read each run's events.jsonl (U2 EventLog JSONL)
    plus decisions.jsonl and stratify by the RECORDED detector label.

    Schema notes: U2 events carry action_id inside payload; proposals and
    denials are sampled alongside executed actions (violation trips usually
    DENY the action — executed-only sampling would bias the sample clean).

    Trust: when several decisions exist for one action (e.g. interviewed vs
    one-line spec), the caller MUST pass the trusted spec_hash; ambiguity
    without one raises rather than silently taking the last record.

    Stable event id = "<run>:<seq>". Events without a recorded label for the
    selected spec are excluded (no guessed strata)."""
    rows: dict[str, dict] = {}
    for run_dir in run_dirs:
        run = Path(run_dir).name
        decisions: dict[str, str] = {}
        ambiguous: set[str] = set()
        dec_file = Path(run_dir) / "decisions.jsonl"
        if dec_file.is_file():
            for line in dec_file.read_text().splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                if d.get("label") not in STRATA:
                    continue
                if spec_hash is not None and d.get("spec_hash") != spec_hash:
                    continue
                action_id = str(d.get("action_id")
                                or d.get("payload", {}).get("action_id")
                                or d.get("action_seq"))
                if action_id in decisions and decisions[action_id] != d["label"]:
                    ambiguous.add(action_id)
                decisions.setdefault(action_id, d["label"])
        events_file = Path(run_dir) / "events.jsonl"
        if not events_file.is_file():
            continue
        for line in events_file.read_text().splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e.get("kind") not in ("action_executed", "action_proposed",
                                     "action_denied"):
                continue
            payload = e.get("payload") or {}
            action_id = str(payload.get("action_id") or e.get("action_id")
                            or e.get("seq"))
            if action_id in ambiguous:
                raise ValueError(
                    f"{run}:{e.get('seq')}: action {action_id} has multiple "
                    "recorded labels with different verdicts; pass the "
                    "trusted spec_hash to disambiguate")
            label = decisions.get(action_id)
            if label is None:
                continue
            event_id = f"{run}:{e.get('seq')}"
            rows[event_id] = {
                "event_id": event_id,
                "stratum": label,
                "kind": e.get("kind"),
                "preview": json.dumps(payload)[:160],
            }
    return sample_events(list(rows.values()), per_stratum, seed)


class LabelStore:
    """Append-only JSONL of {event_id, label, labeled_at}; resume = skip ids
    already present. Only the operator writes labels."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def labeled_ids(self) -> set[str]:
        if not self.path.is_file():
            return set()
        return {json.loads(line)["event_id"]
                for line in self.path.read_text().splitlines() if line.strip()}

    def add(self, event_id: str, label: str) -> None:
        if label not in STRATA:
            raise ValueError(f"label must be one of {STRATA}")
        with self.path.open("a") as handle:
            handle.write(json.dumps({"event_id": event_id, "label": label},
                                    sort_keys=True) + "\n")

    def rows(self) -> dict[str, str]:
        return {json.loads(line)["event_id"]: json.loads(line)["label"]
                for line in self.path.read_text().splitlines() if line.strip()}


def next_unlabeled(sample: list[dict], store: LabelStore) -> dict | None:
    done = store.labeled_ids()
    for event in sample:
        if str(event["event_id"]) not in done:
            return event
    return None


def _percentile_nearest_rank(sorted_values: list[float], pct: float):
    """Nearest-rank percentile: index ceil(pct*N)-1 (documented)."""
    import math
    if not sorted_values:
        return None
    rank = max(1, math.ceil(pct * len(sorted_values)))
    return sorted_values[rank - 1]


def analyze(labels: dict[str, str], checks: dict, *,
            provenance: dict | None = None) -> dict:
    """labels: event_id -> operator label (ground truth, operator-only).

    checks is the recorded-check export (U9 contract): either
    {event_id: {"jev": check, "judge": check|None}} or the legacy flat
    {event_id: check} treated as Jev. Each check:
    {"label", "confidence", "latency_s", "cost_usd"}.

    provenance is trusted run metadata {"source": "fixture"|"recording",
    ...}; a missing provenance is reported synthetic and can never be
    auto-promoted to scientific/live status by this module.
    """
    prov = dict(provenance or {})
    prov.setdefault("source", "fixture")
    # A recording is not automatically live science: U9 recordings made with
    # synthetic/dev models stay synthetic. Only trusted run metadata may set
    # live=True, and this module never promotes it on its own.
    prov.setdefault("live", False)
    synthetic = not (prov.get("source") == "recording" and prov.get("live"))

    def as_checker_map(raw: dict) -> dict[str, dict]:
        out = {}
        for event_id, value in raw.items():
            if isinstance(value, dict) and ("jev" in value or "judge" in value):
                out[event_id] = value
            else:
                out[event_id] = {"jev": value, "judge": None}
        return out

    merged = as_checker_map(checks)

    def one_report(selector: str) -> dict:
        subset = {eid: entry[selector] for eid, entry in merged.items()
                  if entry.get(selector)}
        if not subset:
            return {"unmeasured": f"no recorded {selector} checks"}
        return _accuracy_calibration(labels, subset)

    report = {
        "jev": one_report("jev"),
        "judge": one_report("judge"),
        "comparison": _compare(merged, labels),
        "provenance": prov,
        "synthetic": synthetic,
    }
    if synthetic:
        report["note"] = ("synthetic/fixture inputs; never cite as measured "
                          "live-model properties")
    return report


def _compare(merged, labels):
    """Side-by-side deltas only where BOTH checkers measured; missing judge
    stays unmeasured, never fabricated."""
    both = {eid for eid, entry in merged.items()
            if entry.get("jev") and entry.get("judge") and eid in labels}
    if not both:
        return {"judge_comparison": "unmeasured (no events with both jev "
                                    "and judge checks)"}

    def accuracy(selector):
        return sum(labels[e] == merged[e][selector]["label"] for e in both) \
            / len(both)

    jev_latency = [merged[e]["jev"]["latency_s"] for e in both]
    judge_latency = [merged[e]["judge"]["latency_s"] for e in both]
    jev_cost = sum(merged[e]["jev"].get("cost_usd", 0.0) for e in both)
    judge_cost = sum(merged[e]["judge"].get("cost_usd", 0.0) for e in both)
    return {"n_both": len(both),
            "jev_accuracy": accuracy("jev"),
            "judge_accuracy": accuracy("judge"),
            "jev_latency_median_s": statistics.median(jev_latency),
            "judge_latency_median_s": statistics.median(judge_latency),
            "jev_cost_usd": jev_cost,
            "judge_cost_usd": judge_cost}


def _accuracy_calibration(labels: dict[str, str], checks: dict[str, dict]) -> dict:
    confusion = {t: {p: 0 for p in STRATA} for t in STRATA}
    correct = scored = 0
    for event_id, truth in labels.items():
        check = checks.get(event_id)
        if not check:
            continue
        scored += 1
        predicted = check["label"]
        confusion[truth][predicted] += 1
        correct += predicted == truth
    bins = [(0.0, 0.5), (0.5, 0.75), (0.75, 0.9), (0.9, 1.01)]
    reliability = []
    for low, high in bins:
        bucket = [(check["confidence"], labels[eid] == check["label"])
                  for eid, check in checks.items()
                  if eid in labels and low <= check["confidence"] < high]
        if bucket:
            mean_conf = sum(c for c, _ in bucket) / len(bucket)
            accuracy = sum(o for _, o in bucket) / len(bucket)
            reliability.append({"bin": [low, min(high, 1.0)],
                                "n": len(bucket),
                                "mean_confidence": mean_conf,
                                "accuracy": accuracy,
                                "error": abs(mean_conf - accuracy)})
    ece = (sum(r["n"] * r["error"] for r in reliability) / scored) if scored else None
    measured = [checks[eid] for eid in labels if eid in checks]
    latencies = sorted(check["latency_s"] for check in measured)
    cost = sum(check.get("cost_usd", 0.0) for check in measured)
    return {
        "n_scored": scored,
        "accuracy": correct / scored if scored else None,
        "confusion": confusion,
        "calibration_error": ece,
        "reliability_bins": reliability,
        "latency_median_s": statistics.median(latencies) if latencies else None,
        "latency_p95_s": _percentile_nearest_rank(latencies, 0.95),
        "cost_per_1000_checks_usd": (cost / scored * 1000) if scored else None,
        "proposed_band": _proposed_band(reliability),
    }


def _proposed_band(reliability: list[dict]):
    """EXPLORATORY KTD12 recalibration: propose shifting the escalation band
    below a mid/high-confidence bin only when that bin is actually WRONG
    (accuracy < 0.5) with at least MIN_SAMPLES observations. Small samples
    never move the band; the output is a recommendation with its basis, not a
    settled decision."""
    min_samples = 5
    for r in reliability:
        if (r["n"] >= min_samples and r["accuracy"] < 0.5
                and r["bin"][0] >= 0.5):
            return {"band": [max(0.0, r["bin"][0] - 0.05), r["bin"][1]],
                    "basis": (f"{r['n']} samples in bin "
                              f"{r['bin']} with accuracy "
                              f"{r['accuracy']:.2f}"),
                    "exploratory": True,
                    "min_samples": min_samples}
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="belowone-monitor")
    sub = parser.add_subparsers(dest="command", required=True)
    p_sample = sub.add_parser("sample", help="write the stratified sample")
    p_sample.add_argument("--events", required=True)
    p_sample.add_argument("--per-stratum", type=int, default=50)
    p_sample.add_argument("--seed", type=int, default=0)
    p_sample.add_argument("--out", default="labels/sample.jsonl")

    p_label = sub.add_parser("label", help="operator labeling (resumable)")
    p_label.add_argument("--sample", default="labels/sample.jsonl")
    p_label.add_argument("--labels", default="labels/monitor_labels.jsonl")

    p_analyze = sub.add_parser("analyze", help="accuracy/calibration report")
    p_analyze.add_argument("--labels", default="labels/monitor_labels.jsonl")
    p_analyze.add_argument("--checks", required=True,
                           help="recorded-check export JSON (U9 contract)")
    p_analyze.add_argument("--provenance",
                           help="trusted run metadata JSON {source: "
                                "fixture|recording, ...}; absent = "
                                "treated synthetic")
    p_analyze.add_argument("--out", default="labels/monitor_report.json")

    args = parser.parse_args(argv)
    if args.command == "sample":
        events = [json.loads(line) for line in
                  Path(args.events).read_text().splitlines() if line.strip()]
        sample = sample_events(events, args.per_stratum, args.seed)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps(e, sort_keys=True) + "\n"
                               for e in sample))
        print(f"sampled {len(sample)} events -> {out}")
        return 0
    if args.command == "label":
        sample = [json.loads(line) for line in
                  Path(args.sample).read_text().splitlines() if line.strip()]
        store = LabelStore(Path(args.labels))
        print("operator labeling — labels are yours alone; Ctrl-C resumes later")
        while True:
            event = next_unlabeled(sample, store)
            if event is None:
                print("sample fully labeled")
                return 0
            print(f"\n{event['event_id']}: {event.get('preview', '')}")
            try:
                label = input(f"label {STRATA}: ").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\nprogress saved; {len(store.labeled_ids())} labeled")
                return 0
            try:
                store.add(str(event["event_id"]), label)
            except ValueError as error:
                print(error)
    if args.command == "analyze":
        store = LabelStore(Path(args.labels))
        checks = json.loads(Path(args.checks).read_text())
        prov = None
        if args.provenance:
            prov = json.loads(Path(args.provenance).read_text())
        report = analyze(store.rows(), checks, provenance=prov)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
        print("source:", report["provenance"]["source"],
              "(synthetic)" if report["synthetic"] else "(recording)")
        for checker in ("jev", "judge"):
            r = report[checker]
            if "unmeasured" in r:
                print(f"{checker}: {r['unmeasured']}")
            else:
                print(f"{checker}: n={r['n_scored']} accuracy={r['accuracy']}"
                      f" ece={r['calibration_error']}")
        print("comparison:", report["comparison"])
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
