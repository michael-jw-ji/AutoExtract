# AutoExtract

A structured-extraction service that grades its own output, clusters its
failures, retrains on them, and refuses to ship a model that isn't measurably
better. Being extended to SEC Form 4 insider-transaction filings.

```
serve → verify → buffer → cluster → repair → train → eval gate → promote/reject
```

[![eval](https://github.com/michael-jw-ji/AutoExtract/actions/workflows/eval.yml/badge.svg)](https://github.com/michael-jw-ji/AutoExtract/actions/workflows/eval.yml)

**Status:** Milestone 2 done. On a frozen 189-filing holdout, Qwen2.5-7B goes
from field F1 0.8401 / 54.5% valid (baseline) to **0.9194 / 96.3%**, every
step promoted or rejected by a statistical gate. Qwen2.5-32B on the same
pipeline reaches 0.9931, but makes two failures worse (RESULTS.md).
How everything works, with diagrams: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).
Measured numbers: [RESULTS.md](RESULTS.md). Roadmap: [docs/PLAN.md](docs/PLAN.md).

## Setup

```
python -m venv .venv
.venv/Scripts/activate        # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # then set SEC_USER_AGENT (and LLM_* if not using local Ollama)
pytest
```

## Fetch filings

```
SEC_USER_AGENT="Your Name you@example.com" python scripts/fetch_form4.py --n 200
```

Writes the SEC-rendered HTML (model input) to `data/form4/raw/` and the
official XML (source of gold) to `data/form4/xml/`. Respects SEC's rate limit,
resumes on re-run, and drops (never fabricates) filings it cannot fetch.

## Load, label, check

```
python scripts/load_form4.py       # XML -> gold, HTML -> text, into form4.db
python scripts/label_form4.py      # review tool at http://127.0.0.1:8765
python scripts/load_form4.py       # re-run: applies your labels, moves them to the holdout
python scripts/probe.py --n 20     # base failure rate on live documents
```

## CI eval

Every PR runs the unit tests plus `scripts/ci_eval.py`: 30 recorded model
answers replayed through the PR's code, with no model calls, so it is free and
deterministic. It fails if any scored field drops more than 0.5 points.

**Demo:** [PR #2](https://github.com/michael-jw-ji/AutoExtract/pull/2) is a
deliberate regression that looks like a harmless cleanup (round share counts
to whole numbers). All 75 unit tests pass; the eval catches it, because real
filings report fractional holdings:

```
transactions.*.shares              0.9220 -> 0.8865 -3.6  <-- REGRESSION
transactions.*.shares_owned_after  0.9267 -> 0.8933 -3.3  <-- REGRESSION
```

## Layout

| Path | Role |
|---|---|
| `domains/` | pluggable domains; `form4.py` defines schema, rules, enrichment |
| `serve/` | Baseten client and extraction endpoint |
| `verify/` | deterministic validation and failure signatures |
| `buffer/` | failure store and clustering by signature |
| `repair/` | mechanical fixes and large-model distillation |
| `train/` | dataset builder and LoRA backend |
| `evalgate/` | frozen holdout scorer and promotion gate |
| `api/`, `dashboard/` | FastAPI read endpoints and the Next.js dashboard |
| `scripts/` | fetch, probe, freeze, cycle driver |

Design rules (verified repairs only, frozen holdout, promotion margin) are in
[CLAUDE.md](CLAUDE.md).
