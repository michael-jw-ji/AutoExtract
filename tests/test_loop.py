"""Unit tests for the deterministic parts of the loop.

Everything here runs offline in milliseconds -- no API key, no GPU, no DB
beyond a temp file. These are the pieces that MUST be right, because a bug in
any of them silently corrupts training data or the gate.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest

from core import registry
from repair import mechanical
from verify.compare import counts, f1, matches_gold
from verify.schema import Invoice
from verify.validate import signature_of, strip_fences, validate


def good_invoice() -> dict:
    """A valid invoice built straight from the registry."""
    vendor = "Northwind Logistics Ltd"          # tier B
    product = "Hex bolt M8x40 (box of 100)"
    qty, price = Decimal("10.00"), Decimal("12.50")
    line_total = qty * price                     # 125.00
    subtotal = line_total
    tax = Decimal("25.00")
    total = subtotal + tax                       # 150.00 -> tier B, <=5000 -> NET_15
    return {
        "invoice_number": "NW-10024",
        "issue_date": "2026-03-01",
        "due_date": "2026-03-16",
        "currency": "GBP",
        "payment_terms": registry.terms_for(vendor, total),
        "vendor_id": registry.vendor_id_for(vendor),
        "vendor": {"name": vendor, "address": "14 Harbour Road, Bristol", "tax_id": None},
        "bill_to": {"name": "Pinegrove Retail Co", "address": "51 Market St, Leeds",
                    "tax_id": None},
        "line_items": [{
            "description": product,
            "category": registry.CATEGORIES[product],
            "quantity": str(qty), "unit_price": str(price),
            "line_total": str(line_total),
        }],
        "subtotal": str(subtotal), "tax": str(tax),
        "discount": "0.00", "total": str(total),
    }


# --- schema ---------------------------------------------------------------

def test_valid_invoice_passes():
    assert validate(json.dumps(good_invoice())).valid


def test_broken_line_arithmetic_fails():
    doc = good_invoice()
    doc["line_items"][0]["line_total"] = "999.00"
    out = validate(json.dumps(doc))
    assert not out.valid
    assert any("arithmetic" in e["type"] for e in out.errors)


def test_wrong_vendor_id_fails():
    doc = good_invoice()
    doc["vendor_id"] = "VND-99999"
    out = validate(json.dumps(doc))
    assert not out.valid
    assert any(e["type"] == "registry_vendor_id" for e in out.errors)


def test_wrong_category_fails():
    doc = good_invoice()
    doc["line_items"][0]["category"] = "XX-MISC-01"
    out = validate(json.dumps(doc))
    assert not out.valid
    assert any(e["type"] == "taxonomy_category" for e in out.errors)


def test_policy_violation_fails():
    doc = good_invoice()
    doc["payment_terms"] = "NET_60"     # tier B at 150.00 must be NET_15
    out = validate(json.dumps(doc))
    assert not out.valid
    assert any(e["type"] == "policy_payment_terms" for e in out.errors)


def test_unknown_vendor_fails():
    doc = good_invoice()
    doc["vendor"]["name"] = "Totally Fake Ltd"
    assert not validate(json.dumps(doc)).valid


# --- policy ---------------------------------------------------------------

@pytest.mark.parametrize(
    "vendor,total,expected",
    [
        ("Meridian Tooling GmbH", "20000.00", "NET_60"),   # tier A, over
        ("Meridian Tooling GmbH", "500.00", "NET_30"),     # tier A, under
        ("Northwind Logistics Ltd", "9000.00", "NET_45"),  # tier B, over
        ("Northwind Logistics Ltd", "10.00", "NET_15"),    # tier B, under
        ("Cascadia Print Works", "999999.00", "DUE_ON_RECEIPT"),  # tier C
    ],
)
def test_payment_policy(vendor, total, expected):
    assert registry.terms_for(vendor, total) == expected


def test_policy_boundary_is_strictly_greater():
    # Exactly 5000 is NOT "over 5000".
    assert registry.terms_for("Northwind Logistics Ltd", "5000.00") == "NET_15"
    assert registry.terms_for("Northwind Logistics Ltd", "5000.01") == "NET_45"


def test_policy_v2_is_harder_to_guess(monkeypatch):
    """v2 exists because v1 was mostly satisfiable by guessing NET_30.

    Its tier-B branch is deliberately INVERTED (bigger invoice, shorter terms)
    and tier C depends on currency -- neither follows from any prior.
    """
    monkeypatch.setenv("POLICY_VERSION", "2")
    assert registry.terms_for("Meridian Tooling GmbH", "8000.00") == "NET_60"   # A over
    assert registry.terms_for("Meridian Tooling GmbH", "100.00") == "NET_45"    # A under
    assert registry.terms_for("Northwind Logistics Ltd", "9000.00") == "NET_30"  # B over
    assert registry.terms_for("Northwind Logistics Ltd", "100.00") == "NET_60"   # B under
    assert registry.terms_for("Cascadia Print Works", "10.00", "USD") == "NET_15"
    assert registry.terms_for("Cascadia Print Works", "10.00", "EUR") == "DUE_ON_RECEIPT"


def test_policy_v1_is_the_default(monkeypatch):
    monkeypatch.delenv("POLICY_VERSION", raising=False)
    assert registry.policy_version() == 1
    # v1 ignores currency entirely
    assert registry.terms_for("Cascadia Print Works", "10.00", "USD") == "DUE_ON_RECEIPT"


# --- registry consistency -------------------------------------------------

def test_every_vendor_has_an_address():
    """Generation does VENDOR_ADDRESSES[name] for every registry vendor.

    Adding a vendor without an address is a KeyError that only surfaces
    mid-generation, after you have already paid for part of a corpus.
    """
    from scripts.gen_docs import VENDOR_ADDRESSES
    missing = set(registry.VENDORS) - set(VENDOR_ADDRESSES)
    assert not missing, f"vendors with no address: {sorted(missing)}"


def test_vendor_ids_are_unique_and_well_formed():
    ids = [v["id"] for v in registry.VENDORS.values()]
    assert len(ids) == len(set(ids)), "duplicate vendor id"
    assert all(re.fullmatch(r"VND-\d{5}", i) for i in ids)


def test_vendor_tiers_are_valid():
    assert {v["tier"] for v in registry.VENDORS.values()} <= {"A", "B", "C"}


def test_category_codes_are_unique_and_well_formed():
    codes = list(registry.CATEGORIES.values())
    assert len(codes) == len(set(codes)), "duplicate category code"
    assert all(re.fullmatch(r"[A-Z]{2}-[A-Z]{4}-\d{2}", c) for c in codes)


def test_every_messiness_style_has_a_hint():
    from scripts.gen_docs import MESSINESS, MESSINESS_HINTS
    missing = set(MESSINESS) - set(MESSINESS_HINTS)
    assert not missing, f"styles with no hint: {sorted(missing)}"


def test_redaction_strips_private_fields():
    """The document must never contain fields the model is meant to infer."""
    from scripts.gen_docs import redact
    gold = good_invoice()
    view = redact(gold)
    assert "vendor_id" not in view
    assert "payment_terms" not in view
    assert all("category" not in i for i in view["line_items"])
    # but the readable fields survive
    assert view["invoice_number"] == gold["invoice_number"]
    assert len(view["line_items"]) == len(gold["line_items"])


# --- signatures -----------------------------------------------------------

def test_signature_is_order_independent():
    a = [{"type": "missing", "loc": ("tax",)}, {"type": "enum", "loc": ("currency",)}]
    assert signature_of(a) == signature_of(list(reversed(a)))


def test_signature_normalises_list_indices():
    a = [{"type": "missing", "loc": ("line_items", 3, "unit_price")}]
    b = [{"type": "missing", "loc": ("line_items", 7, "unit_price")}]
    assert signature_of(a) == signature_of(b)
    assert "*" in signature_of(a)


def test_signature_collapses_prefix_duplicates():
    """pydantic and rules.py report the same violation at different depths."""
    errors = [
        {"type": "taxonomy_category", "loc": ("line_items", 0)},
        {"type": "taxonomy_category", "loc": ("line_items", 0, "category")},
    ]
    assert signature_of(errors) == "taxonomy_category:line_items.*"


def test_signature_keeps_distinct_types_at_the_same_location():
    errors = [
        {"type": "missing", "loc": ("line_items", 0, "unit_price")},
        {"type": "taxonomy_category", "loc": ("line_items", 0)},
    ]
    assert len(signature_of(errors).split("|")) == 2


def test_signature_keeps_sibling_fields_apart():
    errors = [
        {"type": "missing", "loc": ("vendor", "name")},
        {"type": "missing", "loc": ("vendor", "address")},
    ]
    assert len(signature_of(errors).split("|")) == 2


def test_json_decode_failure_has_a_signature():
    out = validate("not json at all")
    assert not out.valid
    assert out.signature and out.signature.startswith("json_decode")


def test_strip_fences():
    assert strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fences('Here you go: {"a": 1} hope that helps') == '{"a": 1}'


# --- mechanical repair ----------------------------------------------------

def test_mechanical_fixes_registry_fields():
    doc = good_invoice()
    doc["vendor_id"] = "VND-00000"
    doc["payment_terms"] = "NET_60"
    doc["line_items"][0]["category"] = "XX-MISC-01"
    fixed = mechanical.repair(doc)
    assert validate(json.dumps(fixed)).valid


def test_mechanical_recomputes_totals():
    doc = good_invoice()
    doc["subtotal"] = "1.00"
    doc["total"] = "2.00"
    fixed = mechanical.repair(doc)
    assert validate(json.dumps(fixed)).valid


def test_mechanical_normalises_formats():
    doc = good_invoice()
    doc["issue_date"] = "01/03/2026"
    doc["currency"] = "pounds"
    fixed = mechanical.repair(doc)
    assert fixed["issue_date"] == "2026-03-01"
    assert fixed["currency"] == "GBP"


def test_mechanical_drops_hallucinated_keys():
    doc = good_invoice()
    doc["purchase_order"] = "PO-1234"
    assert "purchase_order" not in mechanical.repair(doc)


def test_mechanical_is_pure():
    doc = good_invoice()
    before = json.dumps(doc, sort_keys=True)
    mechanical.repair(doc)
    assert json.dumps(doc, sort_keys=True) == before


def test_mechanical_cannot_rescue_a_misread_price():
    """The dangerous case: mechanical repair makes this VALID but WRONG.

    This is exactly why repair/run.py verifies against gold before allowing
    anything into a training set. If this test ever starts asserting that the
    repair matches gold, the safety property has been broken.
    """
    gold = good_invoice()
    misread = json.loads(json.dumps(gold))
    misread["line_items"][0]["unit_price"] = "1.25"      # model misread 12.50
    fixed = mechanical.repair(misread)

    assert validate(json.dumps(fixed)).valid             # schema-valid...
    assert not matches_gold(fixed, gold)                 # ...but not correct


# --- rule unmasking -------------------------------------------------------

def test_rules_are_reported_even_when_a_field_fails():
    """The masking fix: a bad line item must not hide vendor/policy errors.

    Without verify/rules.py pydantic aborts at the LineItem and reports only
    taxonomy_category, so the clusters -- and therefore the training data --
    skew toward whichever rule fails first.
    """
    doc = good_invoice()
    doc["line_items"][0]["category"] = "XX-MISC-01"   # breaks the item
    doc["vendor_id"] = "VND-99999"                    # would be masked
    doc["payment_terms"] = "NET_60"                   # would be masked

    types = {e["type"] for e in validate(json.dumps(doc)).errors}
    assert "registry_vendor_id" in types
    assert "policy_payment_terms" in types


def test_rules_survive_a_structurally_broken_payload():
    from verify.rules import check_rules
    junk = {"vendor": {"name": "Northwind Logistics Ltd"}, "line_items": "not a list"}
    types = {e["type"] for e in check_rules(junk)}
    assert "registry_vendor_id" in types


def test_rules_do_not_duplicate_pydantic_errors():
    doc = good_invoice()
    doc["vendor_id"] = "VND-99999"
    errors = validate(json.dumps(doc)).errors
    keys = [(e["type"], tuple(e["loc"])) for e in errors]
    assert len(keys) == len(set(keys))


def test_rules_silent_on_a_clean_invoice():
    from verify.rules import check_rules
    assert check_rules(good_invoice()) == []


# --- truncation guard -----------------------------------------------------

def _write_jsonl(tmp_path, system_chars: int):
    path = tmp_path / "ds.jsonl"
    payload = {"messages": [
        {"role": "system", "content": "s" * system_chars},
        {"role": "user", "content": "u" * 500},
        {"role": "assistant", "content": "a" * 500},
    ]}
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


def test_truncation_guard_fires(tmp_path):
    from train.backend import TruncationError, assert_fits
    ds = _write_jsonl(tmp_path, 20_000)          # ~7k tokens
    with pytest.raises(TruncationError):
        assert_fits(ds, max_length=2048)


def test_truncation_guard_passes_when_it_fits(tmp_path):
    from train.backend import assert_fits
    ds = _write_jsonl(tmp_path, 1_000)           # ~700 tokens
    info = assert_fits(ds, max_length=4096)
    assert info["examples"] == 1
    assert info["est_tokens"] < 4096


# --- scoring --------------------------------------------------------------

def test_identical_documents_score_perfectly():
    doc = good_invoice()
    tp, n_pred, n_gold = counts(doc, doc)
    assert f1(tp, n_pred, n_gold) == 1.0
    assert matches_gold(doc, doc)


def test_missing_field_lowers_f1():
    gold = good_invoice()
    pred = json.loads(json.dumps(gold))
    pred.pop("tax")
    assert f1(*counts(pred, gold)) < 1.0


def test_f1_is_zero_on_empty_prediction():
    assert f1(*counts({}, good_invoice())) == 0.0


def test_invalid_output_is_still_scorable():
    """An invoice that gets 15/16 fields right is not worth zero.

    If invalid documents score 0, field_f1 collapses into valid_rate and the
    promotion gate is back to deciding on 1%-granularity noise -- exactly the
    failure mode field_f1 exists to avoid.
    """
    gold = good_invoice()
    pred = json.loads(json.dumps(gold))
    pred["line_items"][0]["category"] = "XX-MISC-01"    # one wrong field

    out = validate(json.dumps(pred))
    assert not out.valid
    assert out.parsed is None
    assert out.scorable            # payload survives validation failure

    score = f1(*counts(out.scorable, gold))
    assert 0.5 < score < 1.0, f"expected a high partial score, got {score}"
