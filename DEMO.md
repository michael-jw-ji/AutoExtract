# 3-minute demo — every feature, timed

Nine beats, 180 seconds. Two live commands, everything else is persisted
records on the dashboard.

**The rule that shapes this script:** a LoRA fine-tune does not fit in a demo
slot. Training is shown as *evidence already on the board*, never as a live
event. Anything live is under 4 seconds.

---

## Setup (before you walk up)

```powershell
.\scripts\preflight.ps1        # verifies API, dashboard, examples, DB rows
```

Then:

**No terminal. The whole demo is the browser.**

- **Browser** at `http://127.0.0.1:3000`, scrolled to the top, **paused**
  (hit ❚❚ so numbers don't shift mid-sentence).
- The **try it yourself** panel has the documents built in — a row of buttons
  reading `currency symbols · missing totals · scanned fax · short sample`.
  One click loads the document; no pasting, no files.
- Click **missing totals** now so it's already loaded when you start. Leave the
  result area empty.
- `DB_PATH` **unset** — the demo reads `autoextract.db`.
- Dashboard should read **documents 299 · models tried 4 · shipped/blocked 2/1**.

---

## 0:00 — 0:45  ·  One click, the whole thesis

*(Click ▶ **extract**, then talk. It lands in ~2 s.)*

> "This is a real invoice. The model reads it fine — vendor, dates, line items,
> arithmetic. Watch six specific fields."

*(Point at **what the model invented → what code derived**.)*

```
vendor_id                VND-00001    →  VND-01530
Safety boots, size 10    SA-BOOT-10   →  PE-BOOT-07
Steel shelf bracket, h   ST-BRKT-01   →  HW-MOUN-14
Inkjet cartridge, blac   INK-CART-01  →  PP-INKJ-02
Thermal receipt roll 8   TH-ROLL-01   →  PP-ROLL-01
Argon cylinder, 50L      AR-CYL-50    →  CH-GASS-04
```

> "`SA-BOOT-10` isn't a typo — it's an **invention**. Right shape, wrong value.
> Those codes only exist in our systems, so no amount of model scale fixes it."

*(Point one line up: `7 errors → VALID · fixed by code, no model call`.)*

> "Seven errors, then valid — and **no second model call**. The red column is
> what the model said. The green is what code derived: registry lookups,
> arithmetic, date formats."
>
> "And notice what caught it: **pydantic, not an LLM**. Deterministic, free,
> and it never lies. Across the corpus that's **0% to 98.3% valid**, latency
> 6.4 s down to 1.7, at zero cost."

*(The error text in plain English is right below, if you want it:
`vendor_id 'VND-00001' != registry id for 'Solvent Chemical Co' (VND-01530)`.)*

**Features shown:** deterministic validation · business-rule checking · failure
signatures · serve-time enrichment — all in one panel.

## 0:45 — 1:05  ·  Valid is not correct

*(Stay on the same panel — point at the amber box, just above the
red/green table. It appeared at the same time as everything else.)*

```
Valid — and still wrong.  It passed every format, arithmetic and business
rule, and 2 fields still disagree with ground truth.

  tax     0.00       want 1221.75
  total   24435.03   want 25656.78
```

> "Look at this. It says **VALID** up there — and it's still wrong."
>
> "This invoice never prints its totals, so the model guessed zero tax and the
> arithmetic is perfectly self-consistent around a wrong number. **Schema-valid
> is not correct.** Train on that and you actively make the model worse."

The box only appears for the built-in documents, because only those ship
ground truth. Paste your own text and it's absent — which is the honest
behaviour: no gold, no claim about correctness.

**This is the line that separates you from a wrapper. Don't rush it.**

**Feature shown:** gold-based verification, the repair-poisoning guard.

## 1:05 — 1:25  ·  Failures group themselves

*(Dashboard → "what's going wrong, grouped".)*

> "Every failure gets a signature built from the validator's own error list —
> error type plus field path, list indices normalised. Same failure, same key,
> every time. **No embeddings, no clustering model.** It's instant, it's
> reproducible, and it reads as English. Click one and you get the documents."

*(Click a bar. Real documents appear.)*

**Feature shown:** deterministic clustering, drill-down.

## 1:25 — 1:45  ·  Cheapest fix that works

*(Dashboard → "how failures got fixed".)*

> "Failures route to the cheapest fix that clears them: a deterministic lookup,
> or the large model with the registry in context. Then the important part —
> **every repair is checked against ground truth**, and only proven-correct ones
> are allowed into a training set. The rest are recorded and never trained on."

**Feature shown:** two-tier repair, verification gate on training data.

## 1:45 — 2:20  ·  Training — and the control that proves it

*(Dashboard → "every model we tried".)*

> "We trained a LoRA on those verified repairs. **+20.1 points.**"
>
> "But 'number went up' is weak evidence — maybe any fine-tuning helps a weak
> model. So we ran a control: identical documents, identical volume, identical
> output format, with only the **rule knowledge** stripped out."

| Run | mean_f1 | vs base |
|---|---|---|
| base Qwen2.5-1.5B | 0.6147 | — |
| **+ LoRA on repairs** | **0.8158** | **+20.10pp** |
| + LoRA, rules stripped | 0.6961 | +8.13pp |

> "Control +8.1. Real +20.1. **The 12-point difference is the repairs.**
> That's isolated, not asserted."

If asked what it learned: category codes yes (114 → 85 errors), vendor IDs no
— 20 arbitrary five-digit numbers seen once each is too little signal.
**Say it plainly. It's a finding, not a flaw.**

**Feature shown:** LoRA fine-tuning, causal attribution via control.

## 2:20 — 2:40  ·  The gate refuses

*(Point at row 4: rejected, +1.51pp.)*

> "We also trained a deliberately undertrained model. The gate requires +2
> points on field-F1 and no regression in valid-rate. It got **+1.51**, and the
> system **refused to promote it**. Incumbent unchanged, decision written to
> the database with its reason."
>
> "Anyone can show a number going up. This refuses to ship one that didn't earn
> it."

**Feature shown:** the promotion gate, rejection as a persisted record.

## 2:40 — 2:55  ·  Cheap at scale

> "Last thing. Same frozen holdout, same metric, same enrichment: our **local
> 1.5B with the LoRA** against the **hosted model**."

| Model | valid | mean_f1 |
|---|---|---|
| `inkling-small` hosted | 95.8% | 0.9748 |
| **Qwen2.5-1.5B + LoRA** | **96.7%** | **0.9665** |

> "It matches — ahead on valid-rate, 0.8 behind on F1, inside our own 2-point
> margin. That's what training is *for*: the knowledge is stable, the volume is
> high, and otherwise you pay a bigger model on every request forever."

**Feature shown:** the economic case, like-for-like measurement.

## 2:55 — 3:00  ·  Close

> "Change one environment variable and the same loop runs on support-email
> triage — different schema, different registry, different policy. Validator,
> clustering, repair, gate and dashboard are **untouched**."
>
> "It finds its own failures, fixes them, proves the fix, and refuses to ship
> what it can't prove."

**Feature shown:** domain plugin architecture.

---

## Feature checklist — everything the 3 minutes covers

| Feature | Beat |
|---|---|
| Deterministic validation (pydantic, no LLM) | 0:00 |
| Business-rule checking (registry, taxonomy, policy) | 0:00 |
| Failure signatures | 0:00 |
| Serve-time enrichment | 0:25 |
| Ground-truth verification / valid ≠ correct | 0:45 |
| Deterministic clustering + drill-down | 1:05 |
| Two-tier repair (mechanical, then distillation) | 1:25 |
| Training-set hygiene (verified-only) | 1:25 |
| LoRA fine-tuning | 1:45 |
| Causal attribution via control | 1:45 |
| Promotion gate + persisted rejection | 2:20 |
| Cost/quality comparison vs hosted | 2:40 |
| Multi-domain plugin architecture | 2:55 |

Not in the 3 minutes, keep for questions: frozen-holdout leak guards, the
ceiling test, JSON mode, the replay buffer and per-signature cap.

---

## Questions you'll get

**"Isn't this just synthetic data?"** Deliberately — it's the only way to have
exact ground truth for *private* business rules. No public invoice dataset
contains our vendor IDs. And it's what lets us verify every repair rather than
hope.

**"Why not put the registry in the prompt?"** We do — that's the ceiling test,
and it's how we know the information is sufficient: F1 0.66 → 0.93. It costs
8.5k characters on every request forever. Training amortises it to zero.

**"Why is valid-rate 0% on the local rows?"** Those were scored without
enrichment, to isolate what training alone contributes. Full validity needs
every field at once including vendor IDs, which didn't train. That's exactly
why the gate measures F1 — pass/fail is pinned at zero and can't see a 20-point
improvement.

**"Did you train on Baseten?"** Training Jobs returns 403 for our workspace —
reads work, creates don't. The code is written against their CLI and ready. We
ran locally, which is a *cleaner* comparison anyway: same base model, one
variable.

**"How do you know the eval isn't contaminated?"** `freeze_holdout()` refuses a
holdout containing template documents; `assert_no_leak()` refuses a dataset
containing holdout ids. Both hard-fail. We added the first after a rate-limited
run silently made 49% of a holdout trivial — worth 2.7 points against a 2.0
point margin.

**"Has anything ever silently broken?"** Yes, twice, and both are now guarded.
A generator inherited JSON mode from the environment and emitted 9-character
fragments instead of documents — the corpus looked fine until we measured the
shortest document. And a scorer hardcoded to one domain's field list reported
F1 0.0000 on another, which looked like total model failure and was a wiring
bug. **Both were caught by measuring, not by reading code.**

---

## Fallbacks

- **API down** → `.\run_api.ps1`; dashboard shows "api unreachable" in red.
- **Live call hangs** → keep talking. Every number on the dashboard is a
  persisted row and stands alone. Skip to 1:05.
- **No network at all** → skip the extract click entirely and start at 1:05.
  Everything from there is persisted and renders without the model.
- **Want it in a terminal instead** → `python scripts\demo_case.py
  examples\invoice_missing_totals.txt --both` prints the same contrast, and
  `--raw` runs it with enrichment off. `python scripts\demo.py --pause`
  replays all the beats from database rows.
