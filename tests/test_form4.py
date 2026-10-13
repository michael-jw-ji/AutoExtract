"""Form 4 domain: rules, enrichment, scoring, and file handling.

Self-contained: inline fixtures only, so it runs without data/form4/.
"""

from __future__ import annotations

import copy
import json

import pytest

from domains import get_domain
from domains.form4_io import html_to_text, parse_xml
from verify.compare import counts, f1, field_hits, matches_gold
from verify.validate import validate

DOM = get_domain("form4")


def tx(**kw):
    row = {
        "security_title": "Common Stock", "is_derivative": False,
        "date": "2026-10-05", "code": "P", "shares": "100",
        "price_per_share": "10.50", "acquired_disposed": "A",
        "shares_owned_after": "1100", "direct_or_indirect": "D",
    }
    row.update(kw)
    return row


def filing(*rows):
    return {
        "issuer_cik": "0000075252", "issuer_name": "ACME INC",
        "issuer_ticker": "ACME", "period_of_report": "2026-10-05",
        "reporting_owners": [{
            "name": "Doe Jane", "is_director": True, "is_officer": False,
            "is_ten_percent_owner": False, "officer_title": None}],
        "transactions": list(rows),
    }


def gold(*rows):
    return DOM.enrich(filing(*rows))[0]


def run(payload: dict):
    return validate(json.dumps(payload))


# ------------------------------------------------------------------ validation

def test_a_clean_filing_validates():
    assert run(gold(tx())).valid


def test_a_filing_with_no_transactions_is_valid():
    assert run(gold()).valid


def test_unknown_transaction_code_fails():
    out = run(gold(tx(code="Q")))
    assert not out.valid
    assert "enum:transactions.*.code" in out.signature


def test_code_direction_rule_names_the_field():
    # P is always an acquisition.
    out = run(filing(tx(acquired_disposed="D")))
    assert not out.valid
    assert out.signature == "code_direction:transactions.*.acquired_disposed"


def test_m_direction_depends_on_the_table():
    assert run(gold(tx(code="M", is_derivative=True, acquired_disposed="D"))).valid
    assert run(gold(tx(code="M", is_derivative=False, acquired_disposed="A"))).valid
    out = run(filing(tx(code="M", is_derivative=True, acquired_disposed="A")))
    assert "code_direction" in out.signature


def test_owned_after_must_chain_for_same_day_direct_rows():
    a = tx(shares="100", shares_owned_after="1100")
    b = tx(shares="50", shares_owned_after="1150")
    assert run(gold(a, b)).valid
    out = run(filing(a, tx(shares="50", shares_owned_after="9999")))
    assert out.signature == "owned_after_mismatch:transactions.*.shares_owned_after"


def test_owned_after_chain_accepts_rows_listed_newest_first():
    # Real filing: the later transaction is listed first.
    later = tx(shares="170", shares_owned_after="191538")
    earlier = tx(shares="1", shares_owned_after="191368")
    assert run(gold(later, earlier)).valid


def test_owned_after_chain_still_catches_an_inconsistent_filing():
    # Real filing (a filer error): M +210 -> 37706, then F -83 -> 37710.
    m = tx(code="M", shares="210", shares_owned_after="37706")
    f = tx(code="F", acquired_disposed="D", shares="83", shares_owned_after="37710")
    out = run(filing(m, f))
    assert out.signature == "owned_after_mismatch:transactions.*.shares_owned_after"


def test_dates_with_a_timezone_suffix_are_normalised():
    fixed, _ = DOM.enrich({**filing(), "period_of_report": "2026-10-06-05:00"})
    assert fixed["period_of_report"] == "2026-10-06"


def test_owned_after_chain_ignores_different_days_and_indirect_holdings():
    a = tx(shares="100", shares_owned_after="1100")
    far = tx(date="2026-10-06", shares="50", shares_owned_after="5")
    indirect = tx(direct_or_indirect="I", shares="50", shares_owned_after="5")
    assert run(gold(a, far)).valid
    assert run(gold(a, indirect)).valid


@pytest.mark.parametrize("field,value", [("shares", "0"), ("price_per_share", "-1")])
def test_positive_shares_and_nonnegative_price(field, value):
    assert not run(filing(tx(**{field: value}))).valid


def test_price_may_be_null():
    assert run(gold(tx(price_per_share=None))).valid


