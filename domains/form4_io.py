"""Form 4 file handling: official XML -> gold dict, SEC HTML view -> model text.

Kept apart from domains/form4.py because it is only needed to BUILD the corpus,
never to serve. Not listed in domains/__init__.py::_MODULES (registers nothing).

parse_xml() returns a RAW dict (strings, None for absent). It does not validate:
the loader runs it through the Form4 schema, so a filing whose own XML breaks
our rules is reported and dropped rather than quietly "fixed".
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser


def _value(node: ET.Element | None, path: str) -> str | None:
    """Text of `<path><value>..</value></path>`, or of `<path>..</path>`."""
    if node is None:
        return None
    el = node.find(path)
    if el is None:
        return None
    inner = el.find("value")
    text = inner.text if inner is not None else el.text
    text = (text or "").strip()
    return text or None


def _flag(text: str | None) -> bool:
    return (text or "").strip().lower() in {"1", "true"}


def _transaction(t: ET.Element, derivative: bool) -> dict:
    return {
        "security_title": _value(t, "securityTitle"),
        "is_derivative": derivative,
        "date": _value(t, "transactionDate"),
        "code": _value(t, "transactionCoding/transactionCode"),
        "shares": _value(t, "transactionAmounts/transactionShares"),
        "price_per_share": _value(t, "transactionAmounts/transactionPricePerShare"),
        "acquired_disposed": _value(
            t, "transactionAmounts/transactionAcquiredDisposedCode"),
        "shares_owned_after": _value(
            t, "postTransactionAmounts/sharesOwnedFollowingTransaction"),
        "direct_or_indirect": _value(
            t, "ownershipNature/directOrIndirectOwnership"),
    }


def parse_xml(data: bytes | str) -> dict:
    root = ET.fromstring(data)
    owners = []
    for o in root.findall("reportingOwner"):
        rel = o.find("reportingOwnerRelationship")
        owners.append({
            "name": _value(o, "reportingOwnerId/rptOwnerName"),
            "is_director": _flag(_value(rel, "isDirector")),
            "is_officer": _flag(_value(rel, "isOfficer")),
            "is_ten_percent_owner": _flag(_value(rel, "isTenPercentOwner")),
            "officer_title": _value(rel, "officerTitle"),
        })

    rows = [_transaction(t, False)
            for t in root.findall("nonDerivativeTable/nonDerivativeTransaction")]
    rows += [_transaction(t, True)
             for t in root.findall("derivativeTable/derivativeTransaction")]

    ticker = _value(root, "issuer/issuerTradingSymbol")
    return {
        "issuer_cik": _value(root, "issuer/issuerCik"),
        "issuer_name": _value(root, "issuer/issuerName"),
        "issuer_ticker": ticker.upper() if ticker else None,
        "period_of_report": _value(root, "periodOfReport"),
        "reporting_owners": owners,
        "transactions": rows,
    }


# ------------------------------------------------------------------ html -> text

_BLOCK = {"p", "div", "br", "tr", "table", "h1", "h2", "h3", "h4", "li", "hr"}
_CELL = {"td", "th"}
_SKIP = {"style", "script", "head", "title"}
_BREAK = "\x00"  # a real line break, distinct from source whitespace


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._cell = 0

    def _break(self, tag: str) -> None:
        # Inside a table cell, div/p/br are only spacing: a real line break
        # there would split one row across lines and lose the structure.
        self.parts.append(" " if self._cell and tag != "tr" else _BREAK)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP:
            self._skip += 1
        elif tag in _CELL:
            self._cell += 1
        elif tag in _BLOCK:
            self._break(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in _CELL:
            self._cell = max(0, self._cell - 1)
            self.parts.append(" | ")
        elif tag in _BLOCK:
            self._break(tag)

    def handle_data(self, data: str) -> None:
        # Source line breaks inside a text node are formatting, not structure.
        if not self._skip:
            self.parts.append(re.sub(r"\s+", " ", data))


def html_to_text(html: bytes | str) -> str:
    """The SEC-rendered view as plain text: one line per table row, cells
    separated by ' | '. Layout noise is dropped; every printed value stays."""
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    parser = _Text()
    parser.feed(html)
    lines = []
    table2 = False
    for raw in "".join(parser.parts).split(_BREAK):
        cells = [re.sub(r"\s+", " ", c).strip() for c in raw.split("|")]
        if any(c.startswith("Table II") for c in cells):
            table2 = True
        elif any(c.startswith("Explanation of Responses") for c in cells):
            table2 = False
        if table2:
            cells = _derivative_direction(cells)
        line = " | ".join(_checkboxes(cells))
        if line:
            lines.append(line)
    return "\n".join(lines)


_DATE = re.compile(r"\d{2}/\d{2}/\d{4}")


def _derivative_direction(cells: list[str]) -> list[str]:
    """Table II says acquired vs disposed by COLUMN, not by letter: the amount
    sits in either the (A) or the (D) cell. Dropping the empty one loses the
    direction entirely, so write it next to the amount: '100,000 (D)'.

    Every Table II transaction row in the corpus has the same 16 cells
    (17 pieces after the trailing separator), with (A) and (D) at 6 and 7.
    """
    if len(cells) != 17 or not _DATE.match(cells[2]):
        return cells
    acquired, disposed = cells[6], cells[7]
    if bool(acquired) == bool(disposed):
        return cells
    out = list(cells)
    out[6] = f"{acquired} (A)" if acquired else f"{disposed} (D)"
    out[7] = ""
    return out


#: Form labels that each follow their own checkbox cell (box 5 and box 6).
_CHECKBOX_LABELS = frozenset({
    "Director", "10% Owner", "Officer (give title below)", "Other (specify below)",
    "Form filed by One Reporting Person",
    "Form filed by More than One Reporting Person",
})


def _checkboxes(cells: list[str]) -> list[str]:
    """Drop empty cells, but render checkboxes as '[X] label' / '[ ] label'.

    On the form each checkbox is the cell BEFORE its label. Dropping the empty
    (unchecked) cells turned `[ ] Director [X] 10% Owner` into
    `Director | X | 10% Owner`, which reads as if the X belongs to Director --
    misleading to a human labeller and to the model alike.
    """
    out: list[str] = []
    for j, cell in enumerate(cells):
        if cell in _CHECKBOX_LABELS:
            checked = j > 0 and cells[j - 1].upper() == "X"
            if checked and out and out[-1].upper() == "X":
                out.pop()
            out.append(f"[{'X' if checked else ' '}] {cell}")
        elif cell:
            out.append(cell)
    return out


# ------------------------------------------------------------------ difficulty

def features(data: bytes | str) -> list[str]:
    """Why a filing is worth a human look. Read straight from the XML, so these
    are properties of the filing, not of any model's behaviour."""
    root = ET.fromstring(data)
    tags: list[str] = []
    if (root.findtext("documentType") or "").strip() != "4":
        tags.append("amendment")
    if len(root.findall("reportingOwner")) > 1:
        tags.append("multi_owner")

    rows = root.findall("nonDerivativeTable/nonDerivativeTransaction")
    drows = root.findall("derivativeTable/derivativeTransaction")
    if drows:
        tags.append("derivative")
    if len(rows) + len(drows) >= 3:
        tags.append("many_rows")
    if root.findall("nonDerivativeTable/nonDerivativeHolding"):
        tags.append("holdings_row")  # a table row that is NOT a transaction
    if len({_value(t, "securityTitle") for t in rows}) > 1:
        tags.append("multi_class")
    if any(_value(t, "ownershipNature/directOrIndirectOwnership") == "I" for t in rows + drows):
        tags.append("indirect")

    priced = [t.find("transactionAmounts/transactionPricePerShare") for t in rows + drows]
    if any(p is not None and p.find("footnoteId") is not None for p in priced):
        tags.append("footnoted_price")
    if any(p is None or _value(p, "value") is None for p in priced):
        tags.append("blank_price")
    if any(_value(t, "transactionCoding/transactionCode") not in {"P", "S", "A", "M", "F", "D"}
           for t in rows + drows):
        tags.append("unusual_code")
    if root.find("remarks") is not None:
        tags.append("remarks")
    if not rows and not drows:
        tags.append("no_transactions")
    return tags
