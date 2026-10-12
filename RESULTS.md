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
| **Field F1** | **0.8395** under the names policy below (0.8352 strict) |
| Valid rate (passes schema + rules) | 54.5% (103 of 189) |
| Failure rate | 45.5%: at the top edge of the 25-45% target band, so there is plenty to learn from |
| Latency per filing | p50 9.8 s, p99 74.5 s (8 concurrent requests) |

### Scoring policy: names (decided 2026-10-11)

Issuer and owner names are compared by content, not form: case, punctuation,
a trailing EDGAR state suffix (`/PA/`, `\PA\`), spelled-out corporate
suffixes (Corporation = Corp) and word order are ignored. Spelling and any
different word still count. Rationale: `AMERISERV FINANCIAL INC` vs
`AMERISERV FINANCIAL INC /PA/`, or `Joshua Reed` vs `Reed Joshua`, name the
same party; scoring them wrong measured formatting, not reading.

Applied by re-scoring the RECORDED answers (no model calls), so both numbers
describe the same 189 answers:

| Scoring | Field F1 | Valid rate | issuer_name | owner name | Run record |
|---|---|---|---|---|---|
| strict (as first scored) | 0.8352 | 54.5% | 0.905 | 0.918 | `results/runs/2026-10-11_baseline_RECONSTRUCTED` |
| names policy | **0.8395** | 54.5% | 0.989 | 0.984 | `results/runs/2026-10-12T012930Z_rescore` |

Valid rate is unchanged: validation never depended on name formatting. All
later results use the names policy.

### Per-field accuracy (strict scoring, as first measured)

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

## Fix 1: prompt v2 (promoted, with regressions)

Candidate `prompt-v2` (model_version 2): same model, prompt revised for the
five prompt-layer causes in [FAILURES.md](FAILURES.md): invented rows for
empty tables, code copied from the A/D column, holdings lines, `$0` prices,
dropped decimals. Prompt hash `b4955a08` (baseline `4b5b159a`). Run record
`results/runs/2026-10-12T013929Z_candidate`.

| | Baseline | Prompt v2 | Change |
|---|---|---|---|
| Valid rate | 54.5% | **80.4%** | **+25.9 pp** |
| Field F1 | 0.8395 | **0.8606** | +2.11 pp |
| Gate | | **promoted** | needed +2.00 pp F1 and no valid-rate drop |

Rows, out of the 553 real transaction rows in the holdout:

| Rows | Baseline | Prompt v2 |
|---|---|---|
| exactly right | 329 | **383** |
| some field wrong | 220 | 131 |
| invented (extra) | 208 | 141 |
| **real rows dropped** | **4** | **39** |

Per field:

| Field | Baseline | Prompt v2 | Change |
|---|---|---|---|
| `transactions.*.code` | 0.741 | 0.881 | **+13.9** |
| `reporting_owners.*.is_officer` | 0.875 | 0.925 | +5.0 |
| `reporting_owners.*.is_director` | 0.858 | 0.895 | +3.8 |
| `transactions.*.acquired_disposed` | 0.948 | 0.913 | -3.4 |
| `transactions.*.date` | 0.993 | 0.943 | -5.0 |
| `transactions.*.shares` | 0.945 | 0.895 | -5.0 |
| `transactions.*.shares_owned_after` | 0.968 | 0.911 | -5.6 |
| `transactions.*.direct_or_indirect` | 0.991 | 0.926 | -6.5 |
| `transactions.*.is_derivative` | 0.910 | 0.834 | -7.6 |
| `transactions.*.security_title` | 0.949 | 0.870 | -8.0 |
| `transactions.*.price_per_share` | 0.921 | 0.825 | **-9.5** |

(Fields that moved by 1.2 pp or less are omitted.)

**Read this before quoting the headline.** The +25.9 pp valid rate and the
+13.9 pp on codes are real, but eight transaction fields went DOWN. Nearly all
of that is the 35 extra real rows the model now drops: per-field accuracy is
measured over gold rows, so a dropped row counts every one of its fields as
wrong. 88 filings got better, 60 worse.

Why rows were dropped:
- **"Never output a row with 0 shares" was misapplied to shares OWNED AFTER.**
  Rows ending with 0 owned after (conversions, full sales) are dropped 26% of
  the time, other rows 5%. A wording bug in the prompt.
- **37 of the 39 dropped rows are in 6 filings.** On one long United
  Therapeutics filing (18 rows) the model returned 5, four of them holdings
  lines, the exact thing the prompt now forbids: a breakdown on one long
  filing, not a misread rule.

**What this says about the gate.** It promoted on one number (field F1,
+2.11 pp, just over the bar) while eight per-field scores fell. A single
aggregate can hide regressions that matter; that is the case for Milestone 3's
CI rule, "fail if any scored field drops more than a threshold".

## Fix 2: prompt v3 (rejected by the gate)

Candidate `prompt-v3` (model_version 3), compared against the incumbent
`prompt-v2`. One wording change: v2's "never output a row with 0 shares" was
being applied to shares OWNED AFTER, so v3 says shares owned after can be 0
and asks for every transaction row. Prompt hash `ef581452`. Run record
`results/runs/2026-10-12T014852Z_candidate`.

| | Baseline | Prompt v2 (incumbent) | Prompt v3 |
|---|---|---|---|
| Field F1 | 0.8395 | 0.8606 | **0.8729** |
| Valid rate | 54.5% | **80.4%** | 76.7% |
| Rows exactly right (of 553) | 329 | 383 | **411** |
| Real rows dropped | 4 | 39 | 18 |
| Rows invented | 208 | 141 | 143 |
| Gate | | promoted | **rejected**: +1.23 pp < +2.00 pp |

v3 fixed what it targeted (dropped rows 39 → 18) but "report every row" let
non-transaction lines back in: invalid codes 21 → 33, missing dates 5 → 13,
zero-share rows 4 → 9, and 9 filings went from valid to invalid. The two
prompt rules pull against each other. Row selection probably belongs in code
or rendering, not in more prompt wording.

### Is the gate's +2.0 pp margin the right test? (paired bootstrap)

2,000 paired bootstrap resamples over the 189 filings (same filings in both
arms), field F1:

| Change | Δ F1 | 95% interval | P(Δ ≤ 0) | Gate said |
|---|---|---|---|---|
| baseline → v2 | +2.12 pp | [-0.44, +4.63] | 5.1% | promoted |
| v2 → v3 | +1.22 pp | [-0.07, +3.13] | 4.1% | rejected |
| baseline → v3 | +3.34 pp | [+1.28, +5.28] | 0.1% | (not compared) |

**The fixed margin is miscalibrated at n = 189.** It promoted a change whose
interval includes zero and rejected one with equally strong evidence. A fixed
margin is a crude stand-in for "this is not noise"; a paired-bootstrap rule
(lower bound of the interval above zero, plus no per-field regression beyond
a threshold) would test that directly.

**The rejection stands as recorded.** Changing the gate after seeing which
side of it a result landed on is how evaluations get gamed. Any change to the
rule applies to future candidates only.

### Gate policy from here on (decided 2026-10-11)

From the next candidate, the gate promotes only if all three hold: the
paired-bootstrap 95% interval of the field-F1 gain is above zero; no scored
field drops more than 2.0 points; the valid rate does not drop
(`GATE_POLICY=bootstrap`, `evalgate/stats.py`). The prompt-v2 promotion and
the prompt-v3 rejection were made under the old rule and stand.

For reference only (not recorded as decisions), the new rule applied to the
past comparisons:

| Comparison | Δ F1, 95% interval | Fields down > 2 pts | New rule |
|---|---|---|---|
| v2 vs baseline | +2.12 [-0.44, +4.63] | 8 | reject |
| v3 vs v2 | +1.22 [-0.06, +3.15] | 0 | reject (interval; valid rate) |
| v3 vs baseline | +3.34 [+1.37, +5.35] | 6 | reject (field regressions) |

None of the prompt revisions is a clean improvement: each traded fields for
other fields. The next fixes target the causes that can be fixed in code,
where a fix should not trade anything.

## Fix 3: code fixes after the model (promoted, first under the new gate)

Candidate `code-fixes-1` (model_version 4) is prompt v2 plus three changes to
`enrich()` (post-processing version 2, cache label `enrich2+json`):

1. **Owner role flags come from the page.** The `[X]`/`[ ]` boxes are
   rendered from the filing's XML, so code reads them instead of the model.
   Multi-owner pages repeat owner 1's block in the header and then give one
   block per owner; owners are matched by name, or by position when the
   counts agree. Checked on all 528 stored filings: the lookup recovers every
   owner's flags exactly, even from an answer with every flag inverted.
2. **`S/K` and `J/K` become `S` and `J`.** The `/K` marks an equity swap,
   which the XML stores as a separate flag.
3. **Rows no filing can contain are dropped:** a generic "Derivative
   Security" title, 0 shares transacted, or neither date nor code. None of
   the 1,177 gold rows has any of these.

**Measured by replay, not by calling the model.** The prompt is unchanged, so
the model would have been sent identical requests. The candidate's answers
are prompt v2's recorded answers re-run through the new post-processing
(`run_cycle.py --stage candidate --replay-from 2`). Both sides of the
comparison hold the very same model answers, so the difference is the code
change and nothing else. Run record: `results/runs/2026-10-12T171134Z_candidate.*`.

| | Prompt v2 (incumbent) | Code fixes 1 |
|---|---|---|
| Field F1 | 0.8606 | **0.8708** |
| Valid rate | 80.4% | **84.1%** |
| Gate | | **promoted**: +1.02 pts, 95% interval [+0.65, +1.47]; no field down; valid +3.7 pts |

What changed, per field (every other scored field is unchanged to 4 places):

| Field | Prompt v2 | Code fixes 1 | Δ pts |
|---|---|---|---|
| reporting_owners.*.is_director | 0.8954 | 1.0000 | +10.5 |
| reporting_owners.*.is_officer | 0.9247 | 1.0000 | +7.5 |
| transactions.*.code | 0.8807 | 0.8861 | +0.5 |
| reporting_owners.*.is_ten_percent_owner | 0.9959 | 1.0000 | +0.4 |

48 flags were corrected across 32 filings and 9 rows dropped from 5. The
dropped rows do not move any per-field number (those count gold fields
found); they raise precision, which is part of the F1 gain.

Under the old fixed +2.0 margin this would have been rejected: a gain that
is small but certain is exactly what that rule could not see.

### Scoring policy: "See Remarks" titles (decided 2026-10-12)

In 6 holdout filings the officer title box says "See Remarks" and the real
title is written in the Remarks section. The XML keeps the pointer; a reader
reports the title. Both are right, so `officer_title` on those filings is now
ambiguous **by rule** (`Form4Domain.ambiguous_fields`) and scored for neither
side, like the hand-marked ambiguous fields. No stored data changed.

Every version was re-scored from its own recorded answers (no model calls,
no gate decisions re-made; `results/runs/2026-10-12T1726*_rescore.*`):

| Version | Field F1 before | After | Valid rate |
|---|---|---|---|
| base | 0.8395 | 0.8401 | 54.5% |
| prompt-v2 | 0.8606 | 0.8614 | 80.4% |
| prompt-v3 (rejected) | 0.8729 | 0.8736 | 76.7% |
| **code-fixes-1** (incumbent) | 0.8708 | **0.8716** | 84.1% |

All move by +0.06 to +0.08, so no ranking or past decision changes.

## Fix 4: rendering v2 (rejected)

Candidate `render-v2` (model_version 5) changes only the text the model
reads, now rendered at serve time from the raw HTML (FAILURES.md causes 5
and 7): each data row starts with `[Table I]` / `[Table II]`, and a footnote
marker glued to a value is split off (`18.324(1)` -> `18.324 (1)`). Prompt v2
and post-processing v2 unchanged. A fresh model run (189 filings on Modal);
record `results/runs/2026-10-12T173428Z_candidate.*`.

| | Code fixes 1 (incumbent) | Render v2 |
|---|---|---|
| Field F1 | 0.8716 | 0.8721 |
| Valid rate | 84.1% | 83.6% |
| Gate | | **rejected**: +0.05 pts, 95% interval [-0.70, +0.81] |

| Rows (553 in gold) | Code fixes 1 | Render v2 |
|---|---|---|
| Matched to a gold row | 514 | 508 |
| Matched but in the wrong table | 53 | **45** |
| Missing | 39 | 45 |
| Extra | 132 | 127 |

The table labels did what they were for (wrong-table rows -15%), but three
filings lost two real rows each, which cancelled it. Reading them found two
problems with the rendering itself, not the idea:

- **A rendering bug.** In Table II the `(A)`/`(D)` suffix was added before
  the marker split, so `1,002(1)(2) (A)` stayed glued and inconsistent with
  the other cells; the model returned no rows for that filing.
- **Holdings lines got the same `[Table I]` label as transactions**, e.g.
  `[Table I] Common Stock | 166 | I | by Spouse`, which makes a holding look
  more like a transaction, not less.

Holdings lines are also the largest cause of the incumbent's 132 extra rows
(60 of them), despite prompt v2's instruction. Whether a row is a holding is
fixed by the form: no transaction date and no code. That is render v3.

## Fix 5: rendering v3 (promoted)

Candidate `render-v3` (model_version 6): every data row is labelled
`[Table I transaction]`, `[Table II transaction]` or `[Table I/II holding,
not a transaction]`, decided by the form's own transaction-date and code
cells (both present or both absent on all 1,475 data rows of the corpus),
and footnote markers are split off before Table II's `(A)`/`(D)` suffix.
Prompt v2 and post-processing v2 unchanged. Fresh model run; record
`results/runs/2026-10-12T183004Z_candidate.*`.

| | Code fixes 1 (incumbent) | Render v3 |
|---|---|---|
| Field F1 | 0.8716 | **0.9182** |
| Valid rate | 84.1% | **96.3%** |
| Gate | | **promoted**: +4.66 pts, 95% interval [+2.63, +6.70]; no field down more than 2 pts; valid +12.2 pts |

| Rows (553 in gold) | Code fixes 1 | Render v3 |
|---|---|---|
| Matched to a gold row | 514 | 516 |
| Matched but in the wrong table | 53 | 45 |
| Missing | 39 | 37 |
| Extra | 132 | **52** |
| ...of which holdings lines | 59 | **4** |

| Field | Code fixes 1 | Render v3 | Δ pts |
|---|---|---|---|
| transactions.*.price_per_share | 0.8254 | 0.8617 | +3.6 |
| transactions.*.code | 0.8861 | 0.9186 | +3.2 |
| transactions.*.is_derivative | 0.8336 | 0.8517 | +1.8 |
| transactions.*.shares_owned_after | 0.9114 | 0.9259 | +1.4 |
| transactions.*.shares | 0.8952 | 0.9026 | +0.7 |
| transactions.*.direct_or_indirect | 0.9259 | 0.9313 | +0.5 |
| reporting_owners.*.name | 0.9959 | 1.0000 | +0.4 |
| transactions.*.acquired_disposed | 0.9132 | 0.9150 | +0.2 |
| period_of_report | 1.0000 | 0.9947 | -0.5 |
| transactions.*.security_title | 0.8698 | 0.8644 | -0.5 |
| transactions.*.date | 0.9430 | 0.9320 | -1.1 |

The lesson across Fixes 1-5: telling the model which rows to skip (prompt
v2, v3) traded fields; **showing** it, in the input, with a label computed
from the form's structure, removed 55 of 59 holdings rows at once. The
prompt said the same thing all along; the page now says it on every line.
