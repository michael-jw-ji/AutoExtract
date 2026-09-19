"""Deterministic repairs. Free, instant, and exact -- always try these first.

CAUTION: mechanical repair makes output schema-VALID, not necessarily
CORRECT. If the model misread a unit_price, recomputing the totals from it
produces a self-consistent, confidently wrong invoice. Training on that
actively degrades the model. That is why repair/run.py verifies every repair
against gold before it is allowed anywhere near a training set.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from core import registry

CENT = Decimal("0.01")

CURRENCY_MAP = {
    "$": "USD", "us$": "USD", "usd": "USD", "dollar": "USD", "dollars": "USD",
    "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR",
    "£": "GBP", "gbp": "GBP", "pound": "GBP", "pounds": "GBP", "sterling": "GBP",
    "c$": "CAD", "cad": "CAD", "cdn": "CAD", "canadian dollar": "CAD",
}

DATE_FORMATS = [
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y",
    "%d %B %Y", "%B %d, %Y", "%b %d, %Y", "%d %b %Y", "%Y/%m/%d",
    "%d.%m.%Y", "%Y%m%d",
]


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    text = re.sub(r"[^\d.\-]", "", str(value))
    if text in ("", "-", "."):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def fix_money(value: Any) -> Any:
    d = _dec(value)
    return str(d.quantize(CENT)) if d is not None else value


def fix_date(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def fix_currency(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    key = value.strip().lower()
    return CURRENCY_MAP.get(key, value.strip().upper() if len(key) == 3 else value)


def fix_terms(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip().lower()
    if re.search(r"due\s*on\s*receipt|immediate|on receipt", text):
        return "DUE_ON_RECEIPT"
    m = re.search(r"net\s*[-_]?\s*(\d+)", text)
    if m and m.group(1) in {"15", "30", "45", "60"}:
        return f"NET_{m.group(1)}"
    return value.strip().upper().replace(" ", "_").replace("-", "_")


def fix_invoice_number(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = re.sub(r"[\s_]+", "-", value.strip().upper())
    text = re.sub(r"-{2,}", "-", text).strip("-")
    m = re.match(r"^([A-Z]{2,4})-?(\d{4,8})$", text.replace("-", ""))
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return text


def repair(payload: dict) -> dict:
    """Apply every deterministic fix. Pure -- does not mutate the input."""
    doc: dict[str, Any] = dict(payload)

    # Drop hallucinated top-level keys (schema is extra='forbid').
    allowed = {
        "invoice_number", "issue_date", "due_date", "currency", "payment_terms",
        "vendor_id", "vendor", "bill_to", "line_items", "subtotal", "tax",
        "discount", "total",
    }
    doc = {k: v for k, v in doc.items() if k in allowed}

    if "invoice_number" in doc:
        doc["invoice_number"] = fix_invoice_number(doc["invoice_number"])
    for key in ("issue_date", "due_date"):
        if key in doc:
            doc[key] = fix_date(doc[key])
    if "currency" in doc:
        doc["currency"] = fix_currency(doc["currency"])
    if "payment_terms" in doc:
        doc["payment_terms"] = fix_terms(doc["payment_terms"])

    for party in ("vendor", "bill_to"):
        if isinstance(doc.get(party), dict):
            doc[party] = {
                k: v for k, v in doc[party].items()
                if k in {"name", "address", "tax_id"}
            }

    # Line items: recompute line_total from quantity * unit_price.
    items = doc.get("line_items")
    if isinstance(items, list):
        fixed_items = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            item = {
                k: v for k, v in raw.items()
                if k in {"description", "category", "quantity", "unit_price",
                         "line_total"}
            }
            # Exact taxonomy hit only. A paraphrased description falls through
            # to the distillation pass rather than being guessed at here.
            code = registry.category_for(item.get("description"))
            if code is not None:
                item["category"] = code
            qty, price = _dec(item.get("quantity")), _dec(item.get("unit_price"))
            if qty is not None and price is not None:
                item["quantity"] = str(qty.quantize(CENT))
                item["unit_price"] = str(price.quantize(CENT))
                item["line_total"] = str((qty * price).quantize(CENT))
            else:
                for k in ("quantity", "unit_price", "line_total"):
                    if k in item:
                        item[k] = fix_money(item[k])
            fixed_items.append(item)
        doc["line_items"] = fixed_items

        totals = [_dec(i.get("line_total")) for i in fixed_items]
        if totals and all(t is not None for t in totals):
            subtotal = sum(totals, Decimal("0")).quantize(CENT)
            doc["subtotal"] = str(subtotal)
            tax = _dec(doc.get("tax")) or Decimal("0")
            discount = _dec(doc.get("discount")) or Decimal("0")
            doc["tax"] = str(tax.quantize(CENT))
            doc["discount"] = str(discount.quantize(CENT))
            doc["total"] = str((subtotal + tax - discount).quantize(CENT))

    for key in ("subtotal", "tax", "discount", "total"):
        if key in doc:
            doc[key] = fix_money(doc[key])

    # Business rules: pure lookups once the vendor name matches the registry.
    vendor_name = (doc.get("vendor") or {}).get("name")
    vendor_id = registry.vendor_id_for(vendor_name)
    if vendor_id is not None:
        doc["vendor_id"] = vendor_id
        terms = registry.terms_for(
            vendor_name, doc.get("total", "0"), doc.get("currency")
        )
        if terms is not None:
            doc["payment_terms"] = terms

    # due_date before issue_date is unrecoverable mechanically -- leave it,
    # the distillation pass or the eval gate will deal with it.
    return doc
