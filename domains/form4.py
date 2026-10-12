"""SEC Form 4 domain -- insider transactions from EDGAR filings.

  readable from the page   issuer, owners, per-row date/code/shares/price/...
  derived by code          value (shares x price), and the A/D direction for
                           codes that only ever go one way
  gold                     the filing's own XML, parsed (domains/form4_xml.py)

There is no synthetic generator: documents are real filings fetched by
scripts/fetch_form4.py.

Two deliberate departures from a flat "owner + transactions" shape, both from
reading real filings:
  * owners are a LIST with boolean role flags. 9% of filings have several
    reporting owners, and people are routinely director AND officer AND 10%
    holder, so a single `owner_relationship` enum cannot represent them.
  * `transactions` holds rows from BOTH tables, marked `is_derivative`. Mixing
    the two tables up is a failure we want to be able to measure.
"""

from __future__ import annotations

import re
import datetime as dt
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import InitErrorDetails, PydanticCustomError

from domains import register

# ---------------------------------------------------------------- reference data

#: SEC Form 4 transaction codes (General Instruction 8 / column 3).
CODE_MEANINGS: dict[str, str] = {
    "P": "open market or private purchase",
    "S": "open market or private sale",
    "V": "transaction voluntarily reported earlier than required",
    "A": "grant, award or other acquisition from the issuer",
    "D": "disposition to the issuer",
    "F": "payment of exercise price or tax liability by delivering/withholding securities",
    "I": "discretionary transaction",
    "M": "exercise or conversion of a derivative security (Rule 16b-3 exempt)",
    "C": "conversion of a derivative security",
    "E": "expiration of a short derivative position",
    "H": "expiration or cancellation of a long derivative position, value received",
    "O": "exercise of an out-of-the-money derivative",
    "X": "exercise of an in-the-money or at-the-money derivative",
    "G": "bona fide gift",
    "L": "small acquisition (Rule 16a-6)",
    "W": "acquisition or disposition by will or the laws of descent",
    "Z": "deposit into or withdrawal from a voting trust",
    "J": "other acquisition or disposition (see footnote)",
    "K": "equity swap or similar hedging transaction",
    "U": "disposition pursuant to a tender offer",
}

TransactionCode = Enum("TransactionCode", {c: c for c in CODE_MEANINGS}, type=str)

#: Codes whose direction is fixed. M depends on the table: the derivative row of
#: an exercise is a disposal of the option, the non-derivative row an acquisition
#: of shares. Verified against 454 real transactions with zero exceptions.
_DIRECTION = {"P": "A", "S": "D", "A": "A", "D": "D", "F": "D"}


def expected_direction(code: str | None, is_derivative: bool | None) -> str | None:
    if code in _DIRECTION:
        return _DIRECTION[code]
    if code == "M" and is_derivative is not None:
        return "D" if is_derivative else "A"
    return None


# -------------------------------------------------------------------- schema

class Owner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    officer_title: str | None = None


class Transaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    security_title: str = Field(min_length=1)
    is_derivative: bool
    date: dt.date
    code: TransactionCode
    shares: Decimal = Field(gt=0)
    price_per_share: Decimal | None = Field(default=None, ge=0)
    acquired_disposed: Literal["A", "D"]
    shares_owned_after: Decimal = Field(ge=0)
    direct_or_indirect: Literal["D", "I"]
    #: Derived by code (enrich); the model is told not to produce it.
    value: Decimal | None = None


class Form4(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issuer_cik: str = Field(pattern=r"^\d{10}$")
    issuer_name: str = Field(min_length=1)
    issuer_ticker: str = Field(min_length=1)
    period_of_report: dt.date
    reporting_owners: list[Owner] = Field(min_length=1)
    #: May be empty: some filings only report holdings.
    transactions: list[Transaction]

    @model_validator(mode="after")
    def check_rules(self) -> "Form4":
        errors = rule_errors(self.model_dump(mode="python"))
        if errors:
            # Raise with each rule's real location, so failure signatures name
            # the field (`...:transactions.*.shares_owned_after`) rather than
            # collapsing every rule failure onto the model root.
            raise ValidationError.from_exception_data(
                "Form4",
                [
                    InitErrorDetails(
                        type=PydanticCustomError(e["type"], e["msg"]),
                        loc=tuple(e["loc"]),
                        input=None,
                    )
                    for e in errors
                ],
            )
        return self


# --------------------------------------------------------------------- rules

def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value).strip().replace(",", "").lstrip("$")
        d = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _day(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, dt.date) else (
        str(value) if value is not None else None)


