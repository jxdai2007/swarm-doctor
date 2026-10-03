#!/usr/bin/env python3
"""U13 capture prep (owned, thin, canonical-API only):

A) everyday-drift: an ACTUAL sealed synthetic-development recording of the
   drift scenario under the verify arm via the canonical U9 launcher
   (launcher.run) with a scripted off-scope-then-on-task policy — real steer
   events, real grader, real seal registry. No fabricated events.
B) split derived snapshots: per-arm counterfactual branches of the canonical
   pilot-0 recording via the canonical U10 replay_arm + cached interviewed
   decisions. Written to SEPARATE derived-<arm> folders (originals untouched)
   with full provenance flags: source run/commit, spec hash, arm,
   counterfactual, synthetic.

Usage: uv run python scripts/make_capture_preps.py
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RUNS = ROOT / "experiments" / "committed" / "runs"


def head_hash() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()


def drift_demo(commit: str) -> Path:
    from belowone.harness.launcher import synthetic_clients, run
    from belowone.meter import Meter
    from belowone.runstore import RunStore

    store = RunStore(RUNS)
    meter = Meter(cap_usd=15)
    cache = Path(tempfile.mkdtemp(prefix="u13-drift-cache-"))
    # Off-scope refactor first (real steer target), then the actual task.
    scripts = {"a0": [
        {"tool": "write", "path": "utils/formatting.py",
         "content": "def reformat_reports(rows):\n"
                    "    return [list(reversed(r)) for r in rows]\n"},
        {"tool": "write", "path": "reports/export.py",
         "content": "import csv\n\ndef export(rows, handle):\n"
                    "    writer = csv.writer(handle)\n"
                    "    writer.writerows(rows)\n"},
        {"tool": "finish"},
    ]}
    clients = synthetic_clients(cache, meter, spread=False, scripts=scripts)
    workspace = Path(tempfile.mkdtemp(prefix="u13-drift-ws-"))
    asyncio.run(run(store, "drift-demo-2", seed=0, arm="verify",
                    scenario="drift", clients=clients,
                    workspace_root=str(workspace), commit=commit,
                    agent_count=3, steps=20))
    folder = RUNS / "drift-demo-2"
    kinds = [json.loads(line)["kind"]
             for line in (folder / "events.jsonl").read_text().splitlines()
             if line.strip()]
    print(f"drift-demo sealed: {sum(k == 'steer' for k in kinds)} steer "
          f"events, {sum(k == 'freeze' for k in kinds)} freezes, "
          f"{sum(k == 'infection' for k in kinds)} infections")
    return folder


def split_derived(commit: str) -> list[Path]:
    from belowone.eval.arms import replay_arm
    from belowone.eval.decisions import load_decisions
    from belowone.eval.metrics import drift_metrics, outbreak_metrics
    from belowone.dashboard_data import snapshot
    from types import SimpleNamespace

    run = RUNS / "pilot-0"
    digest = json.loads((run / "config.json").read_text())["spec_hash"]
    events = []
    for line in (run / "events.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        events.append(SimpleNamespace(
            seq=e["seq"], agent_id=e.get("agent_id"), kind=e.get("kind"),
            paths=e.get("paths") or [], payload=e.get("payload") or {}))
    decisions = load_decisions(run / "decisions.jsonl", digest)
    agents = len({e.agent_id for e in events if e.agent_id})
    written = []
    for arm in ("no-defense", "prompt-only", "verify"):
        result = replay_arm(events, decisions, spec_hash=digest, arm=arm)
        controls = [(c["agent_id"], float(c["elapsed"]))
                    for c in result.controls
                    if c["kind"] in {"freeze", "release", "kill"}]
        metrics = {**outbreak_metrics(result), **drift_metrics(
            events, controls, total_agents=agents)}
        snap = snapshot(result.events, controls, metrics, synthetic=True)
        snap["provenance"] = {
            "source_run": "pilot-0", "source_commit": commit,
            "spec_hash": digest, "arm": arm,
            "counterfactual": arm != "no-defense", "synthetic": True,
            "derived_by": "scripts/make_capture_preps.py (canonical "
                          "harness.arms.replay_arm)",
        }
        out = RUNS.parent / f"derived-{arm}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "snapshot.json").write_text(json.dumps(snap, indent=1))
        with (out / "events.jsonl").open("w") as f:
            for e in snap["events"]:
                f.write(json.dumps({
                    "seq": e["seq"], "agent_id": e["agent_id"],
                    "kind": e["kind"], "paths": [],
                    "payload": {"elapsed": e["elapsed"],
                                "source_agent":
                                    (e.get("payload") or {}).get("source_agent")},
                }) + "\n")
        written.append(out)
        print(f"derived-{arm}: infected={metrics['infected']} "
              f"r={metrics['r_mean']}")
    return written


def main() -> int:
    commit = head_hash()
    drift_demo(commit)
    split_derived(commit)
    print(f"artifact root for the engine: {RUNS.parent} "
          "(run=pilot-0, drift-demo, derived-no-defense, ...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
