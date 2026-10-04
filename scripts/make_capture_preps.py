#!/usr/bin/env python3
"""U13 capture prep (owned, thin, canonical-API only):

A) everyday-drift: an ACTUAL synthetic-development recording of the drift
   scenario under verify via the canonical launcher. Existing archived
   recordings are read-only inputs; missing demos are generated only under
   the explicit display root. No fabricated events or archive rewrites.
B) split derived snapshots: per-arm counterfactual branches of the canonical
   pilot-0 recording via the canonical U10 eval.arms.replay_arm + cached interviewed
   decisions. Written to SEPARATE derived-<arm> folders (originals untouched)
   with full provenance flags: source run/commit, spec hash, arm,
   counterfactual, synthetic.

Usage: uv run --frozen python scripts/make_capture_preps.py
       --artifact-root experiments/committed/runs --display-root NEW_DIRECTORY
Existing display snapshots are compared to current derivation, never overwritten.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RUNS = ROOT / "experiments" / "committed" / "runs"
DISPLAY = ROOT / "experiments" / "display"


def head_hash() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()


def _latest(root: Path, prefix: str) -> Path | None:
    candidates = sorted(p for p in root.glob(f"{prefix}*")
                        if (p / "summary.json").is_file())
    return candidates[-1] if candidates else None


def drift_demo(commit: str, artifact_root: Path, display_root: Path) -> Path:
    for root in (artifact_root, display_root):
        folder = root / "drift-demo"
        if (folder / "manifest.json").is_file():
            json.loads((folder / "snapshot.json").read_text())
            print(f"using recorded drift-demo: {folder}")
            return folder
    from belowone.harness.launcher import CSV_IMPLEMENTATION, synthetic_clients, run
    from belowone.meter import Meter
    from belowone.runstore import RunStore

    store = RunStore(display_root)
    meter = Meter(cap_usd=15)
    cache = Path(tempfile.mkdtemp(prefix="u13-drift-cache-"))
    # Off-scope refactor first (real steer target), then the actual task.
    scripts = {"a0": [
        {"tool": "write", "path": "utils/formatting.py",
         "content": "def reformat_reports(rows):\n"
                    "    return [list(reversed(r)) for r in rows]\n"},
        {"tool": "write", "path": "reports/export.py",
         "content": CSV_IMPLEMENTATION},
        {"tool": "finish"},
    ]}
    clients = synthetic_clients(cache, meter, spread=False, scripts=scripts)
    workspace = Path(tempfile.mkdtemp(prefix="u13-drift-ws-"))
    asyncio.run(run(store, "drift-demo", seed=0, arm="verify",
                    scenario="drift", clients=clients,
                    workspace_root=str(workspace), commit=commit,
                    agent_count=3, steps=20))
    folder = display_root / "drift-demo"
    kinds = [json.loads(line)["kind"]
             for line in (folder / "events.jsonl").read_text().splitlines()
             if line.strip()]
    print(f"drift-demo sealed: {sum(k == 'steer' for k in kinds)} steer "
          f"events, {sum(k == 'freeze' for k in kinds)} freezes, "
          f"{sum(k == 'infection' for k in kinds)} infections")
    return folder


def hero_verify(commit: str, artifact_root: Path, display_root: Path) -> Path:
    """Reuse an actual freeze recording, or generate one only in display output."""
    existing = _latest(artifact_root, "hero-verify") or _latest(display_root, "hero-verify")
    if existing:
        json.loads((existing / "snapshot.json").read_text())
        return existing
    from belowone.harness.launcher import synthetic_clients, run
    from belowone.meter import Meter
    from belowone.runstore import RunStore
    store = RunStore(display_root)
    meter = Meter(cap_usd=15)
    clients = synthetic_clients(Path(tempfile.mkdtemp(prefix="u13-hero-")),
                                meter, spread=True)
    workspace = Path(tempfile.mkdtemp(prefix="u13-hero-ws-"))
    asyncio.run(run(store, "hero-verify", seed=0, arm="verify",
                    scenario="outbreak", clients=clients,
                    workspace_root=str(workspace), commit=commit,
                    agent_count=3, steps=20))
    folder = _latest(display_root, "hero-verify") or display_root / "hero-verify"
    events = [json.loads(line)
              for line in (folder / "events.jsonl").read_text().splitlines()
              if line.strip()]
    freezes = [e for e in events if e.get("kind") == "freeze"]
    print(f"hero-verify sealed: {len(freezes)} freeze controls, "
          f"{sum(1 for e in events if e.get('kind') == 'infection')} "
          "ground-truth infections")
    return folder


def split_derived(commit: str, artifact_root: Path, display_root: Path) -> list[Path]:
    from belowone.eval.arms import replay_arm
    from belowone.eval.decisions import load_decisions
    from belowone.eval.metrics import drift_metrics, outbreak_metrics
    from belowone.dashboard_data import snapshot
    from types import SimpleNamespace

    run = artifact_root / "pilot-0"
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
        metrics = {**outbreak_metrics(result), **drift_metrics(
            events, controls=result.controls, total_agents=agents)}
        snap = snapshot(result.events, result.controls, metrics,
                        synthetic=True)
        snap["provenance"] = {
            "source_run": "pilot-0",
            "source_commit": (run / "commit.txt").read_text().strip(),
            "derived_commit": commit,
            "spec_hash": digest, "arm": arm,
            "counterfactual": arm != "no-defense", "synthetic": True,
            "derived_by": "scripts/make_capture_preps.py (canonical "
                          "eval.arms.replay_arm)",
        }
        out = display_root / f"derived-{arm}"
        snapshot_text = json.dumps(snap, indent=1)
        stream_text = "".join(json.dumps({
            "seq": e["seq"], "agent_id": e["agent_id"], "kind": e["kind"],
            "paths": e["paths"],
            "payload": {**e["payload"], "elapsed": e["elapsed"]},
        }) + "\n" for e in snap["events"])
        if out.exists():
            if (json.loads((out / "snapshot.json").read_text()) != snap
                    or (out / "events.jsonl").read_text() != stream_text):
                raise ValueError(f"{out} differs from current source derivation; "
                                 "choose a new --display-root (existing artifacts are read-only)")
        else:
            out.mkdir(parents=True)
            (out / "snapshot.json").write_text(snapshot_text)
            (out / "events.jsonl").write_text(stream_text)
        written.append(out)
        print(f"display/{out.name}: infected={metrics['infected']} "
              f"r={metrics['r_mean']}")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, default=RUNS)
    parser.add_argument("--display-root", type=Path, default=DISPLAY)
    args = parser.parse_args()
    artifact_root, display_root = args.artifact_root.resolve(), args.display_root.resolve()
    if (artifact_root.is_relative_to(display_root)
            or display_root.is_relative_to(artifact_root)):
        parser.error("archive and display roots must be separate, non-nested directories")
    commit = head_hash()
    drift_demo(commit, artifact_root, display_root)
    hero_verify(commit, artifact_root, display_root)
    split_derived(commit, artifact_root, display_root)
    print(f"read-only archive root: {artifact_root}; display root: {display_root} "
          "(archived pilot-0 / drift-demo / hero-verify; display derived-<arm>)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