def test_non_iso_date_fails():
    assert not run(filing(tx(date="10/05/2026"))).valid


# ------------------------------------------------------------------ enrichment

def test_enrich_normalises_formats():
    messy = filing(tx(date="10/05/2026", code="P - Purchase", shares="1,000",
                      price_per_share="$12.50", acquired_disposed="Acquired",
                      is_derivative="false", direct_or_indirect="Direct",
                      shares_owned_after="2,000"))
    messy["issuer_cik"] = "75252"
    messy["issuer_ticker"] = " acme "
    fixed, _ = DOM.enrich(messy)
    row = fixed["transactions"][0]
    assert fixed["issuer_cik"] == "0000075252" and fixed["issuer_ticker"] == "ACME"
    assert (row["date"], row["code"], row["shares"], row["price_per_share"]) == (
        "2026-10-05", "P", "1000", "12.50")
    assert (row["acquired_disposed"], row["direct_or_indirect"], row["is_derivative"]) == (
        "A", "D", False)
    assert run(fixed).valid


def test_enrich_derives_value_and_ignores_a_model_supplied_one():
    fixed, report = DOM.enrich(filing(tx(shares="100", price_per_share="10.505",
                                         value="1")))
    assert fixed["transactions"][0]["value"] == "1050.50"
    assert report["value"] == 1


def test_enrich_leaves_value_null_without_a_price():
    fixed, _ = DOM.enrich(filing(tx(price_per_share=None, value="5")))
    assert fixed["transactions"][0]["value"] is None


def test_enrich_derives_direction_from_the_code():
    fixed, report = DOM.enrich(filing(tx(code="S", acquired_disposed="A")))
    assert fixed["transactions"][0]["acquired_disposed"] == "D"
    assert report["direction"] == 1


def test_enrich_does_not_guess_ambiguous_direction():
    fixed, _ = DOM.enrich(filing(tx(code="J", acquired_disposed="A")))
    assert fixed["transactions"][0]["acquired_disposed"] == "A"


def test_mechanical_drops_hallucinated_keys():
    messy = filing(tx(footnote="x"))
    messy["confidence"] = 0.9
    fixed = DOM.mechanical(messy)
    assert "confidence" not in fixed and "footnote" not in fixed["transactions"][0]


def test_enrich_strips_footnote_markers_from_titles():
    fixed, _ = DOM.enrich(filing(tx(security_title="American depositary shares (1)(2)"),
                                 tx(security_title="Series (A) Preferred")))
    assert [r["security_title"] for r in fixed["transactions"]] == [
        "American depositary shares", "Series (A) Preferred"]


def test_enrich_strips_the_equity_swap_suffix():
    fixed, _ = DOM.enrich(filing(tx(code="S/K", acquired_disposed="D"), tx(code="J/K")))
    assert [r["code"] for r in fixed["transactions"]] == ["S", "J"]


def test_enrich_drops_rows_no_filing_can_contain():
    rows = [tx(), tx(security_title="Derivative Security", is_derivative=True),
            tx(shares="0"), tx(date=None, code=None), tx(date="", code="")]
    fixed, report = DOM.enrich(filing(*rows))
    assert fixed["transactions"] == [DOM.enrich(filing(tx()))[0]["transactions"][0]]
    assert report["dropped_rows"] == 4


def test_enrich_keeps_a_real_row_that_ends_at_zero_owned():
    fixed, _ = DOM.enrich(filing(tx(code="S", shares="100", shares_owned_after="0")))
    assert len(fixed["transactions"]) == 1


ONE_OWNER = """1. Name and Address of Reporting Person*
Doe Jane
(Last) | (First) | (Middle)
2. Issuer Name and Ticker or Trading Symbol ACME INC [ ACME ] | 5. Relationship
[ ] Director | [X] 10% Owner
[X] Officer (give title below) | [ ] Other (specify below)
Chief Executive Officer
"""

TWO_OWNERS = ONE_OWNER + """Table I
1. Name and Address of Reporting Person*
Doe Jane
Relationship of Reporting Person(s) to Issuer
[ ] Director | [X] 10% Owner
[X] Officer (give title below) | [ ] Other (specify below)
1. Name and Address of Reporting Person*
Doe Family Trust
Relationship of Reporting Person(s) to Issuer
[ ] Director | [X] 10% Owner
[ ] Officer (give title below) | [ ] Other (specify below)
Explanation of Responses:
"""


