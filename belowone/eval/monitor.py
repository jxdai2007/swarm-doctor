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
import sys
from pathlib import Path

STRATA = ("clean", "drift", "violation")


def sample_events(events: list[dict], per_stratum: int, seed: int) -> list[dict]:
    """Deterministic stratified sample; same seed + events => same sample.
    Stratum key: each event dict carries "stratum" (manifest/label provenance)."""
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


def analyze(labels: dict[str, str], checks: dict[str, dict]) -> dict:
    """labels: event_id -> operator label (ground truth). checks: event_id ->
    {"label", "confidence", "latency_s", "cost_usd"} from recorded runs.
    Accuracy/confusion vs operator labels; calibration error over reliability
    bins; latency median/p95; cost per 1,000 checks."""
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
    p95 = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))] \
        if latencies else None

    def band():
        """Recalibrate the escalation band only where the checker is WRONG
        (accuracy < 0.5), not merely imperfectly confident: an accurate but
        under-confident checker needs no band shift."""
        for r in reliability:
            if r["n"] and r["accuracy"] < 0.5 and r["bin"][0] >= 0.5:
                return [max(0.0, r["bin"][0] - 0.05), r["bin"][1]]
        return None

    return {
        "n_scored": scored,
        "accuracy": correct / scored if scored else None,
        "confusion": confusion,
        "calibration_error": ece,
        "reliability_bins": reliability,
        "latency_median_s": latencies[len(latencies) // 2] if latencies else None,
        "latency_p95_s": p95,
        "cost_per_1000_checks_usd": (cost / scored * 1000) if scored else None,
        "proposed_band": band(),
        "synthetic": True,  # caller flips off only with live recordings
    }


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
                           help="recorded checker outputs JSON")
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
        report = analyze(store.rows(), checks)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
        print(json.dumps({k: report[k] for k in
                          ("n_scored", "accuracy", "calibration_error",
                           "proposed_band")}, sort_keys=True))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
