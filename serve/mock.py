"""Offline mock model. Set MOCK_LLM=1.

Looks up the document's gold record and corrupts it in the ways a small model
actually fails -- broken arithmetic, non-ISO dates, currency symbols, prose
payment terms, hallucinated keys. Corruption probability is tunable so the
whole loop (buffer -> cluster -> repair -> dataset -> gate) can be exercised
and tested with no API key and no GPU time.

This is a DEV HARNESS, not a simulator of model quality. Never report numbers
from mock mode as results.
"""

from __future__ import annotations

import json
import os
import random
from decimal import Decimal

from core.db import one, tx

CORRUPTIONS = [
    "arith_line", "arith_total", "date_format", "currency_word",
    "terms_prose", "extra_key", "drop_field", "invoice_fmt",
    # Business-rule failures. In mock mode these are sampled like any other,
    # but against a real serving model they fire on essentially every
    # document, because the registry is not in its prompt.
    "wrong_vendor_id", "wrong_category", "wrong_terms",
]


def failure_rate() -> float:
    try:
        return float(os.getenv("MOCK_FAILURE_RATE", "0.35"))
    except ValueError:
        return 0.35


def enabled() -> bool:
    return os.getenv("MOCK_LLM") == "1"


def _gold_for(doc_text: str) -> dict | None:
    with tx() as conn:
        row = one(conn, "SELECT gold_json FROM documents WHERE text = ? LIMIT 1",
                  (doc_text,))
    if not row or not row["gold_json"]:
        return None
    return json.loads(row["gold_json"])


def _corrupt(doc: dict, rng: random.Random) -> dict:
    kind = rng.choice(CORRUPTIONS)
    out = json.loads(json.dumps(doc))

    if kind == "arith_line" and out.get("line_items"):
        item = rng.choice(out["line_items"])
        item["line_total"] = str(Decimal(item["line_total"]) + Decimal("3.50"))
    elif kind == "arith_total":
        out["total"] = str(Decimal(out["total"]) + Decimal("11.00"))
    elif kind == "date_format":
        y, m, d = out["issue_date"].split("-")
        out["issue_date"] = f"{d}/{m}/{y}"
    elif kind == "currency_word":
        out["currency"] = {"USD": "dollars", "EUR": "euro", "GBP": "£",
                           "CAD": "C$"}[out["currency"]]
    elif kind == "terms_prose":
        out["payment_terms"] = out["payment_terms"].replace("_", " ").title()
    elif kind == "extra_key":
        out["purchase_order"] = f"PO-{rng.randint(1000, 9999)}"
    elif kind == "drop_field" and out.get("line_items"):
        out["line_items"][0].pop("unit_price", None)
    elif kind == "invoice_fmt":
        out["invoice_number"] = out["invoice_number"].replace("-", " ").lower()
    elif kind == "wrong_vendor_id":
        out["vendor_id"] = f"VND-{rng.randint(10000, 99999)}"
    elif kind == "wrong_category" and out.get("line_items"):
        rng.choice(out["line_items"])["category"] = "XX-MISC-01"
    elif kind == "wrong_terms":
        out["payment_terms"] = rng.choice(
            [t for t in ["NET_15", "NET_30", "NET_45", "NET_60", "DUE_ON_RECEIPT"]
             if t != out["payment_terms"]]
        )

    return out


def respond(system: str, user: str) -> str:
    """Mock a chat completion. Repair prompts get the clean gold back."""
    rng = random.Random(hash(user) & 0xFFFFFFFF)

    # Repair prompts embed the source document after a known header.
    if "PREVIOUS ATTEMPT:" in user:
        doc_text = user.split("SOURCE DOCUMENT:\n", 1)[-1].split("\n\nPREVIOUS ATTEMPT:")[0]
        gold = _gold_for(doc_text.strip())
        return json.dumps(gold) if gold else "{}"

    gold = _gold_for(user.strip())
    if gold is None:
        return "{}"
    if rng.random() < failure_rate():
        return json.dumps(_corrupt(gold, rng))
    return json.dumps(gold)
