"""The strict Invoice schema.

Strictness is a design parameter, not an accident. The whole project needs the
base model to fail 25-45% of the time: too easy and there is no training
signal, too hard and no LoRA can close the gap. The knobs, in order of yield:

  1. arithmetic consistency  -- small models are bad at this, most reliable
  2. enums                   -- currency, payment terms
  3. formats                 -- ISO dates, invoice number regex, 2dp money
  4. structure               -- required nesting, extra='forbid'

If scripts/probe.py reports a rate outside the band, tune HERE first.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from core import registry

CENT = Decimal("0.01")
TOLERANCE = Decimal("0.01")


class Currency(str, Enum):
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    CAD = "CAD"


class PaymentTerms(str, Enum):
    NET_15 = "NET_15"
    NET_30 = "NET_30"
    NET_45 = "NET_45"
    NET_60 = "NET_60"
    DUE_ON_RECEIPT = "DUE_ON_RECEIPT"


class Party(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    address: str = Field(min_length=1)
    tax_id: str | None = None


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    category: str = Field(pattern=r"^[A-Z]{2}-[A-Z]{4}-\d{2}$")
    quantity: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    unit_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    line_total: Decimal = Field(ge=0, max_digits=12, decimal_places=2)

    @model_validator(mode="after")
    def check_line_total(self) -> "LineItem":
        expected = (self.quantity * self.unit_price).quantize(CENT)
        if abs(self.line_total - expected) > TOLERANCE:
            raise PydanticCustomError(
                "arithmetic_line_total",
                "line_total {got} != quantity * unit_price ({want})",
                {"got": str(self.line_total), "want": str(expected)},
            )

        # The category code is not printed anywhere on the document. It has to
        # come from our taxonomy, which the serving model has never seen.
        expected_cat = registry.category_for(self.description)
        if expected_cat is not None and self.category != expected_cat:
            raise PydanticCustomError(
                "taxonomy_category",
                "category {got} != taxonomy code for this product ({want})",
                {"got": self.category, "want": expected_cat},
            )
        return self


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_number: str = Field(pattern=r"^[A-Z]{2,4}-\d{4,8}$")
    issue_date: date
    due_date: date
    currency: Currency
    payment_terms: PaymentTerms
    vendor_id: str = Field(pattern=r"^VND-\d{5}$")
    vendor: Party
    bill_to: Party
    line_items: list[LineItem] = Field(min_length=1)
    subtotal: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    tax: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    discount: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=12, decimal_places=2)
    total: Decimal = Field(ge=0, max_digits=12, decimal_places=2)

    @model_validator(mode="after")
    def check_totals(self) -> "Invoice":
        expected_subtotal = sum(
            (li.line_total for li in self.line_items), Decimal("0.00")
        ).quantize(CENT)
        if abs(self.subtotal - expected_subtotal) > TOLERANCE:
            raise PydanticCustomError(
                "arithmetic_subtotal",
                "subtotal {got} != sum(line_total) ({want})",
                {"got": str(self.subtotal), "want": str(expected_subtotal)},
            )

        expected_total = (self.subtotal + self.tax - self.discount).quantize(CENT)
        if abs(self.total - expected_total) > TOLERANCE:
            raise PydanticCustomError(
                "arithmetic_total",
                "total {got} != subtotal + tax - discount ({want})",
                {"got": str(self.total), "want": str(expected_total)},
            )

        if self.due_date < self.issue_date:
            raise PydanticCustomError(
                "date_order", "due_date precedes issue_date", {}
            )

        # --- Business rules the document does not contain ---
        expected_id = registry.vendor_id_for(self.vendor.name)
        if expected_id is None:
            raise PydanticCustomError(
                "registry_unknown_vendor",
                "vendor {name} is not in the registry",
                {"name": self.vendor.name},
            )
        if self.vendor_id != expected_id:
            raise PydanticCustomError(
                "registry_vendor_id",
                "vendor_id {got} != registry id for {name} ({want})",
                {"got": self.vendor_id, "name": self.vendor.name, "want": expected_id},
            )

        expected_terms = registry.terms_for(
            self.vendor.name, self.total, self.currency.value
        )
        if expected_terms is not None and self.payment_terms.value != expected_terms:
            raise PydanticCustomError(
                "policy_payment_terms",
                "payment_terms {got} != policy for tier {tier} at total {total} ({want})",
                {"got": self.payment_terms.value, "want": expected_terms,
                 "tier": registry.tier_for(self.vendor.name) or "?",
                 "total": str(self.total)},
            )
        return self


# Cached so we do not rebuild it on every prompt construction.
INVOICE_JSON_SCHEMA = Invoice.model_json_schema()

# Flat list of scorable leaf fields, used by the eval scorer for field-level F1.
SCORED_FIELDS = [
    "invoice_number",
    "issue_date",
    "due_date",
    "currency",
    "payment_terms",
    "vendor_id",
    "vendor.name",
    "vendor.address",
    "vendor.tax_id",
    "bill_to.name",
    "bill_to.address",
    "bill_to.tax_id",
    "subtotal",
    "tax",
    "discount",
    "total",
    "line_items",
]
