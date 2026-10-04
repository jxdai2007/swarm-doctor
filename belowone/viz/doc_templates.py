"""Central full-document templates and source-bound metric rendering.

Synthetic-development values never stand in for measured scientific outcomes.
"""
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
            doc_metrics = snap.get("doc_metrics", {})
            if not isinstance(doc_metrics, dict) or not doc_metrics:
                raise ValueError(f"{path}: missing recorded doc_metrics")
            # flatten nested groups to the flat token shape _dynamic_section
            # emits, so checker and regenerate agree byte-for-byte
            for token, value in doc_metrics.items():
                if isinstance(value, dict):
                    for sub_key, sub_value in value.items():
                        out[f"{token}.{sub_key}"] = sub_value
                else:
                    out[token] = value
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
    so substitution is manual). Generic absent groups render 'not recorded';
    an unknown metric within a recorded group fails closed."""
    def sub(match):
        key = match.group(1)
        if key in metrics:
            return _fmt(metrics[key])
        run, _, source = key.partition(".")
        if run in metrics and source in metrics[run]:
            return _fmt(metrics[run][source])
        if run in metrics or any(k.startswith(run + ".") for k in metrics):
            raise KeyError(f"Missing recorded metric: {key}")
        return "not recorded"
    return re.sub(r"\{([a-z0-9-]+\.[a-z0-9_.]+)\}", sub, template)


# R30 authoritative hypothesis statements. Statuses are rendered ONLY from
# deterministic analysis/doc_metrics tokens supplied by the regenerate
# pipeline (`_analyze` promotion); absence renders explicit unmeasured text.
# Data existence never implies "supported".
_HYPOTHESES = {
    "h1": "Damage rises with detection delay",
    "h2": "Verified tracing contains as well as blunt kill with far fewer clean agents frozen",
    "h3": "Strict mode preserves more work than kill-all",
    "h4": "The interview cuts false alarms",
    "h5": "Prevention lowers R",
    "h6": "Fast-checker confidence is/is not calibrated enough to set the radius",
    "h7": "Below One cuts wasted spend versus prompt-only with few false steers",
}
_UNMEASURED_DEFAULT = ("[unmeasured — no eligible real comparison cohort; "
                       "synthetic fixtures are development evidence only]")
_UNMEASURED_H6 = ("[unmeasured — operator labels absent (R31); "
                  "an unlabeled sample is not evidence]")
_HYP_FIELD_DEFAULTS = (("status", _UNMEASURED_DEFAULT),
                       ("n", "not recorded"),
                       ("source", "none"), ("mode", "none"),
                       ("summary", "not yet measured"))
_HYP_DEFAULTS = {hid: dict(_HYP_FIELD_DEFAULTS) for hid in _HYPOTHESES}
_HYP_DEFAULTS["h6"]["status"] = _UNMEASURED_H6


def hypothesis_tokens(metrics: dict) -> dict:
    """Flat `hypotheses.<id>.<status|n|source|mode>` tokens; explicit values
    in `metrics` win verbatim, defaults are explicit-unmeasured."""
    out = {}
    for hid in _HYPOTHESES:
        for field, default in _HYP_FIELD_DEFAULTS:
            key = f"hypotheses.{hid}.{field}"
            out[key] = metrics.get(key, _HYP_DEFAULTS[hid][field])
    return out


def _provenance_tokens(metrics: dict) -> dict:
    """Recording provenance for results/failed-hypothesis sections. Defaults
    are derived deterministically from group meta.synthetic flags: any real
    (synthetic=False) group means the note names the real calibration
    cohorts and the absent main cohort instead of claiming all-synthetic."""
    real = any(key.endswith(".meta.synthetic") and value is False
               for key, value in metrics.items())
    if real:
        provenance = ("Recorded sources include REAL pilot calibration "
                      "recordings (synthetic=False); the confirmatory "
                      "main 5-agent cohort has not been run, so headline "
                      "hypothesis claims stay exploratory/not measured.")
        failed = ("Live hypothesis outcomes are limited to the exploratory "
                  "pilot replay reported above; the confirmatory main "
                  "cohort was not run, so most hypotheses remain "
                  "not measured — including failures — until real main "
                  "data exist (R38).")
    else:
        provenance = ("Recorded sources are synthetic-development; "
                      "live-model results appear only when real live runs "
                      "exist and are labeled as such.")
        failed = ("No live hypothesis outcomes have been measured yet; "
                  "the synthetic checks in this repo are development "
                  "verification, not scientific results. Measured outcomes "
                  "— including failures — are reported as real pilot and "
                  "live data land (R38).")
    out = {"results.provenance.note": provenance,
           "results.failed.note": failed,
           "clips.provenance.note": ("Every clip linked below exists in `clips/` and is a "
                                     "SYNTHETIC DEV development recording (fixture replays, "
                                     "real engine + real CLI output). No narration line "
                                     "states a measured scientific outcome: measured claims "
                                     "are conditional on real pilot data (R30/R33) and "
                                     "render from metrics files.")}
    # Real recorded-live group note (replaces any fixture-aggregate sentence):
    # deterministic summary of the actual recorded-live group, with null R
    # stated as unmeasured denominators, never as R<1/containment success.
    actual_groups = [key[:-len(".meta.source")] for key, value in metrics.items()
                     if key.endswith(".meta.source")
                     and value == "actual-recorded-live-policy"]
    if actual_groups:
        g = sorted(actual_groups)[0]

        def _m(field, default="not recorded"):
            return metrics.get(f"{g}.metrics.{field}", default)
        out["results.actual.note"] = (
            f"The real pilot-calibration aggregate (group `{g}`, role "
            f"{metrics.get(g + '.meta.role', 'not recorded')}, "
            f"{metrics.get(g + '.meta.agent_count', '?')} agents × "
            f"{metrics.get(g + '.meta.model_turn_budget', '?')} turns, "
            f"served {metrics.get(g + '.meta.served_models', 'not recorded')}) "
            f"has {_m('infected')} infected agents per run and finish rate "
            f"{_m('finish_rate')}; with zero observed infections the R and "
            "containment denominators are null — this is NOT a claim of "
            "R below one or of containment. The five-agent main baseline "
            "was not run (N=0).")
    else:
        out["results.actual.note"] = (
            "The synthetic fixture aggregate is development evidence only; "
            "no real recorded-live group exists yet.")
    # Clip filename/narration defaults follow provenance: with a real
    # recorded-live group present, linked beats use the REAL capture names
    # and truthful narration; otherwise the committed synthetic-dev clips.
    if real:
        out.update({
            "clips.provenance.note": (
                "Clips below were captured from the real canonical archive "
                "(provenance per clips/inventory.json): REAL-suffixed beats "
                "show actual recorded pilot surfaces or truthful "
                "absence/unmeasured panels; SYNTHETIC DEV names are "
                "development fixtures only."),
            "clips.b4b.file": "hero-freeze-REAL-no-freeze-observed.webm",
            "clips.b1.file": "intro-board-REAL.webm",
            "clips.b2.file": "repo-close-REAL-documentation.webm",
            "clips.b3.file": "scripted-interview-REAL-recorded-setup.webm",
            "clips.b4.file": "live-catch-REAL.webm",
            "clips.b5.file": "split-race-REAL-exploratory-counterfactual.webm",
            "clips.b6.file": "charts-REAL.webm",
            "clips.b7.file": "receipt-REAL.webm",
            "clips.b8.file": "reproduce-proof-REAL-offline-reproduction.webm",
            "clips.b9.file": "everyday-drift-REAL-no-steer-observed.webm",
            "clips.b10.file": "threat-REAL-documentation.webm",
            "clips.b3.onscreen": "recorded locked setup spec — pilot-0 (both spec envelopes, hashes shown)",
            "clips.b3.narration": (
                "\"Before launch the interview locks what counts as "
                "failure. The live interview itself was not measured — "
                "this is the actual locked pilot-0 spec, hash and all, "
                "walked through verbatim.\""),
            "clips.b4.onscreen": "live pilot board, real agent counters (no freeze occurred)",
            "clips.b4.narration": (
                "\"Here is the real pilot board. No defense catch is "
                "observed: the outbreak never developed (zero secondary "
                "infections), so containment is unmeasured — these are "
                "actual recorded events, not a staged catch.\""),
            "clips.b5.onscreen": "three-column exploratory counterfactual comparison from the actual pilot recordings (N=2, agents 3)",
            "clips.b5.narration": (
                "\"This comparison is built from the actual pilot counterfactual "
                "replay (N=2, 3 agents). The live arm comparison itself "
                "was not run: the five-agent main study has N=0, so this "
                "stays exploratory, never a live three-arm result.\""),
            "clips.b6.narration": (
                "\"Delay-versus-damage figures replayed over the real "
                "pilot recordings. The live main-cohort delay effect "
                "remains unmeasured (main study N=0).\""),
            "clips.b9.onscreen": "steering untested in the pilot — no drift scenario ran; shared-task completion was not achieved (held-out grader case4 failed)",
            "clips.b9.narration": (
                "\"Steering is untested here: no drift scenario ran in "
                "the pilot, so no steer events exist. Wasted-spend versus "
                "prompt-only remains not measured.\""),
        })
    # Per-beat clip files; defaults are the committed synthetic-dev captures.
    # Real recaptures override `clips.b<N>.file` via doc-metrics (see capture
    # script) and MUST carry their own provenance in the beats' on-screen
    # text/narration — a real "no catch observed" beat states the absence.
    out.setdefault("clips.b4b.file", "hero-freeze-SYNTHETIC-DEV.webm")
    out.setdefault("clips.b3.onscreen", "actual interview CLI output")
    out.setdefault("clips.b4.onscreen", "live graph, frozen ring, counters")
    out.setdefault("clips.b4.narration",
                   '"Here the fixture patient zero is frozen. The board '
                   'shows the retained causal trace from the recorded '
                   'write/read chain; these are synthetic-development '
                   'events, not live scientific outcomes."')
    out.setdefault("clips.b3.narration",
                   '"Before launch, the interview locks what counts as '
                   'failure — this is the real CLI: typed questions, '
                   'recommendations, and a locked spec hash."')
    out.setdefault("clips.b4.onscreen", "live graph, frozen ring, counters")
    out.setdefault("clips.b5.onscreen", "three-arm split, same since-poisoning clock")
    out.setdefault("clips.b5.narration",
                   '"Same fixture, three arms, one clock. The counters come '
                   'from the same evaluator that produces the receipts — no '
                   'hand-typed numbers."')
    out.setdefault("clips.b6.narration",
                   '"These figures regenerate offline from committed '
                   'artifacts. What they will show about real delay is '
                   'unmeasured until pilot runs — the sweep machinery is '
                   'already exercised here."')
    out.setdefault("clips.b9.onscreen",
                   "full recorded drift task: steering chronology in the "
                   "replayed event log, then clean completion")
    out.setdefault("clips.b9.narration",
                   '"Everyday use is the other dial: the checker steers an '
                   'agent back from an off-scope refactor — the replayed '
                   'event log shows that chronology, and the recorded run '
                   'still finishes the task. Wasted spend and false steers '
                   'are counted per run."')
    for beat, name in (("b1", "intro-board"), ("b2", "repo-close"),
                       ("b3", "scripted-interview"), ("b4", "live-catch"),
                       ("b5", "split-race"), ("b6", "charts"),
                       ("b7", "receipt"), ("b8", "reproduce-proof"),
                       ("b9", "everyday-drift"), ("b10", "threat")):
        out.setdefault(f"clips.{beat}.file", f"{name}-SYNTHETIC-DEV.webm")
    for key, default in out.items():
        out[key] = metrics.get(key, default)
    return out


def _seed_tokens(metrics: dict) -> dict:
    return {**hypothesis_tokens(metrics), **_provenance_tokens(metrics), **metrics}


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
no API keys). {results.provenance.note}
""",
    "writeup-metrics.md": """\
## Results

- Outbreak arm `verify`: infected = {verify.metrics.infected},
  R = {verify.metrics.r_mean}, wasted spend =
  {verify.metrics.wasted_spend_usd}.
- Hypotheses H1-H7 outcomes: see the per-hypothesis table in
  [docs/writeup.md](../writeup.md); each row carries its own measured
  status,
  N, source, and live/synthetic mode — absent measurements render
  explicitly unmeasured.
""",
}


