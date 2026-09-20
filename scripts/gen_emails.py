"""Synthetic support-email generator for the support_email domain.

Same principle as scripts/gen_docs.py: gold is CONSTRUCTED first, then the
large model renders it as a realistic messy email. The three private fields
(customer_id, category, priority) are REDACTED before rendering, so the model
cannot read them off the page.

One extra constraint over invoices: `category` is derived from keywords in the
subject and summary, so the generator must produce text whose keywords
actually resolve to the intended category. Every ticket is verified against
domains.support_email.category_for() before it is kept -- otherwise the gold
would not validate against its own schema.

    python scripts/gen_emails.py --live 700 --holdout 200 --workers 5
"""

from __future__ import annotations

import argparse
import json
import random
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from core.config import settings
from core.db import init_db, insert, tx
from domains.support_email import (
    CUSTOMERS, SupportTicket, category_for, customer_for, priority_for,
)
from serve.client import chat

SENDERS = ["klaus", "marie", "j.okafor", "s.tanaka", "a.rossi", "d.novak",
           "l.fernandez", "r.oyelaran", "p.andersson", "h.nguyen"]

#: Subject + summary templates per category. The keywords are load-bearing:
#: category_for() must resolve them back to this category.
TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "BILLING": [
        ("Overcharged on last invoice",
         "Customer says they were billed twice for the same order and wants a refund."),
        ("Duplicate charge on our account",
         "A duplicate billing charge appeared this month and needs crediting."),
        ("Refund not received",
         "The refund agreed last week has not appeared on their invoice."),
    ],
    "TECHNICAL": [
        ("Dashboard is down",
         "The reporting dashboard returns a 500 error and is not working for any user."),
        ("Export crashes every time",
         "Their CSV export crashes with a timeout error on large reports."),
        ("API returning errors since this morning",
         "Requests fail intermittently with a 500; suspected outage on our side."),
    ],
    "SHIPPING": [
        ("Where is our delivery?",
         "A shipment is late and the tracking number has not updated in four days."),
        ("Courier never arrived",
         "The scheduled courier dispatch did not happen and delivery is now overdue."),
        ("Tracking shows no movement",
         "Shipping status has been stuck since dispatch and the customer wants an update."),
    ],
    "RETURNS": [
        ("Damaged goods, need to return",
         "Two items arrived damaged and the customer wants an RMA to return them."),
        ("Wrong item delivered",
         "They received the wrong item and would like an exchange."),
        ("Faulty unit, requesting RMA",
         "A faulty unit needs a return and replacement under warranty."),
    ],
    "ACCOUNT": [
        ("Cannot log in after password reset",
         "Their login fails after a password reset and they need account access restored."),
        ("Need another seat on our licence",
         "They want an extra seat added to the account licence."),
        # Avoid TECHNICAL keywords here ("broken", "down", "error"): that
        # pattern is checked first and would steal this template.
        ("SSO access for new starters",
         "New starters need account access set up through SSO."),
    ],
}

STYLES = [
    "a plain business email",
    "a forwarded thread with >> quoted history and a signature block",
    "a reply chain where the newest message is at the top",
    "an email with a long corporate signature, disclaimer and mobile footer",
    "a terse message typed on a phone, minimal punctuation",
    "an email with the original message pasted below a short note",
]

RENDER_SYSTEM = """You write realistic customer-support emails.

Rules:
- Output ONLY the raw email text (headers plus body). No JSON, no commentary.
- Every value from the input MUST appear somewhere in the email.
- Do NOT change any reference, address or date. Present them naturally.
- The email must read like a real person wrote it."""


class RenderFailed(RuntimeError):
    pass


def make_ticket(rng: random.Random) -> SupportTicket:
    domain = rng.choice(list(CUSTOMERS))
    sender = f"{rng.choice(SENDERS)}@{domain}"
    category = rng.choice(list(TEMPLATES))
    subject, summary = rng.choice(TEMPLATES[category])

    # The generator's intent must survive the keyword resolver, or the gold
    # will not validate against its own schema.
    resolved = category_for(f"{subject} {summary}")
    if resolved != category:
        raise ValueError(f"template for {category} resolves to {resolved}")

    cust = customer_for(sender)
    assert cust is not None
    received = date(2026, 1, 1) + timedelta(days=rng.randint(0, 250))
    refs = [f"ORD-{rng.randint(1000000, 9999999)}" for _ in range(rng.randint(0, 2))]

    return SupportTicket(
        ticket_ref=f"TKT-{rng.randint(100000, 999999)}",
        sender_email=sender,
        received_at=received,
        subject=subject,
        summary=summary,
        order_refs=refs,
        customer_id=cust["id"],
        category=category,
        priority=priority_for(cust["tier"], category),
    )


def redact(gold: dict) -> dict:
    return {k: v for k, v in gold.items()
            if k not in ("customer_id", "category", "priority")}


def render(ticket: SupportTicket, style: str, attempts: int = 5) -> str:
    gold = json.loads(ticket.model_dump_json())
    user = (
        f"STYLE: {style}\n\n"
        f"TICKET DATA:\n{json.dumps(redact(gold), indent=2)}\n\nEmail:"
    )
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            text, _ = chat(settings.large_model, RENDER_SYSTEM, user,
                           temperature=0.9, max_tokens=900)
            if text.strip():
                return text.strip()
            last = RuntimeError("empty render")
        except Exception as exc:
            last = exc
        time.sleep(min(2 ** (attempt + 1), 20) * (0.7 + random.random() * 0.6))
    raise RenderFailed(f"{type(last).__name__}: {last}")


def generate(n: int, split: str, seed: int, workers: int) -> int:
    rng = random.Random(seed)
    jobs = [(make_ticket(rng), rng.choice(STYLES)) for _ in range(n)]

    def one(job):
        ticket, style = job
        try:
            return ticket, render(ticket, style), style
        except RenderFailed as exc:
            print(f"  ! DROPPED after retries ({exc})", flush=True)
            return None

    written = dropped = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(one, jobs):
            if result is None:
                dropped += 1
                continue
            ticket, text, style = result
            with tx() as conn:
                insert(conn, "documents", ext_id=uuid.uuid4().hex[:12], text=text,
                       gold_json=ticket.model_dump_json(), split=split,
                       messiness=style[:40])
            written += 1
            if written % 25 == 0 or written + dropped == n:
                print(f"  [{written}/{n}] {split}"
                      f"{f' ({dropped} dropped)' if dropped else ''}", flush=True)
    if dropped:
        print(f"  WARNING: {dropped}/{n} dropped. Lower --workers and re-run.")
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic support emails")
    ap.add_argument("--live", type=int, default=0)
    ap.add_argument("--holdout", type=int, default=0)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    init_db()
    if args.live:
        print(f"Generating {args.live} live emails...")
        generate(args.live, "live", args.seed, args.workers)
    if args.holdout:
        print(f"Generating {args.holdout} holdout emails...")
        generate(args.holdout, "holdout", args.seed + 999, args.workers)
    print("\nDone. Next: DOMAIN=support_email python scripts/freeze_eval.py")


if __name__ == "__main__":
    main()