def test_owner_flags_come_from_the_page_checkboxes():
    fixed, report = DOM.enrich(filing(), ONE_OWNER)
    owner = fixed["reporting_owners"][0]
    assert (owner["is_director"], owner["is_officer"], owner["is_ten_percent_owner"]) == (
        False, True, True)
    assert report["owner_flags"] == 3


def test_multi_owner_pages_skip_the_header_copy_and_match_by_name():
    from domains.form4 import page_owner_flags
    assert [n for n, _ in page_owner_flags(TWO_OWNERS)] == ["Doe Jane", "Doe Family Trust"]
    doc = filing()
    trust = {**doc["reporting_owners"][0], "name": "DOE FAMILY TRUST", "is_officer": True}
    doc["reporting_owners"] = [trust, doc["reporting_owners"][0]]   # answer order differs
    fixed, _ = DOM.enrich(doc, TWO_OWNERS)
    flags = [(o["is_officer"], o["is_ten_percent_owner"]) for o in fixed["reporting_owners"]]
    assert flags == [(False, True), (True, True)]


def test_owner_flags_are_left_alone_when_owners_cannot_be_matched():
    doc = filing()
    doc["reporting_owners"][0]["name"] = "Someone Else"   # 1 unmatched owner, 2 on the page
    fixed, report = DOM.enrich(doc, TWO_OWNERS)
    assert report["owner_flags"] == 0
    assert fixed["reporting_owners"][0]["is_director"] is True


def test_see_remarks_titles_are_ambiguous_by_rule():
    from evalgate.scorer import skip_fields
    g = filing()
    assert skip_fields(None, g) == []
    g["reporting_owners"][0]["officer_title"] = "See remarks"
    assert skip_fields(None, g) == ["reporting_owners.*.officer_title"]
    assert skip_fields('["issuer_name", "reporting_owners.*.officer_title"]', g) == [
        "issuer_name", "reporting_owners.*.officer_title"]
    g["reporting_owners"][0]["officer_title"] = "CEO; see remarks for details"
    assert skip_fields([], g) == []


def test_enrich_survives_garbage():
    assert DOM.enrich("nope")[1] == {"skipped": "not an object"}
    assert DOM.mechanical([1]) == {}


# --------------------------------------------------------------------- scoring

def test_identical_filings_score_perfectly():
    g = gold(tx(), tx(shares="50", shares_owned_after="1150"))
    assert matches_gold(copy.deepcopy(g), g)


def test_numbers_compare_as_numbers():
    g = gold(tx(price_per_share="12.5"))
    p = gold(tx(price_per_share="$12.50"))
    assert matches_gold(p, g)


def test_whitespace_differences_are_not_errors():
    # Real gold: the XML double-spaces titles the page shows single-spaced.
    g = gold(tx(security_title="Class A  Common Stock"))
    p = gold(tx(security_title="Class A Common Stock"))
    assert matches_gold(p, g)


def test_a_wrong_number_is_wrong():
    g = gold(tx(price_per_share="12.50"))
    p = gold(tx(price_per_share="12.51"))
    assert not matches_gold(p, g)


def test_a_dropped_row_does_not_shift_the_rest_out_of_alignment():
    a = tx(date="2026-10-01", shares="10", shares_owned_after="110")
    b = tx(date="2026-10-02", shares="20", shares_owned_after="130")
    c = tx(date="2026-10-03", shares="30", shares_owned_after="160")
    g, p = gold(a, b, c), gold(b, c)
    hits = field_hits(p, g)
    assert hits["transactions.*.shares"] == (2, 3)


def test_per_field_accuracy_names_row_fields():
    g = gold(tx(price_per_share="10"))
    p = gold(tx(price_per_share="11"))
    hits = field_hits(p, g)
    assert hits["transactions.*.price_per_share"] == (0, 1)
    assert hits["transactions.*.shares"] == (1, 1)
    assert "transactions.*.value" not in hits  # derived, deliberately unscored


def test_partial_credit_is_not_all_or_nothing():
    g = gold(tx(), tx(shares="50", shares_owned_after="1150"))
    p = copy.deepcopy(g)
    p["transactions"][0]["code"] = "S"
    tp, n_pred, n_gold = counts(p, g)
    assert 0 < f1(tp, n_pred, n_gold) < 1


