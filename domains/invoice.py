"""Invoice domain — the original use case.

A thin adapter over the existing invoice modules, so the loop can address it
through the same interface as any other domain.
"""

from __future__ import annotations

from typing import Any

from core import registry
from domains import register
from serve.enrich import enrich as _enrich
from serve.extract import COMPACT_SYSTEM, SYSTEM
from verify.rules import check_rules as _check_rules
from verify.schema import INVOICE_JSON_SCHEMA, SCORED_FIELDS as _SCORED, Invoice


#: Verbatim the rules that used to live in repair/distill.py, moved here so
#: the repair model gets THIS domain's rules rather than invoice rules on
#: every domain.
REPAIR_RULES = """- Dates ISO-8601 (YYYY-MM-DD). currency in USD/EUR/GBP/CAD.
- payment_terms in NET_15/NET_30/NET_45/NET_60/DUE_ON_RECEIPT.
- invoice_number matches ^[A-Z]{2,4}-\\d{4,8}$.
- line_total = quantity * unit_price, exactly, for every item.
- subtotal = sum of line_total. total = subtotal + tax - discount.
- vendor_id, line-item category, and payment_terms are NOT printed on the \
document. Derive them from the INTERNAL REFERENCE DATA above.
- Read values from the SOURCE DOCUMENT. Do not invent numbers to satisfy the \
arithmetic -- if the printed total disagrees with the line items, trust the \
line items and recompute."""


class InvoiceDomain:
    name = "invoice"
    Schema = Invoice
    SCORED_FIELDS = _SCORED

    def system_prompt(self) -> str:
        from core.config import settings
        import json as _json
        if settings.compact_prompt:
            return COMPACT_SYSTEM
        return SYSTEM.format(schema=_json.dumps(INVOICE_JSON_SCHEMA, indent=2))

    def reference_prompt(self) -> str:
        return registry.registry_prompt()

    def repair_rules(self) -> str:
        return REPAIR_RULES

    def enrich(self, payload: dict) -> tuple[dict, dict[str, Any]]:
        return _enrich(payload)

    def mechanical(self, payload: dict) -> dict:
        from repair.mechanical import repair as _mechanical
        return _mechanical(payload)

    def check_rules(self, payload: dict) -> list[dict]:
        return _check_rules(payload)


DOMAIN = register(InvoiceDomain())
