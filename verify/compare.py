"""Field-level comparison against gold. Shared by the repair verifier and the
eval scorer so "correct" means exactly one thing everywhere."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from verify.schema import SCORED_FIELDS

MONEY_FIELDS = {"subtotal", "tax", "discount", "total"}


def _norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float, Decimal)):
        try:
            return str(Decimal(str(value)).quantize(Decimal("0.01")))
        except InvalidOperation:
            return str(value)
    return str(value).strip().lower()


def _money(value: Any) -> str:
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except (InvalidOperation, TypeError, ValueError):
        return _norm(value)


def _get(doc: dict, path: str) -> Any:
    cur: Any = doc
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def line_item_key(item: dict) -> tuple:
    # `category` MUST be in this key. It is a business-rule field, so it is
    # precisely what the LoRA is supposed to learn -- leave it out and the
    # model can master the taxonomy without field_f1 moving at all, which
    # makes the promotion gate blind to the thing it is gating on.
    return (
        _norm(item.get("description")),
        _norm(item.get("category")),
        _money(item.get("quantity")),
        _money(item.get("unit_price")),
        _money(item.get("line_total")),
    )


def flatten(doc: dict) -> dict[str, Any]:
    """Leaf fields as comparable scalars, with line_items as a multiset key."""
    out: dict[str, Any] = {}
    for path in SCORED_FIELDS:
        if path == "line_items":
            items = doc.get("line_items") or []
            if isinstance(items, list):
                out["line_items"] = sorted(
                    line_item_key(i) for i in items if isinstance(i, dict)
                )
            continue
        value = _get(doc, path)
        if value in (None, ""):
            continue
        out[path] = _money(value) if path.split(".")[-1] in MONEY_FIELDS else _norm(value)
    return out


def counts(pred: dict, gold: dict) -> tuple[int, int, int]:
    """(true_positives, predicted_count, gold_count) for one document."""
    p, g = flatten(pred), flatten(gold)
    tp = 0
    n_pred = 0
    n_gold = 0

    for path in SCORED_FIELDS:
        if path == "line_items":
            pi, gi = p.get("line_items", []), g.get("line_items", [])
            n_pred += len(pi)
            n_gold += len(gi)
            remaining = list(gi)
            for item in pi:
                if item in remaining:
                    remaining.remove(item)
                    tp += 1
            continue
        has_p, has_g = path in p, path in g
        n_pred += int(has_p)
        n_gold += int(has_g)
        if has_p and has_g and p[path] == g[path]:
            tp += 1

    return tp, n_pred, n_gold


def f1(tp: int, n_pred: int, n_gold: int) -> float:
    if n_pred == 0 or n_gold == 0:
        return 0.0
    precision = tp / n_pred
    recall = tp / n_gold
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def matches_gold(pred: dict, gold: dict, threshold: float = 1.0) -> bool:
    """Exact-enough match. Used to verify repairs before they reach training."""
    tp, n_pred, n_gold = counts(pred, gold)
    return f1(tp, n_pred, n_gold) >= threshold
