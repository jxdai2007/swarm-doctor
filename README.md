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

Native adapter preflight timed out; the real campaign used the in-process
harness. Quick-start commands describe the adapter contract, not verified
native compatibility. No further paid/native preflight is required for final
artifact reproduction.

## Measured results

See [docs/generated/README-metrics.md](docs/generated/README-metrics.md).
Historical numbers remain bound to immutable committed recordings. Pressure-wide
reproduction currently has unadmitted replay-semantic differences after the
pre-policy source correction; it is not promised as passing. The exercised,
network-disabled salvage proof is `python -m scripts.salvage_report --check`.
Final pressure campaign: complete paired seeds101/102 (N=2, six actual arms), scripted patient zero plus four real Kimi peers per run. Natural pressure100 is a separate five-real-actor calibration; failed103/104 are sealed and excluded. The confirmatory planned main study was not run.

Sealed injected-kimi-{101,102}-{no-defense,prompt-only,verify} metrics.json/summary.json record infection1, secondary0 and R0 in every arm. R uses the scripted source denominator, not natural emergence or demonstrated spread benefit. All six held-out graders failed (101 cases6/4/4;102 cases4/0/4). Verify froze two clean peers in101 and four in102 (six total), each on protected-test reads. Latest102 source freeze5.435s, containment5.325s, four clean freezes. Natural100 recorded zero infections/secondary and grader case4FAIL. 600 RULE-DERIVED labels (586 clean/14 violation) span seven eligible recordings:500 injected (seed101:251 + seed102:249) plus100 natural100, bound by label event_id prefixes. Jev overall n564 agreement0.714539/ECE0.187961; matched n359 Jev0.785515 versus judge0.821727. These are rule agreement, not human accuracy; historical frozen85 report unchanged. Cached-policy replay reevaluates scripted setup and may prevent P0; it is not this actual already-compromised comparison. Step A correction: a post-hoc, same-recording fixed-policy replay of the sealed verify runs removes the six protected-test READ freezes (clean peers 6→0; replay source-freeze unchanged at 4.3808s/3.9902s; actual 9.259s/5.435s is a separate recorded reference). Removed READs become uncertain fail-closed denies (no cached checker responses), not restored work — see experiments/committed/salvage-campaign/step-a.json and presentation/figures/step-a-protected-read-before-after.png.

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
# Actual-only salvage proof; no keys or cached organism-policy replay.
/tmp/below-one-live-20261004T0750Z/venv/bin/python -m scripts.salvage_report --check
/tmp/below-one-live-20261004T0750Z/venv/bin/python scripts/check_doc_numbers.py --metrics-glob /Users/jollenshoulddai/Desktop/below-one/experiments/derived/analysis.json
# Historical pressure-wide regeneration/admission is intentionally unchanged.
```

The document check uses the existing Desktop-derived historical source. Its default source is ignored by Git and absent from a fresh clone; this is not a portable default-command claim.
