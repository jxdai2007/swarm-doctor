"""Doc templates (U17, KTD14): every empirical number in generated docs comes
from a metrics file via {key} substitution; unmeasured metrics render
explicitly as 'unmeasured'. H1-H7 are reported as SYNTHETIC DEV / unmeasured
until real pilot data exists — never fictitious scientific outcomes."""
from __future__ import annotations

import json
import re
from pathlib import Path


def load_metrics(paths: list[Path]) -> dict:
    """Token sources. analysis.json (U16 aggregate) maps doc_metrics
    directly ({arm.metrics.key} per-run means / cross-seed R); metrics.json
    maps to metrics.<key> per run; snapshot.json counters map to their
    source citations."""
    out = {}
    for path in paths:
        path = Path(path)
        snap = json.loads(path.read_text())
        if path.name == "analysis.json":
            out.update(snap.get("doc_metrics", {}))
        elif path.name == "metrics.json":
            out[path.parent.name] = {f"metrics.{k}": v
                                     for k, v in snap.items()
                                     if not isinstance(v, (dict, list))}
        else:
            out[path.parent.name] = {row["source"]: row["value"]
                                     for row in snap.get("counters", [])}
    return out


def _fmt(value):
    if value is None:
        return "unmeasured"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _fill(template: str, metrics: dict) -> str:
    """Replace {run.source} tokens (format_map treats dots as attribute
    access, so substitution is manual); unknown keys raise (fail loud)."""
    def sub(match):
        key = match.group(1)
        run, _, source = key.partition(".")
        if run not in metrics or source not in metrics[run]:
            raise KeyError(key)
        return _fmt(metrics[run][source])
    return re.sub(r"\{([a-z0-9-]+\.[a-z0-9_.]+)\}", sub, template)


TEMPLATES = {
    "README-metrics.md": """\
## Measured results (regenerated)

| metric | no-defense | prompt-only | below-one-verify |
|---|---|---|---|
| Infected agents | {pilot-0.metrics.infected} | {pilot-1.metrics.infected} | {pilot-2.metrics.infected} |
| R (secondary per infected) | {pilot-0.metrics.r_mean} | {pilot-1.metrics.r_mean} | {pilot-2.metrics.r_mean} |
| Wasted spend (USD) | {pilot-0.metrics.wasted_spend_usd} | {pilot-1.metrics.wasted_spend_usd} | {pilot-2.metrics.wasted_spend_usd} |

Values are per-run means across seeds (R = cross-seed estimate) from the
U16 aggregate; they regenerate offline with `make reproduce` (no network,
no API keys). Recorded sources are synthetic-development; live-model
results appear only when real live runs exist and are labeled as such.
""",
    "writeup-metrics.md": """\
## Results

- Outbreak arm `below-one-verify`: infected = {pilot-2.metrics.infected},
  R = {pilot-2.metrics.r_mean}, wasted spend =
  {pilot-2.metrics.wasted_spend_usd}.
- Hypotheses H1-H7: **unmeasured — SYNTHETIC DEV fixture data only.** No
  hypothesis is claimed as measured until real pilot recordings and live
  validation runs exist (R30/R33).
""",
}


def build_docs(metrics: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, template in TEMPLATES.items():
        rendered = _fill(template, metrics)
        path = out_dir / name
        rendered = rendered.replace(
            "## ", "# ", 0)  # keep headings as authored
        path.write_text(rendered)
        written.append(path)
    return written
