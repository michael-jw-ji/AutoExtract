"""Frozen-holdout scorer.

Two metrics, deliberately:
  * valid_rate -- fraction that passes the schema at all
  * field_f1   -- micro-averaged field-level F1 against gold

valid_rate alone is far too coarse at n~100. It moves in whole-percent steps,
so noise swamps genuine improvement and the gate ends up promoting on coin
flips. field_f1 moves continuously and is what the promotion margin is
measured on.
"""

from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from core.db import insert, one, rows, tx
from core.freeze import current_hash, holdout_ids
from serve.extract import extract_text
from verify.compare import counts, f1, flatten, scored_fields
from verify.validate import validate


def holdout_docs() -> list[dict]:
    ids = holdout_ids()
    with tx() as conn:
        return [
            r for r in rows(
                conn, "SELECT id, text, gold_json FROM documents WHERE split='holdout'"
            ) if r["id"] in ids
        ]


def score_model(
    model_version_id: int | None,
    model_ref: str | None = None,
    *,
    persist: bool = True,
    progress: bool = False,
    workers: int = 10,
) -> dict:
    """Run the frozen holdout through one model and score it."""
    docs = holdout_docs()
    if not docs:
        raise RuntimeError("Holdout is empty. Run scripts/gen_docs.py then freeze.")

    n_valid = 0
    tp_total = pred_total = gold_total = 0
    per_field_hits: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    def score_one(doc: dict) -> tuple[dict, object]:
        raw, _, _ = extract_text(doc["text"], model_ref)
        return doc, validate(raw)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(score_one, docs))

    for i, (doc, outcome) in enumerate(results, 1):
        if outcome.valid:
            n_valid += 1

        # Best-effort payload, not just validated output -- see Outcome.scorable.
        pred = outcome.scorable
        gold = json.loads(doc["gold_json"]) if doc["gold_json"] else {}
        if gold:
            tp, n_pred, n_gold = counts(pred, gold)
            tp_total += tp
            pred_total += n_pred
            gold_total += n_gold

            fp, fg = flatten(pred), flatten(gold)
            for path in scored_fields():
                if isinstance(fg.get(path), list):
                    continue
                if path in fg:
                    per_field_hits[path][1] += 1
                    if fp.get(path) == fg[path]:
                        per_field_hits[path][0] += 1

            # line_items.category is the single biggest business-rule field,
            # and the per-field loop skips line_items wholesale -- so without
            # this it never appears in the analytics at all. Match items by
            # description, which the model reads off the page and usually gets
            # right, then compare only the code it had to infer.
            pred_by_desc = {
                str(i.get("description", "")).strip().lower(): i.get("category")
                for i in (pred.get("line_items") or [])
                if isinstance(i, dict)
            }
            for item in gold.get("line_items") or []:
                if not isinstance(item, dict):
                    continue
                desc = str(item.get("description", "")).strip().lower()
                per_field_hits["line_items.category"][1] += 1
                if pred_by_desc.get(desc) == item.get("category"):
                    per_field_hits["line_items.category"][0] += 1

        if progress:
            print(f"  [{i}/{len(docs)}] valid={outcome.valid}", flush=True)

    result = {
        "model_version_id": model_version_id,
        "eval_set_hash": current_hash(),
        "valid_rate": round(n_valid / len(docs), 4),
        "field_f1": round(f1(tp_total, pred_total, gold_total), 4),
        "n_docs": len(docs),
        "per_field": {
            k: round(v[0] / v[1], 4) for k, v in per_field_hits.items() if v[1]
        },
    }

    if persist and model_version_id is not None:
        with tx() as conn:
            insert(
                conn,
                "evals",
                model_version_id=model_version_id,
                eval_set_hash=result["eval_set_hash"],
                valid_rate=result["valid_rate"],
                field_f1=result["field_f1"],
                n_docs=result["n_docs"],
                per_field_json=json.dumps(result["per_field"]),
            )
    return result


def latest_eval(model_version_id: int) -> dict | None:
    with tx() as conn:
        return one(
            conn,
            "SELECT * FROM evals WHERE model_version_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (model_version_id,),
        )