def test_ambiguous_fields_are_excluded_from_both_sides():
    from verify.compare import drop_fields
    g = gold(tx(price_per_share=None))
    p = gold(tx(price_per_share="17.25"))   # a defensible reading of a footnote
    assert not matches_gold(p, g)
    skip = ["transactions.*.price_per_share"]
    assert matches_gold(drop_fields(p, skip), drop_fields(g, skip))
    assert "price_per_share" in g["transactions"][0]   # originals untouched


def test_drop_fields_ignores_missing_paths():
    from verify.compare import drop_fields
    g = gold(tx())
    assert drop_fields(g, ["nope", "transactions.*.nope", "a.*.b"]) == g


def test_empty_prediction_scores_zero():
    assert f1(*counts({}, gold(tx()))) == 0.0


# ------------------------------------------------------------------ file handling

XML = b"""<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <periodOfReport>2026-10-05</periodOfReport>
  <issuer><issuerCik>0000075252</issuerCik><issuerName>ACME INC</issuerName>
    <issuerTradingSymbol>acme</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>true</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner><officerTitle>CEO</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable><nonDerivativeTransaction>
    <securityTitle><value>Common Stock</value></securityTitle>
    <transactionDate><value>2026-10-05</value></transactionDate>
    <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
    <transactionAmounts>
      <transactionShares><value>100</value></transactionShares>
      <transactionPricePerShare><value></value></transactionPricePerShare>
      <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
    </transactionAmounts>
    <postTransactionAmounts><sharesOwnedFollowingTransaction><value>1100</value>
    </sharesOwnedFollowingTransaction></postTransactionAmounts>
    <ownershipNature><directOrIndirectOwnership><value>D</value>
    </directOrIndirectOwnership></ownershipNature>
  </nonDerivativeTransaction></nonDerivativeTable>
</ownershipDocument>"""


def test_parse_xml_produces_valid_gold():
    raw = parse_xml(XML)
    assert raw["issuer_ticker"] == "ACME"
    assert raw["reporting_owners"][0]["is_officer"] is True
    assert raw["transactions"][0]["price_per_share"] is None  # blank, not ""
    assert run(DOM.enrich(raw)[0]).valid


def test_checkboxes_keep_their_owner():
    # Real layout: [box][label][box][label]; the X is in the box BEFORE its label.
    html = ("<table><tr><td></td><td>Director</td><td><span>X</span></td>"
            "<td>10% Owner</td></tr><tr><td>X</td><td>Officer (give title below)</td>"
            "<td></td><td>Other (specify below)</td></tr></table>")
    assert html_to_text(html) == (
        "[ ] Director | [X] 10% Owner\n"
        "[X] Officer (give title below) | [ ] Other (specify below)")


def test_table_ii_direction_survives_the_empty_column():
    # Real layout: 16 cells; the amount sits in the (A) cell (6) or the (D) cell (7).
    cells = ["Units(7)", "(8)", "10/01/2026 (1)", "", "J(1)", "", "", "100,000",
             "(8)", "(1)", "Class A Common Stock", "100,000", "(1)", "9,061,249",
             "I", "See Footnote"]
    row = "".join(f"<td>{c}</td>" for c in cells)
    html = f"<table><tr><td>Table II - Derivative Securities</td></tr><tr>{row}</tr></table>"
    assert "| J(1) | 100,000 (D) |" in html_to_text(html)


TABLES = ("<table><thead><tr><td>Table I - Non-Derivative</td></tr>"
          "<tr><td>Code</td><td>V</td></tr></thead><tbody>"
          "<tr><td>Common Stock(1)</td><td>10/05/2026</td><td>S(2)</td>"
          "<td>18.324(1)(3)</td><td>$81.86(2)</td></tr></tbody></table>"
          "<table><thead><tr><td>Table II - Derivative</td></tr></thead><tbody>"
          "<tr><td>Stock Option</td><td>(1)</td><td>Common Stock</td></tr></tbody></table>"
          "<p>Explanation of Responses:</p><table><tbody><tr><td>Owner row</td></tr>"
          "</tbody></table>")


