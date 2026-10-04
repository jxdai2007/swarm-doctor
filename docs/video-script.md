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
uv run --frozen python scripts/make_capture_preps.py \
  --artifact-root experiments/committed/runs --display-root "$DISPLAY_ROOT"
uv run --frozen python scripts/serve_engine_smoke.py 8899 \
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
