# Project 1: AutoExtract for SEC filings (extend, do not restart)

**Repo:** extend `michael-jw-ji/AutoExtract` (public)
**Time:** 2 to 3 weeks part-time, target ship early November
**Proves:** applied AI eval discipline, a public failure story, MCP, document extraction on financial filings
**Best for:** Pennant, Keye, KelAI, Soria, Gecko, Numeral, Financial Datasets, Elimentary, F2

---

## Why extend AutoExtract instead of starting fresh

AutoExtract already has most of what the report asked for:

| Report asked for | Already in AutoExtract |
|---|---|
| Golden set | `examples/*.gold.json`, frozen 120-doc holdout with a hash (`scripts/freeze_eval.py`) |
| Deterministic checks | `verify/` (pydantic schema, rule checks, failure signatures) |
| Regression gate | `evalgate/gate.py` promotes only on a margin and logs rejections |
| Failure analysis | `buffer/cluster.py` groups failures by signature |
| A real result | +20.10pp mean F1 with a negative control (+8.13pp), in `RESULTS.md` |
| Dashboard | Next.js app in `dashboard/` |
| Plugin point | `domains/` (invoice, support_email) with a clean interface |

What is missing:
1. A **financial filings domain** (right now it is synthetic invoices and support emails).
2. **CI**: there is no `.github/workflows`, so the gate never runs automatically.
3. **Tracing** of model calls (cost, latency, tokens per request).
4. An **LLM judge** checked against human labels.
5. An **MCP server**.
6. Windows-only run scripts (`.ps1`), which make it harder for reviewers to run.

So the work is about 60% less than a new project, and the finished repo is far stronger than a new one would be.

---

## Milestone 1: SEC Form 4 domain (week 1)

Form 4 is the right first filing: it is public, structured underneath (SEC also publishes XML), and it is the same data Atlas ingests, so you can tell one coherent story.

- [x] `scripts/fetch_form4.py`: download about 200 recent Form 4 filings from EDGAR (respect SEC's 10 requests/sec limit and set a `User-Agent` with your email).
- [x] Render each filing's HTML/text version as the model input, and parse the official XML as **gold**. This gives you a free, exact golden set without hand-labeling the easy fields.
- [x] `domains/form4.py` implementing the same interface as `domains/invoice.py`:
  - Schema (pydantic): `issuer_cik`, `issuer_ticker`, `reporting_owner`, `owner_relationship` (director/officer/10% owner), `transactions[]` with `date`, `code` (P/S/A/M/F...), `shares`, `price_per_share`, `acquired_disposed`, `shares_owned_after`.
  - `check_rules`: dates ISO; `code` in the SEC code list; `shares > 0`; `price >= 0`; for a single class, `shares_owned_after` consistent across sequential rows.
  - `enrich`: derive anything code can derive (for example transaction value = shares x price) instead of asking the model.
- [x] Hand-label 30 tricky filings yourself (footnotes, multiple transactions, derivative tables, amendments) and mark ambiguous cases as `ambiguous: true` rather than forcing a label. This is exactly Pennant's "make ambiguity explicit" problem.
- [x] Freeze a holdout: about 150 auto-gold + 30 hand-labeled, record the hash.

**Done when:** `run_cycle.py --stage baseline --domain form4` produces per-field scores on the frozen holdout.

## Milestone 2: Failure analysis and fixes (week 2)

This is the part that produces your interview story.

- [x] Run baseline, then open the failure clusters and read at least 50 failures by hand. _(Done as: all 162 failing filings analysed row by row with measured rules; 26 decided by a person, including every flagged judgement call; 136 accepted from the AI pre-review without individual review. See FAILURES.md, "How this was produced".)_
- [x] Write `FAILURES.md`: 5 to 8 failure themes, each with count, example filing, root cause, and where the fix belongs (parser, prompt, enrich code, or eval set). Likely ones: price pulled from a footnote, derivative vs non-derivative table confusion, wrong transaction date on amendments, multiple owners.
- [x] Fix each in the right place. Record per-field accuracy before and after every change in `RESULTS.md`. _(Done: prompt v2 promoted, v3 rejected; code fixes 1 promoted; render v2 rejected; render v3 promoted at F1 0.9182 / valid 96.3%, from a 0.8395 / 54.5% baseline. All in RESULTS.md.)_
- [ ] Find at least one failure a bigger model does **not** fix, and document it. Pennant explicitly asks for this.

**Done when:** `RESULTS.md` has a before/after table per field and `FAILURES.md` exists.

## Milestone 3: CI gate, tracing, judge (week 2 to 3)

- [ ] `.github/workflows/eval.yml`: on every PR, run `pytest` plus a **small cached eval** (20 to 30 holdout docs with recorded model responses so CI is free and deterministic). Fail the build if any scored field drops more than a set threshold vs `main`.
- [ ] Add one deliberate regression in a PR, show CI catching it, and link that PR in the README.
- [ ] Tracing: wrap the model client in `serve/client.py` with OpenTelemetry spans using the GenAI attributes (`gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`), or use LangSmith (Pennant uses it). Report cost and p50/p99 latency per document.
- [ ] LLM judge for the one field code cannot check exactly (for example owner relationship from free text): binary PASS/FAIL, validated against your 30 hand labels, report true positive and true negative rates.
- [ ] Add cross-platform `Makefile` or `scripts/*.sh` next to the `.ps1` files.

## Milestone 4: MCP server and polish (3 to 4 days)

- [ ] `mcp_server.py` exposing `extract_form4(url_or_text)` and `get_eval_summary()`. Show it working from Claude Desktop in a short GIF.
- [ ] README: lead with the Form 4 result, architecture diagram, CI badge, "run in 30 seconds" on Mac/Linux.
- [ ] Make sure no `.env` or keys are committed.

---

## Metrics to report (only what you measure)

- Holdout size and how it was built (auto-gold count, hand-labeled count, ambiguous count)
- Per-field exact-match accuracy before and after fixes
- Judge TPR/TNR vs your labels
- Regressions caught by CI (with PR link)
- Cost and p50/p99 latency per document
- One failure a bigger model did not fix

## Resume bullet templates (fill numbers after measuring)

```latex
\resumeItem{Extended a self-improving LLM extraction service to SEC Form 4 filings, building a [N]-document frozen holdout from EDGAR XML gold and raising per-field accuracy from [X]\% to [Y]\% by fixing [K] failure classes in the parser, prompt, and validation layer.}
\resumeItem{Added a GitHub Actions eval gate that blocks merges on per-field regressions, OpenTelemetry tracing for cost and p99 latency, and an MCP server exposing extraction to AI agents.}
```

## Outreach hook

"I built an extraction pipeline for SEC Form 4 filings with a CI-enforced eval gate. Here is one way it was wrong, how I found it, and what I changed: [link to FAILURES.md]." This is almost word for word what Pennant asks for.
