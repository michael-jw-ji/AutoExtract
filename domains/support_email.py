"""Support-email domain — a second use case, to prove the loop generalises.

Deliberately the same SHAPE as invoices, on completely different content:

  readable from the page   sender address, subject, dates, order references
  private to our systems   customer_id, account tier
  derived by policy        category, priority

That shape is the point. Any use case with "fields you can read" plus "fields
only your organisation knows" plugs into the same loop. The framework does not
change; only this file does.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from domains import register

# ---------------------------------------------------------------- registries

#: Email domain -> internal customer id and support tier. The private fact:
#: nothing in the email says "CUS-00412".
CUSTOMERS: dict[str, dict[str, str]] = {
    "bergmann-elektronik.de": {"id": "CUS-00412", "tier": "gold"},
    "pinegrove-retail.co.uk": {"id": "CUS-01180", "tier": "silver"},
    "halcyonstudios.com":     {"id": "CUS-00733", "tier": "gold"},
    "riverbend-outfitters.ca": {"id": "CUS-02051", "tier": "bronze"},
    "tessellate.design":      {"id": "CUS-00994", "tier": "silver"},
    "aubergine-foods.fr":     {"id": "CUS-01627", "tier": "bronze"},
    "kestrel-analytics.com":  {"id": "CUS-00308", "tier": "gold"},
    "mistral-marine.it":      {"id": "CUS-01845", "tier": "silver"},
}

#: Keyword -> category. Checked in order; first hit wins.
CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    (r"\b(refund|invoice|billing|charge|overcharg|payment|credit note)\b", "BILLING"),
    (r"\b(outage|down|error|crash|bug|broken|not working|500|timeout)\b", "TECHNICAL"),
    (r"\b(deliver|shipment|shipping|tracking|courier|dispatch|late)\b", "SHIPPING"),
    (r"\b(return|rma|damaged|faulty|wrong item|exchange)\b", "RETURNS"),
    (r"\b(password|login|account|access|seat|licen[cs]e|sso)\b", "ACCOUNT"),
]

POLICY_DESCRIPTION = """Priority is NOT stated in the email. It is derived from
the customer's tier and the ticket category:
  gold   + TECHNICAL or BILLING  -> P1, otherwise P2
  silver + TECHNICAL             -> P2, otherwise P3
  bronze                         -> P3, except RETURNS -> P4"""


def customer_for(sender_email: str | None) -> dict | None:
    if not sender_email or "@" not in str(sender_email):
        return None
    return CUSTOMERS.get(str(sender_email).rsplit("@", 1)[-1].strip().lower())


def category_for(text: str | None) -> str | None:
    if not text:
        return None
    low = str(text).lower()
    for pattern, cat in CATEGORY_KEYWORDS:
        if re.search(pattern, low):
            return cat
    return None


def priority_for(tier: str | None, category: str | None) -> str | None:
    if not tier or not category:
        return None
    if tier == "gold":
        return "P1" if category in {"TECHNICAL", "BILLING"} else "P2"
    if tier == "silver":
        return "P2" if category == "TECHNICAL" else "P3"
    return "P4" if category == "RETURNS" else "P3"


# -------------------------------------------------------------------- schema

class Category(str, Enum):
    BILLING = "BILLING"
    TECHNICAL = "TECHNICAL"
    SHIPPING = "SHIPPING"
    RETURNS = "RETURNS"
    ACCOUNT = "ACCOUNT"


class Priority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class SupportTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_ref: str = Field(pattern=r"^TKT-\d{6}$")
    sender_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
    received_at: date
    subject: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    order_refs: list[str] = Field(default_factory=list)

    # --- private / derived ---
    customer_id: str = Field(pattern=r"^CUS-\d{5}$")
    category: Category
    priority: Priority

    @model_validator(mode="after")
    def check_rules(self) -> "SupportTicket":
        for ref in self.order_refs:
            if not re.fullmatch(r"ORD-\d{7}", ref):
                raise PydanticCustomError(
                    "order_ref_format", "order ref {ref} is malformed", {"ref": ref}
                )

        cust = customer_for(self.sender_email)
        if cust is None:
            raise PydanticCustomError(
                "registry_unknown_customer",
                "sender domain for {email} is not a known customer",
                {"email": self.sender_email},
            )
        if self.customer_id != cust["id"]:
            raise PydanticCustomError(
                "registry_customer_id",
                "customer_id {got} != registry id ({want})",
                {"got": self.customer_id, "want": cust["id"]},
            )

        expected = priority_for(cust["tier"], self.category.value)
        if expected and self.priority.value != expected:
            raise PydanticCustomError(
                "policy_priority",
                "priority {got} != policy for tier {tier} + {cat} ({want})",
                {"got": self.priority.value, "want": expected,
                 "tier": cust["tier"], "cat": self.category.value},
            )
        return self


SCORED_FIELDS = [
    "ticket_ref", "sender_email", "received_at", "subject",
    "customer_id", "category", "priority", "order_refs",
]

#: Schema is extra='forbid', so mechanical repair drops everything else.
_ALLOWED = frozenset(SCORED_FIELDS) | {"summary"}

_DATE_FORMATS = [
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y",
    "%d %B %Y", "%B %d, %Y", "%b %d, %Y", "%d %b %Y", "%Y/%m/%d",
    "%d.%m.%Y", "%Y%m%d",
]


def _fix_date(value: str) -> str:
    text = value.strip()
    # Model output is frequently a full timestamp where a date is wanted.
    for candidate in (text, text.split("T")[0], text.split(" ")[0]):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue
    return text

# Fields the email never contains. The generator must strip these before
# rendering, or the model could simply copy them off the page.
REDACTED = ("customer_id", "category", "priority")

SYSTEM = """Extract a support ticket from the email below. Output ONLY JSON.

