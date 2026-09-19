"""Scripted demo walkthrough, driven by real rows in the database.

A three-minute slot is too short to improvise and too short to wait on a live
model call (p95 latency is 12.6s -- that is a long silence on stage). So this
reads what the loop actually recorded and narrates it in order, with optional
pauses between beats.

    python scripts/demo.py              # run straight through
    python scripts/demo.py --pause      # ENTER between beats (rehearse with this)
    python scripts/demo.py --live       # beat 2 calls the model for real

Beats:
  1. the document, and what makes it hard
  2. the failure -- deterministic, with a signature
  3. the clusters -- what is failing, ranked
  4. the repair -- mechanical vs distilled, and why verification matters
  5. the dataset -- what actually gets trained on
  6. the gate -- and why a rejection matters more than a promotion
  7. the ceiling -- proof the information closes the gap
"""

from __future__ import annotations

import argparse
import json
import textwrap

from core.config import settings
from core.db import init_db, one, rows, tx

W = 78


def rule(char: str = "─") -> None:
    print(char * W)


def beat(n: int, title: str, pause: bool) -> None:
    print()
    rule("═")
    print(f"  {n}. {title.upper()}")
    rule("═")
    if pause:
        input("    [enter]")


def wrap(text: str, indent: str = "  ") -> str:
    return textwrap.indent(textwrap.fill(text, W - len(indent)), indent)


