# AutoExtract

An extraction API that grades its own output, clusters its failures, retrains
on them, and refuses to ship a model that isn't measurably better.

```
serve → verify → buffer → cluster → repair → train → eval gate → promote/reject
```

## Quickstart (no API key, no GPU)

The whole loop runs offline against a mock model. Use this to develop and to
verify logic changes.

```bash
uv venv --python 3.13
uv pip install -e ".[dev]"

# Windows PowerShell:  $env:MOCK_LLM="1"; $env:DRY_RUN="1"
export MOCK_LLM=1 DRY_RUN=1

python scripts/gen_docs.py --live 120 --holdout 100 --offline
python scripts/freeze_eval.py
python scripts/run_cycle.py --stage all --n 80
```

Then, in two terminals:

```bash
uvicorn api.main:app --reload --port 8000
cd dashboard && npm install && npm run dev      # http://localhost:3000
```

> Mock mode is a **dev harness**, not a quality simulator. Never report
> numbers produced under `MOCK_LLM=1`.

## Real run

```bash
cp .env.example .env          # paste BASETEN_API_KEY
python scripts/check_models.py    # confirm the model IDs actually exist

python scripts/gen_docs.py --live 150 --holdout 100
python scripts/freeze_eval.py
python scripts/probe.py --n 20    # MUST land in 25-45%
```

`probe.py` is the hour-0 experiment. If the failure rate is outside the band,
tune `verify/schema.py` before building anything on top — see "Tuning" below.

Then run the loop stage by stage:

```bash
python scripts/run_cycle.py --stage baseline        # score base model, becomes incumbent
python scripts/run_cycle.py --stage serve --n 100   # extract + buffer failures
python scripts/run_cycle.py --stage clusters        # inspect what's failing
python scripts/run_cycle.py --stage repair          # mechanical + distillation
python scripts/run_cycle.py --stage train           # build dataset, push Baseten job
# ... wait for training ...
python scripts/run_cycle.py --stage finalize --run 1 --version 2 --job <job_id>
python scripts/run_cycle.py --stage evaluate --version 2
```

## How training works

`train/dataset.py` builds a chat-format JSONL with three enforced invariants:

1. **No holdout leakage.** `core/freeze.py` hashes the holdout id-set at
   freeze time. `build_dataset()` calls `assert_no_leak()` before writing
   anything to disk and raises `EvalLeakError` on any overlap.
2. **70% repairs / 30% replay** (`REPAIR_FRACTION`). Replay examples are
   previously-passing extractions, included so the LoRA doesn't forget what
   already worked.
3. **No signature exceeds 25% of the repair half** (`MAX_CLUSTER_SHARE`).
   Without this the model overfits the single loudest error and regresses
   everywhere else — which the gate would then reject, wasting a GPU run.

### The Baseten job

A Baseten training job is a **directory**, not a config file. `train/backend.py`
generates all of it per run:

```
train/jobs/<run>/config.py      TrainingProject/TrainingJob -- image, compute
train/jobs/<run>/run.sh         installs deps, starts training
train/jobs/<run>/train.py       TRL SFTTrainer + LoRA
train/jobs/<run>/dataset.jsonl  shipped with the job, no hub download
```

```bash
truss train push train/jobs/<run>/config.py       # CONFIG IS POSITIONAL
truss train deploy_checkpoints --config <deploy.py>
```

