# Remaining human delivery

RULE-DERIVED labeling is complete:600 pressure labels (586 clean/14 violation), seven eligible recordings:500 across six injected arms (seed101:251 + seed102:249) plus100 natural100. Frozen85-event historical sample and report remain unchanged; human labels are absent, but human labeling is not a delivery prerequisite. Rule agreement is not independent human accuracy or a calibrated containment-radius proof.

Narrate and assemble using PRESENTATION.md, the eleven historical pilot clips and `presentation/figures/actual-paired-summary.png`. Latest campaign capture failed twice and stopped; no new catch video exists and no third capture is requested. Keep historical pilot captions distinct from final paired101/102 results.

Make repository public with tested default branch before submission (not yet performed):

```bash
gh repo edit jxdai2007/below-one --default-branch feat/below-one --visibility public --accept-visibility-change-consequences
```

Submit at https://swarmchasing.com/logistics/ before5pmPT Sunday. No new paid run, model/native preflight, key setup or human-label wait is required. Native adapter preflight timed out; in-process campaign does not establish native compatibility.

Canonical offline reproduction (plain Makefile default still selects older live-runs):

```bash
make reproduce RUNS=experiments/committed/pressure-campaign LABELS=labels/pressure_labels-RULE-DERIVED.jsonl MONITOR_CHECKS=experiments/derived/monitor-checks.json OUTPUTS=experiments/derived
make check-docs
```
