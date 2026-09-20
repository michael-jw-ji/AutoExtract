# Use cases you can run

Every command here works right now. Copy, paste, watch.

---

# THE 30-SECOND DEMO

Two commands, one document, same model. This is the whole argument.

```powershell
python scripts\demo_case.py examples\invoice_missing_totals.txt --raw
python scripts\demo_case.py examples\invoice_missing_totals.txt
```

| | `--raw` (enrichment off) | default (enrichment on) |
|---|---|---|
| verdict | **INVALID** — 7 errors | **VALID** |
| `vendor_id` | `VND-00001` *(invented)* | `VND-01530` ✓ |
| 5 category codes | all 5 wrong | all 5 correct ✓ |
| field F1 | 0.6 range | **0.8947** |
| latency | ~1.3 s | ~3.6 s |

What the first run prints:

```
registry_vendor_id    vendor_id
  vendor_id 'VND-00001' != registry id for 'Solvent Chemical Co' (VND-01530)
taxonomy_category     line_items.0
  category SA-BOOT-10 != taxonomy code for this product (PE-BOOT-07)

failure signature  registry_vendor_id:vendor_id|taxonomy_category:line_items.*
```

**Say this:** "`SA-BOOT-10` is not a typo — it's a confident invention. It has
the right shape and it's completely wrong, because the real code only exists
in our systems. No bigger model fixes that."

### The second thing to point at

The enriched run is **VALID** and still prints:

```
tax     got '0.00'      want '1221.75'
total   got '24435.03'  want '25656.78'

NOTE  this passed the schema and is still wrong - which is exactly why
      a repair must be checked against gold before it can train anything.
```

This invoice never prints its totals, so the model guessed zero tax and the
arithmetic is self-consistent around a wrong number. **Schema-valid is not
correct.** That distinction is why `repairs.verified` exists and why an
unverified repair never reaches a training set.

### Other examples

```powershell
python scripts\demo_case.py examples\invoice_currency_symbols.txt   # mixed currency symbols
python scripts\demo_case.py examples\invoice_scanned_fax.txt        # OCR artefacts, 5 line items
```

Each `.txt` has a sibling `.gold.json`, which is how the run can say
"correct" rather than just "parsed".

---

## Use case 1 — Invoices `DOMAIN=invoice`

| Field | Where the answer lives |
|---|---|
| `vendor_id` | vendor registry, 20 vendors |
| `line_items[].category` | product taxonomy, 30 codes |
| `payment_terms` | policy: vendor tier × invoice total |

None are printed on the invoice.

### Run the full loop

```powershell
$env:DB_PATH="autoextract.v2.db"; $env:DOMAIN="invoice"

python scripts\freeze_eval.py
python scripts\run_cycle.py --stage baseline
python scripts\run_cycle.py --stage serve   --n 1000 --workers 5
python scripts\run_cycle.py --stage clusters
python scripts\run_cycle.py --stage repair  --limit 1200 --workers 5
```

| Stage | Expect |
|---|---|
| Baseline, enrichment **off** | `valid_rate 0%`, `field_f1 ≈ 0.66` |
| Baseline, enrichment **on** | `valid_rate ≈ 95%`, `field_f1 ≈ 0.97` |
| Serve | ~2% failures; the rest are misread dates |
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
requires `vendor_id`. Say it plainly — it's a finding, not a flaw.

---

## Use case 2 — Support email triage `DOMAIN=support_email`

| Field | Where the answer lives |
|---|---|
| `customer_id` | registry keyed on the sender's email domain |
| `category` | keyword taxonomy |
| `priority` | policy: customer tier × category |

`klaus@bergmann-elektronik.de` → `CUS-00412`, tier gold; a BILLING ticket from
a gold customer is P1. None of that is in the email.

The point is that **nothing in the framework changed** — validator, clustering,
repair, gate and dashboard are untouched. Only `domains/support_email.py` is
new.

### Run it

```powershell
.\scripts\rebuild_email.ps1     # generate -> freeze -> baseline -> serve -> repair -> dataset
```

Or by hand:

```powershell
$env:DB_PATH="autoextract.email.db"; $env:DOMAIN="support_email"

python scripts\gen_emails.py --live 600 --holdout 200 --workers 5
python scripts\freeze_eval.py
python scripts\run_cycle.py --stage baseline
python scripts\run_cycle.py --stage serve --n 600 --workers 5
python scripts\run_cycle.py --stage clusters
python scripts\run_cycle.py --stage repair --limit 700 --workers 5
```

### Training

```powershell
$env:COMPACT_PROMPT="1"
python scripts\score_local.py  --label email-base
python scripts\train_local.py  --epochs 3 --lora-r 16
python scripts\score_local.py  --label email-lora --adapter models\lora-<stamp>
python scripts\selfheal_test.py compare --before email-base --after email-lora
python scripts\register_local.py --base email-base --lora email-lora --adapter models\lora-<stamp>
```

### Status: corpus rebuilding — numbers pending

The first email corpus was unusable and the failure was silent. Generation
inherited `JSON_MODE=1` from the environment, which forced the **renderer**
into JSON mode even though its prompt asks for prose. It returned fragments
like `[2026.08, "a plain business email"]` — 9 to 35 characters. All 800
"emails" were fragments.

Downstream this looked exactly like model failure: 95.8% failure rate,
placeholder outputs (`alice.smith@company.com`, `TKT-000001`), zero verified
repairs. The model wasn't failing; it had nothing to read.

Fixed in `scripts/gen_emails.py`: `json_mode=False` is now explicit, and a
render is **dropped** unless it is ≥120 characters, is not JSON-shaped, and
actually contains the `ticket_ref`, `sender_email` and every `order_ref` it
was told to include. `scripts/gen_docs.py` had the same latent leak and got
the same treatment — the invoice corpus escaped only because it predates JSON
mode.

**Expected once the rebuild lands**, based on the invoice run: format and
structure improve sharply; `category` and `priority` improve partially (both
follow patterns); `customer_id` improves least, because like `vendor_id` it is
an arbitrary 5-digit code seen once per example. If that holds it replicates
the invoice finding on new content — **training teaches habits and structure,
not arbitrary facts** — which is a stronger claim than one domain supports.

---

## What to show, in order

1. **`demo_case.py --raw` then without** — invented IDs, then correct, 30 seconds
2. **The `NOTE`** — valid and still wrong, so repairs must be verified
3. **Ceiling test** — `0.6571 → 0.9269` with the registry in prompt: the gap
   is knowledge, not capability
4. **Enrichment at scale** — `0% → 98.3%` valid, 6.4 s → 1.7 s, cost ~0
5. **Training + control** — +20.10 vs +8.13, so +11.97 is the repairs
6. **The gate rejecting** — a weak candidate blocked at +1.51pp
7. **Switch `DOMAIN`** — the same loop on emails, framework unchanged

## The claim that survives scrutiny

> Most LLM pipelines cannot tell when they are wrong. This one detects
> failures mechanically, groups them so you can see what is actually
> breaking, routes each to the cheapest fix that works — a lookup, a retry, a
> config flag, occasionally training — and refuses to ship a change that
> cannot prove it helped.

Everything measured here supports that, including the parts that say training
was not the answer.
