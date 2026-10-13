# Insider Reading (formerly AutoExtract)

SEC Form 4 insider-transaction extraction with a 7B open model, a frozen
holdout graded against each filing's own XML, a statistical promotion gate,
a CI eval that replays recorded answers, OpenTelemetry tracing, an LLM judge,
an MCP server, and a LoRA trained on the model's own verified failures.
Results: RESULTS.md. Root causes: FAILURES.md. Milestones: docs/PLAN.md
(keep it ticked). Walkthrough: docs/HOW_IT_WORKS.md.

Started as AutoExtract (Hack the North 2026, invoices, Baseten). Renamed
2026-10-13: repo michael-jw-ji/insider-reading; Modal apps
insider-reading-{small,llm,train}, secret insider-reading-llm, volumes
insider-reading-{hf-cache,vllm-cache,adapters}. The local folder is still
called AutoExtract. The invoice code, API, dashboard and Baseten training
were deleted 2026-10-13 (recoverable from git history). Run records in
results/runs/ keep whatever names were current when they ran.

## Layout
- `domains/form4.py`     schema, rules, prompt, post-processing (enrich), scoring policy
- `domains/form4_io.py`  XML -> gold, HTML -> text (versioned renders)
- `serve/`               OpenAI-compatible client (vLLM on Modal), extraction, tracing
- `verify/`              deterministic validation, failure signatures, field comparison
- `evalgate/`            scorer, bootstrap gate, failure explainer, LLM judge
- `scripts/`             fetch, load, label, failure tools, run_cycle, CI eval, LoRA dataset, reports
- `infra/`               Modal: 7B server (+ LoRA), 32B server, LoRA training
- `mcp_server.py`        MCP tools: extract_form4, get_eval_summary
- `web/`                 results page (Vite + React + TypeScript); data from `scripts/site_data.py` -> web/src/data.json (untracked)
- `results/runs/`, `data/eval_cache/`, `data/live_cache/`, `data/ci/`  the evidence (tracked)

Named `evalgate`, not `eval`, to avoid shadowing the builtin.

## Models (Modal)
- Small: `Qwen/Qwen2.5-7B-Instruct`, bf16, vLLM on an L40S (infra/modal_small.py).
  Every adapter on the adapters volume is served by name. Not local Ollama:
  its qwen2.5:7b is a 4-bit GGUF, a different model from the one trained.
- Large: Qwen2.5-32B on an H100 (infra/modal_llm.py), for the "bigger model"
  reference and the judge. Both scale to zero; `modal app stop <app>`.
- URLs carry a region: <workspace>--insider-reading-small-serve.us-east.modal.run.
  Both servers start vLLM with --api-key from the Modal secret.
- NEVER print `ps` inside a container: the vLLM command line has the key.
- After `modal deploy`, a warm old container keeps serving the old config
  (e.g. without a new adapter, or with an old key). Stop it:
  `modal container stop -y <id>`, then poll `/v1/models`.
- Extraction max_tokens is 8192; serve/client.py warns on truncation and
  follows Modal's 303 for requests over 150 s.

## Incumbent
`lora-v1` (adapter `form4-lora-v1`) = Qwen2.5-7B + rank-16 LoRA, prompt v2
(hash b4955a08), render v3, enrich v4. F1 0.9932, valid 99.5%.
`serve.extract.served_model()` resolves it (MCP server, ci_eval --build).
Prompts v3 and v4 were rejected and reverted (commits 3b38eef, 0161151).

## How a change is measured
- Every change is a candidate on the same frozen holdout (189 filings, hash
  9789311604dce853), via `scripts/run_cycle.py`:
  `--stage candidate` (prompt/render/code; calls the model),
  `--stage candidate --replay-from <v>` (change AFTER the model: re-runs
  post-processing over v's recorded answers, no calls; refused if prompt or
  render differ), `--stage adapter` (a LoRA served by name), `--stage
  rescore` (scoring-policy change: no calls, no gate).
- The cache label (e.g. `enrich4+render3+json`) and prompt hash key every
  recorded answer file. Bump `Form4Domain.enrich_version` when enrich's output
  changes and `render_version` when the rendered text changes.
- Rendering is at SERVE time from data/form4/raw/<accession>.html
  (untracked, re-fetchable). documents.text is render v1 and frozen; a
  missing raw file raises, never falls back.
- Scoring caches every answer as it arrives, resumes, and REFUSES to score a
  document with no answer. Never score a missing answer as a wrong one.
- Every run tees to results/runs/<UTC>_<stage>.log + .json + .traces.jsonl
  (core/runlog.py): commit, models, endpoints, config, holdout hash, never keys.

## Gate
- Metrics: valid_rate and field_f1 (micro F1 over fields, rows aligned by
  best overlap). Per-field numbers are RECALL of gold fields: they ignore
  invented rows, which is why a row table is reported beside them.
- GATE_POLICY=bootstrap (default, decided 2026-10-11): promote only if the
  paired-bootstrap 95% interval of the F1 gain is above zero (fixed seed, from
  the recorded answers of both evals), no field drops more than 2.0 points,
  and valid_rate does not drop. `margin` (+2.0pp) decided prompt-v2 and v3;
  those records stand. Never re-decide a past candidate under a rule chosen
  after seeing its result. Every decision, rejections included, goes to
  `promotions`.

## Data and labels
- Gold comes from the EDGAR XML, never from a model. 44 labels in
  data/form4/labels/ are "AI-assisted, human-confirmed" (they record
  `assisted_by`), never "hand-labelled". Ambiguous field paths go to
  documents.ambiguous_json; the scorer drops them from BOTH sides, plus
  rule-based ambiguity (Form4Domain.ambiguous_fields: "See Remarks" titles).
- Holdout filings never appear in prompts or training data. Enforced:
  core/freeze.py stores the id-set hash; `assert_no_leak` hard-fails.
- Training data (scripts/build_lora_dataset.py): serve the LIVE filings;
  a failure's repair is its XML gold, so every repair is verified; 70%
  repairs + 30% replay of correct filings. Train with infra/modal_train.py,
  redeploy infra/modal_small.py, gate with `--stage adapter`.
- SEC fair access: at most 10 requests/s and a User-Agent with a contact.
- Fetching retries with backoff and DROPS a filing on final failure.

## CI
`.github/workflows/eval.yml` runs `make test` and `make eval`
(scripts/ci_eval.py): 30 recorded answers of the served model (model_raw,
BEFORE enrich) in data/ci/form4_ci.jsonl replayed through the current code;
fails on any field drop > 0.5 pt vs data/ci/baseline.json, or if the prompt or
rendered input changed. After a promoted change: `ci_eval.py
--update-baseline`; after a prompt/render/model change: `ci_eval.py --build`.
PR #2 is the deliberate regression it catches; never merge it.

## Rules
- Keys live in .env, never committed, never printed.
- Validation and failure scanning are deterministic code, not an LLM.
- Derive in code anything the page states exactly (owner boxes, marker-only
  prices); leave the model only what needs reading.
- Never let a transient failure silently degrade data. Retry, or drop and
  say so: a plausible silent fallback is worse than a crash.
- Prefer small diffs. Report results honestly, including rejections and
  what is still wrong.
