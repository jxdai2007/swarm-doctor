# Gates: Below One implementation

Scope: implement every unit in the approved plan and preserve operator-only authority. Accepted source remediation is not root verification or scientific completion. Coordinator/parent runs approved checks after landing; reuse one canonical fullsuite result rather than rerunning it for meta ledgers. Historical checks do not satisfy current-source gates.

- [ ] G1: dependency/environment checks report real outcomes separately from the fullsuite
  CHECK: make check-env
  EXPECT: FIRST_HOUR_REPORT_WRITTEN
  EVIDENCE: pending (runner-executed automatic-evidence required; witnessed-only facts live in PROGRESS.md, not gate evidence)

- [ ] G2: every implementation unit scenario and integrated engine path passes
  CHECK: make test
  EXPECT: passed
  EVIDENCE: pending

- [ ] G3: recorded outputs regenerate identically without network or API keys
  CHECK: make reproduce
  EXPECT: REPRODUCE_IDENTICAL
  EVIDENCE: pending

- [ ] G4: all five authored documents and generated measurements byte-compare with metric sources and local links resolve
  CHECK: make check-docs
  EXPECT: DOC_SOURCES_VERIFIED
  EVIDENCE: pending

- [ ] G5: live experiments and operator labels support every reported hypothesis
  EVIDENCE: pending; credentials and operator labels required, synthetic evidence excluded

- [ ] G6: submission video narration, public repository and operator submission complete
  EVIDENCE: pending; operator-only actions

- [ ] G7: fresh-clone keyless tests and comparison-only reproduction pass on the settled canonical source
  EVIDENCE: pending; parent must exercise one fresh clone with provider keys absent; published 207e0ed/ff13462 are not settled-source evidence

- [ ] G8: current-source eleven-beat canonical capture has a successful current invocation inventory
  EVIDENCE: pending; coordinator/parent must regenerate authored/derived documents and prepare a separate display root, follow docs/video-script.md, exercise capture, review clips/current/inventory.json and rerun document/link checks. Prior clips or one-beat smoke capture do not satisfy this gate.

- [ ] G9: pinned Linux omp installer and actual tracked-checkout CI secret scanner pass
  EVIDENCE: pending; Linux release SHA256SUMS verification before execution and omp/18.5.0 identity require actual CI. Coordinator exercises the exact workflow Python heredoc plus existing clean-placeholder/real-shaped-key .example controls; no filename exemptions.
