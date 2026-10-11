# Results: SEC Form 4

Every number here is measured against the frozen holdout below. A number
measured against any other set is not comparable and does not go in this file.

## Frozen holdout

| | |
|---|---|
| **Set hash** | `9789311604dce853` |
| Frozen at | 2026-10-11 20:04:47 UTC |
| Code at freeze | commit `513e087` |
| Documents | 189 real EDGAR Form 4 filings (filed 2026-09-25 to 2026-10-09) |
| Transactions | 553 rows across Table I and Table II |
| Gold source | each filing's own XML, parsed and validated against the `Form4` schema |
| Human-confirmed | 44 filings, AI-assisted (see below) |
| Ambiguous | 5 filings, 9 field paths, excluded from scoring on both sides |

Composition by tag (a filing can have several): derivative rows 88, multiple
reporting owners 13, a blank price 37, 10+ rows 8, amendments (4/A) 8, no
transactions 5.

The hash is also stored in `form4.db` (`eval_freeze`), and `build_dataset()`
hard-fails if any of these 189 documents reaches training data.

### How the 44 labels were made

Each filing's XML gold was checked against the rendered page. A script compared
every transaction row field by field (and was tested against planted errors);
an AI pre-review (`data/form4/review/`) wrote notes on the judgement calls; a
human made every decision in `scripts/label_form4.py`. The label files record
`assisted_by`. Call these "AI-assisted, human-confirmed", not "hand-labelled".

The 5 ambiguous filings, with the reason:

| Filing | Field(s) | Why |
|---|---|---|
| 0000921895-26-002756 | price | price column shows only a footnote; the $0.00001 exercise price is in footnotes 1 and 4 |
| 0001437749-26-031682 | price | merger cash-out; price column footnote-only, footnote 1 states $17.25 per share |
| 0001123292-26-001333 | price | forward-contract settlement; footnote 3 states a $17.83 settlement price |
| 0001140361-26-039027 | date, shares, price | "purchases" are aggregates of many trades over a period, dated by period end |
| 0000912282-26-001310 | owner roles | box 5 checkboxes and the Remarks disagree on who is a director/officer |

## Pipeline config for the numbers below

Small model `Qwen/Qwen2.5-7B-Instruct` in full precision (bf16), served by
vLLM on a Modal L40S (`infra/modal_small.py`), 16K context, 8 requests at a
time; enrichment ON; JSON mode ON; extraction `max_tokens` 8192.

These are the same weights hosted training fine-tunes, so a later fine-tuned
model is compared like for like. Numbers from local Ollama (a 4-bit
compressed build of the same model) are not comparable and are not reported
here.

## Baseline (Milestone 1)

`python scripts/run_cycle.py --stage baseline --domain form4`, 2026-10-11,
eval row 1, config `enrich+json`, holdout `9789311604dce853`.

| Metric | Value |
|---|---|
| **Field F1** | **0.8352** |
| Valid rate (passes schema + rules) | 54.5% (103 of 189) |
| Failure rate | 45.5%: at the top edge of the 25-45% target band, so there is plenty to learn from |
| Latency per filing | p50 9.8 s, p99 74.5 s (8 concurrent requests) |

### Per-field accuracy

Over the fields gold has a value for; rows are paired with gold rows by best
overlap; the 9 ambiguous field paths are excluded.

| Field | Accuracy |
|---|---|
| `transactions.*.code` | 0.741 |
| `reporting_owners.*.is_director` | 0.858 |
| `reporting_owners.*.is_officer` | 0.875 |
| `issuer_name` | 0.905 |
| `transactions.*.is_derivative` | 0.910 |
| `reporting_owners.*.name` | 0.918 |
| `transactions.*.price_per_share` | 0.921 |
| `reporting_owners.*.officer_title` | 0.935 |
| `transactions.*.shares` | 0.945 |
| `transactions.*.acquired_disposed` | 0.948 |
| `transactions.*.security_title` | 0.949 |
| `transactions.*.shares_owned_after` | 0.968 |
| `reporting_owners.*.is_ten_percent_owner` | 0.984 |
| `issuer_ticker` | 0.989 |
| `transactions.*.direct_or_indirect` | 0.991 |
| `transactions.*.date` | 0.993 |
| `issuer_cik` | 0.995 |
| `period_of_report` | 0.995 |

### Valid rate by filing type

| Tag | Valid |
|---|---|
| no transactions | 1 of 5 (20%) |
| plain (no derivatives, one owner, short) | 22 of 80 (28%) |
| multiple owners | 6 of 13 (46%) |
| 10+ rows | 5 of 8 (62%) |
| amendment | 5 of 8 (62%) |
| has derivative rows | 73 of 88 (83%) |
| blank price | 33 of 37 (89%) |

Simple filings fail most. That is the opposite of what was expected, and the
first look below explains it.

### Validation errors

| Count | Error |
|---|---|
| 60 | `literal_error:transactions.*.acquired_disposed` |
| 55 | `greater_than:transactions.*.shares` |
| 39 | `decimal_type:transactions.*.shares_owned_after` |
| 15 | `enum:transactions.*.code` |
| 13 | `owned_after_mismatch:transactions.*.shares_owned_after` |
| 6 | `decimal_parsing:transactions.*.shares_owned_after` |
| 3 | `literal_error:transactions.*.direct_or_indirect` |
| 2 | `string_too_short:transactions.*.security_title` |
| 1 | `json_decode` |

### First look at the failures (to be confirmed in Milestone 2)

79 of the 86 invalid filings contain **more rows than gold**, and in 70 of
them the extra rows have no real shares or direction. Two kinds so far:

1. **Placeholder rows for an empty table.** A filing with no Table II gets an
   invented row anyway, e.g. `{"security_title": "Derivative Security",
   "shares": 0, ...}`, sometimes with `"N/A"` codes. This is why plain filings
   fail and filings with real derivative rows pass.
2. **Holdings rows extracted as transactions**, e.g. a `ADS | 189,988 | I`
   line with no date or code.

The full analysis, with root cause and the right layer to fix each theme in,
goes in `FAILURES.md`.
