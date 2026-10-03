# Operator TODO

- [ ] OpenRouter: add $15 credit and create a key with a $15 credit limit. Put OPENROUTER_API_KEY in .env.
- [ ] Kimi: create a Kimi Code API key in the Kimi console. Put KIMI_API_KEY in .env; log oh-my-pi into the Kimi Code plan for live adapter runs.
- [ ] Make the GitHub repo public before submitting.

## When a pilot sample exists (U15 labeling — executable)

The label CLI only works after U9 pilot recordings produce a sample:

```
# 1. build the stratified sample from pilot recordings (~150 events)
uv run python -m belowone.eval.monitor sample \
  --runs runs --per-stratum 50 --seed 0 --out labels/sample.jsonl

# 2. label (operator-only; quit any time with Ctrl-C — progress is saved)
uv run python -m belowone.eval.monitor label \
  --sample labels/sample.jsonl --labels labels/monitor_labels.jsonl

# 3. analysis (after labeling)
uv run python -m belowone.eval.monitor analyze \
  --labels labels/monitor_labels.jsonl --checks runs/checks.json \
  --provenance runs/provenance.json --out labels/monitor_report.json
```
