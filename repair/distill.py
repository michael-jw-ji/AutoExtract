"""Large-model repair, for failures the mechanical pass cannot fix.

This is distillation: the large model's corrected output becomes training data
for the small model. It is the expensive path, so it only runs on failures
that survive repair/mechanical.py.
"""

from __future__ import annotations

import json

from core.config import settings
from serve.client import chat
from verify.validate import describe, strip_fences

SYSTEM = """You are correcting a failed structured extraction.

You are given a source document, a previous JSON attempt, and the exact \
validation errors it produced. Return ONLY the corrected JSON object. No \
prose, no markdown fences.

Target JSON Schema:
{schema}

Rules:
{rules}"""

USER = """INTERNAL REFERENCE DATA (the serving model does not have this):
{registry}

SOURCE DOCUMENT:
{document}

PREVIOUS ATTEMPT:
{attempt}

VALIDATION ERRORS:
{errors}

Corrected JSON:"""


def _domain():
    """The active domain, lazily, with the invoice domain as the fallback.

    This module used to hardcode the invoice schema, the invoice vendor
    registry and invoice-only rules about payment_terms and line_total. On the
    support_email domain that meant the repair model was asked to turn a
    support email into an INVOICE, using the wrong reference data -- so no
    email repair could ever verify, and any rule added to the email domain's
    reference_prompt() was never read at all.
    """
    global _D
    if _D is None:
        from domains import get_domain
        _D = get_domain()
    return _D


_D = None


def repair(doc_text: str, attempt: str, errors: list[dict]) -> dict | None:
    """Ask the large model for a fix. Returns parsed JSON or None."""
    domain = _domain()
    system = SYSTEM.format(
        schema=json.dumps(domain.Schema.model_json_schema(), indent=2),
        rules=domain.repair_rules(),
    )
    user = USER.format(
        registry=domain.reference_prompt(),
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
