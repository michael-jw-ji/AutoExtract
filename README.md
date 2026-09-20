# AutoExtract

An extraction API that grades its own output, clusters its failures, retrains
on them, and refuses to ship a model that isn't measurably better.

```
serve → verify → buffer → cluster → repair → train → eval gate → promote/reject
```

**Headline result:** a LoRA trained on the loop's own repairs improved
`mean_f1` by **+20.10pp** on a frozen holdout. A negative control — identical
documents, identical volume, identical output format, rule knowledge removed —
gained only +8.13pp. The **+11.97pp difference is attributable to the repairs**.
Full numbers and caveats in [RESULTS.md](RESULTS.md).

---

## The problem

Three fields on every invoice are **not printed on the document**:

| Field | Where the answer lives |
|---|---|
| `vendor_id` | an internal vendor registry (20 vendors) |
| `line_items[].category` | an internal taxonomy (30 codes) |
| `payment_terms` | a policy table keyed on vendor tier × total |

No model can read these off the page, and none appears in any pretraining
corpus. So the serving model fails ~100% of documents — and fine-tuning is the
*right* tool by construction, because the missing ingredient is information
and training is how information gets injected.

`core/registry.py` holds them. The serving prompt never contains it; the
validator does; the repair prompt does. That asymmetry is the whole design.

---

## Quickstart

### Offline — no API key, no GPU

```powershell
uv venv --python 3.13
uv pip install -e ".[dev]"

$env:MOCK_LLM="1"; $env:DRY_RUN="1"
python scripts\gen_docs.py --live 120 --holdout 100 --offline
python scripts\freeze_eval.py
python scripts\run_cycle.py --stage all --n 80
```

Mock mode is a **dev harness, not a quality simulator**. Never report numbers
produced under `MOCK_LLM=1`.

### Real run

```powershell
copy .env.example .env          # paste BASETEN_API_KEY
python scripts\check_models.py  # confirm model IDs actually exist

python scripts\gen_docs.py --live 300 --holdout 120 --workers 5
python scripts\freeze_eval.py
python scripts\probe.py --n 16  # the hour-0 experiment
```

### Servers

```powershell
.\run_api.ps1          # :8000 — preflights the port, names the offender
.\run_dashboard.ps1    # :3000
```

Use `127.0.0.1`, not `localhost` — see Troubleshooting.

---

## The pipeline, stage by stage

| # | Stage | Module | What happens |
|---|---|---|---|
| 1 | **serve** | `serve/` | Document → Baseten model → raw JSON text |
| 2 | **verify** | `verify/` | pydantic validates; failures get a deterministic signature |
| 3 | **buffer** | `buffer/store.py` | Failures queue up with their errors |
| 4 | **cluster** | `buffer/cluster.py` | Group by signature — **no embeddings** |
| 5 | **repair** | `repair/` | Mechanical lookup first, then large-model distillation |
| 6 | **verify repair** | `repair/run.py` | Only repairs matching **gold** may train |
| 7 | **train** | `train/` | Build dataset, fine-tune LoRA |
| 8 | **eval** | `evalgate/scorer.py` | Score on the frozen holdout |
| 9 | **gate** | `evalgate/gate.py` | Promote only on a real margin; log rejections |

Run any stage:

```powershell
python scripts\run_cycle.py --stage baseline      # score the incumbent
python scripts\run_cycle.py --stage serve --n 300 --workers 6
python scripts\run_cycle.py --stage clusters      # what's failing
python scripts\run_cycle.py --stage repair --workers 6
python scripts\run_cycle.py --stage train
python scripts\run_cycle.py --stage evaluate --version 2
```

---

## Enrichment — the single biggest win

`serve/enrich.py` runs at **serve time**, before validation. The model is
asked only what the page *says*; code derives everything that follows.

| Configuration | valid_rate | field_f1 | live failures |
|---|---|---|---|
| Original | 0.0% | 0.6571 | 300/300 |
| + JSON mode | 0.0% | 0.7165 | 300/300 |
| **+ enrichment** | **95.0%** | **0.9738** | **6/300** |

It beats the registry-in-prompt "ceiling" (0.9269) because prompting only
supplies facts — code also fixes arithmetic, date formats and money
precision, which the model still has to *execute* correctly.

Cost: microseconds, no tokens, no training. Compare to a LoRA (+20.1pp,
12 min GPU, 214 curated examples) or prompt-stuffing (+27.0pp, 8,471 chars on
every request forever).