def rule_errors(payload: dict) -> list[dict]:
    """Business-rule violations on a raw or validated payload. Tolerant of
    anything: a malformed field is pydantic's job to report, not ours."""
    errors: list[dict] = []
    rows = payload.get("transactions") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return errors

    prev_after: dict[tuple, tuple[Decimal, Decimal]] = {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        code = row.get("code")
        code = getattr(code, "value", code)
        code = code.strip().upper() if isinstance(code, str) else None
        deriv = row.get("is_derivative")
        deriv = deriv if isinstance(deriv, bool) else None

        want = expected_direction(code, deriv)
        got = row.get("acquired_disposed")
        if want and isinstance(got, str) and got.strip().upper() != want:
            errors.append({
                "type": "code_direction",
                "loc": ("transactions", i, "acquired_disposed"),
                "msg": f"code {code} is always '{want}', got '{got}'",
            })

        shares, price, value = (_dec(row.get(k)) for k in
                                ("shares", "price_per_share", "value"))
        if shares is not None and price is not None and value is not None:
            if value != (shares * price).quantize(Decimal("0.01")):
                errors.append({
                    "type": "value_mismatch",
                    "loc": ("transactions", i, "value"),
                    "msg": "value != shares x price",
                })

        # Adjacent rows of one direct holding on the same day must chain:
        # owned_after = the other row's owned_after +/- shares. Restricted to
        # direct holdings on one date because that is where it is unambiguous
        # -- the unrestricted form is violated by real gold (interleaved
        # indirect holdings under one label, and rows months apart with
        # unreported changes between them).
        #
        # Either direction is accepted: some filers list rows newest-first
        # (seen in real gold), and that is a presentation choice, not an
        # error. A genuinely inconsistent filing fails both directions.
        after = _dec(row.get("shares_owned_after"))
        if deriv is False and row.get("direct_or_indirect") == "D" \
                and shares is not None and after is not None and isinstance(got, str):
            key = (row.get("security_title"), _day(row.get("date")))
            step = shares if got.strip().upper() == "A" else -shares
            if key in prev_after:
                prev, prev_step = prev_after[key]
                forward = prev + step == after         # this row came after
                backward = after + prev_step == prev   # rows listed newest-first
                if not (forward or backward):
                    errors.append({
                        "type": "owned_after_mismatch",
                        "loc": ("transactions", i, "shares_owned_after"),
                        "msg": "does not follow from the adjacent row's "
                               "shares_owned_after and shares",
                    })
            prev_after[key] = (after, step)
    return errors


# ------------------------------------------------------------------ enrichment

SCORED_FIELDS = [
    "issuer_cik", "issuer_name", "issuer_ticker", "period_of_report",
    "reporting_owners", "transactions",
]

#: Leaf names compared as numbers (12.50 == "12.5" == "$12.50").
NUMERIC_FIELDS = frozenset({"shares", "price_per_share", "shares_owned_after", "value"})

#: Derived by code, so scoring it would count one upstream error twice.
UNSCORED_FIELDS = frozenset({"transactions.*.value"})

#: Names compared by content, not form (see Form4Domain.normalize_field).
_NAME_FIELDS = frozenset({"issuer_name", "reporting_owners.*.name"})
#: Spelled-out vs abbreviated corporate suffixes are the same name (form, not
#: content): "UNITED THERAPEUTICS Corporation" vs "... Corp".
_SUFFIX = {"corporation": "corp", "incorporated": "inc", "company": "co",
           "limited": "ltd"}

_TOP = frozenset(SCORED_FIELDS)
_OWNER = frozenset(Owner.model_fields)
_TX = frozenset(Transaction.model_fields)

_DATE_FORMATS = [
    "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y", "%B %d, %Y",
    "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%Y/%m/%d", "%Y%m%d", "%m/%d/%y",
]


def _fix_date(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    for candidate in (text, text.split("T")[0], text.split(" ")[0]):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue
    # xs:date allows a timezone suffix ("2026-10-06-05:00"), and real filings
    # use it. The date part is what the form means.
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:Z|[+-]\d{2}:\d{2})$", text)
    if m:
        return m.group(1)
    return text


def _fix_bool(value: Any) -> Any:
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"1", "true", "yes", "y", "x"}:
            return True
        if low in {"0", "false", "no", "n", ""}:
            return False
    if value in (0, 1) and not isinstance(value, bool):
        return bool(value)
    return value


