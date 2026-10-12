# Self-improving extraction service

## What this is
An API that extracts structured JSON from messy documents, validates its own
output against a strict schema, clusters its failures, and retrains itself on
them. Built for Hack the North 2026 (Baseten track).

## Current project: SEC Form 4 (read docs/PLAN.md first)
The hackathon is over. The active work extends this repo with a `form4`
domain: real EDGAR Form 4 filings as input, the official XML as free exact
gold, plus failure analysis (FAILURES.md), a CI eval gate, OpenTelemetry
tracing, an LLM judge, and an MCP server. Milestones and checklists live in
docs/PLAN.md; keep it ticked as work lands.

- Everything below describes the loop, which is unchanged.
- Unlike the synthetic domains, Form 4 documents are REAL, so the corpus
  hygiene rules below apply differently: gold comes from the XML, never from a
  generator, and there is no template fallback to guard against. Hand-labelled
  filings go in data/form4/labels/ (tracked); raw downloads do not.
- Select the domain with `DOMAIN=form4` or `run_cycle.py --domain form4`.
- Small model is `Qwen/Qwen2.5-7B-Instruct`, bf16, vLLM on a Modal L40S
  (infra/modal_small.py, LoRA enabled). It moved off local Ollama because
  Ollama's qwen2.5:7b is a 4-bit GGUF: a baseline there vs a fine-tune of the
  bf16 weights would change two things at once. Local Ollama
  (`qwen2.5-7b-16k`, infra/qwen2.5-7b-16k.Modelfile; its default 4096 context
  silently truncated long filings) is for free experiments only, never for
  reported numbers. Extraction max_tokens is 8192; serve/client.py warns on a
  truncated answer and follows Modal's 303 for requests over 150s.
- Scoring (evalgate/scorer.py) runs LLM_CONCURRENCY requests at once (1 for
  local Ollama, 8 on Modal), caches every answer in data/eval_cache/ as it
  arrives, resumes on re-run, and REFUSES to score if any document got no
  answer. Never score a missing answer as a wrong one.
- Large model is Qwen2.5-32B-Instruct on Modal (infra/modal_llm.py), routed
  by LARGE_LLM_BASE_URL. It scales to zero; `modal app stop autoextract-llm`.
- The invoice and support_email domains, their generators, mock mode, demo
  scripts and the hackathon docs were deleted. The shared modules no longer
  fall back to an invoice schema; a broken domain now fails loudly.
- Known invoice residue still to generalise: train/dataset.py
  (RULE_FIELDS, build_control_dataset) and api/main.py (rule_fields).
- Hand labels: scripts/label_form4.py is a review tool (XML gold shown beside
  the page text). Labels land in data/form4/labels/ and load_form4.py applies
  them, forcing those filings into the holdout. Ambiguous field paths are
  stored in documents.ambiguous_json and the scorer drops them from BOTH pred
  and gold. Labels made with the AI pre-review (data/form4/review/) record
  `assisted_by`: call them "AI-assisted, human-confirmed", not hand-labelled.
  Freeze the holdout only AFTER labelling: freezing locks the set.
- Text rendering (domains/form4_io.py::html_to_text) has three fixes found
  while labelling: checkboxes render as [X]/[ ], Table II amounts carry
  (A)/(D), and the scorer ignores whitespace. `load_form4.py --refresh-text`
  re-renders stored text, and refuses once the holdout is frozen.
- Every `run_cycle.py` run tees its terminal output to results/runs/<UTC>_<stage>.log
  and writes the numbers to a matching .json (core/runlog.py). Both are tracked:
  they are the record behind RESULTS.md. The header logs commit, models,
  endpoints, config and holdout hash, never keys. The first baseline's record
  is marked RECONSTRUCTED (rebuilt from the DB and answer cache).
- data/eval_cache/ is tracked: the recorded holdout answers behind every score,
  and what Milestone 3's free CI eval will replay.
