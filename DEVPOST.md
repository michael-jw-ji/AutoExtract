# AutoExtract

## Inspiration

Most document extraction systems stop at extraction. They read an invoice, a
contract, or a support email and return structured data — but when they get it
wrong, the process ends there. Fixing mistakes means humans reviewing outputs,
labelling examples, and manually retraining.

We kept asking a simple question:

**Why can't the system learn from its own failures?**

But the moment we built a baseline, we hit a more uncomfortable one. Our model
read an invoice perfectly — vendor, dates, line items, arithmetic — and then
confidently reported the vendor's internal ID as `VND-10001`. The real answer
was `VND-00713`. It hadn't misread anything. It had **invented** a plausibly
formatted value for a fact that exists only inside our company's systems and
appears in no pretraining corpus.

That failure is invisible to every generic benchmark, and it's the one that
actually matters in production. So the project became two things at once: a
system that improves itself, and a system that can *tell when it's wrong* in
the first place — because you cannot learn from failures you cannot detect.

## What it does

AutoExtract turns messy documents into validated, normalized JSON, detects its
own failures deterministically, and improves itself from them — refusing to
ship any change that can't prove it helped.

A document goes in. The system then:

* **Extracts structured data** with a small, cheap LLM whose only job is
  reading the page.
* **Derives everything derivable in code** — normalizes formats, recomputes all
  arithmetic, and resolves private business-rule fields by registry lookup. If
  it can't match a vendor, it refuses to guess.
* **Validates deterministically** against a strict schema — formats,
  cross-field arithmetic, and business rules. No LLM judges anything.
* **Clusters failures exactly**, by a signature built from the validator's own
  error list. Identical failures produce identical signatures.
* **Repairs each failure with the cheapest thing that works** — a deterministic
  lookup first, then a large model that gets the private reference data the
  small model never sees.
* **Verifies every repair against ground truth** before it may become training
  data. Schema-valid is not the bar; matching gold is.
* **Fine-tunes a LoRA** on proven-correct examples only.
* **Gates promotion on a frozen holdout.** A candidate must beat the incumbent
  by +2.0 points of field-F1 and not regress validity. Rejections are recorded
  and shown.

Every stage writes a row. The dashboard is pure reads, so the audit trail
stands on its own: what failed, why, how it was fixed, what trained, and
whether the new model actually won.

The most compelling thing in the demo isn't a single extraction. It's the row
in the model table that says **rejected, +1.51pp** — the system refusing to
promote a model that didn't earn it.

## How we built it

**Ground truth by construction.** We build a valid record in Python first —
including the private fields — then have a large model *render* it as a messy
document with OCR noise, reply chains, missing totals, mixed currencies.
Because the answer exists before the document, we have exact ground truth on
every field. That's what makes automatic verification possible at all: no
public dataset contains *our* vendor IDs.

**Serve-time enrichment.** This turned out to be the single biggest win, and it
isn't machine learning. Before anything is validated, deterministic code
resolves every field that has a right answer — vendor name → vendor ID,
product description → taxonomy code, tier × total → payment terms, plus all
arithmetic and formats. The model is left responsible only for reading the
page. **Validity went from 0% to 98.3%, field-F1 from 0.657 to 0.974, latency
from 6.4s to 1.7s, at effectively zero cost.**

**Deterministic failure clustering — no embeddings.** `ValidationError.errors()`
gives `(type, location)` per error. Sorted and index-normalized, that's the
signature: `registry_vendor_id:vendor_id | taxonomy_category:line_items.*`.
Grouping is exact, instant, reproducible, and human-readable. Embeddings would
only help for output that is schema-valid but semantically wrong, which we
handle with ground truth instead.

**Two-tier repair with a verification gate.** Mechanical repair first; only the
residue reaches the large model. Then the step that matters: a repair counts
only if it's schema-valid **and** matches gold exactly.

**Training data hygiene.** 70% verified repairs, 30% replay of previously
passing examples to prevent catastrophic forgetting, and no single failure
signature may exceed 25% of the repair half — otherwise the model masters one
field and forgets the rest. A mechanical assertion hard-fails if any holdout
document appears in training data.

**A domain plugin layer.** A domain supplies five things — schema, registries,
enrichment, prompts, generator. Everything else is shared. We proved this by
running the identical loop on **support-email triage**: different schema,
different registry (customer ID keyed on sender domain), different policy
(priority from tier × category). Validator, clustering, repair, gate and
dashboard are untouched.

## Challenges we ran into

**Schema-valid is not correct, and conflating them poisons training.**
Mechanical repair can recompute totals from a misread unit price and produce
an invoice that is perfectly self-consistent and completely wrong. Train on
that and you actively degrade the model. This is why no repair enters a
dataset without matching ground truth — and the demo shows a live document
that passes every rule and is *still* wrong on two fields.

