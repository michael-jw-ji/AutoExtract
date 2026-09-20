"""Run ONE document through the whole pipeline and narrate every stage.

This is the live, runnable version of a use case. It calls the real serving
model, runs the real validator, and shows exactly where each field's answer
came from -- the page, arithmetic, or a registry lookup.

    python scripts/demo_case.py examples/invoice_missing_totals.txt
    python scripts/demo_case.py examples/invoice_missing_totals.txt --raw
    python scripts/demo_case.py examples/email_billing.txt --domain support_email

--raw disables enrichment, which is the demo's key contrast: the same model
on the same document, with and without the derivation step. Run it twice and
the valid/invalid flip is the whole argument in ten seconds.

If a sibling <name>.gold.json exists it is used to score the result, so the
output says "correct", not merely "schema-valid" -- those are different, and
conflating them is the mistake this project exists to avoid.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BOLD, DIM, RED, GRN, YEL, CYN, OFF = (
    "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[0m"
)


def rule(title: str) -> None:
    print(f"\n{BOLD}{title}{OFF}\n{DIM}{'-' * 68}{OFF}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one document through the loop")
    ap.add_argument("document", help="path to a .txt document")
    ap.add_argument("--domain", default=None, help="invoice | support_email")
    ap.add_argument("--raw", action="store_true",
                    help="disable enrichment, to show the contrast")
    ap.add_argument("--model", default=None, help="override the serving model")
    args = ap.parse_args()

    # Must be set BEFORE importing anything that resolves the domain or reads
    # settings, because both are cached at first use.
    if args.domain:
        os.environ["DOMAIN"] = args.domain
    if args.raw:
        os.environ["ENRICH"] = "0"

    from core.config import settings
    from domains import get_domain
    from serve.extract import extract_text
    from verify.compare import counts, f1, flatten
    from verify.validate import validate

    path = pathlib.Path(args.document)
    if not path.exists():
        sys.exit(f"no such document: {path}")
    text = path.read_text(encoding="utf-8")

    gold_path = path.with_suffix(".gold.json")
    gold = json.loads(gold_path.read_text(encoding="utf-8")) if gold_path.exists() else None

    domain = get_domain()
    model = args.model or settings.small_model

    print(f"{BOLD}document {OFF}{path.name}   {DIM}{len(text)} chars{OFF}")
    print(f"{BOLD}domain   {OFF}{domain.name}")
    print(f"{BOLD}model    {OFF}{model}")
    print(f"{BOLD}enrich   {OFF}{'OFF  (--raw)' if args.raw else 'ON'}"
          f"   {BOLD}json mode {OFF}{'on' if settings.json_mode else 'off'}")

    rule("1. THE DOCUMENT  (what the model is given)")
    lines = text.strip().splitlines()
    for line in lines[:18]:
        print(f"  {DIM}|{OFF} {line}")
    if len(lines) > 18:
        print(f"  {DIM}| ... {len(lines) - 18} more lines{OFF}")

    rule("2. THE MODEL READS IT")
    started = time.perf_counter()
    raw, _, _ = extract_text(text, model)
    elapsed = int((time.perf_counter() - started) * 1000)
    outcome = validate(raw)
    payload = outcome.scorable
    print(f"  {elapsed} ms, {len(raw)} chars returned")
    if not payload:
        print(f"  {RED}output was not parseable JSON{OFF}")

    rule("3. DETERMINISTIC CHECK  (pydantic, no LLM)")
    if outcome.valid:
        print(f"  {GRN}VALID{OFF} - passed every format, arithmetic and business rule")
    else:
        print(f"  {RED}INVALID{OFF} - {outcome.error_count} error"
              f"{'' if outcome.error_count == 1 else 's'}")
        for err in outcome.errors[:8]:
            loc = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
            print(f"    {RED}{err['type']:<28}{OFF} {loc}")
            print(f"      {DIM}{str(err['msg'])[:96]}{OFF}")
        if outcome.error_count > 8:
            print(f"    {DIM}... {outcome.error_count - 8} more{OFF}")
        print(f"\n  {BOLD}failure signature{OFF}  {CYN}{outcome.signature}{OFF}")
        print(f"  {DIM}identical failures produce an identical signature, which is"
              f" how they cluster{OFF}")

    if gold:
        rule("4. IS IT ACTUALLY CORRECT?  (vs ground truth)")
        tp, n_pred, n_gold = counts(payload, gold)
        print(f"  field F1 {BOLD}{f1(tp, n_pred, n_gold):.4f}{OFF}"
              f"   {DIM}{tp} of {n_gold} fields correct{OFF}")
        pred_flat, gold_flat = flatten(payload), flatten(gold)
        wrong = [k for k in sorted(set(pred_flat) | set(gold_flat))
                 if pred_flat.get(k) != gold_flat.get(k)]
        if not wrong:
            print(f"  {GRN}every scored field matches gold{OFF}")
        for key in wrong[:10]:
            got, want = pred_flat.get(key), gold_flat.get(key)
            if isinstance(got, list) or isinstance(want, list):
                got, want = f"{len(got or [])} items", f"{len(want or [])} items"
            print(f"    {YEL}{key:<24}{OFF} got {RED}{str(got)[:30]!r}{OFF}"
                  f"  want {GRN}{str(want)[:30]!r}{OFF}")

        # Schema-valid and correct are different claims. Saying so out loud is
        # the point of the whole verify step.
        if outcome.valid and wrong:
            print(f"\n  {YEL}NOTE{OFF} this passed the schema and is still wrong - "
                  f"which is exactly why\n       a repair must be checked against "
                  f"gold before it can train anything.")

    rule("5. FINAL JSON")
    print(json.dumps(payload, indent=2)[:1400])

    if args.raw:
        print(f"\n{DIM}Now run the same command WITHOUT --raw to see enrichment "
              f"derive\nthe registry and arithmetic fields.{OFF}")


if __name__ == "__main__":
    main()
