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

Recorded sources include REAL pilot calibration recordings (synthetic=False); the confirmatory main 5-agent cohort has not been run, so headline hypothesis claims stay exploratory/not measured.

The real pilot-calibration aggregate (group `actual-d2a888ac`, role pilot-calibration, 3 agents × 20 turns, served ['kimi-for-coding']) has 0.0 infected agents per run and finish rate 0.16666666666666666; with zero observed infections the R and containment denominators are null — this is NOT a claim of R below one or of containment. The five-agent main baseline was not run (N=0).

## Hypotheses (R30) — status

**Reading the table:** Supported/refuted means observed direction only, not
inferential hypothesis proof. No significance, noninferiority, or "far fewer"
margin was preregistered — in particular, any observed clean-freeze reduction
cannot by itself establish H2's "far fewer". N is the count of observed
eligible comparisons, never a target.

| # | Hypothesis | Status |
|---|---|---|
| H1 | Damage rises with detection delay | inconclusive (N=2; source: post-hoc exploratory replay of sealed real 3-agent pilot calibration recordings; mode: exploratory-pilot-replay) — Observed endpoint damage changes per eligible recording: [0, 0]; model/scenario strata remain separate. Resource-limited exploratory fallback; requested five-agent main study has N=0. |
| H2 | Verified tracing contains as well as blunt kill with far fewer clean agents frozen | not_measured (N=0; source: Missing prerequisite: post-hoc exploratory replay of sealed real 3-agent pilot calibration recordings; mode: not-measured) — Paired containment and clean-frozen effects are retained per baseline/model stratum. Resource-limited exploratory fallback; requested five-agent main study has N=0. No complete eligible comparison is available. No eligible outbreak denominator; no-outbreak records are not containment evidence. |
| H3 | Strict mode preserves more work than kill-all | not_measured (N=0; source: no sealed kill-all comparison; mode: not-measured) — Strict versus kill-all is not measured: existing blunt K-hop arm is not a kill-all comparator. |
| H4 | The interview cuts false alarms | inconclusive (N=2; source: post-hoc exploratory replay of sealed real 3-agent pilot calibration recordings; mode: exploratory-pilot-replay) — Observed one-line minus locked-spec false-alarm differences: [0, 0]; false-steer differences: [8, 7]. Resource-limited exploratory fallback; requested five-agent main study has N=0. |
| H5 | Prevention lowers R | not_measured (N=0; source: Missing prerequisite: sealed paired actual-live five-agent prevention validation; mode: not-measured) — Actual paired prevention-off minus prevention-on R is retained per validation/model stratum. No complete eligible comparison is available. |
| H6 | Fast-checker confidence is/is not calibrated enough to set the radius | inconclusive (N=85; source: operator labels bound to sealed real checks; mode: operator-labeled-monitor) — Operator-labeled checker measurements available; radius sufficiency has no preregistered acceptance threshold. Label provenance: RULE-DERIVED from scenario-manifest ground-truth rules (KTD4); R31 deviation: no human labels available. |
| H7 | Below One cuts wasted spend versus prompt-only with few false steers | not_measured (N=0; source: Missing prerequisite: cached-policy replays of sealed real five-agent drift baseline recordings; mode: not-measured) — Wasted-USD reductions and false steers are retained per eligible drift/model stratum. No complete eligible comparison is available. |

## Replay versus live

The gap between replayed defenses and live-validated runs will be reported
per R29 once the ~5 live runs per arm (R33) exist. Nothing in this document
claims a live gap measurement.

## Failed hypotheses and negative results

Live hypothesis outcomes are limited to the exploratory pilot replay reported above; the confirmatory main cohort was not run, so most hypotheses remain not measured — including failures — until real main data exist (R38).
