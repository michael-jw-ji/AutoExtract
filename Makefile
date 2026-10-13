# Common tasks. Works on macOS/Linux (and Windows with Git Bash + make).
# Uses the project venv when it exists, else whatever `python` is on PATH.
#   make setup        create .venv and install
#   make ci           what GitHub Actions runs: tests + cached eval (free)
#   make candidate NAME=x NOTE="..."   score a pipeline change and gate it

# A system Python to create the venv. On Windows, `python3` can be the
# Microsoft Store stub, so use `python` there.
ifeq ($(OS),Windows_NT)
  VENV_PY := .venv/Scripts/python.exe
  SYS_PY := python
else
  VENV_PY := .venv/bin/python
  SYS_PY := $(shell command -v python3 2>/dev/null || command -v python)
endif
PY := $(if $(wildcard $(VENV_PY)),$(VENV_PY),$(SYS_PY))
export PYTHONUTF8 := 1

.PHONY: help setup test eval ci fetch load label failures baseline candidate replay \
        rescore compare-large trace-report site site-build

help:
	@grep -E '^#   make' Makefile | sed 's/^#   //'
	@echo "targets: setup test eval ci fetch load label failures baseline candidate replay rescore compare-large trace-report site site-build"

setup:
	$(SYS_PY) -m venv .venv
	$(VENV_PY) -m pip install -e ".[dev]"
	@test -f .env || cp .env.example .env
	@echo "edit .env: SEC_USER_AGENT, LLM_BASE_URL, LLM_API_KEY"

test:
	$(PY) -m pytest -q -W error

eval:
	$(PY) scripts/ci_eval.py

ci: test eval

fetch:            # needs SEC_USER_AGENT in .env
	$(PY) scripts/fetch_form4.py --n $(or $(N),200)

load:
	$(PY) scripts/load_form4.py

label:            # review tool, http://127.0.0.1:8765
	$(PY) scripts/label_form4.py

failures:         # failure reader, http://127.0.0.1:8766
	$(PY) scripts/failures_form4.py

baseline:         # calls the model on the whole holdout
	$(PY) scripts/run_cycle.py --stage baseline

candidate:        # make candidate NAME=x NOTE="what changed" (calls the model)
	$(PY) scripts/run_cycle.py --stage candidate --name $(NAME) --note "$(NOTE)"

replay:           # make replay NAME=x FROM=<version> NOTE="..." (no model calls)
	$(PY) scripts/run_cycle.py --stage candidate --name $(NAME) --replay-from $(FROM) --note "$(NOTE)"

rescore:          # make rescore V=<version> (no model calls)
	$(PY) scripts/run_cycle.py --stage rescore --version $(V)

compare-large:    # the holdout through LARGE_MODEL (H100, a few dollars)
	$(PY) scripts/compare_large.py

trace-report:     # make trace-report T=results/runs/<stamp>_<stage>.traces.jsonl
	$(PY) scripts/trace_report.py $(T)

site:             # results page on http://localhost:5173 (needs Node)
	$(PY) scripts/site_data.py
	cd web && npm install && npm run dev

site-build:       # static results page in web/dist/
	$(PY) scripts/site_data.py
	cd web && npm install && npm run build
