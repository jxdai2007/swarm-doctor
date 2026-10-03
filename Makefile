.PHONY: test check-env pilot experiments reproduce regenerate rerun check-docs demo demo-offline

test:
	uv run --frozen pytest -q
	bun test adapters/omp/test/below-one.test.ts

check-env:
	uv run --frozen python scripts/first_hour_checks.py

# Live gate by contract (finding #2): explicit --mode $(MODE), MODE defaults
# to live like experiments/rerun; keyless development callers MUST override
# explicitly with MODE=synthetic (never silent paid action).
pilot: MODE = live
pilot:
	uv run --frozen python -m belowone.experiments pilot --mode $(MODE)

# Deliberate live authorization: explicit --mode live, keys from .env.
experiments: MODE = live
experiments:
	uv run --frozen python -m belowone.experiments experiments --mode $(MODE)

# Optional operator inputs for the U15 monitor section:
#   make reproduce LABELS=labels/monitor_labels.jsonl [MONITOR_CHECKS=experiments/derived/monitor-checks.json]
LABELS ?=
MONITOR_CHECKS ?=

# Comparison-only when experiments/derived exists; full initialization via
# `make regenerate` (a pre-reproduce regenerate would mask edited artifacts).
reproduce:
	uv run --frozen python -m belowone.experiments reproduce \
	  --runs experiments/committed/runs \
	  --outputs experiments/derived \
	  $(if $(LABELS),--labels $(LABELS)) \
	  $(if $(MONITOR_CHECKS),--monitor-checks $(MONITOR_CHECKS))

regenerate:
	uv run --frozen python -m belowone.experiments regenerate \
	  --runs experiments/committed/runs \
	  --outputs experiments/derived \
	  $(if $(LABELS),--labels $(LABELS)) \
	  $(if $(MONITOR_CHECKS),--monitor-checks $(MONITOR_CHECKS))

rerun: MODE = live
rerun:
	uv run --frozen python -m belowone.experiments rerun --mode $(MODE)

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
