# Failures: what the baseline gets wrong, and why

Baseline: Qwen2.5-7B-Instruct (bf16) on the frozen 189-filing holdout
`9789311604dce853`. Field F1 0.8395 (names policy), valid rate 54.5%
([RESULTS.md](RESULTS.md)). **162 of 189 filings** have at least one problem:
86 are invalid, and 76 more are valid but get some field wrong.

## The headline

**144 of the 162 failing filings (89%) fail only for reasons that can be
fixed without changing the model**: in the prompt, in code, in how the page
is rendered as text, or in the eval set. Among the 86 invalid filings, 78 have
no cause that needs a better model.

Five root causes account for most of it:

| # | Root cause | Filings | Of them invalid | Fix belongs in |
|---|---|---|---|---|
| 1 | Invented rows for an empty table | 63 | 53 | prompt |
| 2 | Transaction code copied from the A/D column | 47 | 29 | prompt |
| 3 | Owner checkboxes misread | 40 | 20 | code |
| 4 | Holdings lines reported as transactions | 42 | 28 | prompt |
| 5 | Table II rows read as Table I | 29 | 8 | text rendering |

A filing can have several causes, so the counts overlap.

## How this was produced

- Every failing filing was compared to gold **row by row** with the scorer's
  own alignment (`evalgate/failures.py`): matching, differing field by field,
  extra (invented) or missing.
- Root causes were **measured, not guessed**: each one below states the number
  behind it. They are encoded as rules in `scripts/prereview_failures.py`, so
  the same analysis can be re-run after every fix and compared.
- Provenance of the per-filing notes (`data/form4/failure_notes/`):
  - **26 decided by a person** in `scripts/failures_form4.py`, including all
    20 judgement calls the pre-review flagged. On all 20 the person accepted
    the suggested theme.
  - **136 accepted from the AI pre-review without individual review**, all of
    them cases matching a measured mechanical pattern. Recorded as
    `human_reviewed: false`.

---

## 1. Invented rows for an empty table (63 filings, 53 invalid)

**What happens.** When a filing has no Table II transactions (or no Table I
ones), the model adds a row anyway: a generic title like "Derivative
Security" or "Non-Derivative Securities", 0 shares, a made-up or `"N/A"`
code. BIO-TECHNE (0000914190-26-000142) has three real transactions, all of
which the model gets exactly right, plus nine invented rows that fail the code
and direction checks.

**Why this explains a surprise.** Simple filings were valid only 28% of the
time, filings with derivative rows 83%. The complex filings have something
real in both tables; the simple ones have an empty table to fill.

**Root cause.** The prompt asks for rows from "Table I AND Table II" and never
says what to do when a table is empty.

**Fix (prompt).** Say explicitly that an empty table contributes no rows.
Possibly also a code guard that drops rows whose title is a generic table
name with zero shares.

## 2. Transaction code copied from the A/D column (47 filings, 29 invalid)

**What happens.** The page shows
`Ordinary Shares | 10/09/2026 | P | 18,181(1) | A | $1.67 | ...` (QTREX,
0001185185-26-004675). The model's code is `A`, not `P`.

**Evidence.** 137 of the 139 wrong codes in the whole baseline equal that
row's own acquired/disposed letter. 30 filings get it wrong on every row.

**Why it matters more than the count.** `A` and `D` are themselves valid
transaction codes (grant, disposition to the issuer), and enrichment then
derives a direction that agrees. So most of these answers **pass
validation**: confidently wrong, and invisible to the schema. Only field-level
scoring against gold catches them.

**Fix (prompt).** Spell out that the code is the letter right after the date
(column 3), and that the A/D letter is a different field.

## 3. Owner checkboxes misread (40 filings, 20 invalid)

**What happens.** Pony AI (0001104659-26-113258) shows
`[X] Director | [ ] 10% Owner` and `[X] Officer`; the model answers
`is_director: false`. Elsewhere it marks people as officers when the box is
`[ ]`.

**Root cause.** The model reads the checkbox grid unreliably, even after the
text rendering was fixed to show `[X]`/`[ ]` explicitly.

**Fix (code).** The boxes are machine-readable in our rendering, so code can
derive the flags instead of asking the model. "Derive anything code can
derive" is the design rule this project started from. Multi-owner filings need
care: the page repeats box 5 once per owner, plus a header copy.

## 4. Holdings lines reported as transactions (42 filings, 28 invalid)

