"""Deterministic enrichment — runs at SERVE time, before validation.

This is the single biggest architectural lesson from the experiments: every
field that has a right answer should be computed by code, not guessed by a
model. Previously this logic lived in repair/, i.e. it only ran AFTER a
document had already failed. Running it immediately means most failures never
happen.

What the model is asked for:  what the page SAYS.
What code derives:            everything that follows from that.

Measured on 300 documents before this existed:
  * 66 wrong vendor_id       -> a dictionary lookup
  * 32 unknown-vendor errors -> a missing .lower()
  *  5 more                  -> fuzzy matching at 85%
  * 43 wrong payment_terms   -> a formula
  * arithmetic errors        -> multiplication

None of those needed a model. All of them were being sent to one.
"""

from __future__ import annotations

import difflib
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from core import registry
from repair.mechanical import fix_currency, fix_date, fix_invoice_number, fix_terms

CENT = Decimal("0.01")

#: Below this ratio we do not guess. A wrong confident match is worse than an
#: honest failure -- the validator can flag "unknown vendor", but it cannot
#: flag "plausible vendor, wrong company".
FUZZY_CUTOFF = 0.85

_VENDOR_BY_LOWER = {k.lower(): k for k in registry.VENDORS}
_CATEGORY_BY_LOWER = {k.lower(): v for k, v in registry.CATEGORIES.items()}


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = re.sub(r"[^\d.\-]", "", str(value))
    if text in ("", "-", "."):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def match_vendor(name: str | None) -> tuple[str | None, str]:
    """Resolve a vendor name to a registry key. Returns (key, how)."""
    if not name or not isinstance(name, str):
        return None, "absent"
    raw = name.strip()
    if raw in registry.VENDORS:
        return raw, "exact"
    if raw.lower() in _VENDOR_BY_LOWER:
        return _VENDOR_BY_LOWER[raw.lower()], "case"
    close = difflib.get_close_matches(
        raw.lower(), list(_VENDOR_BY_LOWER), n=1, cutoff=FUZZY_CUTOFF
    )
    if close:
        return _VENDOR_BY_LOWER[close[0]], "fuzzy"
    return None, "unmatched"


def match_category(description: str | None) -> tuple[str | None, str]:
    """Resolve a product description to a taxonomy code. Returns (code, how)."""
    if not description or not isinstance(description, str):
        return None, "absent"
    raw = description.strip()
    if raw in registry.CATEGORIES:
        return registry.CATEGORIES[raw], "exact"
    if raw.lower() in _CATEGORY_BY_LOWER:
        return _CATEGORY_BY_LOWER[raw.lower()], "case"
    close = difflib.get_close_matches(
        raw.lower(), list(_CATEGORY_BY_LOWER), n=1, cutoff=FUZZY_CUTOFF
    )
    if close:
        return _CATEGORY_BY_LOWER[close[0]], "fuzzy"
    return None, "unmatched"


def enrich(payload: dict) -> tuple[dict, dict]:
    """Normalise formats, recompute arithmetic, and derive every rule field.

    Returns (enriched_payload, report). The report says how each derived field
    was resolved, so the dashboard can distinguish "looked it up" from "the
    model guessed and we left it alone".
    """
    if not isinstance(payload, dict):
        return payload, {"skipped": "not an object"}

    doc: dict[str, Any] = dict(payload)
    report: dict[str, Any] = {}

    # --- formats the model routinely gets wrong -----------------------------
    if "invoice_number" in doc:
        doc["invoice_number"] = fix_invoice_number(doc["invoice_number"])
    for key in ("issue_date", "due_date"):
        if key in doc:
            doc[key] = fix_date(doc[key])
    if "currency" in doc:
        doc["currency"] = fix_currency(doc["currency"])

    # --- line items: categories + arithmetic --------------------------------
    items = doc.get("line_items")
    if isinstance(items, list):
        fixed, hows = [], []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            code, how = match_category(item.get("description"))
            hows.append(how)
            if code:
                item["category"] = code

            qty, price = _dec(item.get("quantity")), _dec(item.get("unit_price"))
            if qty is not None and price is not None:
                item["quantity"] = str(qty.quantize(CENT))
                item["unit_price"] = str(price.quantize(CENT))
                # Never trust a printed line total -- derive it.
                item["line_total"] = str((qty * price).quantize(CENT))
            fixed.append(item)
        doc["line_items"] = fixed
        report["categories"] = hows

        totals = [_dec(i.get("line_total")) for i in fixed]
        if totals and all(t is not None for t in totals):
            subtotal = sum(totals, Decimal("0")).quantize(CENT)
            tax = _dec(doc.get("tax")) or Decimal("0")
            discount = _dec(doc.get("discount")) or Decimal("0")
            doc["subtotal"] = str(subtotal)
            doc["tax"] = str(tax.quantize(CENT))
            doc["discount"] = str(discount.quantize(CENT))
            doc["total"] = str((subtotal + tax - discount).quantize(CENT))
            report["arithmetic"] = "recomputed"

    # --- the business rules -------------------------------------------------
    vendor = doc.get("vendor") if isinstance(doc.get("vendor"), dict) else {}
    key, how = match_vendor(vendor.get("name"))
    report["vendor_match"] = how
    if key:
        # Canonicalise the name too, so downstream comparisons line up.
        doc["vendor"] = {**vendor, "name": key}
        doc["vendor_id"] = registry.VENDORS[key]["id"]
        terms = registry.terms_for(key, doc.get("total", "0"), doc.get("currency"))
        if terms:
            doc["payment_terms"] = terms
            report["payment_terms"] = "derived"
    else:
        # Leave the model's guess alone. The validator will flag it, which is
        # the honest outcome: we would rather report "unknown vendor" than
        # silently attach a confidently wrong ID.
        if "payment_terms" in doc:
            doc["payment_terms"] = fix_terms(doc["payment_terms"])

    return doc, report
