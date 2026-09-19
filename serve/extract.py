"""Extraction: document text in, validated Invoice JSON (or a logged failure) out."""

from __future__ import annotations

import json

from core.config import settings
from core.db import connect, incumbent, insert, tx
from serve.client import chat, serving_model
from verify.schema import INVOICE_JSON_SCHEMA
from verify.validate import Outcome, validate

SYSTEM = """You extract structured invoice data from messy documents.

Return ONLY a JSON object matching this JSON Schema. No prose, no markdown \
fences, no explanation.

{schema}

Hard requirements:
- Dates must be ISO-8601 (YYYY-MM-DD).
- currency must be one of USD, EUR, GBP, CAD.
- payment_terms must be one of NET_15, NET_30, NET_45, NET_60, DUE_ON_RECEIPT.
- invoice_number must match ^[A-Z]{{2,4}}-\\d{{4,8}}$.
- Money values are decimals with exactly 2 decimal places.
- For every line item, line_total MUST equal quantity * unit_price.
- subtotal MUST equal the sum of all line_total values.
- total MUST equal subtotal + tax - discount.
Compute the arithmetic carefully. Do not copy a printed total that disagrees \
with the line items; recompute it.

Note: vendor_id, each line item's category, and payment_terms are not printed \
on the document. Supply your best value for each."""


COMPACT_SYSTEM = """Extract invoice data as JSON. Output ONLY the JSON object.

Fields:
  invoice_number  string, ^[A-Z]{2,4}-\\d{4,8}$
  issue_date      ISO date YYYY-MM-DD
  due_date        ISO date YYYY-MM-DD
  currency        USD | EUR | GBP | CAD
  payment_terms   NET_15 | NET_30 | NET_45 | NET_60 | DUE_ON_RECEIPT
  vendor_id       string, ^VND-\\d{5}$
  vendor          {name, address, tax_id|null}
  bill_to         {name, address, tax_id|null}
  line_items      [{description, category, quantity, unit_price, line_total}]
                  category is ^[A-Z]{2}-[A-Z]{4}-\\d{2}$
  subtotal, tax, discount, total   decimals with 2 places

Rules:
  line_total = quantity * unit_price, exactly.
  subtotal = sum of line_total. total = subtotal + tax - discount.
  Recompute totals from the line items; do not copy a printed total that
  disagrees with them.
  vendor_id, each line item's category, and payment_terms are NOT printed on
  the document. Supply your best value for each.
No extra fields."""


def system_prompt() -> str:
    """The serving/training system prompt.

    COMPACT_PROMPT=1 swaps the full JSON Schema (5.9k chars) for a terse field
    list (~1.1k). That matters for local training on an 8GB GPU: with the full
    schema in every example the sequences run past 3.7k tokens, and shrinking
    the prompt is what brings them inside a 2048 window.

    This is read by BOTH serve/extract and train/dataset, so training and
    serving can never silently disagree about the prompt -- if they did, the
    LoRA would be tuned for text the server never sends and would not transfer.
    """
    if settings.compact_prompt:
        return COMPACT_SYSTEM
    return SYSTEM.format(schema=json.dumps(INVOICE_JSON_SCHEMA, indent=2))


def extract_text(doc_text: str, model: str | None = None) -> tuple[str, int, str]:
    """Raw extraction call. Returns (raw_output, latency_ms, model_used)."""
    with tx() as conn:
        inc = incumbent(conn)
    used = model or serving_model(inc["adapter_ref"] if inc else None)
    raw, latency_ms = chat(used, system_prompt(), doc_text)
    return raw, latency_ms, used


def extract_and_record(doc_id: int, doc_text: str, model: str | None = None) -> Outcome:
    """Extract, validate, and append the result (plus any failure) to the DB."""
    raw, latency_ms, _ = extract_text(doc_text, model)
    outcome = validate(raw)

    with tx() as conn:
        inc = incumbent(conn)
        extraction_id = insert(
            conn,
            "extractions",
            doc_id=doc_id,
            model_version_id=inc["id"] if inc else None,
            raw_output=raw,
            parsed_json=json.dumps(outcome.parsed) if outcome.parsed else None,
            valid=int(outcome.valid),
            signature=outcome.signature,
            error_count=outcome.error_count,
            latency_ms=latency_ms,
        )
        if not outcome.valid:
            insert(
                conn,
                "failures",
                extraction_id=extraction_id,
                doc_id=doc_id,
                signature=outcome.signature,
                error_count=outcome.error_count,
                errors_json=json.dumps(outcome.errors, default=str),
            )
    return outcome
