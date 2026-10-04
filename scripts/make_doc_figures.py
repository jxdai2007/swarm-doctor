#!/usr/bin/env python3
"""Render U14 figures from sealed-recording analysis (U16).

The analysis owns replay, measured latency, and cross-seed confidence intervals.
This driver only renders its scenario/model groups and preserves their provenance
in JSON sidecars. Synthetic-development evidence is explicitly labeled.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from belowone.eval.arms import ARMS  # noqa: E402
from belowone.viz import figures  # noqa: E402


def _group_prefix(group_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", group_id.lower()).strip("-")[:64] or "group"
    digest = hashlib.sha256(group_id.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}-"


def generate_figures(analysis: dict, out: Path) -> list[Path]:
    """Write every group's measured SVGs and provenance-bearing JSON sidecars."""
    groups = analysis.get("groups")
    if not groups:
        raise ValueError("Figure analysis must contain scenario/model groups")

    # Validate all groups before writing, so incomplete input cannot look complete.
    for group_id, group in groups.items():
        for key in ("scenario", "served_models", "source", "synthetic",
                    "arms", "curves", "delay"):
            if key not in group:
                raise ValueError(f"Figure group {group_id}: missing {key}")
        if not group["scenario"] or not group["served_models"] or not group["source"]:
            raise ValueError(f"Figure group {group_id}: missing source/model/scenario metadata")
        if not isinstance(group["synthetic"], bool):
            raise ValueError(f"Figure group {group_id}: synthetic must be explicit boolean")
        for arm in ARMS:
            if arm not in group["arms"] or arm not in group["curves"]:
                raise ValueError(f"Figure group {group_id}: missing arm/curve {arm}")
            if not group["curves"][arm]:
                raise ValueError(f"Figure group {group_id}: empty curve {arm}")
            aggregate = group["arms"][arm]
            if any(key not in aggregate for key in ("metrics", "run_count", "seeds")):
                raise ValueError(f"Figure group {group_id}: missing aggregate/provenance {arm}")
            if any(key not in aggregate["metrics"] for key in ("r_mean", "r_ci95")):
                raise ValueError(f"Figure group {group_id}: missing R metrics {arm}")
        if not group["delay"]:
            raise ValueError(f"Figure group {group_id}: missing delay rows")
        for row in group["delay"]:
            if any(key not in row for key in ("label", "delta", "damage", "per_run")):
                raise ValueError(f"Figure group {group_id}: incomplete delay row")
            if not row["per_run"]:
                raise ValueError(f"Figure group {group_id}: delay row lacks measured provenance")
        labels = {row["label"] for row in group["delay"]}
        if not {"measured_jev", "daily"} <= labels:
            raise ValueError(f"Figure group {group_id}: missing measured_jev/daily delay rows")

    outbreak_count = sum(group["scenario"] == "outbreak" and group.get("recorded_arm") == "no-defense"
                         for group in groups.values())
    paths = []
    for group_id, group in sorted(groups.items()):
        prefix = "" if (group["scenario"] == "outbreak" and group.get("recorded_arm") == "no-defense"
                        and outbreak_count == 1) else _group_prefix(group_id)
        label_prefix = "SYNTHETIC: " if group["synthetic"] else ""
        metadata = {key: value for key, value in group.items()
                    if key not in {"arms", "curves", "delay"}}
        metadata["group_id"] = group_id
        metadata["arm_provenance"] = {
            arm: {"run_count": aggregate["run_count"], "seeds": aggregate["seeds"]}
            for arm, aggregate in sorted(group["arms"].items())
        }
        curves = {label_prefix + arm: curve
                  for arm, curve in sorted(group["curves"].items())}
        r_arms = {
            label_prefix + arm: {"mean": aggregate["metrics"]["r_mean"],
                                 "ci95": aggregate["metrics"]["r_ci95"]}
            for arm, aggregate in sorted(group["arms"].items())
        }
        delay = [{**row, "label": label_prefix + row["label"]}
                 for row in group["delay"]]
        rendered = (
            ("epidemic", figures.epidemic_curve(curves, width=1180)),
            ("r-bar", figures.r_bar(r_arms, width=1180)),
            ("delay-damage", figures.delay_damage(delay)),
        )
        for name, (svg, data) in rendered:
            sidecar = json.loads(data)
            sidecar.update(metadata)
            paths.append(figures.write_figure(
                Path(out), prefix + name, svg,
                json.dumps(sidecar, sort_keys=True, separators=(",", ":"))))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path,
                        default=ROOT / "experiments" / "committed" / "runs")
    parser.add_argument("--out", type=Path,
                        default=ROOT / "docs" / "generated" / "figures")
    args = parser.parse_args()

    # Deferred import: experiments.regenerate imports generate_figures.
    from belowone.experiments import analyze_recordings

    paths = generate_figures(analyze_recordings(args.runs), args.out)
    print(f"figures written to {args.out}: {sorted(path.name for path in paths)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
