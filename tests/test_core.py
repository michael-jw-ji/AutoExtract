"""Domain-agnostic checks: signatures, fence stripping, JSON failure handling."""

from __future__ import annotations

from verify.validate import signature_of, strip_fences, validate


def test_signature_is_order_independent():
    a = {"type": "missing", "loc": ("a",)}
    b = {"type": "enum", "loc": ("b",)}
    assert signature_of([a, b]) == signature_of([b, a])


def test_signature_normalises_list_indices():
    one = {"type": "missing", "loc": ("items", 3, "price")}
    two = {"type": "missing", "loc": ("items", 7, "price")}
    assert signature_of([one]) == signature_of([two]) == "missing:items.*.price"


def test_signature_collapses_prefix_duplicates():
    outer = {"type": "arith", "loc": ("items", 0)}
    inner = {"type": "arith", "loc": ("items", 0, "total")}
    assert signature_of([outer, inner]) == "arith:items.*"


def test_signature_keeps_distinct_types_at_the_same_location():
    a = {"type": "missing", "loc": ("x",)}
    b = {"type": "enum", "loc": ("x",)}
    assert signature_of([a, b]) == "enum:x|missing:x"


def test_strip_fences():
    assert strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_json_decode_failure_has_a_signature():
    out = validate("not json at all")
    assert not out.valid
    assert out.signature == "json_decode:"


def test_form4_daily_index_parsing():
    from scripts.fetch_form4 import parse_daily_index, pick_primary_xml

    text = (
        "4   ACME HOLDINGS, INC.   1234567   20261009   edgar/data/1234567/0001234567-26-000123.txt\n"
        "10-K   OTHER CORP   7654321   20261009   edgar/data/7654321/0007654321-26-000009.txt\n"
    )
    [f] = parse_daily_index(text, {"4"})
    assert (f.company, f.cik, f.accession) == (
        "ACME HOLDINGS, INC.", "1234567", "0001234567-26-000123")
    assert f.folder.endswith("/data/1234567/000123456726000123")

    index = {"directory": {"item": [{"name": "xslF345X05"}, {"name": "doc4.xml"},
                                    {"name": "0001234567-26-000123.txt"}]}}
    assert pick_primary_xml(index) == "doc4.xml"
    assert pick_primary_xml({"directory": {"item": []}}) is None
