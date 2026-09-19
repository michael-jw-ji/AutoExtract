"""Private business rules -- the part the serving model cannot know.

This is the pivot that makes the whole project work. The base model handles
invoice arithmetic perfectly, so there was nothing to learn from. But no model
can guess that "Northwind Logistics Ltd" is VND-00713 in OUR system, or that
OUR policy puts tier-B vendors over 5000 on NET_45. That information lives
here, and nowhere in any pretraining corpus.

Three consequences, all deliberate:
  * serve/  does NOT see this file's contents -- the prompt carries the schema
    only, so the model fails on these fields ~100% of the time
  * verify/ DOES import it, so validation stays deterministic and LLM-free
  * repair/ puts it in the large model's context, which is what makes correct
    repairs possible and turns the LoRA into a genuine knowledge transfer

Fine-tuning is the right tool here by construction: the missing ingredient is
information, and training is how information gets injected.
"""

from __future__ import annotations

from decimal import Decimal

# --- Vendor registry: legal name -> internal id + commercial tier ---
VENDORS: dict[str, dict[str, str]] = {
    "Northwind Logistics Ltd":  {"id": "VND-00713", "tier": "B"},
    "Cascadia Print Works":     {"id": "VND-01184", "tier": "C"},
    "Meridian Tooling GmbH":    {"id": "VND-00429", "tier": "A"},
    "Lakeshore Freight Inc":    {"id": "VND-02061", "tier": "B"},
    "Atlas Components SA":      {"id": "VND-00877", "tier": "A"},
    "Solvent Chemical Co":      {"id": "VND-01530", "tier": "C"},
    "Granite Bay Packaging":    {"id": "VND-00992", "tier": "B"},
    "Ferrous Supply Partners":  {"id": "VND-01745", "tier": "A"},
    # --- expansion: a larger registry is a harder, more honest memorisation
    # task, and it includes deliberate near-duplicate names so the model
    # cannot succeed by matching on a prefix.
    "Northwind Marine Ltd":     {"id": "VND-00714", "tier": "C"},
    "Cascadia Print Supply":    {"id": "VND-01185", "tier": "B"},
    "Meridian Tooling UK Ltd":  {"id": "VND-00430", "tier": "B"},
    "Halden Precision Works":   {"id": "VND-02288", "tier": "A"},
    "Brightwater Solvents":     {"id": "VND-01902", "tier": "C"},
    "Orchard Lane Textiles":    {"id": "VND-00355", "tier": "B"},
    "Pike & Sons Hardware":     {"id": "VND-01067", "tier": "C"},
    "Vantage Rail Freight":     {"id": "VND-02510", "tier": "A"},
    "Sable Industrial Gases":   {"id": "VND-00688", "tier": "B"},
    "Kestrel Fasteners plc":    {"id": "VND-01443", "tier": "C"},
    "Dunmore Electrical Co":    {"id": "VND-00921", "tier": "A"},
    "Calder Valley Plastics":   {"id": "VND-02073", "tier": "B"},
}

# --- Line-item taxonomy: product -> internal category code ---
CATEGORIES: dict[str, str] = {
    "Hex bolt M8x40 (box of 100)":  "HW-FAST-02",
    "Anodised bracket, type C":     "HW-MOUN-07",
    "Industrial adhesive 5L":       "CH-ADHE-03",
    "Thermal receipt roll 80mm":    "PP-ROLL-01",
    "Label printer ribbon":         "PP-RIBN-04",
    "Cardboard mailer 300x200":     "PK-MAIL-06",
    "Pallet wrap 500mm":            "PK-WRAP-02",
    "Packing tape 48mm":            "PK-TAPE-05",
    "Courier service, zone 3":      "SV-COUR-08",
    "Warehouse handling fee":       "SV-HAND-01",
    "Forklift rental, daily":       "SV-RENT-09",
    "Safety gloves, size L":        "PE-GLOV-03",
    # --- expansion, including same-family products whose codes differ only in
    # the trailing number, so the taxonomy cannot be guessed from the prefix.
    "Hex bolt M10x60 (box of 50)":  "HW-FAST-05",
    "Carriage bolt M8x50":          "HW-FAST-11",
    "Galvanised bracket, type A":   "HW-MOUN-02",
    "Steel shelf bracket, heavy":   "HW-MOUN-14",
    "Epoxy resin 2L":               "CH-ADHE-07",
    "Solvent degreaser 20L":        "CH-SOLV-01",
    "Argon cylinder, 50L":          "CH-GASS-04",
    "Thermal roll 57mm":            "PP-ROLL-08",
    "Inkjet cartridge, black":      "PP-INKJ-02",
    "Bubble wrap 750mm":            "PK-WRAP-09",
    "Stretch film, machine grade":  "PK-WRAP-12",
    "Corrugated box 400x300x200":   "PK-BOXX-03",
    "Rail freight, zone 5":         "SV-RAIL-02",
    "Pallet storage, monthly":      "SV-STOR-06",
    "Crane hire, half day":         "SV-RENT-15",
    "Safety boots, size 10":        "PE-BOOT-07",
    "Hi-vis vest, XL":              "PE-VEST-01",
    "Ear defenders, class 4":       "PE-EARS-05",
}

# --- Payment policy: (tier, invoice total) -> terms ---
# Deterministic, checkable, and impossible to guess from the document alone.
POLICY_DESCRIPTION = """Payment terms are NOT printed on the invoice. They are
derived from the vendor's tier and the invoice total:
  tier A: total > 10000 -> NET_60, otherwise NET_30
  tier B: total >  5000 -> NET_45, otherwise NET_15
  tier C: always DUE_ON_RECEIPT"""


def vendor_id_for(name: str | None) -> str | None:
    if not name:
        return None
    entry = VENDORS.get(name.strip())
    return entry["id"] if entry else None


def tier_for(name: str | None) -> str | None:
    if not name:
        return None
    entry = VENDORS.get(name.strip())
    return entry["tier"] if entry else None


def category_for(description: str | None) -> str | None:
    if not description:
        return None
    return CATEGORIES.get(description.strip())


def terms_for(vendor_name: str | None, total: Decimal | float | str) -> str | None:
    tier = tier_for(vendor_name)
    if tier is None:
        return None
    try:
        amount = Decimal(str(total))
    except Exception:
        return None
    if tier == "A":
        return "NET_60" if amount > Decimal("10000") else "NET_30"
    if tier == "B":
        return "NET_45" if amount > Decimal("5000") else "NET_15"
    return "DUE_ON_RECEIPT"


def registry_prompt() -> str:
    """The reference block handed to the LARGE model during repair only."""
    vendors = "\n".join(
        f"  {name}  ->  {v['id']}  (tier {v['tier']})"
        for name, v in sorted(VENDORS.items())
    )
    cats = "\n".join(
        f"  {desc}  ->  {code}" for desc, code in sorted(CATEGORIES.items())
    )
    return (
        f"VENDOR REGISTRY (name -> internal id, tier):\n{vendors}\n\n"
        f"LINE-ITEM TAXONOMY (product -> category code):\n{cats}\n\n"
        f"PAYMENT POLICY:\n{POLICY_DESCRIPTION}"
    )
