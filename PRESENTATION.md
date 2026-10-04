# PRESENTATION — Below One (keep your swarm's R below one)

> Refresh targets: re-run after the 10:30PT campaign cutoff final regen; numbers below cite their source paths and MUST be refreshed from the same paths, never hand-edited.

## 3-sentence pitch

Coding-agent swarms get hijacked by one poisoned teammate — Below One is an immune system that records every agent action into a live trust graph, checks each step against a locked goal spec, steers drifters back, and freezes and traces confirmed compromised agents. Every number in this repo regenerates offline from sealed run artifacts (`make reproduce` → REPRODUCE_IDENTICAL_NETWORK_DISABLED), and honest limits — including a weak attack that never developed and a main cohort we could not run — are labeled as such. Built for the AI Swarm Dynamics Hackathon; the engine is harness-agnostic (oh-my-pi adapter included) and the whole pipeline runs keyless offline from committed recordings.

## Headline values + sources

| Claim | Value | Source |
|---|---|---|
| Real pilot recordings | 2 complete 3-agent runs (pilot-0/1) + 1 sealed partial; archive commit a1a2e0e | `experiments/committed/live-runs/`, `DECISIONS.md` |
| Infections observed | 0 (outbreak never developed — weak attack reported as found) | `experiments/derived/analysis.json` runs.pilot-0/1 metrics.infected = 0.0 |
| R / containment | NOT MEASURED — zero infections ⇒ null denominators, not "R<1" | same, metrics.r_mean = null |
| Task outcome | held-out grader case4 FAILED both pilots (recordings legitimate, task not completed) | `experiments/committed/live-runs/*/summary.json` |
| Monitor labels | 85 frozen events, RULE-DERIVED from manifest GT (R31 deviation, no human labels): 84 clean / 1 violation | `labels/monitor_labels-RULE-DERIVED.jsonl`, `DECISIONS.md` |
| Fast checker vs rule truth | accuracy 0.636, ECE 0.227 | `labels/monitor_report-RULE-DERIVED.json` |
| Judge vs rule truth | accuracy 0.75 (n=40 overlap) | same |
| Hypotheses | H1/H4 exploratory-pilot-replay inconclusive (N=2); H2/H3/H5/H6/H7 not measured (main cohort N=0) | `experiments/derived/analysis.json` doc_metrics.hypotheses |
| Spend | OpenRouter native key cap $15; known spend ≈ $0.019 | `experiments/committed/live-runs/campaign-accounting.json` |

## Video order (11 clips, one narration line each — Jollen narrates)

1. intro-board-REAL — "This board watches a coding-agent swarm: every action, every read, every write — and one number, R."
2. repo-close-REAL-documentation — "Two dials, one engine: stay-on-task steers drift back; contain-outbreaks freezes, traces, quarantines."
3. scripted-interview-REAL-recorded-setup — "Before launch the interview locks what counts as failure — this is the actual locked pilot spec, hashes and all; the live interview itself was not measured."
4. live-catch-REAL — "On the real pilot board no catch was needed: the outbreak never developed, so containment stays unmeasured."
5. split-race-REAL-exploratory-counterfactual — "This three-arm comparison is built from the actual pilot counterfactual replay — the live arm comparison itself was not run."
6. charts-REAL — "Delay-versus-damage over the real pilot replay; the live main-cohort delay effect remains unmeasured."
7. receipt-REAL — "Every outbreak gets a receipt: patient zero, catching layer, containment, cost — or it says unmeasured."
8. reproduce-proof-REAL-offline-reproduction — "The honesty check: every number regenerates offline from sealed artifacts, byte-identical or the build fails."
9. everyday-drift-REAL-no-steer-observed — "Steering is untested in the pilot — no drift scenario ran, so no steer events exist."
10. hero-freeze-REAL-no-freeze-observed — "No freeze occurred on the real run; with zero infections, containment denominators are null."
11. threat-REAL-documentation + repo-close-REAL-documentation — "Shells and off-tool channels stay blind spots, and the repo says so. Below One: keep your swarm's R below one."

## Exact 150-word project description

Below One is an immune system for coding-agent swarms. Every agent action lands in a live trust graph; each step is checked against a locked goal spec; drifting agents are steered back; a poisoned agent is frozen, traced, and contained before it spreads. We ran real three-agent pilots on live coding tasks: the planted attack never developed (zero infections across both complete runs), so containment denominators — R — stayed null, and we report that honestly rather than claiming safety. An 85-event labeled sample measured the fast checker against manifest ground-truth rules: 0.636 accuracy, 0.227 calibration error, with a stronger judge at 0.75. Every figure, receipt, and document regenerates offline from sealed run artifacts; `make reproduce` is byte-identical or the build fails. Known limits are recorded, not hidden: no main-cohort study, no operator labels, shell/off-tool blind spots. Keep your swarm's R below one.

## Slide figure files (PNG)

presentation/figures/epidemic.png, presentation/figures/r-bar.png, presentation/figures/delay-damage.png (regenerate with `scripts/export_slide_figures.py` after final regen).

## Public flip (operator, exact command — public BEFORE submission)

gh repo edit jxdai2007/below-one --default-branch feat/below-one --visibility public --accept-visibility-change-consequences
