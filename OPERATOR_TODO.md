# Operator TODO

- [ ] OpenRouter: add $15 credit and create a key with a $15 credit limit. Put OPENROUTER_API_KEY in .env.
- [ ] Kimi: create a Kimi Code API key in the Kimi console. Put KIMI_API_KEY in .env; log oh-my-pi into the Kimi Code plan for live adapter runs.
- [ ] Make the GitHub repo public before submitting.

## When a pilot sample exists (U15 labeling — executable)

The label CLI only works after U9 pilot recordings produce a sample (labels
are OPERATOR-ONLY; agents never write labels):

```
# 1. build the stratified sample from pilot recordings (~150 events)
uv run python -m belowone.eval.monitor sample --runs runs \
  --runs runs --per-stratum 50 --seed 0 --out labels/sample.jsonl

# 2. label (operator-only; quit any time with Ctrl-C — progress is saved)
uv run python -m belowone.eval.monitor label \
  --sample labels/sample.jsonl --labels labels/monitor_labels.jsonl

# 3. analysis (after labeling)
uv run python -m belowone.eval.monitor analyze \
  --labels labels/monitor_labels.jsonl --checks runs/checks.json \
  --provenance runs/provenance.json --out labels/monitor_report.json
```

## Remaining operator items once keys + recordings exist

- **Live setup interview** (replaces offline scripted interview): with keys
  in .env, run `uv run python -m belowone.cli interview --task <task.md>
  --workspace . --out goal-spec.json` — answer live; model suggestions then
  come from the recorded Kimi interviewer instead of offline heuristics.
- **Pilot clips + narration** (video): follow docs/video-script.md — capture
  beats via `uv run python scripts/capture_video.py`, record narration per
  the script's narration column, assemble final video (operator item 6).
- **Final submission** (operator item 7): review name/email/links form at
  https://swarmchasing.com/logistics/ and submit before 5:00pm PT Sunday.
- **Public repo flip**: `gh repo edit jxdai2007/below-one --visibility public`
  (last, after final commit).
