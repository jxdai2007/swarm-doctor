# oh-my-pi adapter (U12)

One real oh-my-pi process per agent, driven through the Below One engine:
every tool call is preceded by `POST /decide`, delivered only after
`POST /start` claims the action ticket, recorded via `POST /record`, and the
turn is gated by `POST /ready` (drains pending traces before the next model
turn). SSE `freeze`/`kill` abort in-flight turns. Provider keys stay
server-side: omp processes talk to a local OpenAI-compatible bridge that
routes to the shared U4 clients.

## Synthetic development mode

```bash
uv run --frozen python -m adapters.omp.launch \
  --synthetic \
  --out /tmp/belowone-u12/runs \
  --workspaces /tmp/belowone-u12/workspaces \
  --commit "$(git rev-parse HEAD)" \
  --seeds 0 1 2 --agents 3
```

`--synthetic` runs the actual omp processes against scripted HTTP responses
explicitly labeled SYNTHETIC DEV — development verification only, never live
evidence. Live mode requires the operator keys in `.env`
(KIMI_API_KEY / OPENROUTER_API_KEY) and is blocked until they exist.

Watch the board while a run is live:
`python scripts/serve_engine_smoke.py 8899` then open
`http://127.0.0.1:8899/?run=live`.

## Tests

```bash
uv run --frozen pytest -q tests/test_omp_launch.py   # python side
cd adapters/omp && bun test                          # adapter TS side
```

Honesty markers: R23 — an unknown resource is uncertainty, never infection
evidence; a native `bash` kill aborts after at most one query; frozen agents
record truthfully (planted decoys are never delivered as PASS).
