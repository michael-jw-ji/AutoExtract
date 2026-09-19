"""HOUR-0 EXPERIMENT. Run this before trusting anything else.

The entire project depends on the base model failing the schema 25-45% of the
time. Too easy and there is no training signal, no clusters, and no demo. Too
hard and no LoRA can close the gap and the gate never promotes.

This measures that rate and tells you which way to tune.

    python scripts/probe.py --n 20
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from core.config import settings
from core.db import init_db, rows, tx
from serve.extract import extract_text
from verify.validate import validate

BAND = (0.25, 0.45)


def main() -> None:
    ap = argparse.ArgumentParser(description="Measure base-model failure rate")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--model", default=None, help="override the model id")
    args = ap.parse_args()

    init_db()
    with tx() as conn:
        docs = rows(
            conn,
            "SELECT id, text, messiness FROM documents WHERE split='live' "
            "ORDER BY RANDOM() LIMIT ?",
            (args.n,),
        )

    if not docs:
        raise SystemExit(
            "No live documents. Run:\n"
            "  python scripts/gen_docs.py --live 40 --offline"
        )

    model = args.model or settings.small_model
    print(f"Probing {model} on {len(docs)} documents...\n")

    failures = 0
    sig_counts: Counter[str] = Counter()
    by_style: Counter[str] = Counter()
    latencies = []

    for i, doc in enumerate(docs, 1):
        raw, latency_ms, _ = extract_text(doc["text"], model)
        outcome = validate(raw)
        latencies.append(latency_ms)
        status = "PASS" if outcome.valid else "FAIL"
        if not outcome.valid:
            failures += 1
            sig_counts[outcome.signature] += 1
            by_style[doc["messiness"]] += 1
        print(f"  [{i:>3}/{len(docs)}] {status}  {latency_ms:>5}ms  {doc['messiness']}")
        if not outcome.valid:
            for err in outcome.errors[:3]:
                loc = ".".join(str(p) for p in err["loc"]) or "<root>"
                print(f"          {err['type']} @ {loc}")

    rate = failures / len(docs)
    print("\n" + "=" * 62)
    print(f"failure rate      {rate:.1%}  ({failures}/{len(docs)})")
    print(f"median latency    {sorted(latencies)[len(latencies) // 2]}ms")
    print(f"distinct signatures {len(sig_counts)}")

    print("\ntop failure signatures:")
    for sig, count in sig_counts.most_common(8):
        print(f"  {count:>3}x  {sig[:100]}")

    if by_style:
        print("\nfailures by messiness style:")
        for style, count in by_style.most_common():
            print(f"  {count:>3}x  {style}")

    rule_types = {"registry_vendor_id", "registry_unknown_vendor",
                  "taxonomy_category", "policy_payment_terms"}
    rule_sigs = sum(
        n for sig, n in sig_counts.items()
        if any(t in sig for t in rule_types)
    )
    capability_rate = (failures - rule_sigs) / len(docs)

    print(f"  of which business-rule failures: {rule_sigs} "
          f"({rule_sigs / len(docs):.1%})")
    print(f"  capability failures (non-rule):  {failures - rule_sigs} "
          f"({capability_rate:.1%})")

    print("\n" + "=" * 62)
    lo, hi = BAND

    # Business-rule failures are EXPECTED to approach 100%: the registry is not
    # in the serving prompt, so the model cannot possibly know these. That is
    # the point -- they are learnable by fine-tuning, which is precisely what
    # the loop does. The 25-45% band applies to capability failures only.
    if rule_sigs and rate >= hi:
        print(f"HIGH FAILURE RATE ({rate:.1%}), driven by business rules "
              f"({rule_sigs}/{failures}).")
        print("This is EXPECTED and healthy. The serving model has never seen")
        print("the registry, so it cannot know vendor_id, category codes, or")
        print("the payment policy. These are exactly what a LoRA can learn.")
        print(f"Capability failures sit at {capability_rate:.1%} -- that is the")
        print("number the 25-45% band applies to.")
    elif rate < lo:
        print(f"TOO EASY ({rate:.1%} < {lo:.0%}). The buffer will not fill.")
        print("Tighten verify/schema.py: add required fields, tighten the")
        print("invoice_number regex, or drop to a smaller SMALL_MODEL.")
        print("Also raise document messiness in scripts/gen_docs.py.")
    elif rate > hi:
        print(f"TOO HARD ({rate:.1%} > {hi:.0%}). A LoRA will not close this.")
        print("Loosen verify/schema.py: relax extra='forbid', widen TOLERANCE,")
        print("or move to a larger SMALL_MODEL.")
    else:
        print(f"IN BAND ({rate:.1%}). Proceed -- build the rest of the loop.")
    print("=" * 62)

    out = settings.data_dir / "probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"model": model, "n": len(docs), "failure_rate": rate,
             "signatures": sig_counts.most_common(), "by_style": by_style.most_common()},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
