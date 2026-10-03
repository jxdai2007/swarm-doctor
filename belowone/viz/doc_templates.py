"""Doc templates (U17, KTD14): every empirical number in generated docs comes
from a metrics file via {key} substitution; unmeasured metrics render
explicitly as 'unmeasured'. H1-H7 are reported as SYNTHETIC DEV / unmeasured
until real pilot data exists — never fictitious scientific outcomes."""
from __future__ import annotations

import json
import re
from pathlib import Path


def load_metrics(paths: list[Path]) -> dict:
    """{run_name: {source_key: value}} from metrics/snapshot files."""
    out = {}
    for path in paths:
        snap = json.loads(Path(path).read_text())
        rows = {row["source"]: row["value"] for row in snap.get("counters", [])}
        out[Path(path).parent.name] = rows
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
| Infected agents | {no-defense-fixture-outbreak.metrics.infected} | {prompt-only-fixture-outbreak.metrics.infected} | {below-one-verify-fixture-outbreak.metrics.infected} |
| R (secondary per infected) | {no-defense-fixture-outbreak.metrics.r_mean} | {prompt-only-fixture-outbreak.metrics.r_mean} | {below-one-verify-fixture-outbreak.metrics.r_mean} |
| Wasted spend (USD) | {no-defense-fixture-outbreak.metrics.wasted_spend_usd} | {prompt-only-fixture-outbreak.metrics.wasted_spend_usd} | {below-one-verify-fixture-outbreak.metrics.wasted_spend_usd} |

Every value regenerates from committed run artifacts with `make reproduce`
(no network, no API keys). Live-model results are reported only when real
pilot recordings exist; synthetic artifacts are labeled SYNTHETIC DEV.
""",
    "writeup-metrics.md": """\
## Results

- Outbreak arm `below-one-verify`: infected = {below-one-verify-fixture-outbreak.metrics.infected},
  R = {below-one-verify-fixture-outbreak.metrics.r_mean}, wasted spend =
  {below-one-verify-fixture-outbreak.metrics.wasted_spend_usd}.
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
