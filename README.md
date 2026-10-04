# Swarm Doctor (formerly Below One)

**Stop the spread.**

The story scenes in the demo video are dramatized; every number shown comes from our real recorded runs.

> ## Try it in 2 minutes
>
> **Fastest, 30 seconds, no Claude or keys:** watch the doctor handle a simulated swarm.
>
> ```bash
> git clone https://github.com/jxdai2007/swarm-doctor && cd swarm-doctor && python3 try.py
> ```
>
> ![What python3 try.py prints: two bad agents quarantined, one steered back to task, the healthy one untouched](docs/img/try-demo.png)
>
> In Claude Code, add this marketplace, then install the plugin:
>
> ```text
> /plugin marketplace add jxdai2007/swarm-doctor
> /plugin install swarm-doctor@swarm-doctor
> ```
>
> Select **Install** in the plugin details panel. Restart Claude Code or run
> `/reload-plugins` if the commands are not visible.
>
> ```text
> /swarm-doctor:doctored-swarm
> /swarm-doctor:status
> /swarm-doctor:release
> ```
>
> No Claude Code? Clone and run the offline hook self-test; Python 3 standard
> library only, no keys, no pip, no model calls:
>
> ```bash
> git clone --branch feat/below-one https://github.com/jxdai2007/swarm-doctor.git
> cd swarm-doctor
> python3 plugins/swarm-doctor/scripts/selftest.py
> ```
>
> [Try the outbreak lab](examples/outbreak-lab/LAB.md): compare fresh disposable
> workspaces with and without the plugin. Live Claude sessions use your account
> and may incur usage charges; the offline self-test does not.

![Swarm Doctor demo: stop the spread](docs/img/hero.gif)

## What the plugin does

Interview first, then lock a goal, editable paths, never-touch paths, and a
pause-or-ask policy. Claude splits the work among 3–4 parallel subagents.
PreToolUse hooks block off-task edits, trip on secret-looking or forbidden
paths, and quarantine only the offending session/subagent. PostToolUse hooks
record reads and writes; a later reader of a quarantined writer's file goes
on **watch**, not automatically into quarantine.

The plugin is new. **We have not run the live Claude Code lab ourselves yet.**
Verification pipes synthetic hook JSON into the real Python entry point in
temporary projects. Research results below are not plugin benchmarks.

### Local CLI
Python 3.9+ on macOS/Linux (`fcntl` locking); no Python packages required.
The optional launcher requires authenticated Claude Code **2.1.259+** for
`--permission-prompts none`. Installing the plugin does not run the launcher.


Run from the project you want to protect (or set `CLAUDE_PROJECT_DIR`):

```bash
DOCTOR=/absolute/path/to/swarm-doctor/plugins/swarm-doctor/scripts/doctor.py
python3 "$DOCTOR" lock --goal 'Implement CSV export' \
  --scope 'reports/**,tests/test_export*.py' \
  --never 'tests/test_reports.py' --on-trip pause --decoy yes
python3 "$DOCTOR" status
python3 "$DOCTOR" report
python3 "$DOCTOR" release --all
# Optional live launcher; uses your Claude account, not executed in our checks:
python3 "$DOCTOR" swarm --agents 3 'Implement CSV export without changing protected tests'
```

State and timestamped events live in `<project>/.swarm-doctor/`. Without
`spec.json`, hooks do nothing. A decoy is created only if `.env.production`
does not exist, contains obviously fake values, and is added to `.gitignore`.
Never-touch paths are **read/write tripwires** in this plugin; an attempted
protected edit is both blocked and counted. `ask` requests approval for the
first trip, but the session remains quarantined until release. The headless
launcher uses `dontAsk`, so requests requiring approval are denied rather
than bypassed.

This is a tool-hook guard, **not a sandbox or proof of infection**. Bash path
scanning is heuristic: dynamically constructed paths, subprocess file access,
and off-tool communication may be invisible. Do not grant untrusted agents
real secrets. Hooks must be enabled; another plugin's behavior and filesystem
access outside Claude's tools remain outside this boundary.

Built-in Grep searches are checked against candidate filenames, not regex
matches; broad searches can trip on protected files even when content would
not match. This conservative scan includes ignored files and does not narrow
by Grep's type filter. Negated and brace globs are rejected; use separate positive globs.

