"""Deterministic validation and failure-signature extraction.

No LLM touches this file. A failure signature is derived purely from
pydantic's error list, which makes clusters reproducible and explainable:

    arithmetic_subtotal:|missing:line_items.*.unit_price

List indices are normalised to `*` so "item 3 is missing a price" and
"item 7 is missing a price" land in the same cluster.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from verify.rules import check_rules as _invoice_rules
from verify.schema import Invoice


def _active():
    """Resolve the active domain's schema + rule checker.

    Imported lazily and cached: domains/invoice.py imports serve.extract,
    which imports this module, so a top-level import would cycle. Falls back
    to the invoice domain if the domain package is unavailable for any reason,
    so validation never breaks because of plugin wiring.
    """
    global _ACTIVE
    if _ACTIVE is None:
        try:
            from domains import get_domain
            d = get_domain()
            _ACTIVE = (d.Schema, d.check_rules)
        except Exception:
            _ACTIVE = (Invoice, _invoice_rules)
    return _ACTIVE


_ACTIVE = None

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


@dataclass
class Outcome:
    valid: bool
    parsed: dict[str, Any] | None = None
    errors: list[dict] = field(default_factory=list)
    signature: str | None = None
    payload: dict[str, Any] | None = None
    """Best-effort decoded JSON, even when validation failed.

    Scoring MUST use this rather than `parsed`. An invoice that gets 14 of 16
    fields right but picks the wrong category code is not worth zero -- and if
    we score it as zero, field_f1 collapses into valid_rate and the promotion
    gate is back to deciding on 1%-granularity noise.
    """

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def scorable(self) -> dict[str, Any]:
        return self.parsed or self.payload or {}


def strip_fences(raw: str) -> str:
    """Models wrap JSON in markdown fences no matter how firmly you ask."""
    text = FENCE.sub("", raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


def normalise_loc(loc: tuple) -> str:
    return ".".join("*" if isinstance(p, int) else str(p) for p in loc)


def error_key(err: dict) -> str:
    return f"{err['type']}:{normalise_loc(err.get('loc', ()))}"


def signature_of(errors: list[dict]) -> str:
    """Stable, order-independent identity for a failure mode.

    pydantic reports a model-validator failure at the model's own location
    (`line_items.*`) while verify/rules.py reports the same violation at the
    offending field (`line_items.*.category`). Left alone, one logical failure
    becomes two cluster members, which splits the clusters AND distorts the
    per-signature cap in train/dataset.py.

    So when two errors share a type and one location strictly extends the
    other, keep only the shorter. Same type + prefix relationship is a narrow
    enough test that genuinely distinct errors are unaffected.
    """
    keys = {error_key(e) for e in errors}
    by_type: dict[str, list[str]] = {}
    for key in keys:
        kind, _, loc = key.partition(":")
        by_type.setdefault(kind, []).append(loc)

    kept = set()
    for kind, locs in by_type.items():
        for loc in locs:
            shorter = any(
                other != loc and (loc.startswith(other + ".") or other == "")
                for other in locs
            )
            if not shorter:
                kept.add(f"{kind}:{loc}")
    return "|".join(sorted(kept))


def validate(raw: str) -> Outcome:
    """Parse and validate a model's raw output. Never raises."""
    text = strip_fences(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        err = {"type": "json_decode", "loc": (), "msg": str(exc)}
        return Outcome(valid=False, errors=[err], signature=signature_of([err]))

    if not isinstance(payload, dict):
        err = {"type": "not_an_object", "loc": (), "msg": f"got {type(payload).__name__}"}
        return Outcome(valid=False, errors=[err], signature=signature_of([err]))

    schema, check_rules = _active()
    try:
        invoice = schema.model_validate(payload)
    except ValidationError as exc:
        errors = [
            {"type": e["type"], "loc": tuple(e["loc"]), "msg": e["msg"]}
            for e in exc.errors()
        ]
        # pydantic stops at the first failing layer, so business-rule
        # violations sitting behind a bad field never get reported. Merge in
        # whatever it missed, or the clusters skew toward whichever rule
        # happens to fail first -- and so does the training data.
        seen = {error_key(e) for e in errors}
        for extra in check_rules(payload):
            if error_key(extra) not in seen:
                errors.append(extra)
                seen.add(error_key(extra))
        return Outcome(valid=False, errors=errors, signature=signature_of(errors),
                       payload=payload)

    dumped = json.loads(invoice.model_dump_json())
    return Outcome(valid=True, parsed=dumped, payload=dumped)


def describe(errors: list[dict]) -> str:
    """Compact human-readable error list, fed to the repair model."""
    return "\n".join(
        f"- {normalise_loc(e.get('loc', ())) or '<root>'}: {e['type']} -- {e['msg']}"
        for e in errors
    )