**What happens.** Table I lists holdings that didn't change, with no date and
no code, e.g. Viomi's `American depositary shares | 189,988 | I | By VioCloud
Limited`. The model reports them as transactions (37 filings). Table II has
the same with option holdings whose only dates are exercise and expiry dates,
which the model uses as a "transaction date" (5 filings).

**Fix (prompt).** A line with no transaction date and no code is a holding;
leave it out.

## 5. Table II rows read as Table I (29 filings, 8 invalid)

**What happens.** OpenWorld (0000891839-26-000467):
`Stock Option (Right to Buy) | $2.18 | 09/30/2026 | A | 216,355 (A) | ... | Common Stock | 216,355 | ...`
comes back as `title: "Common Stock", is_derivative: false`: the title of the
**underlying** security, in the wrong table. A related pattern (9 filings)
reports the derivative row and then again as an underlying-stock row.

**Root cause.** Nothing in the flattened text says which table a row came
from, and a Table II row contains a stock name (column 7).

**Fix (text rendering).** Label each rendered row with its table.

## 6. Prices missed (20 filings)

**What happens.** GrafTech (0000931148-26-000081): `Deferred Share Units | ...
| Common Stock | 3,476.4208 | $0 | 20,559.7025 | D`. The price of the
derivative is `$0` (column 8); the model answers `null`. Grants and gifts at
`$0` in Table I show the same.

**Fix (prompt).** `$0` is a price, not a blank; for Table II the price is
column 8.

## 7. Amounts: footnote markers and decimals (about 25 filings)

- **Footnote markers glued to numbers** (`18.324(1)`): across the baseline,
  share amounts printed with a marker are wrong 10% of the time, without one
  3%. Ventas (0001676558-26-000018): shares `18.324(1)`, the model answers
  67446. **Fix (text rendering):** separate markers from values.
- **Decimals dropped** (6): `67,446.241` becomes `67446`. **Fix (prompt).**
- **A number from another column** (4) and **amount misread** (5): column
  shifts with no marker or decimal explanation. Model capability.

## 8. Smaller patterns

| Pattern | Filings | Fix |
|---|---|---|
| A real row repeated under a footnote's title (RSUs, Restricted Stock) | 7 | prompt |
| A row built from a number that appears only in a footnote | 4 | prompt |
| Officer title filled with text from another box | 4 | prompt |
| Conversion (code C) direction | 2 | prompt |
| Code taken from the "V" column | 1 | prompt |
| `S/K` copied verbatim, not stripped to `S` | 1 | code (enrichment gap) |

---

## Eval-set questions (not model errors)

- **"See Remarks" officer titles (6 filings).** Gold, from the XML, says
  `See Remarks`; the model reads the remarks and gives the actual title, e.g.
  "SVP, Chief Human Resources Officer". The model is arguably right.
  **Open:** treat these as ambiguous (excluded), or count the resolved title
  as correct.
- **A price where the page shows none (1)** and **a title differing only by a
  defined-term suffix `("Common Stock")` (1).** The same convention questions
  as the labelled ambiguous filings.
- **Name formatting (resolved).** Issuer and owner names that differ only in
  form (`/PA/`, `Inc.` vs `INC`, `Last First` order) were counted wrong. A
  scoring policy now compares names by content: F1 0.8352 → 0.8395, with no
  model calls ([RESULTS.md](RESULTS.md)).

## Candidates for "a bigger model doesn't fix this"

Written before the test; the outcome is in [RESULTS.md](RESULTS.md), "Does a
bigger model fix it?". Qwen2.5-32B on the same pipeline fixed all three
below. What it did NOT fix were two failures this list did not predict:
marker-only prices turned into 0, and codes equal to their A/D letter turned
into `V`. Both come from following a prompt rule literally.

1. **Answering from memory.** SailPoint (0001820482-26-000033): the page says
   `SailPoint, Inc.`; the model writes `SailPoint Technologies Holdings,
   Inc.`, a name it knows. A bigger model knows more names, so this is a real
   candidate.
2. **Footnote-only prices** (the labelled ambiguous filings): a convention,
   not a reading problem.
3. **Runaway generation.** Cross Timbers (0001104659-26-115066) has two
   simple transactions; the model generated about 23,700 characters over 195
   seconds and broke its own JSON.

## Fix order

By invalid filings resolved per unit of work: placeholder rows (prompt), the
code column (prompt), holdings lines (prompt) and prices (prompt) in one
prompt revision; then owner checkboxes (code) and the `/K` gap (code); then
table labels and marker separation (text rendering). Each fix is measured on
the frozen holdout with a before/after per field in
[RESULTS.md](RESULTS.md).

## Where each cause stands (after the fixes)

Each fix was a candidate on the frozen holdout, promoted or rejected by the
gate; numbers and run records are in [RESULTS.md](RESULTS.md).

| Cause | Fixed in | Result |
|---|---|---|
| 1. Invented rows for empty tables | prompt v2, then code (drop placeholder rows) | gone |
| 2. Code copied from the A/D column | prompt v2 (a v4 rewording, rejected, removed the 32B-style `V` answers) | code recall 0.74 -> 0.92 |
| 3. Owner checkboxes misread | code: flags read off the page | 1.00 on every flag |
| 4. Holdings lines as transactions | render v3: rows labelled transaction / holding | 59 such extra rows -> 4 |
| 5. Table II rows read as Table I | render v3 labels | wrong-table rows 53 -> 45 |
| 6. Prices missed / footnote-only prices | prompt v2; code: marker-only price cell = null | price recall 0.83 -> 0.86 |
| 7. Footnote markers glued to values | render v3 splits them; code strips them from titles | |

**What is still wrong: rows the model leaves out.** Per transaction field, the
baseline actually found MORE of the gold values than the current version (date
recall 0.99 vs 0.93), because it reported almost every row: 549 of 553 matched,
4 missing, but 208 invented. Prompt v2's "leave holdings out, never invent a
row" cut the invented rows and also made the 7B drop real ones:

| Rows (553 in gold) | Baseline | Prompt v2 | Prompt v3 (rejected) | Render v3 | 32B |
|---|---|---|---|---|---|
| Matched | 549 | 514 | 535 | 516 | 553 |
| Missing | **4** | 39 | 18 | 37 | 0 |
| Extra | 208 | 141 | 143 | **52** | 0 |

F1 rewards the trade (0.84 -> 0.92), but a reader who needs every
transaction would not. Prompt v3's wording ("report every row") halved the
missing rows but was rejected under the old fixed-margin rule; with render v3
now doing the row selection, that wording is the obvious next candidate.

**Update (Fix 8, RESULTS.md):** the LoRA fine-tune trained on the 7B's own
verified failures fixed this: 547 of 553 rows matched, 6 missing, 0 extra,
F1 0.9932 (0.9923 on filings from companies and people unseen in training).
