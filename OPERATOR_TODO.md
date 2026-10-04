# Operator TODO

## REAL pilot U15 sample — DONE (85 events) — NEXT: operator labeling

Sample provenance: sealed real recordings `experiments/committed/live-runs/pilot-0`
and `pilot-1` (both complete primary trajectories; held-out grader case4 FAILED
for both — recordings are legitimate, tasks were not completed). Pilot-2 is a
sealed partial (local Kimi request-window ceiling; 5 proposals/agent, no
finish) and is EXCLUDED. Observed sample: **85 unique events — clean 50
(pilot-0: 28, pilot-1: 22), drift 26 (8+18), violation 9 (4+5)** — the
eligible labeled pool is below the 150-event target; observed strata are
published honestly, no balancing or duplication.

IDs are FROZEN. Never regenerate `labels/sample.jsonl` now that labeling can
start. Old synthetic sample kept as `labels/sample-SYNTHETIC-DEV.jsonl`
(development evidence only).

Run commands with the working runtime environment (the repo `.venv` on
iCloud-synced Desktop stalls; the canonical run env is outside iCloud):

```bash
export UV_PROJECT_ENVIRONMENT=/tmp/below-one-live-20261004T0750Z/venv

# operator-only labeling (resumable; Ctrl-C saves progress)
uv run --frozen python -m belowone.eval.monitor label \
  --sample labels/sample.jsonl --labels labels/monitor_labels.jsonl

# publish the U15 monitor section after labeling, then reproduce + doc check
make regenerate LABELS=labels/monitor_labels.jsonl \
  MONITOR_CHECKS=experiments/derived/monitor-checks.json
make reproduce LABELS=labels/monitor_labels.jsonl \
  MONITOR_CHECKS=experiments/derived/monitor-checks.json
make check-docs
```

H6 stays explicitly unmeasured until operator labels exist (R31). If labels
change figures, re-run regenerate/reproduce/check-docs and recapture affected
clips.

## Operator TODO (current, ordered)

1. Label the 85-event sample (command above) — the only human-only science
   step. Labels enable the monitor accuracy/calibration evaluation; the
   confidence-vs-radius threshold is NOT automatically established by labels
   alone.
2. Regenerate/reproduce/check-docs (recapture only if labels change figures).
3. Narration + final video assembly (script: docs/video-script.md).
4. Make the repo public BEFORE submitting, with the tested branch as default
   (GitHub default `main` is setup-only; tested code/artifacts live on
   `feat/below-one`):
   `gh repo edit jxdai2007/below-one --default-branch feat/below-one --visibility public --accept-visibility-change-consequences`
5. Submission form: https://swarmchasing.com/logistics/ — before 5:00pm PT
   Sunday.

Credentials are DONE: KIMI_API_KEY and OPENROUTER_API_KEY are present and
first-hour checks PASS live; the OpenRouter key carries a native $15 usage
cap (key hardcap observed in campaign accounting — this is the key's spend
limit, NOT a statement about deposited credit balance). No further key setup
is needed. If actual adapter timeouts recur, the native omp operator login
step from the plan still applies.

## Capture (agent-run, real sources)

Clips are recaptured from the real pilot board pages with LIVE badges and
truthful "no freeze observed"/"steering untested" labels
(`clips/sources.json`). Synthetic-DEV fixture clips remain in `clips/` as
development evidence only and are never reused as real-catch footage.

```
export UV_PROJECT_ENVIRONMENT=/tmp/below-one-live-20261004T0750Z/venv
uv run --frozen python scripts/serve_engine_smoke.py 8899 \
  --artifact-root experiments/committed/live-runs
uv run --frozen python scripts/capture_video.py --out clips \
  --sources clips/sources.json
```

Require exit zero; review `clips/inventory.json` from that invocation before
using the clips. Prior clips never prove a new capture succeeded.