## Recorded research results

Source: our **5-agent research harness runs: 1 scripted compromised source +
4 real Kimi agents**. The product-name charts describe the research engine,
not this newly shipped Claude Code plugin.

| Measure | Recorded result | Interpretation |
|---|---|---|
| Outbreaks stopped | **2 of 2** with containment vs **0 of 5** without containment | “Stopped” means the already-compromised source was contained; not demonstrated prevention of secondary spread. Five is a descriptive archive count: four complete no-defense/prompt-only recordings plus provider-failed seed103; not an eligible five-run study denominator. |
| Poisoned agent loose | **5–9 seconds** vs **6–9 minutes** without containment | Rounded recorded source-freeze times, 5.435s and 9.259s; comparison agents were never contained, so minutes describe observed source-actor activity windows, not time to eventual containment. |
| Healthy agents frozen | **0** in corrected-policy **replay of recorded runs** | Posthoc replay, not a new live result. Original live runs froze 6 healthy agents; no replacement turns were generated. |
| Doctor overhead | **<½¢ per run** | Recorded defense API cost: $0.004654104 and $0.003587766. Excludes agent subscription usage and total campaign costs; not a Claude plugin cost estimate. |

![Recorded source containment times](docs/img/chart-time.png)
![Recorded containment counts](docs/img/chart-outbreaks.png)
![Posthoc replay healthy-agent freezes](docs/img/chart-frozen.png)
![Recorded research defense cost](docs/img/chart-cost.png)

Evidence: [sealed pressure campaign](experiments/committed/pressure-campaign/),
[posthoc replay receipt](experiments/committed/salvage-campaign/step-a.json),
and [historical metrics](docs/generated/README-metrics.md). Both complete
paired seeds 101/102 recorded **zero secondary infections in every arm**.
All six held-out graders failed. These observations demonstrate source
containment, not successful task completion or a measured reduction in spread.
Incomplete provider failures remain excluded from scientific denominators.

The “0 of 5” archive/chart count includes seeds 101/102 no-defense and
prompt-only (four complete Kimi recordings) plus seed103 no-defense
(provider-failed, excluded from the study). Restricting to complete paired
comparisons gives **0 of 4**. It is not five independent matched baselines.

## Capabilities: plugin vs research harness

| Capability | Claude Code plugin today | Research harness only |
|---|---|---|
| Goal and file-scope lock | Local interview and JSON spec | Interviewed goal contracts |
| Off-task edits | Deterministic path-based blocking and steer message | Semantic progress evaluation |
| Secret/decoy and never-touch tripwires | PreToolUse deny/ask and actor quarantine | Verified violation escalation |
| Parallel workers | Command prompts 3–4 subagents; optional headless launcher | Controlled five-actor recorded runs |
| Cross-agent tracing | Timestamped observed write → later read; taint/watch heuristic | Directional evidence and reached-agent verification |
| Release and report | Local commands, session counts and event log | Freeze scheduling and scientific metrics |
| Model-backed checker/judge | No model calls; Python standard library | Recorded Kimi/OpenRouter responses and offline replay |
| Live visual board | Coming soon | Existing historical research dashboard |
| Validation | Offline real-script hook tests; live Claude lab **not run** | Recorded research runs; limitations documented below |

## Roadmap

`/swarm-doctor:board` — live visual board **coming soon**. Not a shipped command.

## Claude Code format references

Formats checked against official documentation before implementation:
[plugin manifests](https://code.claude.com/docs/en/plugins/manifest-reference),
[components and commands](https://code.claude.com/docs/en/plugins/components),
[marketplaces](https://code.claude.com/docs/en/plugins/create-marketplace),
[installation](https://code.claude.com/docs/en/plugins/install),
[hooks guide](https://code.claude.com/docs/en/hooks-guide),
[hook JSON](https://code.claude.com/docs/en/hooks), and
[CLI flags](https://code.claude.com/docs/en/cli-reference).

## Historical research engine (oh-my-pi)

The original Below One engine is retained for research reproduction.
The following instructions concern that engine, **not plugin installation**.

### Quick start (oh-my-pi swarm)

```bash
# Historical research dependencies; unnecessary for the Claude Code plugin:
git clone --branch feat/below-one https://github.com/jxdai2007/swarm-doctor.git
cd swarm-doctor
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