from belowone.eval.arms import ARMS


def _dynamic_section(metrics: dict) -> str:
    """One table row per recorded group that has no generic alias — never
    averaged across served models; meta lines cited when present."""
    groups = {}
    for key, value in metrics.items():
        group = key.split(".", 1)[0]
        if group in ARMS or "." not in key:
            continue
        groups.setdefault(group, {})[key.split(".", 1)[1]] = value
    for key, value in metrics.items():
        if isinstance(value, dict) and key not in ARMS:
            groups.setdefault(key, {}).update(value)
    if not groups:
        return ""
    lines = ["", "## Other recorded groups (per group, never averaged "
             "across served models)", ""]
    for group in sorted(groups):
        g = groups[group]
        meta = []
        for meta_key in ("meta.served_models", "meta.scenario",
                         "meta.recorded_arm", "meta.synthetic",
                         "meta.agent_count", "meta.role", "meta.steps"):
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
        rendered = _fill(template, _seed_tokens(metrics)) + _dynamic_section(metrics)
        path = out_dir / name
        path.write_text(rendered)
        written.append(path)
    return written


def render_authored(metrics: dict) -> dict[str, str]:
    """Render complete authored documents, not selected number-like spans."""
    return {name: _fill(template, _seed_tokens(metrics))
            for name, template in AUTHORED_TEMPLATES.items()}