def _fix_number(value: Any) -> Any:
    if isinstance(value, str):
        d = _dec(value)
        if d is not None:
            return format(d, "f")
    return value


def _fix_code(value: Any) -> Any:
    if isinstance(value, str):
        m = re.match(r"^\s*([A-Za-z])\s*(?:$|[-:(\s])", value)
        if m:
            return m.group(1).upper()
    return value


def _fix_letter(value: Any, words: dict[str, str]) -> Any:
    if isinstance(value, str):
        low = value.strip().lower()
        for prefix, letter in words.items():
            if low == prefix.lower() or low.startswith(prefix.lower()):
                return letter
    return value


def _clean_row(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k in _TX}
    if "date" in out:
        out["date"] = _fix_date(out["date"])
    if "is_derivative" in out:
        out["is_derivative"] = _fix_bool(out["is_derivative"])
    for key in ("shares", "price_per_share", "shares_owned_after"):
        if key in out:
            out[key] = _fix_number(out[key])
    if "code" in out:
        out["code"] = _fix_code(out["code"])
    if "acquired_disposed" in out:
        out["acquired_disposed"] = _fix_letter(
            out["acquired_disposed"], {"acquired": "A", "a": "A", "disposed": "D", "d": "D"})
    if "direct_or_indirect" in out:
        out["direct_or_indirect"] = _fix_letter(
            out["direct_or_indirect"], {"direct": "D", "d": "D", "indirect": "I", "i": "I"})
    return out


def _clean(payload: dict) -> dict:
    """Format normalisation only. Never invents a value."""
    doc = {k: v for k, v in payload.items() if k in _TOP}
    if isinstance(doc.get("issuer_cik"), (str, int)):
        digits = re.sub(r"\D", "", str(doc["issuer_cik"]))
        if digits:
            doc["issuer_cik"] = digits.zfill(10)
    if isinstance(doc.get("issuer_ticker"), str):
        doc["issuer_ticker"] = doc["issuer_ticker"].strip().upper()
    if "period_of_report" in doc:
        doc["period_of_report"] = _fix_date(doc["period_of_report"])

    owners = doc.get("reporting_owners")
    if isinstance(owners, list):
        cleaned = []
        for o in owners:
            if isinstance(o, dict):
                o = {k: v for k, v in o.items() if k in _OWNER}
                for flag in ("is_director", "is_officer", "is_ten_percent_owner"):
                    if flag in o:
                        o[flag] = _fix_bool(o[flag])
            cleaned.append(o)
        doc["reporting_owners"] = cleaned

    rows = doc.get("transactions")
    if isinstance(rows, list):
        doc["transactions"] = [_clean_row(r) if isinstance(r, dict) else r for r in rows]
    return doc