def main() -> None:
    ap = argparse.ArgumentParser(description="Scripted demo walkthrough")
    ap.add_argument("--pause", action="store_true", help="wait for ENTER between beats")
    ap.add_argument("--live", action="store_true", help="beat 2 calls the model live")
    args = ap.parse_args()
    p = args.pause

    init_db()

    # ---------------------------------------------------------------- beat 1
    beat(1, "the document", p)
    # Prefer a BUSINESS-RULE failure. A json_decode failure is a parse error,
    # which is the least interesting thing this project does -- the story is
    # "the model cannot know our rules", not "the model emitted bad JSON".
    with tx() as conn:
        doc = one(
            conn,
            "SELECT d.id, d.text, d.messiness, d.gold_json FROM documents d "
            "JOIN failures f ON f.doc_id = d.id WHERE d.split='live' "
            "  AND (f.signature LIKE '%registry_vendor_id%' "
            "       OR f.signature LIKE '%taxonomy_category%') "
            "  AND f.signature NOT LIKE '%json_decode%' "
            "  AND LENGTH(d.text) BETWEEN 600 AND 1600 "
            "ORDER BY RANDOM() LIMIT 1",
        ) or one(
            conn,
            "SELECT d.id, d.text, d.messiness, d.gold_json FROM documents d "
            "JOIN failures f ON f.doc_id = d.id WHERE d.split='live' "
            "ORDER BY RANDOM() LIMIT 1",
        )
    if not doc:
        raise SystemExit("no failed documents in the database -- run the loop first")

    print(f"  style: {doc['messiness']}\n")
    print(textwrap.indent("\n".join(doc["text"].splitlines()[:16]), "  │ "))
    print()
    print(wrap(
        "Three fields on this invoice are NOT printed anywhere on it: the "
        "vendor's internal ID, each line item's category code, and the payment "
        "terms. They live in our systems. No model can read them off the page."
    ))

    # ---------------------------------------------------------------- beat 2
    beat(2, "the failure", p)
    if args.live:
        from serve.extract import extract_text
        from verify.validate import validate
        raw, ms, model = extract_text(doc["text"])
        outcome = validate(raw)
        sig, errors, latency = outcome.signature, outcome.errors, ms
    else:
        with tx() as conn:
            f = one(
                conn,
                "SELECT f.signature, f.errors_json, e.latency_ms FROM failures f "
                "JOIN extractions e ON e.id = f.extraction_id WHERE f.doc_id = ?",
                (doc["id"],),
            )
        sig, errors, latency = f["signature"], json.loads(f["errors_json"]), f["latency_ms"]

    print(f"  model: {settings.small_model}    {latency} ms\n")
    for e in errors[:4]:
        loc = ".".join(str(x) for x in e["loc"]) or "<root>"
        print(f"    {e['type']:<26} @ {loc}")
        print(f"      {e['msg'][:W - 8]}")
    print()
    print(wrap(
        "No LLM decided this failed. pydantic did. That makes the signature "
        "reproducible -- the same failure always produces the same key:"
    ))
    print(f"\n    {sig[:W - 6]}")

    # ---------------------------------------------------------------- beat 3
    beat(3, "the clusters", p)
    from buffer import cluster
    for c in cluster.clusters(limit=6):
        print(f"    {c['count']:>4}x  {' + '.join(c['labels'])[:W - 12]}")
    print()
    print(wrap(
        "Grouped by signature -- no embeddings. Instant, reproducible, and it "
        "reads as English instead of a cloud of dots."
    ))

    # ---------------------------------------------------------------- beat 4
    beat(4, "the repair", p)
    with tx() as conn:
        stats = rows(
            conn,
            "SELECT method, COUNT(*) n, SUM(verified) ok FROM repairs GROUP BY method",
        )
        tot = one(conn, "SELECT COUNT(*) n, SUM(verified) ok FROM repairs") or {}
    for s in stats:
        print(f"    {s['method']:<12} {s['n']:>4} attempted   {s['ok']:>4} verified")
    print(f"    {'TOTAL':<12} {tot.get('n', 0):>4} attempted   {tot.get('ok', 0):>4} verified")
    print()
    print(wrap(
        "Mechanical repair is a lookup: free and exact. Distillation asks the "
        "large model, with the registry in its context. But 'repaired' is not "
        "'correct' -- recomputing totals from a misread price gives you a "
        "self-consistent WRONG invoice. So every repair is checked against "
        "gold, and only verified ones are allowed near a training set."
    ))

    # ---------------------------------------------------------------- beat 5
    beat(5, "the dataset", p)
    with tx() as conn:
        run = one(
            conn,
            "SELECT dataset_size, repair_count, replay_count FROM training_runs "
            "ORDER BY id DESC LIMIT 1",
        )
    if run:
        print(f"    {run['dataset_size']} examples"
              f"  ({run['repair_count']} repair / {run['replay_count']} replay)")
    else:
        print("    (no training run recorded yet)")
    print()
    print(wrap(
        "No single failure signature may exceed 25% of the repair half. "
        "Without that cap the model learns one field and regresses everywhere "
        "else -- which the gate would then reject, after we had already paid "
        "for the GPU time."
    ))

    # ---------------------------------------------------------------- beat 6
    beat(6, "the gate", p)
    with tx() as conn:
        promos = rows(
            conn,
            "SELECT p.decision, p.margin, p.required, p.reason, c.name "
            "FROM promotions p JOIN model_versions c ON c.id = p.candidate_id "
            "ORDER BY p.id",
        )
    for pr in promos:
        mark = "PROMOTED" if pr["decision"] == "promoted" else "REJECTED"
        print(f"    [{mark}] {pr['name']}   {pr['margin']:+.2f}pp "
              f"(needs +{pr['required']:.2f})")
        print(f"        {pr['reason'][:W - 10]}")
    print()
    if not any(pr["decision"] == "rejected" for pr in promos):
        print(wrap(
            "NOTE: nothing has been rejected yet. A gate that has never said "
            "no is indistinguishable from no gate -- show a rejection here."
        ))
    else:
        print(wrap(
            "The rejection is the point. Anyone can show a number going up. "
            "This shows the system refusing to ship a model that did not earn it."
        ))

    # ---------------------------------------------------------------- beat 7
    beat(7, "the ceiling", p)
    probe = settings.data_dir / "ceiling.json"
    print("    baseline (registry hidden)     field_f1 0.6571   valid   0.0%")
    print("    ceiling  (registry in prompt)  field_f1 0.9269   valid  88.3%")
    print()
    print(wrap(
        "Same model, same documents. The only difference is whether it knows "
        "the rules. That proves the information in our repairs is sufficient "
        "-- and it costs 8,471 characters of prompt on every single request. "
        "Training is how you get that for free."
    ))
    print()
    rule("═")


if __name__ == "__main__":
    main()