def test_render_v1_is_unchanged_and_v2_labels_rows_and_splits_markers():
    assert html_to_text(TABLES, 1).splitlines()[2] == (
        "Common Stock(1) | 10/05/2026 | S(2) | 18.324(1)(3) | $81.86(2)")
    lines = html_to_text(TABLES, 2).splitlines()
    assert lines[1] == "Code | V"                       # header rows stay unlabelled
    assert lines[2] == ("[Table I] Common Stock (1) | 10/05/2026 | S (2) | "
                        "18.324 (1)(3) | $81.86 (2)")
    assert lines[4] == "[Table II] Stock Option | (1) | Common Stock"   # lone marker kept
    assert lines[-1] == "Owner row"                     # outside the tables: no label


def test_render_v3_labels_holdings_and_splits_markers_before_the_direction():
    t1 = "".join(f"<td>{c}</td>" for c in
                 ["Common Stock", "10/05/2026", "", "S", "", "100(1)", "D", "$5", "900", "D", ""])
    hold = "".join(f"<td>{c}</td>" for c in
                   ["Common Stock", "", "", "", "", "", "", "", "166", "I", "by Spouse"])
    t2 = "".join(f"<td>{c}</td>" for c in
                 ["Units", "(1)", "10/05/2026", "", "A", "", "1,002(1)(2)", "", "(1)", "(1)",
                  "Common Stock", "1,002", "$0", "5,276", "D", ""])
    html = (f"<table><thead><tr><td>Table I - x</td></tr></thead><tbody><tr>{t1}</tr>"
            f"<tr>{hold}</tr></tbody></table><table><thead><tr><td>Table II - x</td></tr>"
            f"</thead><tbody><tr>{t2}</tr></tbody></table>")
    lines = html_to_text(html, 3).splitlines()
    assert lines[1].startswith("[Table I transaction] Common Stock | 10/05/2026 | S | 100 (1) |")
    assert lines[2] == "[Table I holding, not a transaction] Common Stock | 166 | I | by Spouse"
    assert "| 1,002 (1)(2) (A) |" in lines[4] and lines[4].startswith("[Table II transaction]")


def test_model_input_renders_from_the_raw_html_and_never_falls_back(tmp_path, monkeypatch):
    from core.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    doc = {"ext_id": "0001-26-1", "text": "Issuer CIK: 0000000001\nold render"}
    with pytest.raises(FileNotFoundError):
        DOM.model_input(doc)
    (tmp_path / "form4" / "raw").mkdir(parents=True)
    (tmp_path / "form4" / "raw" / "0001-26-1.html").write_text(TABLES, encoding="utf-8")
    text = DOM.model_input(doc)
    assert text.startswith("Issuer CIK: 0000000001\n") and "[Table I transaction]" in text


def test_html_rows_stay_on_one_line():
    html = ("<html><head><style>.x{}</style></head><body><table><tr>"
            "<td><div>Common Stock</div></td><td>10/05/2026\n</td>"
            "<td><font>P</font></td></tr></table></body></html>")
    assert html_to_text(html) == "Common Stock | 10/05/2026 | P"


@pytest.mark.parametrize("pred,gold_name", [
    ("AMERISERV FINANCIAL INC", "AMERISERV FINANCIAL INC /PA/"),
    ("UNIVERSAL DISPLAY CORP", r"UNIVERSAL DISPLAY CORP \PA\ ".strip()),
    ("Amerant Bancorp Inc.", "AMERANT BANCORP INC"),
])
def test_issuer_names_forgive_form(pred, gold_name):
    g = gold(tx()); g["issuer_name"] = gold_name
    p = gold(tx()); p["issuer_name"] = pred
    assert matches_gold(p, g)


def test_owner_names_forgive_order_and_punctuation():
    g = gold(tx()); g["reporting_owners"][0]["name"] = "MFA Holding S.a.r.l."
    p = gold(tx()); p["reporting_owners"][0]["name"] = "MFA Holding S.a r.l."
    assert matches_gold(p, g)
    g["reporting_owners"][0]["name"] = "Reed Joshua"
    p["reporting_owners"][0]["name"] = "Joshua Reed"
    assert matches_gold(p, g)


def test_names_still_need_the_right_words():
    g = gold(tx()); g["issuer_name"] = "SailPoint, Inc."
    p = gold(tx()); p["issuer_name"] = "SailPoint Technologies Holdings"
    assert not matches_gold(p, g)


def test_spelled_out_corporate_suffixes_are_the_same_name():
    g = gold(tx()); g["issuer_name"] = "WORLD KINECT CORP"
    p = gold(tx()); p["issuer_name"] = "WORLD KINECT CORPORATION"
    assert matches_gold(p, g)
