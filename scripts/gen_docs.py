"""Synthetic invoice generator.

Gold is CONSTRUCTED, never extracted. We build a valid Invoice object in
Python (so the arithmetic is exact by definition), then ask the large model to
render it as a messy human document. That gives us ground truth for free on
every document, including live traffic -- which is what lets repair/run.py
verify repairs against gold instead of settling for "schema-valid".

    python scripts/gen_docs.py --live 120 --holdout 100
    python scripts/gen_docs.py --live 40 --offline      # no API calls
"""

from __future__ import annotations

import argparse
import json
import random
import string
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal

from core import registry
from core.config import settings
from core.db import init_db, insert, tx
from core.freeze import TEMPLATE_MARKER
from serve.client import chat
from verify.schema import CENT, Currency, Invoice, LineItem, Party, PaymentTerms

VENDOR_ADDRESSES = {
    "Northwind Logistics Ltd": "14 Harbour Road, Bristol BS1 5TY, UK",
    "Cascadia Print Works": "889 SE Belmont St, Portland, OR 97214, USA",
    "Meridian Tooling GmbH": "Industriestrasse 42, 80339 Munchen, Germany",
    "Lakeshore Freight Inc": "220 Lakeshore Blvd W, Toronto, ON M5V 1A1, Canada",
    "Atlas Components SA": "7 Rue du Commerce, 75015 Paris, France",
    "Solvent Chemical Co": "3 Dock Lane, Hull HU1 2AB, UK",
    "Granite Bay Packaging": "455 Industrial Way, Sacramento, CA 95814, USA",
    "Ferrous Supply Partners": "18 Foundry Road, Sheffield S3 8HT, UK",
    "Northwind Marine Ltd": "2 Quay Parade, Plymouth PL1 3TD, UK",
    "Cascadia Print Supply": "1140 NW Glisan St, Portland, OR 97209, USA",
    "Meridian Tooling UK Ltd": "6 Wharfside Way, Birmingham B16 8LR, UK",
    "Halden Precision Works": "31 Granby Street, Leicester LE1 6EQ, UK",
    "Brightwater Solvents": "44 Canal Road, Bradford BD1 4SU, UK",
    "Orchard Lane Textiles": "9 Orchard Lane, Manchester M4 2BS, UK",
    "Pike & Sons Hardware": "77 High Street, Norwich NR2 1JT, UK",
    "Vantage Rail Freight": "Unit 4, Rail Court, Crewe CW1 2LR, UK",
    "Sable Industrial Gases": "12 Avenue des Usines, 69007 Lyon, France",
    "Kestrel Fasteners plc": "58 Attercliffe Road, Sheffield S4 7WL, UK",
    "Dunmore Electrical Co": "200 Bay Street, Toronto, ON M5J 2J1, Canada",
    "Calder Valley Plastics": "3 Mill Lane, Halifax HX1 2AN, UK",
}
VENDORS = [(name, VENDOR_ADDRESSES[name]) for name in registry.VENDORS]
BUYERS = [
    ("Pinegrove Retail Co", "51 Market Street, Leeds LS1 6DT, UK"),
    ("Halcyon Studios LLC", "1200 Congress Ave, Austin, TX 78701, USA"),
    ("Bergmann Elektronik AG", "Hauptplatz 9, 10178 Berlin, Germany"),
    ("Riverbend Outfitters", "77 Queen St E, Ottawa, ON K1P 5C8, Canada"),
    ("Tessellate Design Ltd", "18 Hoxton Square, London N1 6NT, UK"),
    ("Aubergine Foods SARL", "22 Rue Saint-Denis, 75001 Paris, France"),
    ("Kestrel Analytics Inc", "500 Boylston St, Boston, MA 02116, USA"),
    ("Mistral Marine Supply", "Molo Audace 3, 34124 Trieste, Italy"),
]
PRODUCTS = list(registry.CATEGORIES)

# Fields that exist ONLY in our systems. They must never appear in the
# rendered document -- if the model can read them off the page, it is not
# learning our business rules, it is copying.
REDACTED = ("vendor_id", "payment_terms")

MESSINESS = [
    "clean", "ocr_noise", "reordered_fields", "currency_symbols",
    "handwritten_notes", "missing_printed_totals", "mixed_date_formats",
    "two_column_layout", "scanned_fax_artifacts",
    # harder styles, added to stretch the corpus beyond the easy middle
    "email_forward", "multipage_split", "bilingual_labels",
    "contradictory_total", "table_misalignment",
]

RENDER_SYSTEM = """You render structured invoice data as a realistic, MESSY \
document, the way it would look after being scanned, OCR'd, or copy-pasted \
out of a PDF.

Rules:
- Output ONLY the document text. No JSON, no commentary, no markdown fences.
- Every value from the input MUST appear somewhere in the document.
- Do NOT change any number, name, or date. Present them differently, do not \
alter them.
- Apply the requested messiness style aggressively but keep the document \
humanly readable."""

