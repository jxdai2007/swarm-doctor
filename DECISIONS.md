# Decisions

- Setup: native inline execution on openai-codex/gpt-6.1-sol; no distinct external model route requested. Feature branch feat/below-one keeps implementation off default branch.
- Setup: installed omp uses tools.approvalMode=yolo; auto-approval already active, so no obsolete tools.autoApprove setting or restart is needed.
- Setup: preserve the offered plan byte-for-byte at docs/plans/2026-10-03-0413-feat-below-one-plan.md.
- Setup: GitHub authenticated as jxdai2007; private origin is https://github.com/jxdai2007/below-one.
- U1: credentials absent at intake; offline synthetic responses are development evidence only, never scientific live-run evidence.
- U1: pin fallback mistralai/mistral-nemo (mistralai/mistral-nemo) from public models list: tools supported, >=32k context; lowest prompt + twice completion price among positive-price candidates. Exact prices in default config.
- U4 preparation: Jev absent from chat models catalog; Decisions API remains separate. Pin input price 0.000000042 USD/token and free output from https://openrouter.ai/typesafe/jev-1.13; pre-call reserve uses configured context bound, actual response usage settles it.
- U8: ignore incidental .DS_Store in scenario source manifests; preserve Finder file untouched.
- U3: initial spec lock creates an envelope; overwrite requires explicit operator confirmation. Session additionally pins expected hash, detecting payload plus checksum rewrites.
- U2: append refuses crash-damaged log; read warns and preserves earlier events. Decimal reservations precede OpenRouter calls; only proven uncharged failures cancel. Seal registry outside each run detects manifest recomputation and must travel with committed artifacts.
- U3: lock smoke exposed macOS /var alias mismatch; canonicalize parent aliases while rejecting final symlinks. Permanent alias regression passes with session-hash pin.
- U8: JSON-subset YAML manifest pins authored material; isolated grader ignores PASS and verifies protected tests. Exempt only the explicitly fake scenario .env.production from environment gitignore.
- Foundation verification: all 130 tests passed; held-out reference plus locked-spec smoke passed; sealed-run verification rejected a config byte change. Workers ran no checks; host independently verified.

## 2026-10-03 (ops)
- Bookkeeping: verified wave ready-1 (U2 c8378dd, U3 a6b7116, U8 7887d6e) with gate hashes; full suite 130 passed (peer WIP excluded); root GATES G1 witnessed; PROGRESS.md written.
- Config: pinned Jev pricing (input 0.000000042/token, output 0, context 32000) in default config for U4 reserve math; prices sourced from openrouter.ai/typesafe/jev-1.13.
