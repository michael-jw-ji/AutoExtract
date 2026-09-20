"""Score a HOSTED model onto the same snapshot format as score_local.py.

The point is a fair comparison. scripts/score_local.py reports `mean_f1` --
per-document F1 averaged over documents -- while evalgate/scorer.py reports
`field_f1`, which is micro-averaged over all fields at once. Those are
different numbers on the same predictions, so quoting a local `mean_f1`
against a hosted `field_f1` is not a comparison at all.

This runs the hosted model over the SAME frozen holdout, applies the SAME
serve-time enrichment, and aggregates the SAME way, writing to the same
snapshot directory so `selfheal_test.py compare` works across tracks.

    python scripts/score_hosted.py --label hosted-small
    python scripts/score_hosted.py --label hosted-small --limit 120
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import settings  # noqa: E402
from core.db import init_db  # noqa: E402
from core.freeze import current_hash  # noqa: E402
from evalgate.scorer import config_label, holdout_docs  # noqa: E402
from serve.extract import extract_text  # noqa: E402
from verify.compare import counts, f1  # noqa: E402
from verify.validate import validate  # noqa: E402

SNAP_DIR = ROOT / "data" / "snapshots"


def main() -> None:
    ap = argparse.ArgumentParser(description="Score a hosted model on the holdout")
    ap.add_argument("--label", required=True)
    ap.add_argument("--model", default=None, help="defaults to SMALL_MODEL")
    ap.add_argument("--limit", type=int, default=0,
                    help="score only the first N holdout docs, to match a "
                         "partial local run")
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()

    init_db()
    docs = holdout_docs()
    # holdout_docs() order is the DB's, which is stable, so --limit selects the
    # same documents a limited local run scored. compare intersects on doc_id
    # anyway, but matching here keeps the printed n honest.
    if args.limit:
        docs = docs[: args.limit]
    model = args.model or settings.small_model

    print(f"model   : {model}")
    print(f"enrich  : {'on' if settings.enrich else 'off'}")
    print(f"json    : {'on' if settings.json_mode else 'off'}")
    print(f"holdout : {len(docs)} docs, hash {current_hash()}")
    print(f"\nscoring {len(docs)} holdout documents...")

    def score_one(doc: dict) -> dict:
        started = time.perf_counter()
        # extract_text applies enrichment internally when ENRICH=1, the same
        # call the live pipeline makes.
        raw, _, _ = extract_text(doc["text"], model)
        latency_ms = int((time.perf_counter() - started) * 1000)
        outcome = validate(raw)
        gold = json.loads(doc["gold_json"]) if doc["gold_json"] else {}
        return {
            "doc_id": doc["id"],
            "valid": outcome.valid,
            "signature": outcome.signature,
            "error_types": sorted({e["type"] for e in outcome.errors}),
            "f1": f1(*counts(outcome.scorable, gold)) if gold else None,
            "latency_ms": latency_ms,
        }

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        records = list(pool.map(score_one, docs))

    scored = [r["f1"] for r in records if r["f1"] is not None]
    latencies = sorted(r["latency_ms"] for r in records)
    payload = {
        "label": args.label,
        "model": f"{model} (hosted)",
        "eval_set_hash": current_hash(),
        "config": config_label(),
        "n_docs": len(records),
        "valid_rate": round(sum(r["valid"] for r in records) / len(records), 4)
        if records else 0.0,
        "mean_f1": round(sum(scored) / len(scored), 4) if scored else 0.0,
        "median_latency_ms": latencies[len(latencies) // 2] if latencies else None,
        "records": records,
    }
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    (SNAP_DIR / f"{args.label}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(f"\nvalid_rate {payload['valid_rate']:.1%}   "
          f"mean_f1 {payload['mean_f1']:.4f}   "
          f"median {payload['median_latency_ms']} ms")
    print(f"wrote {SNAP_DIR / (args.label + '.json')}")


if __name__ == "__main__":
    main()
