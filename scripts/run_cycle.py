"""Drive the loop end to end.

    python scripts/run_cycle.py --stage baseline     # score the base model, make it incumbent
    python scripts/run_cycle.py --stage serve --n 80 # extract, validate, buffer failures
    python scripts/run_cycle.py --stage repair       # mechanical + distillation
    python scripts/run_cycle.py --stage train        # build dataset, push Baseten job
    python scripts/run_cycle.py --stage evaluate --version 3
    python scripts/run_cycle.py --stage all --n 80

Stages are separate on purpose: training is the slow one, and you want to be
able to re-run everything around it without paying for it again.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from buffer import cluster, store
from core.config import settings
from core.db import incumbent, init_db, insert, one, rows, tx
from evalgate import gate, scorer
from repair import run as repair_run
from serve.extract import extract_and_record
from train import run as train_run


def stage_baseline() -> dict:
    """Register the untuned base model and score it. It becomes the incumbent."""
    with tx() as conn:
        existing = one(
            conn, "SELECT * FROM model_versions WHERE name = ?", ("base",)
        )
        if existing:
            version_id = existing["id"]
        else:
            version_id = insert(
                conn,
                "model_versions",
                name="base",
                base_model=settings.small_model,
                adapter_ref=None,
                status="candidate",
                notes=json.dumps({"kind": "untuned baseline"}),
            )

    print(f"scoring baseline (model_version {version_id}) on the frozen holdout...")
    result = scorer.score_model(version_id, settings.small_model, progress=True)
    print(f"  valid_rate {result['valid_rate']:.1%}   field_f1 {result['field_f1']:.4f}")

    verdict = gate.decide(version_id)
    print(f"  -> {verdict['decision']}: {verdict['reason']}")
    return verdict


def stage_serve(n: int, workers: int = 10) -> dict:
    """Run live documents through the incumbent, buffering every failure."""
    with tx() as conn:
        docs = rows(
            conn,
            "SELECT d.id, d.text FROM documents d "
            "WHERE d.split='live' AND d.id NOT IN "
            "(SELECT doc_id FROM extractions) ORDER BY d.id LIMIT ?",
            (n,),
        )
    if not docs:
        print("no unprocessed live documents -- generate more with scripts/gen_docs.py")
        return {"processed": 0}

    failures = 0
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(extract_and_record, d["id"], d["text"]): d for d in docs
        }
        for fut in as_completed(futures):
            done += 1
            try:
                if not fut.result().valid:
                    failures += 1
            except Exception as exc:
                failures += 1
                print(f"  ! extraction failed: {type(exc).__name__}: {exc}")
            if done % 20 == 0 or done == len(docs):
                print(f"  [{done}/{len(docs)}] failures so far: {failures}", flush=True)

    rate = failures / len(docs)
    print(f"\nprocessed {len(docs)}, failed {failures} ({rate:.1%})")
    print(f"buffer depth now {store.depth()}")
    return {"processed": len(docs), "failures": failures, "failure_rate": rate}


def stage_repair(limit: int, allow_distill: bool, workers: int = 10) -> dict:
    print(f"repairing up to {limit} buffered failures (distill={allow_distill})...")
    stats = repair_run.run(limit=limit, allow_distill=allow_distill, workers=workers)
    print(
        f"  processed {stats['processed']}  verified {stats['verified']}  "
        f"(mechanical {stats['mechanical']}, distill {stats['distill']})"
    )
    if stats["processed"] and not stats["verified"]:
        print(
            "  WARNING: zero verified repairs. Unverified repairs are never "
            "trained on, so the dataset will be empty."
        )
    return stats


def stage_train(max_examples: int, lora_r: int, epochs: int) -> dict:
    print("building dataset and pushing a training job...")
    info = train_run.launch_retrain(
        max_examples=max_examples, lora_r=lora_r, epochs=epochs
    )
    m = info["manifest"]
    print(f"  dataset      {m['path']}")
    print(f"  size         {m['size']} ({m['repair_count']} repair / {m['replay_count']} replay)")
    print(f"  signatures   {json.dumps(m['signatures'], indent=2)[:400]}")
    print(f"  job_id       {info['job_id']}  (dry_run={info['dry_run']})")
    print(f"  version      {info['model_version_id']} ({info['run_name']})")

    if info["dry_run"]:
        print("\n  DRY RUN -- no GPU time used. Finalizing with a synthetic adapter ref.")
        train_run.finalize(info["training_run_id"], info["model_version_id"], info["job_id"])
    else:
        print(
            f"\n  Training is running on Baseten. When it finishes:\n"
            f"    python scripts/run_cycle.py --stage finalize "
            f"--run {info['training_run_id']} --version {info['model_version_id']} "
            f"--job {info['job_id']}"
        )
    return info


def stage_finalize(run_id: int, version_id: int, job_id: str) -> dict:
    deployed = train_run.finalize(run_id, version_id, job_id)
    print(f"deployed adapter: {deployed['adapter_ref']}")
    return deployed


def stage_evaluate(version_id: int) -> dict:
    with tx() as conn:
        version = one(conn, "SELECT * FROM model_versions WHERE id = ?", (version_id,))
    if not version:
        raise SystemExit(f"no model version {version_id}")

    print(f"scoring candidate {version['name']} on the frozen holdout...")
    result = scorer.score_model(version_id, version["adapter_ref"], progress=True)
    print(f"  valid_rate {result['valid_rate']:.1%}   field_f1 {result['field_f1']:.4f}")

    verdict = gate.decide(version_id)
    print("\n" + "=" * 62)
    print(f"GATE: {verdict['decision'].upper()}")
    print(f"  candidate  {verdict['candidate']}  f1={verdict['candidate_f1']}")
    print(f"  incumbent  {verdict['incumbent']}  f1={verdict['incumbent_f1']}")
    print(f"  margin     {verdict['margin_pp']:+.2f}pp (required +{verdict['required_pp']:.2f}pp)")
    print(f"  reason     {verdict['reason']}")
    print("=" * 62)
    return verdict


def main() -> None:
    ap = argparse.ArgumentParser(description="Drive the self-improvement loop")
    ap.add_argument(
        "--stage",
        required=True,
        choices=["baseline", "serve", "repair", "train", "finalize", "evaluate",
                 "clusters", "all"],
    )
    ap.add_argument("--n", type=int, default=80, help="documents to serve")
    ap.add_argument("--limit", type=int, default=200, help="failures to repair")
    ap.add_argument("--max-examples", type=int, default=800)
    ap.add_argument("--lora-r", type=int, default=32)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--version", type=int, help="model_version id")
    ap.add_argument("--run", type=int, help="training_run id")
    ap.add_argument("--job", type=str, help="baseten job id")
    ap.add_argument("--no-distill", action="store_true", help="mechanical repairs only")
    ap.add_argument("--workers", type=int, default=10, help="parallel API calls")
    args = ap.parse_args()

    init_db()

    if args.stage == "baseline":
        stage_baseline()
    elif args.stage == "serve":
        stage_serve(args.n, args.workers)
    elif args.stage == "repair":
        stage_repair(args.limit, not args.no_distill, args.workers)
    elif args.stage == "train":
        stage_train(args.max_examples, args.lora_r, args.epochs)
    elif args.stage == "finalize":
        stage_finalize(args.run, args.version, args.job)
    elif args.stage == "evaluate":
        if not args.version:
            raise SystemExit("--version is required for the evaluate stage")
        stage_evaluate(args.version)
    elif args.stage == "clusters":
        for c in cluster.clusters(limit=15):
            print(f"{c['count']:>4}x  {' + '.join(c['labels'])}")
    elif args.stage == "all":
        with tx() as conn:
            if incumbent(conn) is None:
                stage_baseline()
        stage_serve(args.n, args.workers)
        print()
        for c in cluster.clusters(limit=8, status="new"):
            print(f"  {c['count']:>4}x  {' + '.join(c['labels'])[:90]}")
        print()
        stage_repair(args.limit, not args.no_distill, args.workers)
        info = stage_train(args.max_examples, args.lora_r, args.epochs)
        if info["dry_run"]:
            stage_evaluate(info["model_version_id"])


if __name__ == "__main__":
    main()