When a vendor can't be matched it **refuses to guess**, leaving the model's
answer for the validator to flag. A confidently wrong ID is worse than an
honest failure.

## Multiple domains

The loop is domain-agnostic. A domain supplies five things; everything else
is shared:

```
DOMAIN=invoice         invoices  → vendor_id, category, payment_terms
DOMAIN=support_email   emails    → customer_id, category, priority
```

| What a domain provides | Invoice | Support email |
|---|---|---|
| Schema | `Invoice` | `SupportTicket` |
| Private registry | vendor → ID, tier | email domain → customer ID, tier |
| Derived by policy | payment_terms | priority |
| Taxonomy | product → category code | keywords → category |
| Redacted from the document | vendor_id, terms, category | customer_id, category, priority |

Both have the same shape: **fields you can read off the page, plus fields
only your organisation knows.** Any use case with that shape plugs in —
write `domains/<name>.py`, implement the `Domain` protocol, done. The
validator, clustering, repair, gate and dashboard need no changes.

## Design decisions that matter

**Clusters are deterministic, not embedded.** `ValidationError.errors()`
already says precisely what went wrong. The cluster key is the sorted,
index-normalised signature — instant, reproducible, and readable as English.
Embeddings would only help for output that is schema-valid but semantically
wrong, which is explicitly out of scope.

**Repairs are verified against gold before training.** Mechanical repair makes
output schema-**valid**, not **correct** — recomputing totals from a misread
price yields a self-consistent, confidently wrong invoice. Training on that
degrades the model. `test_mechanical_cannot_rescue_a_misread_price` guards this.

**Repair only short-circuits on success.** A mechanical result that is valid
but unverified falls through to distillation, which sees the registry and
often gets it right. Returning early on any parseable result silently capped
the verified yield (257 → 283 when fixed).

**The gate measures F1, not pass/fail.** `valid_rate` is pinned at 0% and
cannot detect a 20-point improvement. `field_f1` moves continuously.

**Gold is constructed, never extracted.** Documents are generated *from* a
valid invoice object, so ground truth is exact by definition — which is what
makes verifying repairs possible at all.

---

## Guards that hard-fail

Each one caught a real bug. None of them warn; they raise.

| Guard | Refuses to |
|---|---|
| `core/freeze.py::assert_no_leak` | build a dataset containing holdout documents |
| `core/freeze.py::freeze_holdout` | freeze a holdout containing template documents |
| `train/backend.py::assert_fits` | launch training whose examples would truncate |

The second exists because a rate-limited generation run silently fell back to
template rendering and **49% of an 80-document holdout was trivially easy**
before anyone noticed. It shifted `field_f1` by 2.7pp — against a promotion
margin of 2.0pp.

---

## Training

### Local track (works today)

Baseten Training Jobs returns **403** for this workspace, so the local track
is the working path — and gives a cleaner comparison anyway: Qwen-base vs
Qwen+LoRA, same model, one variable.

```powershell
uv pip install --index-url https://download.pytorch.org/whl/cu128 torch
uv pip install transformers peft trl accelerate datasets

$env:COMPACT_PROMPT="1"     # REQUIRED — see below
python scripts\score_local.py --label local-base
python scripts\train_local.py --epochs 3
python scripts\score_local.py --label local-lora --adapter models\lora-<stamp>
python scripts\selfheal_test.py compare --before local-base --after local-lora
python scripts\register_local.py --adapter models\lora-<stamp>
```

`COMPACT_PROMPT=1` swaps the full JSON Schema (5,868 chars) for a terse field
list (984), bringing examples from ~3.7k to ~2.1k tokens so they fit 8GB. It
**must be identical** for dataset build, training, and scoring — both read
`serve.extract.system_prompt()`, so they cannot silently disagree.

Memory plan for 8GB: Qwen2.5-1.5B in bf16 (~3.1GB), LoRA only, **no
quantization** — which avoids `bitsandbytes` entirely. 12 min for 3 epochs on
an RTX 5050.

### Baseten track (blocked)

A training job is a **directory** — `config.py`, `run.sh`, `train.py`,
`dataset.jsonl` — all generated by `train/backend.py`.

```powershell
baseten train push --config train\jobs\<run>\config.py   # --config flag
truss  train push        train\jobs\<run>\config.py      # positional!
baseten train checkpoint deploy --job-id <id>
```

Gotchas already paid for: the subcommand is `deploy_checkpoints`, not
`checkpoint deploy`; it is interactive unless given `--config`; deploying needs
`hf_access_token` in Baseten Secrets.

---

