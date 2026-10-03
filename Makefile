.PHONY: test check-env pilot experiments reproduce rerun check-docs demo

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
	uv run --frozen python adapters/omp/launch.py
