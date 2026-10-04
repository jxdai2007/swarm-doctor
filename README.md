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
uv run python -m belowone.cli interview \
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
