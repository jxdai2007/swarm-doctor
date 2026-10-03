# Gates: Below One implementation

Scope: implement every unit in the approved plan and preserve operator-only authority.

- [x] G1: scaffold checks report real dependency outcomes and offline tests pass
  CHECK: make test && make check-env
  EXPECT: FIRST_HOUR_REPORT_WRITTEN
  EVIDENCE: witnessed 2026-10-03; docs/first-hour-checks.md committed fb721e1 (public catalog PASS, four real omp Codex probes PASS, Kimi/Jev fallbacks recorded missing-key); full offline suite re-confirmed 130 passed (ops full-suite run, peer WIP excluded). Endpoint probes not re-run — no behavior change since fb721e1.

- [ ] G2: every implementation unit scenario and integrated engine path passes
  CHECK: make test
  EXPECT: passed
  EVIDENCE: pending

- [ ] G3: recorded outputs regenerate identically without network or API keys
  CHECK: make reproduce
  EXPECT: REPRODUCE_IDENTICAL
  EVIDENCE: pending

- [ ] G4: all displayed measurements bind to generated metric sources
  CHECK: make check-docs
  EXPECT: DOC_SOURCES_VERIFIED
  EVIDENCE: pending

- [ ] G5: live experiments and operator labels support every reported hypothesis
  EVIDENCE: pending; credentials and operator labels required, synthetic evidence excluded

- [ ] G6: submission video narration, public repository and operator submission complete
  EVIDENCE: pending; operator-only actions
