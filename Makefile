.PHONY: test check-env pilot experiments reproduce rerun check-docs demo demo-offline

test:
	uv run --frozen pytest -q
	bun test adapters/omp/test/below-one.test.ts

check-env:
	uv run --frozen python scripts/first_hour_checks.py

pilot:
	uv run --frozen python -m belowone.experiments pilot

experiments:
	uv run --frozen python -m belowone.experiments experiments

reproduce:
	uv run --frozen python -m belowone.experiments reproduce --runs experiments/committed/runs

rerun:
	uv run --frozen python -m belowone.experiments rerun

check-docs:
	uv run --frozen python scripts/check_doc_numbers.py --docs docs/generated

demo:
	@test -f .env || { echo "make demo needs .env with KIMI_API_KEY and OPENROUTER_API_KEY (operator TODO). Offline: make demo-offline"; exit 1; }
	set -a; . ./.env; set +a
	mkdir -p /tmp/belowone-demo
	uv run --frozen python -m adapters.omp.launch \
	  --out /tmp/belowone-demo/runs \
	  --workspaces /tmp/belowone-demo/workspaces \
	  --commit $$(git rev-parse HEAD) \
	  --seeds 0 --agents 3

demo-offline:
	@echo "open http://127.0.0.1:8899/?run=live  (SYNTHETIC DEV board)"
	uv run --frozen python scripts/serve_engine_smoke.py 8899
