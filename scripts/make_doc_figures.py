#!/usr/bin/env python3
"""Generate deterministic doc figures from committed artifacts (U17).

Writes docs/generated/figures/{epidemic,r-bar,delay-damage}.svg (+ .json
sidecars). All values come from experiments/committed snapshots and the U10
delay sweep on those recorded events — nothing hand-typed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from belowone.eval.delay import delay_sweep  # noqa: E402
from belowone.eval.metrics import outbreak_metrics  # noqa: E402
from belowone.eval.replay import replay_freeze_schedule, replay_no_defense  # noqa: E402
from belowone.viz import figures  # noqa: E402


def _load(run_dir: Path):
    events = []
    for line in (run_dir / "events.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        from types import SimpleNamespace
        events.append(SimpleNamespace(
            seq=e["seq"], agent_id=e.get("agent_id"), kind=e.get("kind"),
            paths=e.get("paths") or [], payload=e.get("payload") or {}))
    freezes = sorted({(e.agent_id, float(e.payload.get("elapsed", 0.0)))
                      for e in events if e.kind == "freeze"})
    snap = json.loads((run_dir / "snapshot.json").read_text())
    return events, freezes, snap


def main() -> int:
    committed = ROOT / "experiments" / "committed"
    out = ROOT / "docs" / "generated" / "figures"
    out.mkdir(parents=True, exist_ok=True)

    per_arm, r_arms, sweep_source = {}, {}, None
    for run_dir in sorted(committed.iterdir()):
        if not (run_dir / "events.jsonl").is_file():
            continue
        events, freezes, snap = _load(run_dir)
        arm = run_dir.name.rsplit("-", 3)[0]  # strip -SYNTHETIC? keep full name
        result = replay_freeze_schedule(events, freezes)
        series, count = [], 0
        for ev in sorted(events, key=lambda e: e.payload.get("elapsed", 0)):
            if ev.kind == "infection":
                count += 1
            series.append((float(ev.payload.get("elapsed", 0)), count))
        per_arm[run_dir.name] = series
        metrics = outbreak_metrics(result, events, seed=0)
        r_arms[run_dir.name] = {"mean": metrics["r_mean"],
                                "ci95": metrics["r_ci95"]}
        if "verify" in run_dir.name:
            sweep_source = (events, freezes)

    svg, data = figures.epidemic_curve(per_arm)
    figures.write_figure(out, "epidemic", svg, data)
    svg, data = figures.r_bar(r_arms)
    figures.write_figure(out, "r-bar", svg, data)
    if sweep_source:
        events, freezes = sweep_source
        from belowone.eval.replay import replay_freeze_schedule as rfs
        grid = [0.0, 5.0, 60.0, 300.0, 86400.0]
        points = [{"delta": d,
                   "damage": len(rfs(events,
                                     [(a, t + d) for a, t in freezes])
                               .infections)} for d in grid]
        svg, data = figures.delay_damage(points)
        figures.write_figure(out, "delay-damage", svg, data)
    print(f"figures written to {out}: {sorted(p.name for p in out.glob('*.svg'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
