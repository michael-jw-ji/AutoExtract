# AutoExtract

## 1. Big Idea

A harness to fine-tune an LLM that extracts normalized, structured data from
messy company information — emails, invoices, and anything else with the same
shape.

The shape that matters:

> **fields you can read off the page** + **fields only your organisation knows**

The second group is the whole problem. `VND-00713`, `CUS-00412`, `HW-FAST-02`
appear in no pretraining corpus. A model asked for them doesn't say "I don't
know" — it invents `VND-10001`, correctly formatted and completely wrong. That
failure is invisible to every generic eval, and it's the one that matters in
production.

---

## 2. High-Level Workflow

```
  ┌─────────────────────────────────────────────────────────────────┐
  │                                                                 │
  ▼                                                                 │
GENERATE ──► SERVE ──► ENRICH ──► VERIFY ──► CLUSTER ──► REPAIR ──► VERIFY
                ▲       (code)   (pydantic)  (signature) (LLM)     THE REPAIR
                │                    │                                 │
                │                    └── passes ──► done               │
                │                                                      ▼
                │                                              BUILD DATASET
                │                                                      │
                │                                                      ▼
                └──── PROMOTE ◄──── EVAL GATE ◄──── TRAIN LoRA ◄───────┘
                         or
                       REJECT (logged, incumbent unchanged)
```

Nine stages. Each description below is ~20 seconds spoken.

---

### 1 · GENERATE — build ground truth first

We construct a valid record in Python first — vendor, line items, totals, the
business-rule fields — then ask a large model to *render* it as a messy
document: OCR noise, rotated labels, reply chains, missing totals. Because we
built the answer before the document, we have exact ground truth for every
single field, including the private ones. No public dataset can give you that,
because no public dataset contains your vendor IDs.

> **Solves:** you cannot verify what you cannot check against.

### 2 · SERVE — the small model reads the page

A small, cheap model receives the document and returns JSON. That's all it
does: read the page. It is never asked to know your vendor registry, compute
your totals, or apply your payment policy — those aren't reading tasks, and
asking for them is what produces confident inventions. In production this is
the only step that costs money per document.

> **Solves:** use the model for the one thing only a model can do.

### 3 · ENRICH — code derives everything derivable

Before anything is validated, deterministic code takes over: it normalizes
dates and money formats, recomputes every arithmetic field from the line
items, and resolves the business-rule fields by lookup — vendor name to vendor
ID, product description to taxonomy code, tier plus total to payment terms. If
it can't match a vendor, it refuses to guess. This single step took validity
from 0% to 98.3%, cut latency from 6.4s to 1.7s, and costs nothing.

> **Solves:** the cheapest fix always goes first. Most "AI problems" are lookups.

### 4 · VERIFY — deterministic, no LLM

A strict pydantic schema checks three layers: formats (ISO dates, ID patterns,
2-decimal money), arithmetic (line totals, subtotal, total), and business rules
(does this vendor ID match the registry? does this category match the
taxonomy?). No LLM judges anything here. The check is free, instant, and it
never hallucinates a verdict — which is exactly why you can build a training
loop on top of it.

> **Solves:** knowing you're wrong. Most pipelines cannot tell.

### 5 · CLUSTER — failures group themselves

Every failure produces a signature built from the validator's own error list:
error type plus field path, with list indices normalized. `registry_vendor_id:
vendor_id | taxonomy_category:line_items.*`. Identical failures produce
identical signatures, so grouping is exact — no embeddings, no clustering
model, no tuning. It's instant, reproducible, and reads as English on the
dashboard.

> **Solves:** seeing *what* is breaking, not just *how often*.

### 6 · REPAIR — cheapest fix that works

Each failure is routed to the least expensive thing that could fix it. First a
deterministic pass: normalize formats, recompute arithmetic, look up anything
the registry can answer. Only what survives that goes to the large model, which
gets the private reference data in its context — the registry the small model
never sees. Most failures never reach the expensive tier.

> **Solves:** you don't need a frontier model for a formatting error.

### 7 · VERIFY THE REPAIR — the step everyone skips

A repair is accepted only if it's schema-valid **and** matches ground truth
exactly. This matters more than it sounds. Mechanical repair can make an
invoice perfectly self-consistent around a misread price — valid, confident,
and wrong. Train on that and you actively degrade the model. Unverified repairs
are recorded for the dashboard and never enter a training set.

> **Solves:** schema-valid is not correct. Conflating them poisons training.

### 8 · TRAIN — a LoRA on proven-correct examples

Verified repairs become the training set: 70% new repairs, 30% replay of
previously-passing examples to prevent catastrophic forgetting, with no single
failure signature allowed past 25% of the repair half — otherwise the model
masters one field and forgets the rest. A mechanical assertion hard-fails if
any holdout document appears in training data. Then a LoRA fine-tune on the
small model.

> **Solves:** turning observed failures into a permanent capability.

### 9 · EVAL GATE — promote or reject, and log both

The candidate is scored against a frozen holdout it has never seen. To be
promoted it must beat the incumbent by **+2.0 points of field-F1** and not
regress validity. Anything less is rejected, the incumbent keeps serving, and
the decision is written to the database with its reason. A promoted model
becomes the new serving model, and the loop closes.

> **Solves:** refusing to ship a change that can't prove it helped.

---

## 3. What makes this more than a wrapper

**The control experiment.** Training gave +20.1 points. But "number went up" is
weak evidence — maybe any fine-tuning helps a weak model. So we trained a
control on identical documents, identical volume, identical output format, with
only the *rule knowledge* stripped out. It got +8.1. The real one got +20.1.
**The 12-point gap is the repairs**, and that's isolated rather than asserted.

**The gate actually refuses.** We trained a deliberately undertrained model. It
scored +1.51 against a required +2.00 and was rejected — that row is on the
dashboard. A system that only ever succeeds has no gate.

**It's honest about what training can't do.** The LoRA learned category codes
(114 → 85 errors) but *not* vendor IDs — 20 arbitrary five-digit numbers seen
once each is too little signal. Training teaches habits and structure, not
arbitrary facts. Those belong in a lookup, which is why enrichment exists.

**It's cheap at scale.** On the same frozen holdout, same metric, same
enrichment: a local Qwen2.5-1.5B with our LoRA hits **96.7% valid / 0.9665 F1**
against the hosted model's **95.8% / 0.9748**. It matches — ahead on validity,
0.8 behind on F1, inside our own 2-point margin — on a laptop GPU.

**It's domain-agnostic.** Switching from invoices to support-email triage
changes one file. The validator, clustering, repair, gate and dashboard are
untouched. A domain supplies five things — schema, registries, enrichment,
prompts, generator — and plugs into the same loop.

---

## 4. The one-sentence version

> Most LLM pipelines cannot tell when they are wrong. This one detects failures
> mechanically, groups them so you can see what is actually breaking, routes
> each to the cheapest fix that works — a lookup, a retry, a config flag,
> occasionally training — and refuses to ship a change that cannot prove it
> helped.
