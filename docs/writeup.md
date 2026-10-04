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
fixture aggregate has **2.00 infected agents per run**
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
