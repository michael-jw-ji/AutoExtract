"""Large-model repair, for failures the mechanical pass cannot fix.

This is distillation: the large model's corrected output becomes training data
for the small model. It is the expensive path, so it only runs on failures
that survive repair/mechanical.py.
"""

from __future__ import annotations

import json

from core import registry
from core.config import settings
from serve.client import chat
from verify.schema import INVOICE_JSON_SCHEMA
from verify.validate import describe, strip_fences

SYSTEM = """You are correcting a failed structured extraction.

You are given a source document, a previous JSON attempt, and the exact \
validation errors it produced. Return ONLY the corrected JSON object. No \
prose, no markdown fences.

Target JSON Schema:
{schema}

Rules:
- Dates ISO-8601 (YYYY-MM-DD). currency in USD/EUR/GBP/CAD.
- payment_terms in NET_15/NET_30/NET_45/NET_60/DUE_ON_RECEIPT.
- invoice_number matches ^[A-Z]{{2,4}}-\\d{{4,8}}$.
- line_total = quantity * unit_price, exactly, for every item.
- subtotal = sum of line_total. total = subtotal + tax - discount.
- vendor_id, line-item category, and payment_terms are NOT printed on the \
document. Derive them from the INTERNAL REFERENCE DATA above.
- Read values from the SOURCE DOCUMENT. Do not invent numbers to satisfy the \
arithmetic -- if the printed total disagrees with the line items, trust the \
line items and recompute."""

USER = """INTERNAL REFERENCE DATA (the serving model does not have this):
{registry}

SOURCE DOCUMENT:
{document}

PREVIOUS ATTEMPT:
{attempt}

VALIDATION ERRORS:
{errors}

Corrected JSON:"""


def repair(doc_text: str, attempt: str, errors: list[dict]) -> dict | None:
    """Ask the large model for a fix. Returns parsed JSON or None."""
    system = SYSTEM.format(schema=json.dumps(INVOICE_JSON_SCHEMA, indent=2))
    user = USER.format(
        registry=registry.registry_prompt(),
        document=doc_text,
        attempt=attempt[:4000],
        errors=describe(errors),
    )
    raw, _ = chat(settings.large_model, system, user, temperature=0.0, max_tokens=2048)
    try:
        payload = json.loads(strip_fences(raw))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
