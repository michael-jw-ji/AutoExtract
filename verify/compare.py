"""Field-level comparison against gold. Shared by the repair verifier and the
eval scorer so "correct" means exactly one thing everywhere.

Domain-agnostic. A domain supplies:
  SCORED_FIELDS    dotted paths; a path whose value is a list is scored row by
                   row (a list of dicts) or as a multiset (a list of scalars)
  NUMERIC_FIELDS   optional, leaf names compared as numbers, so 12.50, "12.5"
                   and "$12.50" agree while a wrong number still does not
  UNSCORED_FIELDS  optional, `path.*.field` names left out of scoring (derived)
  normalize_field  optional, (path, value) -> value, for domain text quirks

Rows of a list field are paired with gold rows by best field overlap, then each
sub-field is scored on its own. That gives per-field accuracy for fields inside
repeated rows (`transactions.*.price_per_share`) and keeps one wrong cell from
discarding the rest of its row.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def _domain():
    """The active domain, resolved lazily (import cycle) and cached.

    No fallback: scoring against the wrong domain's fields yields 0/0 counts,
    which looks exactly like a model that got everything wrong.
    """
    global _D
    if _D is None:
        from domains import get_domain
        _D = get_domain()
    return _D


_D = None


def scored_fields() -> list[str]:
    return list(_domain().SCORED_FIELDS)


def _num(value: Any) -> str | None:
    """Canonical number text, or None if the value is not a number."""
    if isinstance(value, bool):
        return None
    try:
        text = str(value).strip().replace(",", "").lstrip("$")
        d = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    return format(d.normalize(), "f") if d != 0 else "0"


def _norm(path: str, value: Any) -> str:
    """Comparable text for one leaf. Empty string means 'no value'."""
    dom = _domain()
    fix = getattr(dom, "normalize_field", None)
    if fix is not None:
        value = fix(path, value)
    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    leaf = path.split(".")[-1]
    if leaf in getattr(dom, "NUMERIC_FIELDS", ()):
        n = _num(value)
        if n is not None:
            return n
    if isinstance(value, (int, float, Decimal)):
        n = _num(value)
        if n is not None:
            return n
    # Collapse internal whitespace: filers' XML has 'Class A  Common Stock'
    # (two spaces) where the rendered page shows one, so a model that copies
    # the page exactly would otherwise be scored wrong on 7% of titles.
    return " ".join(str(value).split()).lower()


def _get(doc: dict, path: str) -> Any:
    cur: Any = doc
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _row(path: str, item: Any) -> dict[str, str]:
    """A list-of-dicts row as {field: comparable text}, empty values dropped."""
    if not isinstance(item, dict):
        return {}
    skip = getattr(_domain(), "UNSCORED_FIELDS", ())
    out = {}
    for key, value in item.items():
        if f"{path}.*.{key}" in skip:
            continue
        text = _norm(f"{path}.*.{key}", value)
        if text != "":
            out[key] = text
    return out


def _is_row_list(*values: Any) -> bool:
    return any(isinstance(v, list) and any(isinstance(i, dict) for i in v)
               for v in values)


def _align(pred: list[dict], gold: list[dict]) -> list[tuple[int | None, int | None]]:
    """Greedy best-overlap pairing of predicted rows to gold rows.

    Index-by-index pairing would let one dropped row shift every later row out
    of alignment and zero the rest of the table. Pairs need at least one equal
    field; leftovers on either side stay unpaired (all-wrong / all-spurious).
    """
    scored = []
    for gi, g in enumerate(gold):
        for pi, p in enumerate(pred):
            overlap = sum(1 for k, v in g.items() if p.get(k) == v)
            if overlap:
                scored.append((-overlap, gi, pi))
    scored.sort()
    used_g: set[int] = set()
    used_p: set[int] = set()
    pairs: list[tuple[int | None, int | None]] = []
    for _, gi, pi in scored:
        if gi in used_g or pi in used_p:
            continue
        used_g.add(gi)
        used_p.add(pi)
        pairs.append((pi, gi))
    pairs += [(None, gi) for gi in range(len(gold)) if gi not in used_g]
    pairs += [(pi, None) for pi in range(len(pred)) if pi not in used_p]
    return pairs


def _rows(path: str, pred: Any, gold: Any):
    p = [_row(path, i) for i in (pred if isinstance(pred, list) else [])]
    g = [_row(path, i) for i in (gold if isinstance(gold, list) else [])]
    for pi, gi in _align(p, g):
        yield (p[pi] if pi is not None else {}), (g[gi] if gi is not None else {})


def pair_rows(path: str, pred: Any, gold: Any) -> list[tuple[int | None, int | None]]:
    """(pred index, gold index) pairs, using the scorer's own row alignment.

    For tools that need to SHOW why a score is what it is: a pair with a None
    side is an extra (pred only) or a missing (gold only) row.
    """
    p = [_row(path, i) for i in (pred if isinstance(pred, list) else [])]
    g = [_row(path, i) for i in (gold if isinstance(gold, list) else [])]
    return _align(p, g)


def same(path: str, a: Any, b: Any) -> bool:
    """Whether two leaf values count as equal to the scorer."""
    return _norm(path, a) == _norm(path, b)


def counts(pred: dict, gold: dict) -> tuple[int, int, int]:
    """(true_positives, predicted_count, gold_count) for one document.

    Every non-empty leaf is one unit, including each cell of each row.
    """
    tp = n_pred = n_gold = 0
    for path in scored_fields():
        pv, gv = _get(pred, path), _get(gold, path)
        if _is_row_list(pv, gv):
            for prow, grow in _rows(path, pv, gv):
                n_pred += len(prow)
                n_gold += len(grow)
                tp += sum(1 for k, v in grow.items() if prow.get(k) == v)
        elif isinstance(pv, list) or isinstance(gv, list):
            pi = [_norm(path, i) for i in (pv or []) if _norm(path, i)]
            gi = [_norm(path, i) for i in (gv or []) if _norm(path, i)]
            n_pred += len(pi)
            n_gold += len(gi)
            remaining = list(gi)
            for item in pi:
                if item in remaining:
                    remaining.remove(item)
                    tp += 1
        else:
            p, g = _norm(path, pv), _norm(path, gv)
            n_pred += int(p != "")
            n_gold += int(g != "")
            if p != "" and p == g:
                tp += 1
    return tp, n_pred, n_gold


def field_hits(pred: dict, gold: dict) -> dict[str, tuple[int, int]]:
    """Per-field (hits, total) for one document, over fields gold has a value for.

    Fields inside list-of-dicts rows are reported as `path.*.field`. A gold row
    with no predicted counterpart counts every one of its fields as missed.
    """
    out: dict[str, list[int]] = {}

    def bump(key: str, hit: bool) -> None:
        cell = out.setdefault(key, [0, 0])
        cell[1] += 1
        cell[0] += int(hit)

    for path in scored_fields():
        pv, gv = _get(pred, path), _get(gold, path)
        if _is_row_list(pv, gv):
            for prow, grow in _rows(path, pv, gv):
                for key, value in grow.items():
                    bump(f"{path}.*.{key}", prow.get(key) == value)
        elif isinstance(pv, list) or isinstance(gv, list):
            continue
        else:
            g = _norm(path, gv)
            if g != "":
                bump(path, _norm(path, pv) == g)
    return {k: (v[0], v[1]) for k, v in out.items()}


def drop_fields(doc: Any, paths: list[str]) -> Any:
    """A copy of `doc` without the given fields, for excluding ambiguous ones.

    Paths are dotted, with `*` for "every row": `issuer_name`,
    `transactions.*.price_per_share`. Missing paths are ignored. Never mutates.
    """
    import copy

    out = copy.deepcopy(doc)
    for path in paths:
        parts = path.split(".")

        def walk(node: Any, i: int) -> None:
            if i == len(parts) - 1:
                if isinstance(node, dict):
                    node.pop(parts[i], None)
                return
            if parts[i] == "*":
                for item in node if isinstance(node, list) else []:
                    walk(item, i + 1)
            elif isinstance(node, dict) and parts[i] in node:
                walk(node[parts[i]], i + 1)

        walk(out, 0)
    return out


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
