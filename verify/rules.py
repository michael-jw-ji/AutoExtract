"""Business-rule checks that run independently of pydantic's ordering.

pydantic validates fields before model validators, so a single bad line item
aborts the whole Invoice validator -- and the vendor_id and payment-policy
violations sitting right next to it are never reported. The violations are
real; they are just masked.

That skews the failure clusters toward whichever rule happens to fail first,
which in turn skews what the LoRA gets trained on. So these checks also run
directly against the raw payload, tolerating any amount of malformedness, and
validate.py merges whatever pydantic missed.

Still fully deterministic. No LLM touches this.
"""

from __future__ import annotations

from typing import Any

from core import registry


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def check_rules(payload: dict) -> list[dict]:
    """Every business-rule violation in a payload, however broken it is."""
    errors: list[dict] = []
    if not isinstance(payload, dict):
        return errors

    vendor = _as_dict(payload.get("vendor"))
    vendor_name = vendor.get("name")
    vendor_name = vendor_name.strip() if isinstance(vendor_name, str) else None

    # --- vendor registry ---
    if vendor_name:
        expected_id = registry.vendor_id_for(vendor_name)
        if expected_id is None:
            errors.append({
                "type": "registry_unknown_vendor",
                "loc": ("vendor", "name"),
                "msg": f"vendor {vendor_name!r} is not in the registry",
            })
        elif payload.get("vendor_id") != expected_id:
            errors.append({
                "type": "registry_vendor_id",
                "loc": ("vendor_id",),
                "msg": f"vendor_id {payload.get('vendor_id')!r} != "
                       f"registry id for {vendor_name!r} ({expected_id})",
            })

    # --- line-item taxonomy ---
    items = payload.get("line_items")
    if isinstance(items, list):
        for i, raw in enumerate(items):
            item = _as_dict(raw)
            expected_cat = registry.category_for(item.get("description"))
            if expected_cat is None:
                continue
            if item.get("category") != expected_cat:
                errors.append({
                    "type": "taxonomy_category",
                    "loc": ("line_items", i, "category"),
                    "msg": f"category {item.get('category')!r} != taxonomy code "
                           f"for this product ({expected_cat})",
                })

    # --- payment policy ---
    if vendor_name and payload.get("total") is not None:
        expected_terms = registry.terms_for(vendor_name, payload["total"])
        if expected_terms and payload.get("payment_terms") != expected_terms:
            errors.append({
                "type": "policy_payment_terms",
                "loc": ("payment_terms",),
                "msg": f"payment_terms {payload.get('payment_terms')!r} != policy "
                       f"for tier {registry.tier_for(vendor_name)} at total "
                       f"{payload['total']} ({expected_terms})",
            })

    return errors
