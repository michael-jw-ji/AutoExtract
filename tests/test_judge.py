"""The judge sees the right person's part of the page; the report's maths."""

from __future__ import annotations

import sys
from pathlib import Path

from evalgate.judge import owner_context

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from trace_report import pct  # noqa: E402

PAGE = """1. Name and Address of Reporting Person*
Doe Jane
[ ] Director | [X] 10% Owner
[X] Officer (give title below) | [ ] Other (specify below)
See Remarks
3. Date of Earliest Transaction (Month/Day/Year) 10/07/2026
Table I
1. Name and Address of Reporting Person*
Doe Jane
Relationship of Reporting Person(s) to Issuer
[X] Officer (give title below)
1. Name and Address of Reporting Person*
Roe Ann
Relationship of Reporting Person(s) to Issuer
[X] Director | [ ] 10% Owner
Remarks:
Jane Doe is the Chief Financial Officer.
/s/ Jane Doe
** Signature of Reporting Person | Date
"""


def test_context_is_the_persons_own_block_plus_remarks():
    ctx = owner_context(PAGE, "Roe Ann")
    assert ctx.startswith("Name and Address of Reporting Person*\nRoe Ann")
    assert "Doe Jane" not in ctx.split("Remarks:")[0]
    assert "Jane Doe is the Chief Financial Officer." in ctx
    assert "Signature" not in ctx


def test_a_repeated_owner_gets_their_last_block_and_names_match_in_any_order():
    ctx = owner_context(PAGE, "Jane Doe")
    assert "Relationship of Reporting Person(s) to Issuer\n[X] Officer" in ctx


def test_nearest_rank_percentiles():
    xs = list(range(1, 101))
    assert (pct(xs, 50), pct(xs, 99), pct(xs, 100)) == (50, 99, 100)
    assert pct([7.0], 99) == 7.0 and pct([], 50) == 0.0
