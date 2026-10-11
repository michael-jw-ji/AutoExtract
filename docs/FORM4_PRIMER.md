# Form 4: a primer

Enough to read any filing in this corpus fluently and talk about it in an
interview. Every example is a real filing from this project's data.

---

## 1. What a Form 4 is

US securities law (Section 16 of the Securities Exchange Act of 1934) treats
three kinds of people as **insiders** of a public company:

- its **directors**,
- its **officers** (CEO, CFO, general counsel and so on),
- anyone who owns **more than 10%** of a class of its stock.

When an insider's holdings change, they must report it on **Form 4** within
**two business days**. That deadline is why Form 4s are a near-real-time
signal: investors watch them to see what the people who know the company best
are doing with their own money.

Related forms you will see mentioned:

| Form | When |
|---|---|
| Form 3 | the initial statement, when someone first becomes an insider |
| **Form 4** | each change in holdings, within 2 business days |
| Form 5 | an annual catch-up for small or exempt transactions |
| Form 4/A | an **amendment**, correcting or completing an earlier Form 4 |

Section 16 also has a "short-swing profit" rule, 16(b): an insider who buys
and sells (or sells and buys) within six months must hand any profit back to
the company. It is why insiders care so much about how each transaction is
coded.

---

## 2. The boxes at the top

| Box | What it says | Our field |
|---|---|---|
| 1 | name and address of the reporting person (Last, First, Middle) | `reporting_owners[].name` |
| 2 | issuer name and **ticker** in brackets: `UNITED THERAPEUTICS Corp [ UTHR ]` | `issuer_name`, `issuer_ticker` |
| 3 | date of earliest transaction | `period_of_report` |
| 4 | date of the original filing, if this is an amendment | (not extracted) |
| 5 | **relationship**: checkboxes for Director, Officer (with title), 10% Owner, Other | `is_director`, `is_officer`, `is_ten_percent_owner`, `officer_title` |
| 6 | filed by one person, or by more than one | (implied by the owner count) |

In our text rendering, box 5 looks like this. Each `[X]` belongs to the label
right after it:

```
[X] Director | [ ] 10% Owner
[X] Officer (give title below) | [ ] Other (specify below)
Chairperson & CEO
```