- Milestone 2 failure reading: `python scripts/failures_form4.py`
  (http://127.0.0.1:8766) shows each failure's page, errors and a row-by-row
  answer-vs-gold diff from the recorded answers. Theme guesses come from
  evalgate/failures.py; the human's notes go to data/form4/failure_notes/
  (tracked) and are what FAILURES.md is written from.
- SEC fair-access: max 10 requests/sec and a `User-Agent` with a contact email.

## The loop
serve (Baseten) -> verify (pydantic) -> failure buffer -> cluster ->
repair -> train LoRA -> eval gate -> promote or reject -> deploy

A new model version is ONLY promoted if it beats the incumbent on a frozen
holdout set. Rejected retrains are logged and shown in the dashboard.

## Layout
/core       config, sqlite connection, schema DDL, repo helpers
/serve      baseten client + extraction endpoint
/verify     validate() + failure signatures (schema comes from the domain)
/buffer     failure store, signatures, clustering
/repair     mechanical fixes + large-model distillation
/train      dataset builder (repairs + replay buffer, ~70/30)
/evalgate   frozen holdout scorer + promotion gate
/api        FastAPI app, read endpoints for the dashboard
/dashboard  next.js
/domains    pluggable domains (form4); schema, rules, enrich, repair rules
/scripts    fetch_form4, probe, freeze, cycle driver

Named `evalgate`, not `eval`, to avoid shadowing the builtin.

## Stack
- Baseten for inference (OpenAI-compatible, base_url
  https://inference.baseten.co/v1) and for LoRA training
- Small model serves and gets fine-tuned; large model produces repairs
- FastAPI, pydantic, sqlite

## The document domain
SEC Form 4 insider-transaction filings (domains/form4.py). Input is the
filing's rendered HTML/text; gold is the filing's official XML. Failure
sources to expect: price pulled from a footnote, derivative vs non-derivative
table confusion, wrong dates on amendments, multiple reporting owners. The
schema and rule checks live in the domain, not in a shared verify/schema.py.
Target base failure rate 25-45% still applies; measure it with scripts/probe.py.

## Failure clustering (buffer/)
NO embeddings. `ValidationError.errors()` gives `(type, loc)` per error.
The signature is the sorted, index-normalised tuple:
`missing:line_items.*.unit_price`, `enum:currency`, `arithmetic:subtotal`.
Deterministic, instant, and human-readable in the dashboard. Cluster = group
by signature. Embeddings are explicitly out of scope; they only help for
output that is schema-valid but semantically wrong, which we do not tackle.

## Data model (sqlite, core/schema.sql)
Every stage appends a row. The dashboard is pure reads. If a live component
dies mid-demo, the audit trail still tells the whole story.

- `documents`       id, text, gold_json, split ('live'|'holdout'), created_at
- `extractions`     doc_id, model_version_id, raw_output, parsed_json, valid,
                    signature, latency_ms
- `failures`        extraction_id, doc_id, signature, error_count, status
- `repairs`         failure_id, doc_id, method ('mechanical'|'distill'),
                    repaired_json, verified
- `model_versions`  name, base_model, adapter_ref, status
                    ('candidate'|'promoted'|'rejected'), parent_id
- `training_runs`   model_version_id, baseten_job_id, dataset_size,
                    repair_count, replay_count, status
- `evals`           model_version_id, eval_set_hash, valid_rate, field_f1,
                    n_docs, per_field_json
- `promotions`      candidate_id, incumbent_id, decision, margin, reason

## Repairs must be VERIFIED before training (repair/)
Mechanical repair makes output schema-VALID, not CORRECT. Recomputing totals
from a misread unit_price yields a self-consistent, confidently wrong invoice;
training on it actively degrades the model.

So a repair is `verified` only if it is schema-valid AND matches gold exactly.
Because documents are generated from constructed gold, we have ground truth on
every document including live traffic. Unverified repairs are recorded for the
dashboard and NEVER enter a training set.

## Corpus hygiene
Gold comes from the EDGAR XML, never from a model, and there is no template
fallback. Hand-labelled filings (data/form4/labels/) mark genuinely ambiguous
cases `ambiguous: true` rather than forcing a label.
- Fetching retries with backoff and DROPS a filing on final failure; a smaller
  honest corpus beats a contaminated one.
- core/freeze.py::freeze_holdout still refuses a holdout containing any
  TEMPLATE_MARKER document (legacy guard, kept as a cheap tripwire).
- There is no offline mock mode any more (serve/mock.py was invoice-specific).
  `DRY_RUN=1` still skips the Baseten job. CI will use recorded model
  responses instead (docs/PLAN.md, Milestone 3).

## Training data policy (train/)
- 70% repairs (newly fixed failures), 30% replay (previously-passing
  examples), to prevent catastrophic forgetting.
- No single failure signature may exceed 25% of the repair half. Otherwise
  the LoRA learns one field and forgets the rest. Enforced in
  `train/dataset.py::build_dataset`.
- Chat-format JSONL: system (schema) / user (document) / assistant (JSON).

## Eval gate (eval/)
- Two metrics: `valid_rate` (schema-valid fraction) and `field_f1`
  (field-level F1 vs gold). Pass/fail alone is too coarse at n=100 -- noise
  swamps real movement and we would promote on coin flips.
- Promotion requires beating the incumbent by a **margin of +2.0pp on
  field_f1** and not regressing valid_rate. Margin lives in `PROMOTION_MARGIN`.
- Every decision is written to `promotions`, including rejections.

## Rules
- API key lives in .env, never committed
- Eval set is frozen and never leaks into training data. This is enforced
  MECHANICALLY, not by convention: `core/freeze.py` stores the holdout id-set
  hash, and `build_dataset()` asserts no holdout id appears in training data
  and hard-fails otherwise.
- The LLM does not do the failure scanning; validation is deterministic
- Prefer small diffs
- Never let a transient API failure silently degrade data quality. Retry, or
  drop and say so. A silent fallback that produces *plausible* output is worse
  than a crash, because nothing downstream can tell it happened.

## Baseten notes (verified against truss 0.18.30, 2026-09-19)
A training job is a DIRECTORY, not a config file:
  config.py   TrainingProject / TrainingJob -- image, compute, runtime
  run.sh      installs deps, starts training
  train.py    the actual TRL SFT script
  dataset.jsonl  shipped with the job, so no hub download
`train/backend.py` generates all four per run.

  truss train push <jobdir>/config.py          # CONFIG IS POSITIONAL
  truss train deploy_checkpoints --config <deploy.py>

Gotchas paid for already:
- The subcommand is `deploy_checkpoints`, NOT `checkpoint deploy`.
- `deploy_checkpoints` is INTERACTIVE without `--config`; unattended runs must
  generate a DeployCheckpointsConfig.
- Deploying needs `hf_access_token` in Baseten Secrets.
- On Windows the venv Scripts/ dir is not on PATH, so `detect_cli()` looks
  next to sys.executable before falling back to PATH.
- Reference timing: ~2 min on 1x H100 for 50 steps. Training is NOT the demo
  bottleneck; queueing and deployment are.
- `truss train init --list-examples` is the fastest way to a correct config.
  `qwen3-4b-lora-sft-trl` is the one this project is modelled on.

The Model APIs catalogue (17 models on our key) has no small open model, which
is why TRAIN_BASE_MODEL is separate from SMALL_MODEL.

**Loops** (early access) is a Tinker-compatible LoRA SDK with nicer
ergonomics. Ask the Baseten booth; if granted, swap `train/backend.py`.

### BLOCKER: training is 403 on this workspace
`baseten train push` -> 403 "You are not authorized for Baseten training"
on POST /v1/teams/qzzvoxq/training_projects. Confirmed with BOTH truss and
the official `baseten` CLI v1.0.0 (the official CLI delegates to truss and
hits the same endpoint). Reads work -- `baseten train project list` returns
200 -- so it is a workspace permission flag, not auth or billing.

Install the official CLI on Windows from
https://github.com/basetenlabs/baseten-cli/releases (baseten_<ver>_windows_amd64.zip);
there is no pip package (the PyPI `baseten` is a stale legacy SDK). We vendor
it to .tools/ and detect_cli() prefers it. NOTE the flag difference:
`baseten train push --config X` vs `truss train push X` (positional).

Until that flag is flipped, the LOCAL track (scripts/train_local.py) is the
real path -- and it gives a cleaner comparison anyway: Qwen-base vs
Qwen+LoRA, same model, one variable.
