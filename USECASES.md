# Two use cases, end to end

The loop is domain-agnostic. A domain supplies five things — schema,
registries, enrichment, prompts, generator — and everything else is shared.
These two prove it on completely different content.

Both have the same **shape**:

> fields you can read off the page  +  fields only your organisation knows

That second group is the whole problem. No model can know them, they appear
in no pretraining corpus, and they are exactly what a lookup or a fine-tune
has to supply.

---

## Use case 1 — Invoice extraction

**`DOMAIN=invoice`** · `autoextract.v2.db` · 1,000 live + 250 holdout

| Field | Where the answer lives |
|---|---|
| `vendor_id` | vendor registry (20 vendors) |
| `line_items[].category` | product taxonomy (30 codes) |
| `payment_terms` | policy: vendor tier × invoice total |

None are printed on the invoice. The model invents `VND-10001` when the
answer is `VND-00713` — plausibly formatted, entirely wrong.

### Run it

```powershell
$env:DB_PATH="autoextract.v2.db"; $env:DOMAIN="invoice"

python scripts\freeze_eval.py
python scripts\run_cycle.py --stage baseline
python scripts\run_cycle.py --stage serve --n 1000 --workers 5
python scripts\run_cycle.py --stage clusters
python scripts\run_cycle.py --stage repair --limit 1200 --workers 5
```

### Expected results

| Stage | Expect |
|---|---|
| Baseline, enrichment **off** | `valid_rate 0%`, `field_f1 ≈ 0.66` |
| Baseline, enrichment **on** | `valid_rate ≈ 95%`, `field_f1 ≈ 0.97` |
| Serve (enrichment on) | ~2% failures; the rest are misread dates |
| Clusters | led by `registry_vendor_id + taxonomy_category` |
| Repair | ~90%+ verified; **mechanical 0** once enrichment runs first |

`mechanical 0` is the tell that the architecture is right: enrichment already
did everything a lookup can, so the large model only sees the hard residue.

### Training result (measured)

| Run | mean_f1 | vs base | gate |
|---|---|---|---|
| `local-base` (Qwen2.5-1.5B) | 0.6147 | — | baseline |
| **`local-lora`** | **0.8158** | **+20.10pp** | promoted |
| `local-control` (rules stripped) | 0.6961 | +8.13pp | — |
| `local-undertrained` | 0.6298 | +1.51pp | **rejected** |

**The control is the result.** Identical documents, identical volume,
identical output format — only the rule knowledge removed. It gained +8.13.
The real one gained +20.10. The **+11.97pp difference is the repairs**, which
"any fine-tuning helps a weak model" cannot explain.

Per-error-type isolation:

| | base | lora | control |
|---|---|---|---|
| `taxonomy_category` | 114 | **85** | 115 |
| `string_type` | 110 | 0 | 0 |
| `string_pattern_mismatch` | 109 | 2 | 36 |

Both fixed format errors. **Only the repair-trained model learned category
codes.**

**Honest limitation:** `vendor_id` was not learned (115 vs the control's 114 —
no better than chance). Category codes carry semantic structure and recur
several times per document; a 5-digit vendor ID is arbitrary and appears once.
This is also why `valid_rate` stays at 0% for the local track: full validity
requires `vendor_id`.

---

## Use case 2 — Support email triage

**`DOMAIN=support_email`** · `autoextract.email.db` · 600 live + 200 holdout

| Field | Where the answer lives |
|---|---|
| `customer_id` | registry keyed on the sender's email domain |
| `category` | keyword taxonomy (BILLING / TECHNICAL / SHIPPING / RETURNS / ACCOUNT) |
| `priority` | policy: customer tier × category |

Same structure, no invoice logic anywhere. `klaus@bergmann-elektronik.de`
resolves to `CUS-00412`, tier gold; a BILLING ticket from a gold customer is
P1. None of that is in the email.

### Run it

```powershell
$env:DB_PATH="autoextract.email.db"; $env:DOMAIN="support_email"

python scripts\gen_emails.py --live 600 --holdout 200 --workers 5
python scripts\freeze_eval.py
python scripts\run_cycle.py --stage baseline
python scripts\run_cycle.py --stage serve --n 600 --workers 5
python scripts\run_cycle.py --stage clusters
python scripts\run_cycle.py --stage repair --limit 700 --workers 5
```

Or just `.\scripts\run_email_cycle.ps1`, which waits for generation and runs
the whole thing.

### Expected results

| Stage | Expect |
|---|---|
| Baseline, enrichment **off** | `valid_rate ≈ 0%` — it cannot know customer IDs |
| Baseline, enrichment **on** | high validity; the three rule fields are all derived |
| Clusters | `registry_customer_id`, `policy_priority`, `order_ref_format` |
| Repair | verified repairs feed the same dataset builder |

The point is not the numbers. It is that **nothing in the framework changed** —
validator, clustering, repair, gate and dashboard are untouched. Only
`domains/support_email.py` is new.

### Training

```powershell
$env:DB_PATH="autoextract.email.db"; $env:DOMAIN="support_email"
$env:COMPACT_PROMPT="1"

python scripts\score_local.py --label email-base
python scripts\train_local.py --epochs 3 --lora-r 16
python scripts\score_local.py --label email-lora --adapter models\lora-<stamp>
python scripts\selfheal_test.py compare --before email-base --after email-lora
python scripts\register_local.py --base email-base --lora email-lora --adapter models\lora-<stamp>
```

**Expected, based on the invoice run:** format and structure improve sharply;
`category` and `priority` improve partially (both are derivable from patterns);
`customer_id` improves least, because like `vendor_id` it is an arbitrary
5-digit code seen once per example.

If that holds, it replicates the invoice finding on new content — **training
teaches habits and structure, not arbitrary facts** — which is a stronger
claim than one domain alone supports.

---

## What to show, in order

1. **Live playground** — paste an invoice, watch it invent `VND-10001`
2. **Ceiling test** — `0.6571 → 0.9269` with the registry in prompt: the gap
   is knowledge, not capability
3. **Enrichment** — `0% → 98.3%` valid, latency 6.4s → 1.7s, cost ~0
4. **Training + control** — +20.10 vs +8.13, so +11.97 is the repairs
5. **The gate rejecting** — a weak candidate blocked at +1.51pp
6. **Switch `DOMAIN`** — the same loop on emails, framework unchanged

## The claim that survives scrutiny

> Most LLM pipelines cannot tell when they are wrong. This one detects
> failures mechanically, groups them so you can see what is actually
> breaking, routes each to the cheapest fix that works — a lookup, a retry, a
> config flag, occasionally training — and refuses to ship a change that
> cannot prove it helped.

Everything measured here supports that, including the parts that say training
was not the answer.
