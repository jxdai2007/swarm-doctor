# Operator TODO

- [ ] OpenRouter: add $15 credit and create a key with a $15 credit limit. Put OPENROUTER_API_KEY in .env.
- [ ] Kimi: create a Kimi Code API key in the Kimi console. Put KIMI_API_KEY in .env; log oh-my-pi into the Kimi Code plan for live adapter runs.
- [ ] Make the GitHub repo public before submitting.

## When a pilot sample exists (U15 labeling — executable)

The label CLI only works after U9 pilot recordings produce a sample (labels
are OPERATOR-ONLY; agents never write labels):

```
# 0. initialize trusted normalized check exports (intentional publication)
make regenerate

# 1. build the stratified sample from actual pilot run directories (up to 150)
uv run --frozen python -m belowone.eval.monitor sample \
  --runs experiments/committed/runs/pilot-*/ \
  --per-stratum 50 --seed 0 --out labels/sample.jsonl

# 2. label (operator-only; quit any time with Ctrl-C — progress is saved)
uv run --frozen python -m belowone.eval.monitor label \
  --sample labels/sample.jsonl --labels labels/monitor_labels.jsonl

# 3. intentionally publish the U15 monitor section after operator labeling
make regenerate LABELS=labels/monitor_labels.jsonl \
  MONITOR_CHECKS=experiments/derived/monitor-checks.json

# 4. comparison-only reproduction with the SAME inputs, then document check
make reproduce LABELS=labels/monitor_labels.jsonl \
  MONITOR_CHECKS=experiments/derived/monitor-checks.json
make check-docs

# 5. optional standalone analysis (conservative synthetic provenance);
#    canonical source-bound report is experiments/derived/monitor.json
uv run --frozen python -m belowone.eval.monitor analyze \
  --labels labels/monitor_labels.jsonl \
  --checks experiments/derived/monitor-checks.json \
  --out labels/monitor_report.json
```

## Remaining operator items once keys + recordings exist

- **Live setup interview** (replaces offline scripted interview): with keys
  in .env, run `uv run python -m belowone.cli interview --task <task.md>
  --workspace . --out goal-spec.json` — answer live; model suggestions then
  come from the recorded Kimi interviewer instead of offline heuristics.
- **Pilot clips + narration** (video): follow [docs/video-script.md](docs/video-script.md)
  for the display-server prerequisites. Capture via
  `uv run --frozen python scripts/capture_video.py --out clips`;
  require exit zero and review `clips/inventory.json` before using
  clips from that invocation. Prior clips never prove a new capture succeeded.
  Record narration per the script and assemble the final video.
- **Final submission** (operator item 7): review name/email/links form at
  https://swarmchasing.com/logistics/ and submit before 5:00pm PT Sunday.
- **Public repo flip**: `gh repo edit jxdai2007/below-one --visibility public`
  (last, after final commit).
