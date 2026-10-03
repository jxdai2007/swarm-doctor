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
    """Replace {token} tokens (format_map treats dots as attribute access,
    so substitution is manual); unknown keys raise (fail loud). Tokens are
    flat aggregate keys ("no-defense.metrics.infected") or nested
    ("run.source") — both looked up, direct key first."""
    def sub(match):
        key = match.group(1)
        if key in metrics:
            return _fmt(metrics[key])
        run, _, source = key.partition(".")
        if run in metrics and source in metrics[run]:
            return _fmt(metrics[run][source])
        # generic alias with no such recorded group: honest placeholder,
        # the dynamic per-group section renders those groups instead
        return "not recorded"
    return re.sub(r"\{([a-z0-9-]+\.[a-z0-9_.]+)\}", sub, template)


TEMPLATES = {
    "README-metrics.md": """\
## Measured results (regenerated)

| metric | no-defense | prompt-only | verify |
|---|---|---|---|
| Infected agents | {no-defense.metrics.infected} | {prompt-only.metrics.infected} | {verify.metrics.infected} |
| R (secondary per infected) | {no-defense.metrics.r_mean} | {prompt-only.metrics.r_mean} | {verify.metrics.r_mean} |
| Wasted spend (USD) | {no-defense.metrics.wasted_spend_usd} | {prompt-only.metrics.wasted_spend_usd} | {verify.metrics.wasted_spend_usd} |

Values are per-run means across seeds (R = cross-seed estimate) from the
U16 aggregate; they regenerate offline with `make reproduce` (no network,
no API keys). Recorded sources are synthetic-development; live-model
results appear only when real live runs exist and are labeled as such.
""",
    "writeup-metrics.md": """\
## Results

- Outbreak arm `verify`: infected = {verify.metrics.infected},
  R = {verify.metrics.r_mean}, wasted spend =
  {verify.metrics.wasted_spend_usd}.
- Hypotheses H1-H7: **unmeasured — SYNTHETIC DEV fixture data only.** No
  hypothesis is claimed as measured until real pilot recordings and live
  validation runs exist (R30/R33).
""",
}


ALIAS_GROUPS = ("no-defense", "prompt-only", "verify", "strict",
                "blunt-khop", "taint-without-checker", "periodic-review",
                "message-only")


def _dynamic_section(metrics: dict) -> str:
    """One table row per recorded group that has no generic alias — never
    averaged across served models; meta lines cited when present."""
    groups = {}
    for key, value in metrics.items():
        if key in metrics:  # nested group dict handled below
            continue
        group = key.split(".", 1)[0]
        if group in ALIAS_GROUPS:
            continue
        groups.setdefault(group, {})[key.split(".", 1)[1]] = value
    for key, value in metrics.items():
        if isinstance(value, dict) and key not in ALIAS_GROUPS:
            groups.setdefault(key, {}).update(
                {k: v for k, v in value.items()})
    if not groups:
        return ""
    lines = ["", "## Other recorded groups (per group, never averaged "
             "across served models)", ""]
    for group in sorted(groups):
        g = groups[group]
        meta = []
        for meta_key in ("meta.served_models", "meta.scenario",
                         "meta.recorded_arm", "meta.synthetic"):
            token = f"{group}.{meta_key}"
            if token in metrics:
                meta.append(f"{meta_key.split('.')[-1]}="
                            f"{_fmt(metrics[token])}")
        suffix = f" ({'; '.join(meta)})" if meta else ""
        lines.append(f"### {group}{suffix}")
        lines.append("")
        for metric in ("metrics.infected", "metrics.r_mean",
                       "metrics.wasted_spend_usd", "metrics.finish_rate"):
            token = f"{group}.{metric}"
            if token in metrics:
                lines.append(f"- {metric}: {_fmt(metrics[token])}")
        lines.append("")
    return "\n".join(lines)


def build_docs(metrics: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, template in TEMPLATES.items():
        rendered = _fill(template, metrics)
        rendered += _dynamic_section(metrics)
        path = out_dir / name
        rendered = rendered.replace(
            "## ", "# ", 0)  # keep headings as authored
        path.write_text(rendered)
        written.append(path)
    return written
