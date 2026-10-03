# Threat model

## What the engine sees

Below One's trust graph and detection stack observe **harness-mediated tool
calls**: writes, reads, messages, and their paths, delivered through the
oh-my-pi adapter into `Engine.record` with an explicit decision from the
engine before execution (fail-closed, KTD9).

## What the engine cannot see — and says so

1. **Shell / subprocess blind spot.** A tool call that spawns a shell can read
   or write anything the OS user can. The engine's path confinement applies to
   the paths declared in the tool call, not to arbitrary code execution that
   the tool performs. Any filesystem access performed *inside* a spawned
   process is invisible to the trust graph. This is a real gap, not a
   theoretical one; the capability table marks it per harness.
2. **Off-tool channels.** Network requests, background daemons, scheduled
   jobs, and direct IPC initiated by agent code bypass the engine entirely.
   Poison can leave traces the graph never sees.
3. **Boundary honesty.** Tools path-confinement and workspace resolution are
   best-effort lexical + resolved-path checks inside the adapter. They are NOT
   an OS sandbox. Claims of containment apply to harness-mediated actions
   only; an agent that escapes the tool interface is out of model.

## Trust and capability model

- The operator holds a **capability token** (>= 24 chars, generated at server
  start, never embedded in static assets, never placed in agent environment or
  model input). `/control` (freeze/release/kill/end/off) requires it as a
  Bearer credential; agents cannot revoke each other's tickets.
- `/start` claims an action ticket immediately before tool delivery; operator
  controls revoke unclaimed tickets; a kill after a successful claim aborts
  in-flight tool work **best effort** — a tool already past the delivery
  boundary may complete outside engine sight.
- The locked spec file and run artifacts are protected resources: writing to
  them is itself a violation; run artifacts are sealed and verified
  (U2), and the public API allowlists only explicit artifact files.

## Ground truth integrity

Infection ground truth comes from the scenario manifest (KTD4), never from
the checker being judged. Replay pruning follows KTD6 counterfactual
semantics; provenance is conjunctive write/read chains with disjunctive
alternatives, derived from observed versions and infection sequence — not
from authored intent.
