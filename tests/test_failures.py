"""The failure explainer names the failure modes seen in the baseline."""

from __future__ import annotations

import json

from evalgate.failures import analyze
from tests.test_form4 import gold, tx

PAGE = ("Common Stock | 10/05/2026 | P | 100 | A | $10.50 | 1,100 | D\n"
        "Common Stock | 40,513 | D\n")


def run(pred: dict, g: dict, ambiguous=None):
    return analyze(PAGE, g, json.dumps(pred), ambiguous)


def test_a_correct_answer_has_no_themes():
    g = gold(tx())
    r = run(g, g)
    assert r["valid"] and r["f1"] == 1.0 and r["themes"] == ["correct"]


def test_placeholder_row_for_an_empty_table_is_named():
    g = gold(tx())
    # Appended after enrich, which now drops such rows: the explainer still has
    # to name them in answers recorded before that (the baseline, prompt v2).
    p = gold(tx())
    p["transactions"].append(tx(security_title="Derivative Security", is_derivative=True,
                                shares="0", code="A"))
    r = run(p, g)
    assert "placeholder row for an empty table" in r["themes"]
    assert [row["kind"] for row in r["tables"]["transactions"]] == ["match", "extra"]


def test_holdings_row_is_named():
    g = gold(tx())
    p = gold(tx(), tx(date="2026-10-05", code="J", shares="40513", shares_owned_after="40513"))
    assert "holdings row extracted" in run(p, g)["themes"]


def test_wrong_field_and_missing_row_are_named():
    g = gold(tx(), tx(date="2026-10-06", shares="5", shares_owned_after="1105"))
    p = gold(tx(code="S", acquired_disposed="D"))
    themes = run(p, g)["themes"]
    assert "wrong code" in themes and "missing row" in themes


def test_ambiguous_fields_are_not_blamed():
    g = gold(tx(price_per_share=None))
    p = gold(tx(price_per_share="17.25"))
    assert run(p, g, ["transactions.*.price_per_share"])["themes"] == ["correct"]


def test_garbage_output_is_named():
    r = analyze(PAGE, gold(tx()), "not json", None)
    assert r["themes"] == ["unparseable output"] and r["f1"] == 0.0
