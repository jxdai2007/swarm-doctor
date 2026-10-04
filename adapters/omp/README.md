# oh-my-pi adapter (U12)

One real [oh-my-pi v18.5.0](https://github.com/can1357/oh-my-pi/releases/tag/v18.5.0)
process per agent, driven through the Below One engine. `omp --version` must
report `omp/18.5.0`; the unrelated npm package `omp@1.0.0` is not this runtime.
Every tool call passes `POST /decide`, then awaited `POST /start`, then
`POST /record`. File read/write claims remain leased through their observed or
conservative possible-effects receipt, including failed or killed writes.
`POST /ready` drains contact reviews before the next model turn.

On POSIX, freeze suspends the actual process and its external descendants with
`SIGSTOP`; clean release resumes the same process and conversation with
`SIGCONT`. Freeze does not call omp's terminal `ctx.abort()`. Kill terminates
the process tree, settles outstanding claims with `ok: false`, and never resumes.
Read selectors follow v18.5.0's literal-file preference: an existing `file:1`
is that literal file; otherwise supported selectors map to the canonical
workspace resource before the gate. Unknown selectors, missing/fuzzy targets,
symlink paths, URL targets, and locked-spec access fail closed.
The extension re-registers `read` using v18.5.0's public same-tool
`ctx.invokeTool` contract: ordinary filenames delegate to the original native
reader (not the registered wrapper), keeping its rendering and bookkeeping.
Existing literal-colon resources are read directly from their canonical claimed
file, so `archive.zip:member` cannot silently open a different archive member.
This bounded branch supports regular UTF-8 text up to 4 MiB, `:raw`, line/count/
tail/multi-range selectors, and native text defaults (300-line head, 3000-line
cap, at least 50 KiB byte budget, 512-byte non-raw columns and 1/3-line context).
Binary/control-byte text, directories, and `:img`/`:conflicts` rendering fail
explicitly; use an ordinary filename for those native features. It does not
create aliases, snapshots, file copies, or hashline edit anchors for literals.
Protected-path policy is evaluated before any literal content is read.

Launcher issues a separate per-agent Bearer capability through
`BELOW_ONE_AGENT_TOKEN`; extension transport and the local model bridge use that
same capability. Operator control token and real provider keys stay server-side.
Held-out completion grading uses its full trusted deadline; only the native
finish HTTP request allows 59 seconds (ordinary engine requests remain 30).
Launch always runs a final trusted regrade, regardless of agent finish claims.

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

Watch the **actual launch board** while the run is live: open the exact
`BOARD_URL=http://127.0.0.1:<assigned-port>/` printed by this launcher. That
server owns the running agents' engine, SSE feed, and snapshots; do not start
`serve_engine_smoke.py`, which creates a different engine. Each seed prints its
own URL and closes that server after cleanup. Split display artifacts come from
the separate `experiments/display` root, never mutable run archives.
Runtime stdout stays in each run's `runtime/<agent>.stdout.txt` as native JSON
print-mode events; stderr remains separate. The transcript records actual
tool-result success/error status, not just proposed engine paths. A blocked tool
may still emit a synthetic execution-start event, so that event alone does not
prove tool execution.

## Tests

```bash
uv run --frozen pytest -q tests/test_omp_launch.py   # python side
bun test adapters/omp/test/below-one.test.ts           # adapter TS side
```

Run these checks sequentially, not alongside another native suite. Python
regressions prove real-process freeze/clean-release conversation preservation,
protected selector denial (including a literal colon file), and external-child
PID termination with no post-kill marker. Kill startup has a separate 90-second
budget; observed termination must complete within 5 seconds. All scripted
provider responses remain explicitly synthetic development evidence.

Runtime contracts checked against primary source:
[print lifecycle](https://github.com/can1357/oh-my-pi/blob/v18.5.0/packages/coding-agent/src/modes/print-mode.ts),
[extension hooks](https://github.com/can1357/oh-my-pi/blob/v18.5.0/packages/coding-agent/src/extensibility/extensions/wrapper.ts),
[literal preference](https://github.com/can1357/oh-my-pi/blob/v18.5.0/packages/coding-agent/src/tools/path-utils.ts),
[same-tool native delegation](https://github.com/can1357/oh-my-pi/blob/v18.5.0/packages/coding-agent/src/extensibility/extensions/runner.ts),
and [selector grammar](https://github.com/can1357/oh-my-pi/blob/v18.5.0/packages/tui/src/tools/read.ts).