MESSINESS_HINTS = {
    "clean": "A tidy, well-formatted invoice.",
    "ocr_noise": "OCR artifacts: 0/O and 1/l/I confusions in LABELS ONLY (never "
                 "inside numeric values), stray characters, broken spacing.",
    "reordered_fields": "Unusual field order; totals near the top, dates at the "
                        "bottom, vendor and buyer blocks swapped.",
    "currency_symbols": "Use currency symbols and words rather than ISO codes "
                        "(e.g. '$', 'USD', 'dollars') inconsistently.",
    "handwritten_notes": "Include bracketed annotations like [handwritten: rush "
                         "order] and margin scribbles transcribed inline.",
    "missing_printed_totals": "Omit the printed subtotal/total lines entirely so "
                              "they must be derived from the line items.",
    "mixed_date_formats": "Write dates in inconsistent non-ISO formats "
                          "(15/03/2026, Mar 15 2026, 03.15.26).",
    "two_column_layout": "Flatten a two-column layout so fields interleave oddly.",
    "scanned_fax_artifacts": "Add header/footer noise, page numbers, a fax banner, "
                             "and repeated dashes.",
    "email_forward": "Present it as a forwarded email thread: headers, quoted "
                     ">> lines, a signature block, and the invoice pasted inline.",
    "multipage_split": "Split across 'Page 1 of 2' and 'Page 2 of 2' with a line "
                       "item continuing across the break and headers repeated.",
    "bilingual_labels": "Field labels in two languages (e.g. 'Rechnung / Invoice', "
                        "'Gesamtbetrag / Total'). VALUES stay exactly as given.",
    "contradictory_total": "Print a grand total that DISAGREES with the line items "
                           "by a small amount, as a transcription error would. The "
                           "line items remain authoritative and unchanged.",
    "table_misalignment": "Columns drift out of alignment so quantities and prices "
                          "nearly run together; wrap long descriptions onto a "
                          "second line.",
}


def _money(lo: float, hi: float) -> Decimal:
    return Decimal(str(round(random.uniform(lo, hi), 2))).quantize(CENT)


def make_invoice(rng: random.Random) -> Invoice:
    vendor_name, vendor_addr = rng.choice(VENDORS)
    buyer_name, buyer_addr = rng.choice(BUYERS)

    prefix = "".join(rng.choices(string.ascii_uppercase, k=rng.choice([2, 3])))
    number = f"{prefix}-{rng.randint(1000, 99999999):04d}"[: 4 + 9]

    issue = date(2026, 1, 1) + timedelta(days=rng.randint(0, 250))

    items = []
    for _ in range(rng.randint(1, 6)):
        product = rng.choice(PRODUCTS)
        qty = Decimal(str(rng.choice([1, 2, 3, 5, 10, 12, 24, 50, 100]))).quantize(CENT)
        price = _money(2.5, 480.0)
        items.append(
            LineItem(
                description=product,
                category=registry.CATEGORIES[product],
                quantity=qty,
                unit_price=price,
                line_total=(qty * price).quantize(CENT),
            )
        )

    subtotal = sum((i.line_total for i in items), Decimal("0")).quantize(CENT)
    tax = (subtotal * Decimal(str(rng.choice([0.0, 0.05, 0.13, 0.20])))).quantize(CENT)
    discount = (subtotal * Decimal("0.10")).quantize(CENT) if rng.random() < 0.25 \
        else Decimal("0.00")
    total = (subtotal + tax - discount).quantize(CENT)

    # Currency is picked BEFORE terms: policy v2 makes tier-C terms depend on
    # it, so the order matters.
    currency = rng.choice(list(Currency))

    # Terms follow from policy, not from the document.
    terms = PaymentTerms(registry.terms_for(vendor_name, total, currency.value))
    offset = {"NET_15": 15, "NET_30": 30, "NET_45": 45, "NET_60": 60,
              "DUE_ON_RECEIPT": 0}[terms.value]

    return Invoice(
        invoice_number=number,
        issue_date=issue,
        due_date=issue + timedelta(days=offset),
        currency=currency,
        payment_terms=terms,
        vendor_id=registry.VENDORS[vendor_name]["id"],
        vendor=Party(name=vendor_name, address=vendor_addr,
                     tax_id=f"TX{rng.randint(100000, 999999)}" if rng.random() < 0.6
                     else None),
        bill_to=Party(name=buyer_name, address=buyer_addr, tax_id=None),
        line_items=items,
        subtotal=subtotal,
        tax=tax,
        discount=discount,
        total=total,
    )


def redact(gold: dict) -> dict:
    """Strip the fields that exist only in our systems before rendering."""
    view = {k: v for k, v in gold.items() if k not in REDACTED}
    view["line_items"] = [
        {k: v for k, v in item.items() if k != "category"}
        for item in gold.get("line_items", [])
    ]
    return view


class RenderFailed(RuntimeError):
    """Rendering exhausted its retries. The caller must NOT silently downgrade."""