**A silent corpus contamination that invalidated a week of numbers.** Our
generator's `chat()` call inherited `JSON_MODE=1` from the environment, which
forced the *renderer* into JSON mode even though its prompt asks for prose. It
returned fragments like `[2026.08, "a plain business email"]` — nine
characters. All 800 documents were fragments. Downstream this looked exactly
like catastrophic model failure: 95.8% failure rate, placeholder outputs,
zero verified repairs. The model wasn't failing; it had nothing to read. The
generator now passes `json_mode=False` explicitly and **drops** any render that
is too short, JSON-shaped, or missing the values it was told to include. A
smaller honest corpus beats a larger contaminated one.

**Four separate places hardcoded to one domain.** Adding a second use case
exposed that our scorer, mechanical repair, domain loader, and distillation
prompt were all invoice-specific. The worst was distillation: the repair model
was being asked to turn support emails into *invoices*, using the invoice
vendor registry as reference. Verified email repairs went from **3/141 to
120/141** once each path resolved through the active domain.

**A race that only appeared under load.** The domain loader guarded on "is the
registry empty", but the scorer resolves domains from a thread pool — so one
thread could see `invoice` registered while `support_email` was still
importing, then skip loading entirely. It failed intermittently and looked
like a config error.

**Knowing what training can and cannot teach.** Our first result was "+20
points, training works." That's weak evidence — maybe any fine-tuning helps a
weak model. Designing the control that isolates the claim was harder than
running the training.

## Accomplishments that we're proud of

**We can prove the improvement is causal, not incidental.** We trained a
control on identical documents, identical volume, identical output format,
with only the *rule knowledge* stripped out.

| Run | mean_F1 | vs base |
|---|---|---|
| base (Qwen2.5-1.5B) | 0.6147 | — |
| **+ LoRA on verified repairs** | **0.8158** | **+20.10pp** |
| + LoRA, rules stripped (control) | 0.6961 | +8.13pp |
| + LoRA, undertrained | 0.6298 | +1.51pp → **rejected** |

The control gained 8. The real one gained 20. **The ~12-point gap is the
repairs** — and "any fine-tuning helps" cannot explain it.

**The gate actually refuses.** The undertrained model scored +1.51 against a
required +2.00 and was blocked. The incumbent kept serving and the decision is
in the database with its reason. A system that only ever succeeds has no gate.

**It's cheap at scale.** On the same frozen holdout, same metric, same
enrichment, a local Qwen2.5-1.5B with our LoRA reaches **96.7% valid / 0.9665
F1** against a hosted model's **95.8% / 0.9748** — ahead on validity, 0.8
behind on F1, inside our own promotion margin, running on a laptop GPU.

**We're honest about what didn't work.** The LoRA learned category codes
(114 → 85 errors) but **not** vendor IDs — no better than the control. Twenty
arbitrary five-digit numbers seen once each is too little signal. Training
teaches habits and structure, not arbitrary facts. Those belong in a lookup,
which is exactly why enrichment exists.

## What we learned

**The model was rarely the bottleneck.** Our biggest single improvement — 0% to
98.3% validity — came from deleting model responsibilities, not adding them.
Enrichment even beat the ceiling we measured by pasting the entire registry
into the prompt (0.974 vs 0.927), at a fraction of the cost. Before reaching
for a bigger model or a fine-tune, check whether the answer is a lookup.

**Training is a specific tool, not a default.** It's right when the knowledge
is stable, the volume is high, and the alternative is paying a larger model on
every request forever. It's wrong for arbitrary facts that change — that's a
database.

**Self-improving systems fail silently unless you instrument the inputs.** Both
of our worst bugs produced *plausible* output. A corpus of 9-character
fragments and a domain-blind scorer both presented as "the model is bad."
Neither was caught by reading code; both were caught by measuring something
basic — the shortest document in the corpus, the field names being compared.
We now have hard guards for both, and a preflight script that checks them.

**Explainability matters more as autonomy increases.** If the system is
changing itself, "trust me, it improved" is not good enough. Every number on
our dashboard traces to a persisted row, and every promotion decision — including
the rejections — carries the reason it was made.

## What's next for AutoExtract

**Semantic consistency checks** for output that passes every schema rule and is
still wrong. We detect this today only where we hold ground truth; production
traffic needs a different signal.

**Active learning** that routes only genuinely ambiguous cases to a human,
using cluster size and repair-verification rate to decide what's worth asking
about.

**Multimodal documents** — scanned forms, handwritten notes, complex tables.
Today our messiness is described in text rather than rendered as pixels.

**Online evaluation on production traffic**, to catch failure modes that emerge
after deployment rather than only those present in a frozen holdout.

**Content-addressed eval sets.** Our holdout hash is computed over document
IDs, so two different corpora that reuse the same IDs hash identically. Hashing
content would make every stored evaluation unambiguously traceable to the exact
documents it was measured on.