def build_authored(metrics: dict, root: Path) -> list[Path]:
    written = []
    for name, text in render_authored(metrics).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        written.append(path)
    return written


def compare_authored(metrics: dict, root: Path) -> list[str]:
    return [f"{name}: missing or byte-different authored document"
            for name, text in render_authored(metrics).items()
            if not (root / name).is_file()
            or (root / name).read_bytes() != text.encode("utf-8")]


# Edit narrative here; regenerate publishes the complete authored documents.
AUTHORED_TEMPLATES = {
    'README.md': """\
# Below One

**Keep your swarm's R below one.** Below One is an immune system for coding-agent
swarms: every agent action lands in a live trust graph, each step is checked
against a locked goal spec, drifting agents are steered back, and a poisoned
agent is frozen, traced, and contained before it spreads.

Two dials, one engine:

- **Stay on task** (everyone, every day): catches drift — refactor loops, scope
  creep, flaky-test rabbit holes — and steers the agent back.
- **Contain outbreaks** (high-stakes runs): verifies violations, freezes the
  agent, traces exactly which agents it reached, quarantines only the
  confirmed ones.

## Quick start (oh-my-pi swarm)

```bash
# 0. prerequisites — while the repo default branch is setup-only, clone the
#    tested branch explicitly:
git clone --branch feat/below-one https://github.com/jxdai2007/below-one.git
cd below-one
uv sync                                    # python engine + deps
uv run python scripts/first_hour_checks.py # verify model endpoints + omp

# 1. lock what counts as failure BEFORE launch (setup interview)
uv run python -m belowone.cli interview \\
  --task path/to/task.md --workspace . --out goal-spec.json

# 2. launch the swarm under the engine (oh-my-pi adapter)
make demo   # live 3-agent swarm; needs .env with KIMI/OPENROUTER keys
make demo-offline   # SYNTHETIC DEV board without keys

# 3. watch the native adapter board while make demo is running
# Open BOARD_URL printed by the native launcher in your browser.
# The demo owns this server; do not launch an unrelated smoke engine here.
```

`make demo` fails loudly until the oh-my-pi adapter and model keys are in
place (operator items in `OPERATOR_TODO.md`) — it never fakes a run.

## Measured results

See [docs/generated/README-metrics.md](docs/generated/README-metrics.md).
Every number there regenerates offline from committed run artifacts with
`make reproduce` — no network, no API keys. {results.provenance.note}

## Docs

- [docs/writeup.md](docs/writeup.md) — results narrative, hypotheses H1-H7
  (explicitly unmeasured until real data), replay-vs-live gap.
- [docs/threat-model.md](docs/threat-model.md) — what Below One can and cannot
  see (shell/subprocess blind spot, off-tool channels, capability model).
- [docs/capability-table.md](docs/capability-table.md) — adapter contract vs
  known platform support.
- [docs/video-script.md](docs/video-script.md) — storyboard beats mapped to
  captured clips and narration lines.
- [docs/plans/2026-10-03-0413-feat-below-one-plan.md](docs/plans/2026-10-03-0413-feat-below-one-plan.md)
  — full product/technical plan.

## Reproduce

```bash
make test          # offline python tests (+ omp adapter TS suite when present)
make reproduce     # comparison-only: rebuild in temporary storage and byte-diff
make check-docs    # full authored documents and generated metrics must match
# After an intentional source/template or operator-label change only:
make regenerate    # publish derived outputs and source-bound submission docs
make reproduce     # compare without changing published artifacts
```
""",
    'docs/writeup.md': """\
# Write-up

Status marker used throughout: **[SYNTHETIC DEV]** marks numbers from fixture
recordings; **[unmeasured]** marks claims we have not measured. No hypothesis
below is claimed as measured until real pilot recordings and live validation
runs exist (R30/R33).

## Problem

Agent swarms need the same immunity biology gives them: detect, freeze,
trace, contain — with containment measured as R, the average number of agents
each poisoned agent goes on to poison. The motivating incident and its
investigation are cited in the plan's Sources section
([docs/plans/2026-10-03-0413-feat-below-one-plan.md](plans/2026-10-03-0413-feat-below-one-plan.md),
"Sources / Research": swarmchasing.com logistics; METR investigation of the
OpenAI/Hugging Face incident) rather than restated here — specifics stay
attributed to the source, not paraphrased into claims we have not verified.

## Approach

One engine, two dials (KTD2): the same detection path steers everyday drift
and contains safety violations. Ground truth comes from the scenario manifest,
never from the checker (KTD4). Defenses are evaluated by replaying recorded
runs (KTD6 counterfactual semantics); a subset runs live with the defense
active, and the replay-vs-live gap is reported (R29).

## Results

{results.provenance.note}

{results.actual.note}

## Hypotheses (R30) — status

**Reading the table:** Supported/refuted means observed direction only, not
inferential hypothesis proof. No significance, noninferiority, or "far fewer"
margin was preregistered — in particular, any observed clean-freeze reduction
cannot by itself establish H2's "far fewer". N is the count of observed
eligible comparisons, never a target.

| # | Hypothesis | Status |
|---|---|---|
| H1 | Damage rises with detection delay | {hypotheses.h1.status} (N={hypotheses.h1.n}; source: {hypotheses.h1.source}; mode: {hypotheses.h1.mode}) — {hypotheses.h1.summary} |
| H2 | Verified tracing contains as well as blunt kill with far fewer clean agents frozen | {hypotheses.h2.status} (N={hypotheses.h2.n}; source: {hypotheses.h2.source}; mode: {hypotheses.h2.mode}) — {hypotheses.h2.summary} |
| H3 | Strict mode preserves more work than kill-all | {hypotheses.h3.status} (N={hypotheses.h3.n}; source: {hypotheses.h3.source}; mode: {hypotheses.h3.mode}) — {hypotheses.h3.summary} |
| H4 | The interview cuts false alarms | {hypotheses.h4.status} (N={hypotheses.h4.n}; source: {hypotheses.h4.source}; mode: {hypotheses.h4.mode}) — {hypotheses.h4.summary} |
| H5 | Prevention lowers R | {hypotheses.h5.status} (N={hypotheses.h5.n}; source: {hypotheses.h5.source}; mode: {hypotheses.h5.mode}) — {hypotheses.h5.summary} |
| H6 | Fast-checker confidence is/is not calibrated enough to set the radius | {hypotheses.h6.status} (N={hypotheses.h6.n}; source: {hypotheses.h6.source}; mode: {hypotheses.h6.mode}) — {hypotheses.h6.summary} |
| H7 | Below One cuts wasted spend versus prompt-only with few false steers | {hypotheses.h7.status} (N={hypotheses.h7.n}; source: {hypotheses.h7.source}; mode: {hypotheses.h7.mode}) — {hypotheses.h7.summary} |

## Replay versus live

The gap between replayed defenses and live-validated runs will be reported
per R29 once the ~5 live runs per arm (R33) exist. Nothing in this document
claims a live gap measurement.

## Failed hypotheses and negative results

{results.failed.note}
""",
    'docs/threat-model.md': """\
# Threat model

## What the engine sees

Below One's trust graph and detection stack observe **harness-mediated tool
calls**: writes, reads, messages, and their paths, delivered through the
oh-my-pi adapter into `Engine.record` with an explicit decision from the
engine before execution (fail-closed, KTD9).

## What the engine cannot see — and says so

1. **Shell / subprocess blind spot.** A tool call that spawns a shell can read
   or write anything the OS user can. The engine's path confinement applies to
   the paths declared in the tool call, not to arbitrary code execution that
   the tool performs. Any filesystem access performed *inside* a spawned
   process is invisible to the trust graph. This is a real gap, not a
   theoretical one; the capability table marks it per harness.
2. **Off-tool channels.** Network requests, background daemons, scheduled
   jobs, and direct IPC initiated by agent code bypass the engine entirely.
   Poison can leave traces the graph never sees.
3. **Boundary honesty.** Tools path-confinement and workspace resolution are
   best-effort lexical + resolved-path checks inside the adapter. They are NOT
   an OS sandbox. Claims of containment apply to harness-mediated actions
   only; an agent that escapes the tool interface is out of model.

## Trust and capability model

- The operator holds a **capability token** (>= 24 chars, generated at server
  start, never embedded in static assets, never placed in agent environment or
  model input). `/control` (freeze/release/kill/end/off) requires it as a
  Bearer credential; agents cannot revoke each other's tickets.
- Each agent receives an independent launch-issued Bearer capability.
  `/ready`, `/decide`, `/start`, `/record`, `/state/{agent_id}`, and native
  model/finish routes validate that capability for the named agent. Agent
  credentials never authorize operator controls or another agent.
- `/start` claims an action ticket immediately before tool delivery; operator
  controls revoke unclaimed tickets; a kill after a successful claim aborts
  in-flight tool work. Tracked file reads/writes hold a claim-through-receipt
  lease; failed or cancelled writes record observed or possible effects
  conservatively without claiming tool success. Shell and off-tool effects
  remain outside this guarantee.
- The locked spec file and run artifacts are protected resources: writing to
  them is itself a violation; run artifacts are sealed and verified
  (U2), and the public API allowlists only explicit artifact files.

## Ground truth integrity

Infection ground truth comes from the scenario manifest (KTD4), never from
the checker being judged. Replay pruning follows KTD6 counterfactual
semantics; provenance is conjunctive write/read chains with disjunctive
alternatives, derived from observed versions and infection sequence — not
from authored intent.
""",
    'docs/capability-table.md': """\
# Capability table

Evidence column states what is KNOWN about platform support today (documented
platform behavior); it is not a benchmark result.

| Requirement | oh-my-pi | Claude Code | Codex CLI | Below One harness |
|---|---|---|---|---|
| Per-action pre-approval hook (fail-closed) | yes — tool approval pipeline, engine decides | yes — permission prompts / hooks | yes — approval policy + sandbox policy | yes — Engine.decide before delivery |
| Declared paths per tool call | yes | yes | yes | yes |
| Agent identity per process | yes — one process per agent (U12 target) | single process, multiple sessions | single process, sandboxed tasks | one process per agent |
| Message gating between agents | yes — message tool visible | n/a (no inter-agent messaging) | n/a | yes — message precheck |
| Network access control | platform setting | platform setting | platform setting | out of engine scope (see threat model) |
| Shell/subprocess visibility | **no — blind spot** | **no — blind spot** | **no — blind spot** | **no — blind spot** |
| Off-tool channel visibility | **no** | **no** | **no** | **no** |
| TS adapter surface | yes — omp extensions are TypeScript (KTD1) | n/a | n/a | n/a |

Notes:
- "Blind spot" rows repeat the threat model: no harness exposes spawned-process
  filesystem activity to the engine; containment claims cover harness-mediated
  actions only.
- The adapter contract (U12): one oh-my-pi process per agent, every tool call
  preceded by `/decide`, results recorded via `/record`, operator `/control`
  with Bearer capability, `/start` ticket claimed immediately before tool
  delivery. Agent routes require independent agent-scoped Bearer capabilities;
  the operator capability is separate.
""",
    'docs/video-script.md': """\
# Video script — storyboard beats, clips, narration

{clips.provenance.note} Narration is prepared
text for Jollen (operator items: narration + final assembly). Total runtime
target: ~120s.

| # | Beat | Dur | Clip (exists) | On-screen | Narration (facts exercised in this repo) |
|---|---|---|---|---|---|
| 1 | Hook | 10s | [{clips.b1.file}](../clips/{clips.b1.file}) | live board, R strip | "This board watches a coding-agent swarm. Every action, every read, every write — and one number: R, how many agents each poisoned agent infects." |
| 2 | Two dials | 10s | [{clips.b2.file}](../clips/{clips.b2.file}) | README dials section (reuse close clip at assembly) | "Two dials, one engine. Stay on task steers everyday drift back. Contain outbreaks freezes, traces, and quarantines." |
| 3 | Locked rules | 15s | [{clips.b3.file}](../clips/{clips.b3.file}) | {clips.b3.onscreen} | {clips.b3.narration} |
| 4 | The catch | 15s | [{clips.b4.file}](../clips/{clips.b4.file}) | {clips.b4.onscreen} | {clips.b4.narration} |
| 5 | The race | 20s | [{clips.b5.file}](../clips/{clips.b5.file}) | {clips.b5.onscreen} | {clips.b5.narration} |
| 6 | Delay vs damage | 10s | [{clips.b6.file}](../clips/{clips.b6.file}) | epidemic, R bars, delay-damage SVGs | {clips.b6.narration} |
| 7 | Receipts | 10s | [{clips.b7.file}](../clips/{clips.b7.file}) | rendered outbreak receipt | "Every outbreak gets a receipt: patient zero, catching layer, containment, cost — each value bound to a metric key, or it says unmeasured." |
| 8 | Reproduce proof | 15s | [{clips.b8.file}](../clips/{clips.b8.file}) | actual `make reproduce` output | "And this is the honesty check: regenerate every number from the committed artifacts, offline, no keys — byte-identical or the build fails." |
| 9 | Everyday dial | 20s | [{clips.b9.file}](../clips/{clips.b9.file}) | {clips.b9.onscreen} | {clips.b9.narration} |
| 10 | Honest limits | 5s | [{clips.b10.file}](../clips/{clips.b10.file}) | threat-model blind-spot rows | "And the honest part: shells and off-tool channels are still blind spots. The threat model says so on the repo." |
| 11 | Close | 5s | [{clips.b2.file}](../clips/{clips.b2.file}) | repo README | "Below One. Keep your swarm's R below one." |

Continuation clip (beat 4 alternate, source-bound):
[hero-freeze-REAL-no-freeze-observed.webm](../clips/{clips.b4b.file}) — the
pilot-0 board after the catch window: no freeze occurs (containment
unmeasured, zero-infection denominators).

Total: 130s (target ~120s; trim beats 2/9 at assembly if needed — operator
call). Operator TODO: narration, assembly, final submission. REAL-suffixed
clips embed actual recorded pilot surfaces, reproduced output, and rendered
figures/receipts from the committed real archive; SYNTHETIC DEV names are
development fixtures only.

## Capture policy

Two server modes.

Fixture/development capture (SYNTHETIC DEV clips):

```bash
DISPLAY_ROOT=$(mktemp -d)   # fresh display outputs; archives stay read-only
uv run --frozen python scripts/make_capture_preps.py \
  --artifact-root experiments/committed/runs --display-root "$DISPLAY_ROOT"
uv run --frozen python scripts/serve_engine_smoke.py 8899 \
  --artifact-root experiments/committed/runs --display-root "$DISPLAY_ROOT"
# In another terminal (required beats fail nonzero):
uv run --frozen python scripts/capture_video.py --out clips
```

REAL capture over the committed live archive (empty display root so the
split panel states its unmeasured truthfully):

```bash
mkdir -p /tmp/empty-display
uv run --frozen python scripts/serve_engine_smoke.py 8899 \
  --artifact-root experiments/committed/live-runs --real \
  --display-root /tmp/empty-display
# In another terminal (required beats fail nonzero):
uv run --frozen python scripts/capture_video.py --out clips \
  --sources clips/sources.json
```

`clips/inventory.json` lists only clips produced by that invocation,
never old clips, and carries per-beat source_real/mode/run provenance.
Failed required beats or `make reproduce` fail the capture. Existing links
above are the current capture's outputs, not proof that a future capture
succeeded.
""",
}
