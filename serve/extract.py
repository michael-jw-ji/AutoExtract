"""Extraction: document text in, validated JSON (or a logged failure) out."""

from __future__ import annotations

import json

from core.config import settings
from core.db import connect, incumbent, insert, tx
from serve.client import chat, serving_model
from verify.validate import Outcome, validate

def system_prompt() -> str:
    """The serving/training system prompt for the ACTIVE domain.

    COMPACT_PROMPT=1 swaps the full JSON Schema (5.9k chars) for a terse field
    list (~1.1k). That matters for local training on an 8GB GPU: with the full
    schema in every example the sequences run past 3.7k tokens, and shrinking
    the prompt is what brings them inside a 2048 window.

    This is read by BOTH serve/extract and train/dataset, so training and
    serving can never silently disagree about the prompt -- if they did, the
    LoRA would be tuned for text the server never sends and would not transfer.
    """
    from domains import get_domain
    return get_domain().system_prompt()


def extract_text(doc_text: str, model: str | None = None) -> tuple[str, int, str]:
    """Raw extraction call. Returns (raw_output, latency_ms, model_used).

    If ENRICH=1, the model's answer is passed through serve/enrich.py before
    being returned: formats normalised, arithmetic recomputed, and every rule
    field derived by lookup. The model is left responsible only for reading
    the page -- which is the only part that needs a model.
    """
    with tx() as conn:
        inc = incumbent(conn)
    used = model or serving_model(inc["adapter_ref"] if inc else None)
    # The largest gold answer is ~2.4k tokens pretty-printed (30-row filings),
    # and funds can file far longer. 8192 is a ceiling, not a cost, and the
    # longest prompt (~5.4k) plus 8192 still fits the 16K local context. A
    # truncated answer would score as a json_decode "model" failure.
    raw, latency_ms = chat(used, system_prompt(), doc_text, max_tokens=8192)

    if settings.enrich:
        raw = apply_enrichment(raw)
    return raw, latency_ms, used


def _domain():
    from domains import get_domain
    return get_domain()


def apply_enrichment(raw: str) -> str:
    """Run deterministic enrichment over a raw model response.

    Returns the raw text unchanged if it cannot be parsed -- enrichment has
    nothing to work with, and silently swallowing the failure would hide it
    from the validator.
    """
    from verify.validate import strip_fences

    try:
        payload = json.loads(strip_fences(raw))
    except json.JSONDecodeError:
        return raw
    if not isinstance(payload, dict):
        return raw
    enriched, _report = _domain().enrich(payload)
    return json.dumps(enriched, default=str)


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
