.PHONY: test check-env pilot experiments reproduce rerun check-docs demo demo-offline

test:
	uv run --frozen pytest -q
	@if test -f adapters/omp/package.json; then cd adapters/omp && bun test; fi

check-env:
	uv run --frozen python scripts/first_hour_checks.py

pilot:
	uv run --frozen python -m belowone.experiments pilot

experiments:
	uv run --frozen python -m belowone.experiments experiments

reproduce:
	uv run --frozen python -m belowone.experiments reproduce --runs experiments/committed

rerun:
	uv run --frozen python -m belowone.experiments rerun

check-docs:
	uv run --frozen python scripts/check_doc_numbers.py --docs docs/generated

demo:
	@test -n "$$KIMI_API_KEY" -a -n "$$OPENROUTER_API_KEY" || { \
	  echo "make demo requires KIMI_API_KEY and OPENROUTER_API_KEY (operator TODO)."; \
	  echo "Offline board demo instead: make demo-offline"; exit 1; }
	uv run --frozen python adapters/omp/launch.py

demo-offline:
	uv run --frozen python scripts/serve_engine_smoke.py 8899
	@echo "open http://127.0.0.1:8899/?run=live  (SYNTHETIC DEV board)"