Gotchas already paid for: the subcommand is `deploy_checkpoints` (not
`checkpoint deploy`); it is **interactive** unless given `--config`, so
unattended runs generate a `DeployCheckpointsConfig`; deploying needs
`hf_access_token` in [Baseten Secrets](https://app.baseten.co/settings/secrets);
and on Windows the venv's `Scripts/` is not on PATH, so `detect_cli()` looks
next to `sys.executable` first.

LoRA rank must be one Baseten accepts: 8/16/32/64/128/256/320/512.
`DRY_RUN=1` skips the real job and returns a synthetic id.

Reference timing from Baseten's own qwen3-4b example: **~2 minutes on 1×H100**
for 50 steps. Training is not the demo bottleneck — queueing and deployment are.

### SMALL_MODEL vs TRAIN_BASE_MODEL

These are deliberately different. Baseten's Model APIs serve a fixed catalogue
(17 models on our key, none of them small open models), while Training Jobs
fine-tune any HF model and deploy it separately. So `SMALL_MODEL` serves
production traffic and `TRAIN_BASE_MODEL` (`Qwen/Qwen3-4B`) is what actually
gets a LoRA.

**Consequence for the gate:** comparing a LoRA'd Qwen3-4B against an
`inkling-small` incumbent is not an apples-to-apples test. For the self-healing
claim, compare Qwen3-4B **base** against Qwen3-4B **+LoRA** — same base model,
one variable.

### Only verified repairs are trained on

Mechanical repair makes output schema-**valid**, not **correct**. If the model
misread a `unit_price`, recomputing the totals from it yields a
self-consistent, confidently wrong invoice — and training on that actively
degrades the model.

So `repair/run.py` marks a repair `verified` only if it is schema-valid **and**
matches gold exactly (`verify/compare.py`). Because documents are generated
from constructed gold, we have ground truth for every document, including live
traffic. Unverified repairs are recorded for the dashboard and never enter a
training set.

## How evaluation works

`evalgate/scorer.py` runs the frozen holdout and reports two numbers:

- `valid_rate` — fraction passing the schema
- `field_f1` — micro-averaged field-level F1 vs gold

`valid_rate` alone is too coarse at n≈100: it moves in whole-percent steps, so
noise swamps genuine improvement and the gate promotes on coin flips.
`field_f1` moves continuously and is what the margin is measured against.

`evalgate/gate.py` promotes only if `field_f1` improves by at least
`PROMOTION_MARGIN` (default +2.0pp) **and** `valid_rate` does not regress.
Both outcomes are written to `promotions`.

## Local training track (the Baseten fallback)

Baseten Training Jobs returned **403 "not authorized for Baseten training"**
for this workspace — confirmed with both `truss` and the official `baseten`
CLI v1.0.0. Reads succeed (`baseten train project list` → 200), creates do
not, so it is a workspace permission flag, not auth or billing.

The local track trains on one consumer GPU instead, and is arguably better
science: it compares **Qwen-base against Qwen+LoRA** — same model, one
variable — which is the clean A/B the Baseten path could not give without
first deploying a base endpoint.

```powershell
# one-time
uv pip install --index-url https://download.pytorch.org/whl/cu128 torch
uv pip install transformers peft trl accelerate datasets

$env:COMPACT_PROMPT="1"     # REQUIRED, see below
python scripts/run_cycle.py --stage train   # (skip; builds dataset only)
python scripts/score_local.py --label local-base
python scripts/train_local.py --epochs 3
python scripts/score_local.py --label local-lora --adapter models/lora-<stamp>
python scripts/selfheal_test.py compare --before local-base --after local-lora
```

**`COMPACT_PROMPT=1` is mandatory and must be identical for dataset build,
training, and scoring.** It swaps the full JSON Schema (5,868 chars) for a
terse field list (984), which brings examples from ~3.7k tokens to ~2.1k so
they fit an 8GB GPU. Both the dataset builder and the scorer call
`serve.extract.system_prompt()`, so they cannot silently disagree — if they
did, the LoRA would be tuned against a prompt the scorer never sends and
would not transfer at all.

Memory plan for 8GB: Qwen2.5-1.5B-Instruct in bf16 (~3.1GB), LoRA only, no
quantization — which avoids `bitsandbytes` entirely, the genuinely painful
Windows dependency. Gradient checkpointing, batch 1, accumulation 16.

Expect the local **base** numbers to look poor — a 1.5B is far weaker than
`inkling-small`. That is fine. The number that matters is the delta between
base and LoRA on the same model.

## Proving it self-heals

"field_f1 went up" is weak evidence — a model can improve for reasons that
have nothing to do with the repairs. `scripts/selfheal_test.py` isolates the
claim.

```bash
python scripts/selfheal_test.py snapshot --label before
# ... serve, repair, train, deploy ...
python scripts/selfheal_test.py snapshot --label after --model <adapter_ref>
python scripts/selfheal_test.py compare --before before --after after
```

`compare` refuses to run if the two snapshots have different eval-set hashes,
and reports **healed** (failed before, pass now), **regressed** (passed before,
fail now — the forgetting check), and which error types moved.

The decisive test is the **negative control**:

```bash
python scripts/selfheal_test.py control --size 180   # same size, ZERO repairs
# train a second LoRA on this dataset, deploy it
python scripts/selfheal_test.py snapshot --label control --model <control_ref>
python scripts/selfheal_test.py compare --before before --after control
```

The repair-trained model must beat the control. If it doesn't, the gain came
from fine-tuning in general, not from the repairs, and the self-healing claim
is unsupported.

Unit tests cover the deterministic pieces — schema rules, policy boundaries,
signature normalisation, mechanical repair, scoring:

```bash
python -m pytest tests/ -q      # 30 tests, no API key needed
```

Two of them guard properties that are easy to break silently:
`test_mechanical_cannot_rescue_a_misread_price` (repair can make output valid
but wrong — which is why repairs are verified against gold) and
`test_invalid_output_is_still_scorable` (if invalid documents score 0,
`field_f1` collapses into `valid_rate` and the gate goes blind).

## Tuning the failure rate

The project needs the base model to fail 25–45% of the time.

| Symptom | Fix |
|---|---|
| Rate too low (<25%) | Tighten `verify/schema.py`, raise messiness in `gen_docs.py`, use a smaller `SMALL_MODEL` |
| Rate too high (>45%) | Relax `extra="forbid"`, widen `TOLERANCE`, use a larger `SMALL_MODEL` |

Highest-yield strictness knobs, in order: arithmetic consistency → enums →
date/number formats → structural requirements.

## Running it

```powershell
.\run_api.ps1          # API on :8000  — refuses to start if the port is held
.\run_dashboard.ps1    # dashboard on :3000
```

Both scripts preflight their port and print the offending process instead of
failing silently.

## Troubleshooting

Every entry here cost real debugging time. They are all environment issues,
not application bugs.

**New API routes return 404 after you added them.**
Something else is already on :8000 and your new server never bound. The global
interpreter on this machine also has fastapi + uvicorn installed, so a stray
`python -m uvicorn` wins the port and keeps answering with old code. `run_api.ps1`
now catches this. To clear it by hand:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match "uvicorn|multiprocessing-fork" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Note that `uv` venvs do not copy `python.exe` — they shim to the uv-managed
base interpreter, so a process command line showing an `AppData\Roaming\uv`
path does **not** mean the venv is inactive.

**Everything local feels slow, uniformly ~2s.**
Use `127.0.0.1`, not `localhost`. uvicorn binds IPv4-only; Windows resolves
`localhost` to `::1` first and that attempt stalls before falling back.
Measured on the same endpoint: `localhost` 2061ms vs `127.0.0.1` 3.8ms.

**`uvicorn --reload` crashes at startup with
`watch() got an unexpected keyword argument 'ignore_permission_denied'`.**
The traceback is entirely inside `uvicorn/supervisors/` and `Uvicorn running
on …` prints *before* it — your app is fine, only the file watcher died.
watchfiles ships a compiled Rust extension alongside its Python wrapper and a
partial install leaves them out of sync. Fix with
`pip install --force-reinstall watchfiles`, or drop `--reload`.

**Dashboard 500s with `__webpack_modules__[moduleId] is not a function`.**
You ran `npm run build` while `npm run dev` was running; the production build
clobbered the dev server's chunk map. Stop dev, `rm -r dashboard/.next`,
restart. Don't run both at once.

## Layout

| Path | Role |
|---|---|
| `core/` | config, sqlite, DDL, freeze enforcement |
| `verify/` | strict `Invoice` schema, validation, failure signatures, gold comparison |
| `serve/` | Baseten client, extraction, offline mock |
| `buffer/` | failure store, signature clustering |
| `repair/` | mechanical fixes, large-model distillation, verification |
| `train/` | dataset builder, Axolotl config, Baseten job control |
| `evalgate/` | frozen-holdout scorer, promotion gate |
| `api/` | FastAPI: `/extract` + read endpoints |
| `dashboard/` | Next.js, polls the read endpoints every 5s |
| `scripts/` | probe, data gen, freeze, cycle driver |

Named `evalgate` rather than `eval` to avoid shadowing the builtin.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `BASETEN_API_KEY` | — | required for real runs |
| `BASETEN_BASE_URL` | `https://inference.baseten.co/v1` | OpenAI-compatible endpoint |
| `SMALL_MODEL` | `Qwen/Qwen3-8B` | serves + gets fine-tuned — **verify this id** |
| `LARGE_MODEL` | `deepseek-ai/DeepSeek-V3.1` | data gen + repairs — **verify this id** |
| `PROMOTION_MARGIN` | `2.0` | required field_f1 gain, in pp |
| `REPAIR_FRACTION` | `0.7` | repair share of the training set |
| `MAX_CLUSTER_SHARE` | `0.25` | per-signature cap on the repair half |
| `MOCK_LLM` | unset | `1` = offline mock model |
| `MOCK_FAILURE_RATE` | `0.35` | mock corruption probability |
| `DRY_RUN` | auto | `1` = skip real training |
