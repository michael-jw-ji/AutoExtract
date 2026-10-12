"""Explain one scored answer: what matched, what differed, and a first guess at why.

Used by scripts/failures_form4.py to read failures by hand. The theme guesses
are a starting point for a human, not a verdict: the point of Milestone 2 is
that a person reads the failures and decides the root cause.
"""

from __future__ import annotations

import re
from typing import Any

from verify.compare import counts, drop_fields, f1, pair_rows, same, scored_fields
from verify.validate import validate

LIST_FIELDS = ("reporting_owners", "transactions")
UNSCORED = {"value"}
_PLACEHOLDER_TITLE = re.compile(r"^\s*(non-?)?derivative\s+securit(y|ies)\s*$", re.I)
_DATE = re.compile(r"\d{2}/\d{2}/\d{4}")


def _rows(doc: Any, path: str) -> list:
    value = doc.get(path) if isinstance(doc, dict) else None
    return value if isinstance(value, list) else []


def _amount_forms(value: Any) -> list[str]:
    """How a number from the JSON would be printed on the page."""
    try:
        n = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return []
    if n <= 0:
        return []
    whole = f"{int(n):,}" if n == int(n) else f"{n:,}"
    return [whole]


def _is_placeholder(row: dict) -> bool:
    title = str(row.get("security_title") or "")
    code = str(row.get("code") or "").strip().upper()
    return bool(_PLACEHOLDER_TITLE.match(title)) or code in {"", "N/A", "NA", "NONE"} \
        or str(row.get("shares")).strip() in {"0", "0.0", "None", ""}


def _is_holding(row: dict, text: str) -> bool:
    """An extra row whose amount appears on a page line with no date: a holdings row."""
    forms = _amount_forms(row.get("shares_owned_after")) + _amount_forms(row.get("shares"))
    for line in text.splitlines():
        if "|" in line and not _DATE.search(line) and any(f in line for f in forms):
            return True
    return False


def analyze(text: str, gold: dict, raw: str, ambiguous: list[str] | None = None) -> dict:
    outcome = validate(raw)
    pred = outcome.scorable if isinstance(outcome.scorable, dict) else {}
    from evalgate.scorer import skip_fields
    skip = skip_fields(ambiguous or [], gold)
    p_s, g_s = drop_fields(pred, skip), drop_fields(gold, skip)
    tp, n_pred, n_gold = counts(p_s, g_s)

    themes: list[str] = []

    def theme(name: str) -> None:
        if name not in themes:
            themes.append(name)

    if any(e["type"] == "json_decode" for e in outcome.errors):
        theme("unparseable output")
    for e in outcome.errors:
        if e["type"] in {"decimal_type", "decimal_parsing"}:
            theme("non-numeric value")

    scalars = []
    for path in scored_fields():
        if path in LIST_FIELDS:
            continue
        pv, gv = pred.get(path), gold.get(path)
        ok = same(path, pv, gv) or path in skip
        scalars.append({"field": path, "pred": pv, "gold": gv, "ok": ok})
        if not ok:
            theme(f"wrong {path}")

    tables = {}
    for path in LIST_FIELDS:
        pr, gr = _rows(pred, path), _rows(gold, path)
        out = []
        for pi, gi in pair_rows(path, _rows(p_s, path), _rows(g_s, path)):
            p = pr[pi] if pi is not None and isinstance(pr[pi], dict) else None
            g = gr[gi] if gi is not None and isinstance(gr[gi], dict) else None
            keys = [k for k in dict.fromkeys(list((g or {}).keys()) + list((p or {}).keys()))
                    if k not in UNSCORED]
            cells = []
            for k in keys:
                leaf = f"{path}.*.{k}"
                ok = leaf in skip or (p is not None and g is not None and
                                      same(leaf, (p or {}).get(k), (g or {}).get(k)))
                cells.append({"field": k, "pred": (p or {}).get(k), "gold": (g or {}).get(k),
                              "ok": ok, "ambiguous": leaf in skip})
            if p is not None and g is None:
                kind = "extra"
                if path == "transactions":
                    theme("placeholder row for an empty table" if _is_placeholder(p)
                          else "holdings row extracted" if _is_holding(p, text)
                          else "extra row")
                else:
                    theme("extra owner")
            elif g is not None and p is None:
                kind = "missing"
                theme("missing row" if path == "transactions" else "missing owner")
            else:
                kind = "match" if all(c["ok"] for c in cells) else "diff"
                for c in cells:
                    if c["ok"]:
                        continue
                    if path == "reporting_owners":
                        theme("owner roles" if c["field"].startswith("is_") or
                              c["field"] == "officer_title" else "owner name")
                    elif c["field"] == "is_derivative":
                        theme("table I vs II confusion")
                    elif c["field"] in {"shares", "shares_owned_after"}:
                        theme("wrong amount")
                    else:
                        theme(f"wrong {c['field']}")
            out.append({"kind": kind, "cells": cells})
        tables[path] = out

    if "unparseable output" in themes:
        # Every field "differs" when nothing parsed; naming them all would
        # bury the one real cause.
        themes = ["unparseable output"]

    return {
        "valid": outcome.valid,
        "signature": outcome.signature,
        "errors": [{"type": e["type"], "loc": ".".join(str(x) for x in e.get("loc", ())),
                    "msg": e["msg"]} for e in outcome.errors],
        "f1": round(f1(tp, n_pred, n_gold), 4),
        "scalars": scalars,
        "tables": tables,
        "themes": themes or (["correct"] if outcome.valid else ["other"]),
    }