The **issuer CIK** (the SEC's ID for the company) isn't printed on the page.
We add it as the first line from EDGAR's metadata, so the model copies it and
doesn't have to guess.

There's also a checkbox for **Rule 10b5-1**: a plan the insider set up in
advance that trades automatically. It is a defence against accusations of
trading on inside information, and it's why many sales say "pursuant to a
10b5-1 plan" in a footnote.

---

## 3. Table I: non-derivative securities (shares)

Ordinary stock: common shares, Class A shares, ordinary shares, ADSs.

```
Common Stock | 10/05/2026 | S(1) | 501 | D | $536.2579(3) | 333,442 | I | by Trust(2)
```

| Column | Value above | Meaning | Our field |
|---|---|---|---|
| 1. Title | `Common Stock` | which security | `security_title` |
| 2. Date | `10/05/2026` | when it happened | `date` (ISO: 2026-10-05) |
| 3. Code | `S(1)` | what kind of transaction (section 5); `(1)` is a footnote | `code` = `S` |
| 4. Amount | `501` | shares in this transaction | `shares` |
| 4. (A) or (D) | `D` | **A**cquired or **D**isposed | `acquired_disposed` |
| 4. Price | `$536.2579(3)` | price per share | `price_per_share` |
| 5. Owned after | `333,442` | total held after this row | `shares_owned_after` |
| 6. Ownership | `I` | **D**irect, or **I**ndirect (via a trust, fund or spouse) | `direct_or_indirect` |
| 7. Nature | `by Trust(2)` | how an indirect holding is held | (not extracted) |

Footnote 3 on that filing says the $536.2579 is a **weighted average** of
several trades between $535.88 and $536.57. That is very common: a "sale" row
is often many small trades rolled up.

**Holdings rows.** Table I also lists holdings that didn't change, so the
reader sees the full picture. They have **no date and no code**:

```
Common Stock | 40,513 | D
Common Stock | 249,108 | I | by Trust(14)
```

These are not transactions. A model that extracts them is wrong.

---

## 4. Table II: derivative securities (options, RSUs, warrants…)

A **derivative** here means a right to get shares later: stock options,
restricted stock units (RSUs), warrants, convertible notes or preferred stock,
and LLC "units" that can be swapped for shares.

```
Stock Option | $117.76 | 10/05/2026 | M(1) | 9,500 (D) | (17) | 03/15/2027 | Common Stock | 9,500 | $0.00 | 299,910 | I | by Trust(18)
```

| Column | Value above | Meaning |
|---|---|---|
| 1. Title | `Stock Option` | the derivative |
| 2. Exercise price | `$117.76` | what you pay per share to exercise; **not** `price_per_share` |
| 3. Date | `10/05/2026` | transaction date |
| 4. Code | `M(1)` | exercise (section 5) |
| 5. Number, (A) or (D) | `9,500 (D)` | how many **derivatives**, and the direction. On the real form this is shown by **which column** the number is in; we render it as `(A)`/`(D)` |
| 6. Exercisable / expires | `(17)`, `03/15/2027` | when it can be used and when it lapses |
| 7. Underlying | `Common Stock`, `9,500` | what it converts into, and how many shares |
| 8. Price of derivative | `$0.00` | the price of the option itself. **This** is our `price_per_share` for Table II rows |
| 9. Owned after | `299,910` | derivatives held after |
| 10. Ownership | `I` | direct or indirect |

Two classic traps:

- **Two prices.** The exercise price (column 2) and the derivative's own price
  (column 8) are different things.
- **Two quantities.** The number of derivatives (column 5) and the number of
  underlying shares (column 7) can differ. Preferred stock converting
  "1-for-1.1717" shows 3,907,547 preferred shares becoming 3,334,938 common.

**An exercise appears in both tables.** Exercising 9,500 options shows up as
an `M` row in Table II (9,500 options **disposed**) and an `M` row in Table I
(9,500 shares **acquired**), often followed by an `S` row selling them.

---

## 5. Transaction codes

| Code | Meaning | Direction | Price usually |
|---|---|---|---|
| **P** | open-market or private **purchase** | always A | a real price |
| **S** | open-market or private **sale** | always D | a real price, often a weighted average |
| **A** | **grant or award** from the company (pay, RSUs) | always A | $0 or blank |
| **D** | **disposition to the company** (buyback, merger cash-out) | always D | varies; sometimes only in a footnote |
| **F** | shares **withheld to pay tax** or exercise price | always D | the market price that day |
| **M** | **exercise** of an option or RSU | A in Table I, D in Table II | the exercise price, or blank |
| **C** | **conversion** (preferred into common) | either | blank |
| **X** | exercise of an in-the-money or at-the-money derivative | either | exercise price |
| **G** | **gift** | usually D | $0 |
| **J** | **other**: explained in a footnote | either | varies |
| **K** | equity swap or similar | either | varies |
| V, I, E, H, O, L, W, Z, U | rarer: voluntary report, discretionary, expirations, small acquisitions, inheritance, voting trust, tender offer | | |

Real examples from the corpus:

```
G  Common Stock | 10/07/2026 | G | 455 | D | $0 | 1,000 | D                 (Universal Display: a gift)
F  Common Stock(1) | 09/30/2026 | F | 17,412 | D | $0.7202 | 829,784 | D    (Intrusion: tax withholding)
P  Ordinary Shares | 10/09/2026 | P | 18,181(1) | A | $1.67 | 3,664,314 | D (QTREX: a purchase)
A  Common Stock(1) | 10/07/2026 | A | 35,749 | A | $0 | 191,784 | D         (Lifevantage: a grant)
```

**`J/K` and `S/K`.** When a transaction involves an equity swap, the rendered
page shows the code with `/K` appended. The actual code is the first letter.

**The codes that matter most for signals** are P and S. An insider *buying* on
the open market with their own money (P) is the most watched event of all.
Grants (A), tax withholding (F) and exercises (M) are mostly routine pay
mechanics.

---

## 6. Footnotes

Almost every value can carry a footnote marker: `S(1)`, `$536.2579(3)`,
`2,180,631(5)`, `D(2)(3)`. The marker is not part of the value. Footnotes
explain things like:

- a **weighted-average price** and its range;
- that a sale was under a **10b5-1 plan**;
- **who actually holds** indirect shares (a trust, a fund, a spouse);
- **vesting schedules** of options and RSUs;
- what a **J** transaction was;
- a price that exists **only** in the footnote (see section 9).

---

## 7. Multiple reporting owners

Investment funds often file one Form 4 for a whole chain of entities: the
fund, its general partner, the manager, and the person who controls them.
Each gets a box 1 and box 5 block, and all share one set of tables. Bitfury's
filing lists five owners, all 10% holders. Footnotes usually say who
"disclaims beneficial ownership" except to the extent of their financial
interest.

---

## 8. Amendments (Form 4/A)

A 4/A corrects or completes an earlier Form 4, and the Remarks say how. From
the corpus:

- "being amended to include shares withheld to satisfy tax withholding
  obligations": adds `F` rows;
- "to correct the stock option information initially reported": replaces
  rows;
- "to add each of Dark Mirage, LP and MILFAM GP, LLC as a reporting person":
  adds owners and leaves the transactions alone.

---

## 9. Hard cases from this project

These are the filings marked ambiguous while labelling, and why. They are the
best interview examples.

1. **A price only in a footnote.** A merger cash-out (code D) leaves the price
   column empty, but footnote 1 says each share became $17.25 in cash. Is the
   price null or $17.25? It depends on whether you mean "what the form's price
   column says" or "what the insider received".
2. **A forward contract settling.** The price column shows only footnote
   markers. Footnote 3 gives a $17.83 settlement price.
3. **Aggregated "purchases".** A voluntary filing lists `P` rows that are each
   months of purchases rolled together, dated by the period's end, with no
   price. Every field is debatable.
4. **The form contradicts itself.** The box-5 checkboxes say the owners are
   only 10% holders, but the Remarks say two of them are directors and
   officers.
5. **A filer arithmetic error.** Alaska Air reported an exercise taking holdings
   to 37,706, then a sale of 83 shares leaving 37,710. That is impossible, and
   the filing was dropped from the corpus.

---

## 10. How a row maps to our JSON

The S row from section 3 becomes:

```json
{
  "security_title": "Common Stock",
  "is_derivative": false,
  "date": "2026-10-05",
  "code": "S",
  "shares": "501",
  "price_per_share": "536.2579",
  "acquired_disposed": "D",
  "shares_owned_after": "333442",
  "direct_or_indirect": "I",
  "value": "268665.21"
}
```

`value` (shares x price) is computed by code, never asked of the model.
Footnote markers, commas and `$` are stripped; dates become ISO.

---

## 11. Interview questions to be ready for

**"What is a Form 4 and why does it matter?"**
An insider's report of a change in their holdings, due within two business
days. It's public, structured, and time-sensitive, which is why it feeds
trading signals and compliance tools.

**"Why not just parse the XML?"**
You can, for Form 4. That's exactly why it's a good benchmark: the XML is a
free, exact answer key for testing extraction from the human-readable page.
The same skills apply to documents without XML (10-Ks, proxies, PDFs,
contracts).

**"What makes extraction hard here?"**
Two tables with different layouts, footnote markers glued to values,
direction encoded by column position in Table II, holdings rows mixed with
transactions, multiple owners, and prices that live only in footnotes.

**"How did you know your measurement was right?"**
Rules were checked against real gold before being adopted (and one was
fixed when real data broke it); every gold value was checked to appear on its
page; a human reviewed 44 filings; and three rendering bugs were found that
way, each of which would have looked like model error.

**"Give me an example of ambiguity."**
The merger cash-out: the form's price column is empty, but a footnote says
$17.25. We mark it ambiguous and exclude it from scoring, not guess.

---

## Glossary

| Term | Meaning |
|---|---|
| CIK | Central Index Key: the SEC's ID number for a filer |
| EDGAR | the SEC's public filing system |
| accession number | a filing's unique ID, e.g. `0001106578-26-000157` |
| issuer | the company whose stock it is |
| reporting person | the insider filing the form |
| beneficial owner | someone who controls or profits from shares, even if not in their name |
| RSU | restricted stock unit: a promise of shares once vesting conditions are met |
| vesting | when granted shares or options become the holder's |
| exercise | using an option to buy shares at its exercise price |
| 10b5-1 plan | a pre-arranged trading plan that defends against insider-trading claims |
| weighted average price | the volume-weighted average across several trades reported as one row |
| Up-C units | LLC interests exchangeable 1:1 for company stock, common after IPOs |
| ADS | American depositary share: a US-traded wrapper for a foreign company's shares |