class Form4Domain:
    name = "form4"
    Schema = Form4
    SCORED_FIELDS = SCORED_FIELDS
    NUMERIC_FIELDS = NUMERIC_FIELDS
    UNSCORED_FIELDS = UNSCORED_FIELDS

    def system_prompt(self) -> str:
        codes = ", ".join(sorted(CODE_MEANINGS))
        return f"""Extract the SEC Form 4 filing below as JSON. Output ONLY the JSON object.

Fields:
  issuer_cik        10 digits, keep leading zeros
  issuer_name       string
  issuer_ticker     trading symbol
  period_of_report  ISO date YYYY-MM-DD
  reporting_owners  list, one per reporting person:
                    {{name, is_director, is_officer, is_ten_percent_owner, officer_title|null}}
                    the three is_* values are true/false
  transactions      list, one per row of Table I (is_derivative false) AND
                    Table II (is_derivative true), in the order shown:
                    {{security_title, is_derivative, date, code, shares,
                     price_per_share|null, acquired_disposed, shares_owned_after,
                     direct_or_indirect}}

Rules:
  date             ISO YYYY-MM-DD (the transaction date, column 2)
  code             one letter from: {codes}
  shares           the amount in column 4 as a plain number, no commas
  price_per_share  plain number, no $; null when the price column is blank.
                   Take it from the Price column, not from a footnote.
  acquired_disposed  A or D, from column 4
  shares_owned_after  amount owned after the transaction (column 5)
  direct_or_indirect  D or I (column 6)
Table II (derivative securities) rows are transactions too: set is_derivative
to true. If the filing lists no transactions, use an empty list.
Do NOT output a `value` field. No extra fields."""

    def reference_prompt(self) -> str:
        codes = "\n".join(f"  {c}  {m}" for c, m in CODE_MEANINGS.items())
        return (
            f"TRANSACTION CODES:\n{codes}\n\n"
            "DIRECTION: P, A, and M-in-Table-I are always 'A' (acquired); S, D, F, "
            "and M-in-Table-II are always 'D' (disposed).\n\n"
            "CONVENTIONS:\n"
            "  - Table I = non-derivative (common stock...). Table II = derivative "
            "(options, RSUs, warrants...). An option exercise appears as an M row "
            "in BOTH tables.\n"
            "  - A price shown as a footnote reference (e.g. '(1)') is NOT the price; "
            "the price is the number in the column, or blank (null) if none is shown. "
            "A weighted-average footnote explains the price, it does not replace it.\n"
            "  - A filing with several reporting owners lists every one of them.\n"
            "  - shares_owned_after for consecutive same-day direct rows of one "
            "security chains: previous owned_after +/- this row's shares."
        )

    def repair_rules(self) -> str:
        return (
            "- Dates ISO-8601 (YYYY-MM-DD). issuer_cik is 10 digits with leading zeros.\n"
            "- code is one letter from the transaction-code list above.\n"
            "- shares > 0, price_per_share >= 0 or null when the price column is blank.\n"
            "- acquired_disposed must agree with the code (see DIRECTION above).\n"
            "- Rows from Table II have is_derivative true.\n"
            "- Do not output `value`; it is computed.\n"
            "- Read every value from the SOURCE DOCUMENT. Do not invent numbers to "
            "satisfy a consistency rule."
        )

    def enrich(self, payload: dict) -> tuple[dict, dict[str, Any]]:
        """Normalise formats, then derive every field that has a right answer."""
        if not isinstance(payload, dict):
            return payload, {"skipped": "not an object"}
        doc = _clean(payload)
        report: dict[str, Any] = {"value": 0, "direction": 0}
        rows = doc.get("transactions")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                shares, price = _dec(row.get("shares")), _dec(row.get("price_per_share"))
                if shares is not None and price is not None:
                    row["value"] = format((shares * price).quantize(Decimal("0.01")), "f")
                    report["value"] += 1
                else:
                    row["value"] = None

                code = row.get("code")
                deriv = row.get("is_derivative")
                want = expected_direction(
                    code if isinstance(code, str) else None,
                    deriv if isinstance(deriv, bool) else None)
                if want and row.get("acquired_disposed") != want:
                    row["acquired_disposed"] = want
                    report["direction"] += 1
        return doc, report

    def normalize_field(self, path: str, value: Any) -> Any:
        """Scoring policy for NAMES (decided 2026-10-11): forgive form, not content.

        `AMERISERV FINANCIAL INC` vs `... INC /PA/`, `Amerant Bancorp Inc.` vs
        `AMERANT BANCORP INC`, `Joshua Reed` vs `Reed Joshua` name the same
        party; scoring them wrong measures formatting, not reading. So case,
        punctuation, a trailing EDGAR state suffix (/PA/, \\PA\\), spelled-out
        corporate suffixes (Corporation = Corp) and word order are ignored. Spelling and any different word still count:
        `SailPoint Technologies Holdings` vs `SailPoint, Inc.` stays wrong.
        """
        if path not in _NAME_FIELDS or not isinstance(value, str):
            return value
        text = re.sub(r"\s*[/\\][A-Za-z]{2}[/\\]\s*$", "", value.strip())
        words = re.sub(r"[^\w\s]", " ", text.lower()).split()
        return " ".join(sorted(_SUFFIX.get(w, w) for w in words))

    def mechanical(self, payload: dict) -> dict:
        """Deterministic repair: formats, then everything enrich() derives."""
        if not isinstance(payload, dict):
            return {}
        doc, _ = self.enrich(payload)
        return doc

    def check_rules(self, payload: dict) -> list[dict]:
        return rule_errors(payload)


DOMAIN = register(Form4Domain())
