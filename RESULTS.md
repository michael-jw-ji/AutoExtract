# Results

Snapshots, adapters and the database are gitignored (they are large and
reproducible), so the findings are recorded here.

Frozen holdout: **120 documents, hash `90c45a2b4f844847`**, 0 template-rendered.
Corpus: 300 live / 120 holdout, 20 vendors, 30 category codes.

---

## 1. The baseline gap (Baseten track, `inkling-small`)

| Metric | Value |
|---|---|
| `valid_rate` | 0.0% |
| `field_f1` | 0.6571 |
| `vendor_id` accuracy | 0% |
| `line_items.category` accuracy | 1% |
| every other field | 78–82% |

Every document fails, because three fields are not printed on it and live
only in `core/registry.py`, which the serving prompt never contains.

### Where the low per-field numbers actually come from

Roughly a quarter of documents return **no parseable JSON at all**, and those
score zero on every field:

| Subset | share | mean_f1 |
|---|---|---|
| `json_decode` failures | 27% | 0.0000 |
| parseable | 73% | 0.7466 |
| overall | | 0.5475 |

So "78% per-field" is not "misreads one field in five" — it is "one document
in four returns garbage, the rest read well."

## 2. The ceiling test — is the information sufficient?

Same model, same documents, registry pasted into the prompt:

| | `valid_rate` | `field_f1` |
|---|---|---|
| baseline (registry hidden) | 0.0% | 0.6571 |
| **ceiling (registry in prompt)** | **88.3%** | **0.9269** |

The gap is knowledge, not capability. It costs **8,471 characters of prompt on
every request** — which is what training exists to eliminate.

## 3. Local LoRA — Qwen2.5-1.5B, 214 examples, 3 epochs, rank 16

Trained on one RTX 5050 (8GB) in 12.3 min; train_loss 0.2854 → 0.0356,
token accuracy 93.4% → 98.8%.

| Run | mean_f1 | vs base | gate |
|---|---|---|---|
| `local-base` | 0.6147 | — | baseline |
| **`local-lora`** | **0.8158** | **+20.10pp** | promoted |
| `local-control` | 0.6961 | +8.13pp | (control) |
| `local-undertrained` | 0.6298 | +1.51pp | **rejected** |

### The negative control

The control saw identical documents, identical volume and identical output
format, with only the **rule knowledge** removed — its `vendor_id`,
`payment_terms` and `category` carry the model's own wrong values.

It gained +8.13pp. The repair-trained LoRA gained +20.10pp. **The +11.97pp
difference is attributable to the rule information in the repairs**, which
"any fine-tuning helps a weak model" does not explain.

| error type | base | lora | control |
|---|---|---|---|
| `taxonomy_category` | 114 | **85** | 115 |
| `string_type` | 110 | 0 | 0 |
| `string_pattern_mismatch` | 109 | 2 | 36 |
| `registry_unknown_vendor` | 37 | 5 | 6 |
| `registry_vendor_id` | 66 | 115 | 114 |

Both runs fixed format errors. Only the repair-trained model improved
category codes.

### What did NOT work

**`vendor_id` was not learned** — 115 for the LoRA vs 114 for the control, no
better than chance. Category codes carry semantic structure (`HW-FAST-02` ↔
"Hex bolt") and recur several times per document; a 5-digit vendor ID is
arbitrary and appears once. Far less signal per example.

This is also why `valid_rate` stays at 0% across every run: full schema
validity requires `vendor_id`, and it is usually still wrong.

### Reading the apparent regressions

`registry_vendor_id` rising 66 → 115 is **unmasking**, not regression:

1. Documents that previously died on format errors never reached the vendor
   check. Now they do.
2. `registry_unknown_vendor` fell 37 → 5 — the model learned to emit vendor
   names that exist in the registry, so those documents graduated from
   "vendor not found" to "vendor found, ID wrong."

## 4. Corpus contamination (a result worth recording)

An early rate-limited generation run silently fell back to template rendering,
and **49% of an 80-document holdout was trivially easy** before anyone noticed.

| Metric | Contaminated | Clean | Δ |
|---|---|---|---|
| `mean_f1` | 0.7861 | 0.5475 | −23.9pp |
| `field_f1` | 0.6839 | 0.6571 | −2.7pp |

The `field_f1` shift is the alarming one: **2.7pp of measurement artifact
against a promotion margin of 2.0pp.** Eval-set hygiene is the same order of
magnitude as the signal being gated on. `freeze_holdout()` now refuses to
freeze a holdout containing template documents.

## 5. Open

- **Baseten Training Jobs: 403** for team `qzzvoxq`. Reads work
  (`train project list` → 200), creates do not. A workspace permission flag.
- **JSON mode** (`JSON_MODE=1`) is verified working and not yet used for a full
  cycle. Format fixes were ~40% of the LoRA's gain; one API parameter delivers
  them for free and frees the whole LoRA budget for rules.
- **`payment_terms` is a weak rule** under policy v1 — 78% baseline but only ~6
  of 300 documents failed on it, because NET_30 is both the model's default
  guess and v1's answer for the commonest case. `POLICY_VERSION=2` fixes this
  but requires regenerating the corpus.

---

## Cheap at scale: does a 1.5B + LoRA match the hosted model?

The question was whether training buys the same quality for less money, or
only looks good against a weak baseline. Answered on the **same frozen
holdout** (`90c45a2b4f844847`, n=120), the **same metric** (`mean_f1`), the
same enrichment and the same compact prompt — measured with
`scripts/score_hosted.py`, which exists precisely so this comparison is not
`mean_f1` against `field_f1`.

| Model | valid_rate | mean_f1 | median latency |
|---|---|---|---|
| `inkling-small` (hosted) | 95.8% | 0.9748 | 1079 ms |
| Qwen2.5-1.5B (local, base) | 50.8% | 0.8295 | — |
| **Qwen2.5-1.5B + LoRA (local)** | **96.7%** | **0.9665** | — |

**The local 1.5B with a LoRA matches the hosted model.** It is +0.8pp *ahead*
on valid_rate and 0.83pp behind on mean_f1 — inside the project's own 2.0pp
promotion margin, i.e. not a difference this system would call real.

Training moved the local model +45.9pp valid_rate and +13.7pp mean_f1 over its
own base. That is the case where training is the right tool: the knowledge is
stable, the volume is high, and the alternative is paying for a larger hosted
model on every request forever.

**Caveat worth stating.** Both sides run enrichment, so the registry fields are
code-derived on both. What the LoRA bought is format and structure discipline
at 1.5B scale — which is exactly what the earlier control isolated. It did not
learn arbitrary IDs, and this result does not claim it did.