## Proving it self-heals

```powershell
python scripts\selfheal_test.py snapshot --label before
# ... train ...
python scripts\selfheal_test.py snapshot --label after --model <ref>
python scripts\selfheal_test.py compare --before before --after after
python scripts\selfheal_test.py control --size 190      # the negative control
```

`compare` refuses to run if the eval-set hashes differ, and reports **healed**,
**regressed** (the forgetting check), and which error types moved.

The control is the decisive experiment — see [RESULTS.md](RESULTS.md).

```powershell
python -m pytest tests\ -q     # 43 tests, no API key needed
python scripts\demo.py --pause # 7-beat scripted walkthrough
python scripts\ceiling_test.py # registry-in-prompt upper bound
```

---

## Experiments without destroying results

```powershell
python scripts\fork_db.py --to autoextract.jsonmode.db
$env:DB_PATH="autoextract.jsonmode.db"
```

Keeps documents and the freeze; clears extractions, failures, repairs and
versions. The comparison is only meaningful against the same frozen holdout.

---

## Layout

| Path | Role |
|---|---|
| `core/` | config, sqlite, DDL, **registry**, freeze enforcement |
| `verify/` | schema, validation, signatures, rules, gold comparison |
| `serve/` | Baseten client, extraction, offline mock |
| `buffer/` | failure store, signature clustering |
| `repair/` | mechanical fixes, distillation, verification |
| `train/` | dataset builder, job generator, Baseten CLI |
| `evalgate/` | frozen-holdout scorer, promotion gate |
| `api/` | FastAPI: `/extract` + read endpoints |
| `dashboard/` | Next.js, polls every 5s |
| `scripts/` | probe, gen, freeze, cycle, local training, demo |

Named `evalgate`, not `eval`, to avoid shadowing the builtin.

---

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `BASETEN_API_KEY` | — | required for real runs |
| `SMALL_MODEL` | `thinkingmachines/inkling-small` | serves traffic |
| `LARGE_MODEL` | `moonshotai/Kimi-K2.6` | data gen + repairs |
| `TRAIN_BASE_MODEL` | `Qwen/Qwen3-4B` | Baseten fine-tune target |
| `LOCAL_BASE_MODEL` | `Qwen/Qwen2.5-1.5B-Instruct` | local fine-tune target |
| `PROMOTION_MARGIN` | `2.0` | required gain, in pp |
| `REPAIR_FRACTION` | `0.7` | repair share of training set |
| `MAX_CLUSTER_SHARE` | `0.25` | per-signature cap |
| `JSON_MODE` | `0` | structured output — kills `json_decode` |
| `COMPACT_PROMPT` | `0` | terse prompt for local training |
| `POLICY_VERSION` | `1` | `2` = harder payment policy |
| `DB_PATH` | `autoextract.db` | which database to operate on |
| `MOCK_LLM` / `DRY_RUN` | unset | offline development |

`SMALL_MODEL` and `TRAIN_BASE_MODEL` differ deliberately: Baseten's Model APIs
serve a fixed catalogue with no small open models, while Training Jobs
fine-tune any HF model and deploy it separately.

---

## Troubleshooting

Every entry cost real debugging time. All are environment issues.

**New API routes 404 after you added them.** Something else holds :8000 and
your server never bound — the global interpreter also has uvicorn installed.
`run_api.ps1` catches this. Note that `uv` venvs shim to the base interpreter,
so an `AppData\Roaming\uv` path does **not** mean the venv is inactive.

**Everything local feels slow, uniformly ~2s.** Use `127.0.0.1`. uvicorn binds
IPv4-only; Windows resolves `localhost` to `::1` first and stalls. Measured:
2061ms vs 3.8ms.

**`uvicorn --reload` crashes with `ignore_permission_denied`.** The traceback
is inside `uvicorn/supervisors/` and "Uvicorn running" prints first — only the
watcher died. `pip install --force-reinstall watchfiles`, or drop `--reload`.

**Dashboard 500s with `__webpack_modules__[moduleId] is not a function`.** You
ran `npm run build` while `npm run dev` was running. Stop dev, delete
`dashboard/.next`, restart. Use `npx tsc --noEmit` to typecheck instead.

**`set VAR=1 && cmd` in cmd.exe** assigns `"1 "` with a trailing space. Use
`set "VAR=1" && cmd`, or PowerShell's `$env:VAR="1"`.

**truss output crashes with `UnicodeDecodeError`.** It emits box-drawing
characters; pass `encoding="utf-8"` to `subprocess.run`.
