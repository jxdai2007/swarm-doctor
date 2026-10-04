# Capability table

Evidence column states what is KNOWN about platform support today (documented
platform behavior); it is not a benchmark result.

| Requirement | oh-my-pi | Claude Code | Codex CLI | Below One harness |
|---|---|---|---|---|
| Per-action pre-approval hook (fail-closed) | yes — tool approval pipeline, engine decides | yes — permission prompts / hooks | yes — approval policy + sandbox policy | yes — Engine.decide before delivery |
| Declared paths per tool call | yes | yes | yes | yes |
| Agent identity per process | yes — one process per agent (U12 target) | single process, multiple sessions | single process, sandboxed tasks | one process per agent |
| Message gating between agents | yes — message tool visible | n/a (no inter-agent messaging) | n/a | yes — message precheck |
| Network access control | platform setting | platform setting | platform setting | out of engine scope (see threat model) |
| Shell/subprocess visibility | **no — blind spot** | **no — blind spot** | **no — blind spot** | **no — blind spot** |
| Off-tool channel visibility | **no** | **no** | **no** | **no** |
| TS adapter surface | yes — omp extensions are TypeScript (KTD1) | n/a | n/a | n/a |

Notes:
- "Blind spot" rows repeat the threat model: no harness exposes spawned-process
  filesystem activity to the engine; containment claims cover harness-mediated
  actions only.
- The adapter contract (U12): one oh-my-pi process per agent, every tool call
  preceded by `/decide`, results recorded via `/record`, operator `/control`
  with Bearer capability, `/start` ticket claimed immediately before tool
  delivery. Agent routes require independent agent-scoped Bearer capabilities;
  the operator capability is separate.
