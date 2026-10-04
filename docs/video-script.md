# Video script — storyboard beats, clips, narration

Clips below were captured from the real canonical archive (provenance per clips/inventory.json): REAL-suffixed beats show actual recorded pilot surfaces or truthful absence/unmeasured panels; SYNTHETIC DEV names are development fixtures only. Narration is prepared
text for Jollen (operator items: narration + final assembly). Total runtime
target: ~120s.

| # | Beat | Dur | Clip (exists) | On-screen | Narration (facts exercised in this repo) |
|---|---|---|---|---|---|
| 1 | Hook | 10s | [intro-board-REAL.webm](../clips/intro-board-REAL.webm) | live board, R strip | "This board watches a coding-agent swarm. Every action, every read, every write — and one number: R, how many agents each poisoned agent infects." |
| 2 | Two dials | 10s | [repo-close-REAL-documentation.webm](../clips/repo-close-REAL-documentation.webm) | README dials section (reuse close clip at assembly) | "Two dials, one engine. Stay on task steers everyday drift back. Contain outbreaks freezes, traces, and quarantines." |
| 3 | Locked rules | 15s | [scripted-interview-REAL-recorded-setup.webm](../clips/scripted-interview-REAL-recorded-setup.webm) | recorded locked setup spec — pilot-0 (both spec envelopes, hashes shown) | "Before launch the interview locks what counts as failure. The live interview itself was not measured — this is the actual locked pilot-0 spec, hash and all, walked through verbatim." |
| 4 | The catch | 15s | [live-catch-REAL.webm](../clips/live-catch-REAL.webm) | live pilot board, real agent counters (no freeze occurred) | "Here is the real pilot board. No defense catch is observed: the outbreak never developed (zero secondary infections), so containment is unmeasured — these are actual recorded events, not a staged catch." |
| 5 | The race | 20s | [split-race-REAL-exploratory-counterfactual.webm](../clips/split-race-REAL-exploratory-counterfactual.webm) | three-column exploratory counterfactual comparison from the actual pilot recordings (N=2, agents 3) | "This comparison is built from the actual pilot counterfactual replay (N=2, 3 agents). The live arm comparison itself was not run: the five-agent main study has N=0, so this stays exploratory, never a live three-arm result." |
| 6 | Delay vs damage | 10s | [charts-REAL.webm](../clips/charts-REAL.webm) | epidemic, R bars, delay-damage SVGs | "Delay-versus-damage figures replayed over the real pilot recordings. The live main-cohort delay effect remains unmeasured (main study N=0)." |
| 7 | Receipts | 10s | [receipt-REAL.webm](../clips/receipt-REAL.webm) | rendered outbreak receipt | "Every outbreak gets a receipt: patient zero, catching layer, containment, cost — each value bound to a metric key, or it says unmeasured." |
| 8 | Reproduce proof | 15s | [reproduce-proof-REAL-offline-reproduction.webm](../clips/reproduce-proof-REAL-offline-reproduction.webm) | actual `make reproduce` output | "And this is the honesty check: regenerate every number from the committed artifacts, offline, no keys — byte-identical or the build fails." |
| 9 | Everyday dial | 20s | [everyday-drift-REAL-no-steer-observed.webm](../clips/everyday-drift-REAL-no-steer-observed.webm) | steering untested in the pilot — no drift scenario ran; shared-task completion was not achieved (held-out grader case4 failed) | "Steering is untested here: no drift scenario ran in the pilot, so no steer events exist. Wasted-spend versus prompt-only remains not measured." |
| 10 | Honest limits | 5s | [threat-REAL-documentation.webm](../clips/threat-REAL-documentation.webm) | threat-model blind-spot rows | "And the honest part: shells and off-tool channels are still blind spots. The threat model says so on the repo." |
| 11 | Close | 5s | [repo-close-REAL-documentation.webm](../clips/repo-close-REAL-documentation.webm) | repo README | "Below One. Keep your swarm's R below one." |

Continuation clip (beat 4 alternate, source-bound):
[hero-freeze-REAL-no-freeze-observed.webm](../clips/hero-freeze-REAL-no-freeze-observed.webm) — the
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
uv run --frozen python scripts/make_capture_preps.py   --artifact-root experiments/committed/runs --display-root "$DISPLAY_ROOT"
uv run --frozen python scripts/serve_engine_smoke.py 8899   --artifact-root experiments/committed/runs --display-root "$DISPLAY_ROOT"
# In another terminal (required beats fail nonzero):
uv run --frozen python scripts/capture_video.py --out clips
```

REAL capture over the committed live archive (empty display root so the
split panel states its unmeasured truthfully):

```bash
mkdir -p /tmp/empty-display
uv run --frozen python scripts/serve_engine_smoke.py 8899   --artifact-root experiments/committed/live-runs --real   --display-root /tmp/empty-display
# In another terminal (required beats fail nonzero):
uv run --frozen python scripts/capture_video.py --out clips   --sources clips/sources.json
```

`clips/inventory.json` lists only clips produced by that invocation,
never old clips, and carries per-beat source_real/mode/run provenance.
Failed required beats or `make reproduce` fail the capture. Existing links
above are the current capture's outputs, not proof that a future capture
succeeded.