Fields:
  ticket_ref    string, ^TKT-\\d{6}$
  sender_email  the sender's address
  received_at   ISO date YYYY-MM-DD
  subject       the email subject line
  summary       one sentence describing the request
  order_refs    list of strings matching ^ORD-\\d{7}$ (may be empty)
  customer_id   string, ^CUS-\\d{5}$
  category      BILLING | TECHNICAL | SHIPPING | RETURNS | ACCOUNT
  priority      P1 | P2 | P3 | P4

customer_id, category and priority are NOT stated in the email. Supply your
best value for each. No extra fields."""


class SupportEmailDomain:
    name = "support_email"
    Schema = SupportTicket
    SCORED_FIELDS = SCORED_FIELDS

    def system_prompt(self) -> str:
        return SYSTEM

    def reference_prompt(self) -> str:
        custs = "\n".join(
            f"  @{d}  ->  {v['id']}  (tier {v['tier']})"
            for d, v in sorted(CUSTOMERS.items())
        )
        kws = "\n".join(f"  {pat}  ->  {cat}" for pat, cat in CATEGORY_KEYWORDS)
        return (
            f"CUSTOMER REGISTRY (email domain -> id, tier):\n{custs}\n\n"
            f"CATEGORY KEYWORDS:\n{kws}\n\n"
            f"PRIORITY POLICY:\n{POLICY_DESCRIPTION}"
        )

    def enrich(self, payload: dict) -> tuple[dict, dict[str, Any]]:
        """Derive customer_id, category and priority. Same principle as
        invoices: the model reads the email, code decides everything that
        follows from it."""
        if not isinstance(payload, dict):
            return payload, {"skipped": "not an object"}
        doc = dict(payload)
        report: dict[str, Any] = {}

        refs = doc.get("order_refs")
        if isinstance(refs, list):
            doc["order_refs"] = [
                r.strip().upper().replace(" ", "-")
                for r in refs if isinstance(r, str)
            ]

        cust = customer_for(doc.get("sender_email"))
        report["customer_match"] = "exact" if cust else "unmatched"
        if cust:
            doc["customer_id"] = cust["id"]

        # Category from the text the model DID read -- never its own guess.
        text = f"{doc.get('subject','')} {doc.get('summary','')}"
        cat = category_for(text)
        if cat:
            doc["category"] = cat
            report["category"] = "derived"
        if cust and doc.get("category"):
            pri = priority_for(cust["tier"], str(doc["category"]))
            if pri:
                doc["priority"] = pri
                report["priority"] = "derived"
        return doc, report

    def normalize_field(self, path: str, value: Any) -> Any:
        """Strip email-thread noise from the subject before comparison.

        The generator renders realistic threads, so a subject appears on the
        page as 'Re: Fwd: Overcharged on last invoice [TKT-482911]' while gold
        holds the canonical 'Overcharged on last invoice'. A model that copies
        the line verbatim is RIGHT; only the comparison was wrong. This strips
        reply/forward prefixes and a trailing bracketed ticket ref, and
        nothing else -- it does not make a wrong subject match a right one.
        """
        if path != "subject" or not isinstance(value, str):
            return value
        text = value.strip()
        text = re.sub(r"^(?:\s*(?:re|fwd|fw)\s*:\s*)+", "", text, flags=re.I)
        text = re.sub(r"\s*[\[(]\s*TKT-\d{6}\s*[\])]\s*$", "", text, flags=re.I)
        return text.strip()

    def mechanical(self, payload: dict) -> dict:
        """Format normalisation, then the same derivation enrich() does.

        Only formats are guessed at here. `customer_id`, `category` and
        `priority` are never invented -- enrich() derives them from the
        registry or leaves them alone, so a repair that cannot resolve the
        sender stays wrong and fails verification, which is the intent.
        """
        if not isinstance(payload, dict):
            return {}
        doc = {k: v for k, v in payload.items() if k in _ALLOWED}

        ref = doc.get("ticket_ref")
        if isinstance(ref, str):
            m = re.search(r"(\d{6})", ref)
            if m:
                doc["ticket_ref"] = f"TKT-{m.group(1)}"

        got = doc.get("received_at")
        if isinstance(got, str):
            doc["received_at"] = _fix_date(got)

        refs = doc.get("order_refs")
        if isinstance(refs, list):
            out = []
            for r in refs:
                m = re.search(r"(\d{7})", str(r))
                if m:
                    out.append(f"ORD-{m.group(1)}")
            doc["order_refs"] = out
        elif "order_refs" in doc:
            doc["order_refs"] = []

        for key in ("subject", "summary"):
            if isinstance(doc.get(key), str):
                doc[key] = doc[key].strip()

        doc, _ = self.enrich(doc)
        return doc

    def check_rules(self, payload: dict) -> list[dict]:
        errors: list[dict] = []
        if not isinstance(payload, dict):
            return errors
        cust = customer_for(payload.get("sender_email"))
        if payload.get("sender_email") and cust is None:
            errors.append({"type": "registry_unknown_customer",
                           "loc": ("sender_email",),
                           "msg": "sender domain is not a known customer"})
        elif cust and payload.get("customer_id") != cust["id"]:
            errors.append({"type": "registry_customer_id", "loc": ("customer_id",),
                           "msg": f"expected {cust['id']}"})
        if cust and payload.get("category"):
            want = priority_for(cust["tier"], str(payload["category"]))
            if want and payload.get("priority") != want:
                errors.append({"type": "policy_priority", "loc": ("priority",),
                               "msg": f"expected {want}"})
        return errors


DOMAIN = register(SupportEmailDomain())
