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
import os
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from buffer import cluster, store
from core import runlog
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
        if existing and existing["base_model"] != settings.small_model:
            # A baseline belongs to one model. Re-scoring a different model
            # under the same row would label its numbers with the wrong name.
            raise SystemExit(
                f"model_version 'base' is registered to {existing['base_model']!r} but "
                f"SMALL_MODEL is {settings.small_model!r}. Point SMALL_MODEL back, or "
                f"register the new model as its own version.")
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
    runlog.save_result({"score": result, "gate": verdict})
    return verdict


def stage_serve(n: int, workers: int | None = None) -> dict:
    """Run live documents through the incumbent, buffering every failure."""
    workers = workers or settings.llm_concurrency
    with tx() as conn:
        docs = rows(
            conn,
            "SELECT d.id, d.text FROM documents d "
            "WHERE d.split='live' AND d.id NOT IN "
            "(SELECT doc_id FROM extractions) ORDER BY d.id LIMIT ?",
            (n,),
        )
    if not docs:
        print("no unprocessed live documents -- fetch more with scripts/fetch_form4.py")
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


def stage_repair(limit: int, allow_distill: bool, workers: int | None = None) -> dict:
    workers = workers or settings.large_llm_concurrency
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
    runlog.save_result({"score": result, "gate": verdict})
    return verdict


def stage_candidate(name: str, note: str, replay_from: int | None = None) -> dict:
    """Register a PIPELINE change (prompt, rendering, code) as a candidate on
    the same base model, score it, and let the gate decide.

    A prompt revision changes the served system as much as a fine-tune does,
    so it faces the same gate as a fine-tune.

    `replay_from` (a model_version id) is for changes AFTER the model only:
    the candidate's answers are that version's recorded answers re-run
    through the current post-processing, so no model is called. Refused if
    the model or prompt differs, because then the model would have been sent
    a different request.
    """
    from evalgate.scorer import prompt_hash
    source = None
    if replay_from is not None:
        with tx() as conn:
            src = one(conn, "SELECT * FROM model_versions WHERE id = ?", (replay_from,))
        ev = scorer.latest_eval(replay_from) if src else None
        if ev is None:
            raise SystemExit(f"model version {replay_from} has no eval to replay")
        model = src["adapter_ref"] or src["base_model"]
        if src["adapter_ref"] or model != settings.small_model:
            raise SystemExit(f"cannot replay {src['name']}: model {model} is not {settings.small_model}")
        if ev["prompt"] != prompt_hash():
            raise SystemExit(f"cannot replay {src['name']}: its prompt p{ev['prompt']} is not the "
                             f"current p{prompt_hash()}; that would change two things at once")
        source = scorer.answers_for(ev, model)
    with tx() as conn:
        if one(conn, "SELECT id FROM model_versions WHERE name = ?", (name,)):
            raise SystemExit(f"model version {name!r} already exists; pick a new --name")
        notes = {"kind": "pipeline change", "note": note, "prompt": prompt_hash()}
        if source:
            notes["replayed_from"] = source.name
        version_id = insert(
            conn, "model_versions", name=name, base_model=settings.small_model,
            adapter_ref=None, status="candidate", notes=json.dumps(notes),
        )
    print(f"registered candidate {name!r} (model_version {version_id}): {note}")
    if source:
        dest = scorer.replay(source, settings.small_model)
        print(f"  replayed {source.name}\n        -> {dest.name} (no model calls)")
    return stage_evaluate(version_id)


def stage_rescore(version_id: int) -> dict:
    """Re-score a model from its RECORDED answers under the current scorer.

    For scoring-policy changes (e.g. how names are compared): no model calls,
    no gate decision, just a new evals row so old and new numbers sit side by
    side. Refuses if any answer is missing rather than calling the model.
    """
    with tx() as conn:
        version = one(conn, "SELECT * FROM model_versions WHERE id = ?", (version_id,))
    if not version:
        raise SystemExit(f"no model version {version_id}")
    model = version["adapter_ref"] or version["base_model"]
    if not scorer.cache_path(model).exists():
        raise SystemExit(f"no recorded answers for {model}; run the baseline/evaluate stage")
    before = scorer.latest_eval(version_id)
    print(f"re-scoring {version['name']} ({model}) from recorded answers...")
    result = scorer.score_model(version_id, model, progress=True)
    if before:
        print(f"  before: valid_rate {before['valid_rate']:.1%}   field_f1 {before['field_f1']:.4f}")
    print(f"  now:    valid_rate {result['valid_rate']:.1%}   field_f1 {result['field_f1']:.4f}")
    runlog.save_result({"score": result, "previous_eval": before})
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Drive the self-improvement loop")
    ap.add_argument(
        "--stage",
        required=True,
        choices=["baseline", "serve", "repair", "train", "finalize", "evaluate", "rescore", "candidate",
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
    ap.add_argument("--workers", type=int, default=None,
                    help="parallel API calls (default: LLM_CONCURRENCY for the small "
                         "model, LARGE_LLM_CONCURRENCY for repairs)")
    ap.add_argument("--name", default=None, help="name for a candidate version")
    ap.add_argument("--note", default=None, help="what the candidate changes")
    ap.add_argument("--replay-from", type=int, default=None,
                    help="candidate stage: re-run post-processing over this version's "
                         "recorded answers instead of calling the model")
    ap.add_argument("--domain", default=None,
                    help="domain to run (default: $DOMAIN, else form4)")
    args = ap.parse_args()
    if args.domain:
        # Every module resolves the domain lazily from $DOMAIN on first use,
        # so setting it here, before any stage runs, is enough.
        os.environ["DOMAIN"] = args.domain

    init_db()
    # Every run's terminal output and numbers go to results/runs/ (tracked).
    with runlog.run(args.stage):
        _dispatch(args)


def _dispatch(args) -> None:
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
    elif args.stage == "candidate":
        if not args.name:
            raise SystemExit("--name is required for the candidate stage")
        stage_candidate(args.name, args.note or "", args.replay_from)
    elif args.stage == "rescore":
        if not args.version:
            raise SystemExit("--version is required for the rescore stage")
        stage_rescore(args.version)
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
