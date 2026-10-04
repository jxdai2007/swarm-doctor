.PHONY: test check-env pilot experiments reproduce regenerate rerun check-docs demo demo-offline

test:
	uv run --frozen pytest -q
	bun test adapters/omp/test/below-one.test.ts

check-env:
	uv run --frozen python scripts/first_hour_checks.py

# Study targets deliberately authorize live mode by default.
# Keyless development: make pilot MODE=synthetic (also experiments/rerun).
# Synthetic runs never count as scientific live evidence.
PILOT_ROOT ?= /tmp/belowone-pilot
KIMI_REQUESTS_USED ?= 0
RUNS ?= $(if $(wildcard experiments/committed/live-runs/.seals),experiments/committed/live-runs,experiments/committed/runs)
EXPERIMENT_ROOT ?= /tmp/belowone-experiments
pilot: MODE = live
pilot:
	uv run --frozen python -m belowone.experiments pilot --mode $(MODE) \
	  --out $(PILOT_ROOT)/runs \
	  --workspaces $(PILOT_ROOT)/workspaces \
	  --kimi-requests-used $(KIMI_REQUESTS_USED) \
	  --commit $$(git rev-parse HEAD)

# Deliberate live authorization; credentials load from environment or .env.
experiments: MODE = live
experiments:
	uv run --frozen python -m belowone.experiments experiments --mode $(MODE) \
	  --runs $(RUNS) \
	  --out $(EXPERIMENT_ROOT)/runs \
	  --workspaces $(EXPERIMENT_ROOT)/workspaces \
	  --kimi-requests-used $(KIMI_REQUESTS_USED) \
	  --commit $$(git rev-parse HEAD)

# Optional operator inputs for the U15 monitor section:
#   make reproduce LABELS=labels/monitor_labels.jsonl [MONITOR_CHECKS=experiments/derived/monitor-checks.json]
LABELS ?=
MONITOR_CHECKS ?=
OUTPUTS ?= experiments/derived

# Comparison-only when experiments/derived exists; full initialization via
# `make regenerate` (a pre-reproduce regenerate would mask edited artifacts).
reproduce:
	uv run --frozen python -m belowone.experiments reproduce \
	  --runs $(RUNS) \
	  --outputs $(OUTPUTS) \
	  $(if $(LABELS),--labels $(LABELS)) \
	  $(if $(MONITOR_CHECKS),--monitor-checks $(MONITOR_CHECKS))

regenerate:
	uv run --frozen python -m belowone.experiments regenerate \
	  --runs $(RUNS) \
	  $(if $(LABELS),--labels $(LABELS)) \
	  $(if $(MONITOR_CHECKS),--monitor-checks $(MONITOR_CHECKS))

rerun: MODE = live
rerun:
	uv run --frozen python -m belowone.experiments rerun --mode $(MODE) \
	  --runs $(RUNS) \
	  --out /tmp/belowone-rerun/runs \
	  --workspaces /tmp/belowone-rerun/workspaces \
	  --kimi-requests-used $(KIMI_REQUESTS_USED) \
	  --commit $$(git rev-parse HEAD)

check-docs:
	uv run --frozen python scripts/check_doc_numbers.py --docs docs/generated

demo:
	@test -f .env || { echo "make demo needs .env with KIMI_API_KEY and OPENROUTER_API_KEY (operator TODO). Offline: make demo-offline"; exit 1; }
	mkdir -p /tmp/belowone-demo
	uv run --frozen --env-file .env python -m adapters.omp.launch \
	  --out /tmp/belowone-demo/runs \
	  --workspaces /tmp/belowone-demo/workspaces \
	  --commit $$(git rev-parse HEAD) \
	  --seeds 0 --agents 3

demo-offline:
	@echo "open http://127.0.0.1:8899/?run=live  (SYNTHETIC DEV board)"
	uv run --frozen python scripts/serve_engine_smoke.py 8899
