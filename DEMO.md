# 3-minute demo

## Before you start

```powershell
.\run_api.ps1          # :8000   (separate window)
.\run_dashboard.ps1    # :3000   (separate window)
```

- **Unset `DB_PATH`** — the demo lives in `autoextract.db`, not the jsonmode fork.
- Dashboard should read: **extractions 300 · versions 4 · promoted/rejected 2/1**.
- Browser at `http://localhost:3000`, scrolled to the **playground**.
- Hit **pause** on the dashboard so numbers don't shift mid-sentence.

**The one timing trap:** a live extraction takes 5–13 seconds. Click
**▶ extract** *before* you start talking, so it computes while you deliver the
opening line. Never do two live calls.

---

## 0:00 — 0:30  The hook

*(Click ▶ extract first, then talk over it.)*

> "This is an invoice. Our model reads it correctly — vendor, dates, line
> items, arithmetic, all fine. Watch what it does with three specific fields."

*(Result lands. Point at the errors.)*

```
registry_vendor_id   vendor_id 'VND-10001' != registry id for
                     'Northwind Logistics Ltd' (VND-00713)
taxonomy_category    category 'HW-BOLT-01' != taxonomy code (HW-FAST-02)
```

> "It invented those. They're plausibly formatted and completely wrong —
> because `VND-00713` only exists in *our* systems. It's not on the document
> and it's not in any pretraining corpus."

**Why this works:** it's live, it's unfakeable, and confabulation is instantly
legible to anyone.

## 0:30 — 1:00  Why this isn't a "use a smarter model" problem

> "We tested the ceiling. Same model, same documents, registry pasted into the
> prompt: field-F1 goes 0.66 to 0.93, valid-rate 0 to 88%. So it's not a
> capability gap — it's a knowledge gap."
>
> "But that costs eight and a half thousand characters of prompt on every
> single request, forever. Training is how you pay that once."

**This is the thesis.** Don't rush it.

## 1:00 — 1:35  The loop

*(Scroll to failure clusters.)*

> "Every failure is validated by pydantic, not by an LLM — so the same failure
> always produces the same signature. We group by signature. No embeddings,
> no clustering model. It's instant and it reads as English."

*(Scroll to the repair funnel.)*

> "Failures get repaired two ways: a deterministic registry lookup, or the
> large model with the registry in context. Then the important bit —"

> **"Every repair is checked against ground truth before it's allowed to
> train. Mechanical repair can make an invoice *schema-valid* and still
> *wrong* — recompute totals from a misread price and you get something
> perfectly self-consistent and false. We have a test that asserts exactly
> that."**

**This is the line that separates you from a wrapper.**

## 1:35 — 2:20  Did it work

*(Scroll to model lineage.)*

> "We trained a LoRA on those verified repairs. +20.1 points."
>
> "But 'number went up' is weak evidence — maybe any fine-tuning helps a weak
> model. So we ran a control: identical documents, identical volume, identical
> output format, with only the *rule knowledge* stripped out."
>
> "The control got +8.1. The real one got +20.1. **The 12-point difference is
> the repairs.** That's the claim, and it's isolated."

If asked what it learned: category codes yes (114 → 85 errors), vendor IDs no
— 20 arbitrary five-digit numbers appearing once each is too little signal.
**Say this plainly; it's a strength, not a weakness.**

## 2:20 — 2:50  The gate

*(Point at row 4: rejected, +1.51pp.)*

> "We also trained a deliberately undertrained model. The gate required +2
> points. It got +1.51, and the system refused to promote it — incumbent
> unchanged."
>
> "Anyone can show a number going up. This is a system that refuses to ship a
> model that didn't earn it."

## 2:50 — 3:00  Close

> "Serve, verify, cluster, repair, verify the repair, train, gate. It finds
> its own failures, fixes them, and proves the fix before shipping."

---

## Questions you'll get

**"Isn't this just synthetic data?"** Yes, deliberately — it's the only way to
have exact ground truth for private business rules. No public invoice dataset
contains *our* vendor IDs. And it lets us verify every repair rather than hope.

**"Why not just put the registry in the prompt?"** We do, as the ceiling test —
that's how we know the information is sufficient. It costs 8.5k characters per
request. Training amortises it to zero.

**"Why is valid-rate still 0%?"** Full schema validity needs every field right
at once, including vendor IDs, which didn't train. That's why the gate measures
F1 — pass/fail is pinned at zero and can't detect a 20-point improvement.

**"Did you train on Baseten?"** Training Jobs returns 403 for our workspace —
reads work, creates don't. The code is written against their CLI and ready.
We ran locally on a laptop GPU, which is a *cleaner* comparison anyway: same
base model, one variable.

**"How do you know the eval isn't contaminated?"** `freeze_holdout()` refuses
to freeze a holdout containing easy template documents, and `assert_no_leak()`
refuses to build a dataset containing holdout documents. Both hard-fail. We
added the first after a rate-limited run silently made 49% of a holdout trivial
— it moved field-F1 by 2.7 points against a 2.0-point promotion margin.

---

## Fallbacks

- **API down** → `.\run_api.ps1`; the dashboard shows "api unreachable" in red.
- **Live call hangs** → keep talking; the numbers on the dashboard are all
  persisted rows and stand on their own.
- **No projector / terminal only** → `python scripts\demo.py --pause` walks the
  same seven beats from real database rows.