def render(invoice: Invoice, style: str, offline: bool, attempts: int = 5) -> str:
    """Render via the large model, retrying through rate limits.

    Falling back to the template on a transient 429 is the dangerous move:
    template documents are far easier than model-rendered ones (they scored a
    0% failure rate in the hour-0 probe), so a silent fallback quietly fills
    the corpus with unrepresentative easy examples and flatters every metric
    downstream. Back off and retry instead; if it still fails, say so loudly
    and tag the document so it can be excluded.
    """
    gold = json.loads(invoice.model_dump_json())
    if offline:
        return render_offline(gold, style)

    user = (
        f"MESSINESS STYLE: {style} -- {MESSINESS_HINTS[style]}\n\n"
        f"INVOICE DATA:\n{json.dumps(redact(gold), indent=2)}\n\nDocument:"
    )
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            text, _ = chat(settings.large_model, RENDER_SYSTEM, user,
                           temperature=0.9, max_tokens=1600)
            if text.strip():
                return text.strip()
            last = RuntimeError("empty render")
        except Exception as exc:  # rate limits, timeouts, transient 5xx
            last = exc
        # exponential backoff with jitter: 2s, 4s, 8s, 16s
        time.sleep(min(2 ** (attempt + 1), 20) * (0.7 + random.random() * 0.6))
    raise RenderFailed(f"{type(last).__name__}: {last}")


def render_offline(gold: dict, style: str) -> str:
    """Template fallback so the pipeline is exercisable with no API key."""
    lines = [
        f"{TEMPLATE_MARKER} {gold['invoice_number']} =====",
        f"From: {gold['vendor']['name']} / {gold['vendor']['address']}",
        f"To:   {gold['bill_to']['name']} / {gold['bill_to']['address']}",
        f"Issued {gold['issue_date']}   Due {gold['due_date']}",
        f"Currency: {gold['currency']}",
        "-" * 52,
    ]
    for item in gold["line_items"]:
        lines.append(
            f"{item['description']:<34} {item['quantity']:>8} x "
            f"{item['unit_price']:>10} = {item['line_total']:>10}"
        )
    lines.append("-" * 52)
    if style != "missing_printed_totals":
        lines += [
            f"Subtotal: {gold['subtotal']}",
            f"Tax:      {gold['tax']}",
            f"Discount: {gold['discount']}",
            f"TOTAL:    {gold['total']}",
        ]
    else:
        lines.append("(totals illegible -- derive from line items)")
    lines.append(f"[style: {style}]")
    return "\n".join(lines)


def generate(n: int, split: str, offline: bool, seed: int, workers: int = 8) -> int:
    """Render in parallel, insert sequentially.

    Rendering is the slow part and it is pure network I/O, so a few hundred
    documents go from ~an hour to a few minutes. Inserts stay on one thread --
    sqlite does not need the contention, and the DB is not the bottleneck.
    """
    rng = random.Random(seed)
    jobs = [(make_invoice(rng), rng.choice(MESSINESS)) for _ in range(n)]

    def render_one(job: tuple[Invoice, str]) -> tuple[Invoice, str, str] | None:
        invoice, style = job
        try:
            return invoice, style, render(invoice, style, offline)
        except RenderFailed as exc:
            # Drop it rather than substituting an easy template document.
            # A smaller honest corpus beats a larger contaminated one.
            print(f"  ! DROPPED after retries ({exc})", flush=True)
            return None

    written = 0
    dropped = 0
    with ThreadPoolExecutor(max_workers=1 if offline else workers) as pool:
        for result in pool.map(render_one, jobs):
            if result is None:
                dropped += 1
                continue
            invoice, style, text = result
            with tx() as conn:
                insert(
                    conn,
                    "documents",
                    ext_id=uuid.uuid4().hex[:12],
                    text=text,
                    gold_json=invoice.model_dump_json(),
                    split=split,
                    messiness=style,
                )
            written += 1
            if written % 20 == 0 or written + dropped == n:
                print(f"  [{written}/{n}] {split}"
                      f"{f' ({dropped} dropped)' if dropped else ''}", flush=True)

    if dropped:
        print(f"  WARNING: {dropped}/{n} dropped after retries. Lower --workers "
              f"(rate limit) and re-run to top up.", flush=True)
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic invoice documents")
    ap.add_argument("--live", type=int, default=0, help="documents for the live stream")
    ap.add_argument("--holdout", type=int, default=0, help="documents for the frozen eval set")
    ap.add_argument("--offline", action="store_true", help="template rendering, no API calls")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int, default=8, help="parallel render calls")
    args = ap.parse_args()

    init_db()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    if args.live:
        print(f"Generating {args.live} live documents...")
        generate(args.live, "live", args.offline, args.seed, args.workers)
    if args.holdout:
        print(f"Generating {args.holdout} holdout documents...")
        generate(args.holdout, "holdout", args.offline, args.seed + 999, args.workers)

    print("\nDone. Next: python scripts/freeze_eval.py")


if __name__ == "__main__":
    main()
