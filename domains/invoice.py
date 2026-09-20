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

    def enrich(self, payload: dict) -> tuple[dict, dict[str, Any]]:
        return _enrich(payload)

    def check_rules(self, payload: dict) -> list[dict]:
        return _check_rules(payload)


DOMAIN = register(InvoiceDomain())
