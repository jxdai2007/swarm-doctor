# Gates: Below One implementation

Scope: implement every unit in the approved plan and preserve operator-only authority. Accepted source remediation is not root verification or scientific completion. Coordinator/parent runs approved checks after landing; reuse one canonical fullsuite result rather than rerunning it for meta ledgers. Historical checks do not satisfy current-source gates.

- [x] G1: dependency/environment checks report real outcomes separately from the fullsuite
  CHECK: make check-env
  EXPECT: FIRST_HOUR_REPORT_WRITTEN
  EVIDENCE: FIRST_HOUR_REPORT_WRITTEN (2026-10-04 local run; real probes; missing keys degrade to operator-blocked without faking: KIMI identity/OPENROUTER miss reported FAIL-open operational, npi-mistral/omp extension/served-path PASS). Overnight live run: Kimi + Jev endpoint calls PASS with keys present; four omp probes FAIL TimeoutExpired; the study used the experiment-harness fallback (script child exit 0 / report written is not a native-probe PASS; exact wrapper exit unknown).

- [x] G2: every implementation unit scenario and integrated engine path passes
  CHECK: make test
  EXPECT: passed
  EVIDENCE: macOS 522 pytest passed 390.03s + bun 7 pass (2026-10-04, HEAD ad4eb30 content); Linux CI run 37175671867 (sha fb8e086): 519 pytest + 3 Playwright dashboard regressions + bun suite green after pinned chromium-headless-shell install

- [x] G3: recorded outputs regenerate identically without network or API keys
  CHECK: make reproduce
  EXPECT: REPRODUCE_IDENTICAL
  EVIDENCE: REPRODUCE_IDENTICAL_NETWORK_DISABLED exit0 — original checkout (2026-10-04) and fresh clone /tmp/belowone-freshclone @ f94af23 (1.45s)

- [x] G4: all five authored documents and generated measurements byte-compare with metric sources and local links resolve
  CHECK: make check-docs
  EXPECT: DOC_SOURCES_VERIFIED
  EVIDENCE: DOC_SOURCES_VERIFIED exit0 — original checkout and fresh clone (0.24s), after current regeneration (REGENERATED_ALL_OUTPUTS, 41 changes confined to admitted message-only + provenance)

- [ ] G5: live experiments and operator labels support every reported hypothesis
  EVIDENCE (2026-10-04 overnight, partial): real 3-agent pilot calibration recordings exist (pilot-0/1 complete primary, pilot-2 sealed partial; archived a1a2e0e, runtime 10b84a2); 85-event frozen sample published (sha ccb1ad5b…), ZERO operator labels; H1/H4 exploratory-pilot-replay inconclusive N=2, H2/H3/H5/H6/H7 not_measured, main cohort N=0. GATE STILL UNMET: no operator labels, no main-cohort confirmatory evidence. Synthetic evidence excluded throughout.

- [ ] G6: submission video narration, public repository and operator submission complete
  EVIDENCE (2026-10-04 overnight, partial): 11/11 REAL capture beats produced and parent-visual-accepted (invocation 3c5df18594214c26a5d0b2220f6a2a05; inventory sha 2306b67c…; ffprobe vp8 1440×900); docs/video-script.md links current REAL names with truthful narration. STILL UNMET: narration/assembly, public flip with feat/below-one default BEFORE submission, final submission.
  EVIDENCE: pending; operator-only actions

- [x] G7: fresh-clone keyless tests and comparison-only reproduction pass on the settled canonical source
  EVIDENCE: fresh clone --no-local @ f94af23 (2026-10-04, /tmp/belowone-freshclone): uv sync --frozen --offline exit0; make test 522 pytest + bun 7 pass (292.68s); make reproduce REPRODUCE_IDENTICAL_NETWORK_DISABLED; make check-docs DOC_SOURCES_VERIFIED; keys absent throughout; all five sealed archives verify under clone-rooted RunStore (transport fix ad4eb30)

- [x] G8: current-source eleven-beat canonical capture has a successful current invocation inventory
  EVIDENCE: current-source capture invocation c0fbc8c51aa341a2b5cfead6080b4d78 complete: 11/11 beats produced, clips/inventory.json committed (c240a8a; explicit --out clips convention superseded the earlier clips/current default); beats read real archived SSE/figures/receipts; document/link checks rerun green afterward; superseded duplicate takes and _pages scaffold removed in the same commit

- [x] G9: pinned Linux omp installer and actual tracked-checkout CI secret scanner pass
  EVIDENCE: GitHub Actions run 37175671867 (https://github.com/jxdai2007/below-one/actions/runs/37175671867, sha fb8e086) SUCCESS, zero failed steps: omp-linux-x64 downloaded with SHA256SUMS grep+`sha256sum -c`, `omp --version | grep -qx omp/18.5.0` identity, make test green, make reproduce/check-docs green, tracked-file scan_secrets heredoc step exit0 (no exemptions, .example controls included)
