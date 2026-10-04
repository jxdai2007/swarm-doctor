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
        rendered = _fill(template, metrics) + _dynamic_section(metrics)
        path = out_dir / name
        path.write_text(rendered)
        written.append(path)
    return written


def render_authored(metrics: dict) -> dict[str, str]:
    """Render complete authored documents, not selected number-like spans."""
    return {name: _fill(template, metrics)
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
# 0. prerequisites
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
`make reproduce` — no network, no API keys. Current committed artifacts are
**SYNTHETIC DEV fixtures**; live-model results are added only when real pilot
recordings exist (R33).

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

## Results — [unmeasured] / [SYNTHETIC DEV]

Current committed artifacts are synthetic fixture recordings
(`experiments/committed/`). The metric values they produce are visible in
[docs/generated/README-metrics.md](generated/README-metrics.md) and the
outbreak receipts; they demonstrate that the pipeline computes and binds
numbers, and are NOT evidence about real model swarms. The no-defense
fixture aggregate has **{no-defense.metrics.infected} infected agents per run**
([SYNTHETIC DEV]; source: `analysis.json/doc_metrics/no-defense/metrics.infected`).

## Hypotheses (R30) — status

| # | Hypothesis | Status |
|---|---|---|
| H1 | Damage rises with detection delay | [unmeasured] — delay sweep implemented; awaits pilot recordings |
| H2 | Verified tracing contains as well as blunt kill with far fewer clean agents frozen | [unmeasured] |
| H3 | Strict mode preserves more work than kill-all | [unmeasured] |
| H4 | The interview cuts false alarms | [unmeasured] — ablation implemented on decision caches |
| H5 | Prevention lowers R | [unmeasured] |
| H6 | Fast-checker confidence is/is not calibrated enough to set the radius | [unmeasured] — calibration analysis implemented; needs operator labels |
| H7 | Below One cuts wasted spend versus prompt-only with few false steers | [unmeasured] |

## Replay versus live

The gap between replayed defenses and live-validated runs will be reported
per R29 once the ~5 live runs per arm (R33) exist. Nothing in this document
claims a live gap measurement.

## Failed hypotheses and negative results

No live hypothesis outcomes have been measured yet; the synthetic checks in
this repo are development verification, not scientific results. Measured
outcomes — including failures — will be reported here as real pilot and live
data land (R38).
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

Every clip linked below exists in `clips/` and is a SYNTHETIC DEV development
recording (fixture replays, real engine + real CLI output). No narration line
states a measured scientific outcome: measured claims are conditional on real
pilot data (R30/R33) and render from metrics files. Narration is prepared
text for Jollen (operator items: narration + final assembly). Total runtime
target: ~120s.

| # | Beat | Dur | Clip (exists) | On-screen | Narration (facts exercised in this repo) |
|---|---|---|---|---|---|
| 1 | Hook | 10s | [intro-board-SYNTHETIC-DEV.webm](../clips/intro-board-SYNTHETIC-DEV.webm) | live board, R strip | "This board watches a coding-agent swarm. Every action, every read, every write — and one number: R, how many agents each poisoned agent infects." |
| 2 | Two dials | 10s | [repo-close-SYNTHETIC-DEV.webm](../clips/repo-close-SYNTHETIC-DEV.webm) | README dials section (reuse close clip at assembly) | "Two dials, one engine. Stay on task steers everyday drift back. Contain outbreaks freezes, traces, and quarantines." |
| 3 | Locked rules | 15s | [scripted-interview-SYNTHETIC-DEV.webm](../clips/scripted-interview-SYNTHETIC-DEV.webm) | actual interview CLI output | "Before launch, the interview locks what counts as failure — this is the real CLI: typed questions, recommendations, and a locked spec hash." |
| 4 | The catch | 15s | [live-catch-SYNTHETIC-DEV.webm](../clips/live-catch-SYNTHETIC-DEV.webm) | live graph, frozen ring, counters | "Here the fixture patient zero is frozen. The board shows the retained causal trace from the recorded write/read chain; these are synthetic-development events, not live scientific outcomes." |
| 5 | The race | 20s | [split-race-SYNTHETIC-DEV.webm](../clips/split-race-SYNTHETIC-DEV.webm) | three-arm split, same since-poisoning clock | "Same fixture, three arms, one clock. The counters come from the same evaluator that produces the receipts — no hand-typed numbers." |
| 6 | Delay vs damage | 10s | [charts-SYNTHETIC-DEV.webm](../clips/charts-SYNTHETIC-DEV.webm) | epidemic, R bars, delay-damage SVGs | "These figures regenerate offline from committed artifacts. What they will show about real delay is unmeasured until pilot runs — the sweep machinery is already exercised here." |
| 7 | Receipts | 10s | [receipt-SYNTHETIC-DEV.webm](../clips/receipt-SYNTHETIC-DEV.webm) | rendered outbreak receipt | "Every outbreak gets a receipt: patient zero, catching layer, containment, cost — each value bound to a metric key, or it says unmeasured." |
| 8 | Reproduce proof | 15s | [reproduce-proof-SYNTHETIC-DEV.webm](../clips/reproduce-proof-SYNTHETIC-DEV.webm) | actual `make reproduce` output | "And this is the honesty check: regenerate every number from the committed artifacts, offline, no keys — byte-identical or the build fails." |
| 9 | Everyday dial | 20s | [everyday-drift-SYNTHETIC-DEV.webm](../clips/everyday-drift-SYNTHETIC-DEV.webm) | full recorded drift task: steering chronology in the replayed event log (8 steer records; scrub the timeline slider), then clean completion | "Everyday use is the other dial: the checker steers an agent back from an off-scope refactor — the replayed event log shows that chronology, and the recorded run still finishes the task. Wasted spend and false steers are counted per run." |
| 10 | Honest limits | 5s | [threat-SYNTHETIC-DEV.webm](../clips/threat-SYNTHETIC-DEV.webm) | threat-model blind-spot rows | "And the honest part: shells and off-tool channels are still blind spots. The threat model says so on the repo." |
| 11 | Close | 5s | [repo-close-SYNTHETIC-DEV.webm](../clips/repo-close-SYNTHETIC-DEV.webm) | repo README | "Below One. Keep your swarm's R below one." |

Total: 130s (target ~120s; trim beats 2/9 at assembly if needed — operator
call). Operator TODO: narration, assembly, final submission. Clips for beats
3/6/7/8 embed ACTUAL captured command output (interview CLI, make reproduce)
and ACTUAL rendered figures/receipts from committed artifacts; all labeled
SYNTHETIC DEV.

## Capture policy

Start the fixture display server separately from the native live demo:

```bash
DISPLAY_ROOT=$(mktemp -d)   # fresh display outputs; archives stay read-only
uv run --frozen python scripts/make_capture_preps.py \\
  --artifact-root experiments/committed/runs --display-root "$DISPLAY_ROOT"
uv run --frozen python scripts/serve_engine_smoke.py 8899 \\
  --artifact-root experiments/committed/runs --display-root "$DISPLAY_ROOT"
# In another terminal (required beats fail nonzero):
uv run --frozen python scripts/capture_video.py --out clips
```

`clips/inventory.json` lists only clips produced by that invocation,
never old clips. Failed required beats or `make reproduce` fail the capture.
Split, hero-freeze, and everyday-drift are optional only when their source
snapshots are absent (HTTP 404); when present, capture failures are fatal.
Skipped optional beats need operator omission from final assembly, not an
outbreak clip relabeled as drift. Existing links above are prior development
clips, not proof that a new capture succeeded.
""",
}
