"""Ceiling test: how well would the model do if it simply KNEW the rules?

The loop's premise is that its verified repairs carry exactly the information
the serving model lacks. This tests that premise directly, without a LoRA and
without GPU time: score the frozen holdout with the registry pasted into the
system prompt.

  * If accuracy jumps to near-perfect, the information is sufficient and the
    only open question is whether a LoRA can internalise it. That makes this
    number the realistic TARGET for a successful retrain.
  * If it barely moves, the repairs are not the bottleneck and fine-tuning was
    never going to fix it -- which would be a much more important finding.

This is NOT a substitute for training, and its number must never be reported
as a fine-tune result. The registry is in the prompt here; the whole point of
training is to get that behaviour WITHOUT a 3k-token prompt on every request.

    python scripts/ceiling_test.py --workers 6
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from core import registry
from core.config import settings
from core.db import init_db
from evalgate.scorer import holdout_docs
from serve.client import chat
from serve.extract import system_prompt
from verify.compare import counts, f1, flatten, scored_fields
from verify.validate import validate


def ceiling_prompt() -> str:
    return (
        system_prompt()
        + "\n\nINTERNAL REFERENCE DATA (authoritative — use it):\n"
        + registry.registry_prompt()
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Registry-in-prompt ceiling test")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="0 = whole holdout")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    init_db()
    docs = holdout_docs()
    if args.limit:
        docs = docs[: args.limit]
    model = args.model or settings.small_model
    system = ceiling_prompt()

    print(f"ceiling test: {model} on {len(docs)} holdout documents")
    print(f"system prompt is {len(system)} chars (registry included)\n")

    def one(doc: dict) -> tuple[dict, object]:
        raw, _ = chat(model, system, doc["text"], temperature=0.0, max_tokens=2048)
        return doc, validate(raw)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(one, docs))

    n_valid = 0
    tp = pred = gold_n = 0
    per_field: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    for doc, outcome in results:
        if outcome.valid:
            n_valid += 1
        gold = json.loads(doc["gold_json"]) if doc["gold_json"] else {}
        if not gold:
            continue
        a, b, c = counts(outcome.scorable, gold)
        tp, pred, gold_n = tp + a, pred + b, gold_n + c

        fp, fg = flatten(outcome.scorable), flatten(gold)
        for path in scored_fields():
            if isinstance(fg.get(path), list) or path not in fg:
                continue
            per_field[path][1] += 1
            if fp.get(path) == fg[path]:
                per_field[path][0] += 1

        pred_by_desc = {
            str(i.get("description", "")).strip().lower(): i.get("category")
            for i in (outcome.scorable.get("line_items") or [])
            if isinstance(i, dict)
        }
        for item in gold.get("line_items") or []:
            if not isinstance(item, dict):
                continue
            per_field["line_items.category"][1] += 1
            if pred_by_desc.get(str(item.get("description", "")).strip().lower()) == item.get("category"):
                per_field["line_items.category"][0] += 1

    print("=" * 62)
    print(f"valid_rate  {n_valid / len(docs):.1%}   ({n_valid}/{len(docs)})")
    print(f"field_f1    {f1(tp, pred, gold_n):.4f}")
    print("\nbusiness-rule fields:")
    for k in ("vendor_id", "line_items.category", "payment_terms"):
        if per_field[k][1]:
            print(f"  {per_field[k][0] / per_field[k][1]:6.1%}  {k}")
    print("=" * 62)
    print("This is the CEILING, not a fine-tune result. The registry is in the")
    print("prompt here; training exists to get this without paying 3k tokens")
    print("of prompt on every single request.")


if __name__ == "__main__":
    main()
