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

Small model `qwen2.5-7b-16k` (qwen2.5:7b, 16K context) on local Ollama;
enrichment ON; JSON mode ON; extraction `max_tokens` 8192.

## Baseline

_Pending: `python scripts/run_cycle.py --stage baseline --domain form4`._
