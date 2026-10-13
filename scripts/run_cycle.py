"""Score pipeline versions on the frozen holdout and let the gate decide.

    python scripts/run_cycle.py --stage baseline                       # score the base model; first incumbent
    python scripts/run_cycle.py --stage candidate --name X --note "..."  # a prompt/render/code change (calls the model)
    python scripts/run_cycle.py --stage candidate --name X --replay-from 6 --note "..."  # change after the model: no calls
    python scripts/run_cycle.py --stage adapter --name X --adapter form4-lora-v1 --note "..."  # a LoRA served by name
    python scripts/run_cycle.py --stage evaluate --version 3            # re-score + gate an existing version
    python scripts/run_cycle.py --stage rescore --version 3             # scoring-policy change: no calls, no gate

Every run tees its output to results/runs/ (core/runlog.py). The LoRA itself
is built by scripts/build_lora_dataset.py and infra/modal_train.py.
"""

from __future__ import annotations

import argparse
import json
import os

from core import runlog
from core.config import settings
from core.db import init_db, insert, one, tx
from evalgate import gate, scorer


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
        # The model's INPUT must be identical too: everything in the config
        # label except the enrich step, which runs after the model.
        def before_model(label: str) -> list[str]:
            return [p for p in label.split("+") if not p.startswith("enrich")]
        if before_model(ev["config"]) != before_model(scorer.config_label()):
            raise SystemExit(f"cannot replay {src['name']}: its config {ev['config']} reads a "
                             f"different input than {scorer.config_label()}")
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


def stage_adapter(name: str, adapter: str, note: str) -> dict:
    """Register a LoRA adapter served by the small model's vLLM server (by
    its adapter name) as a candidate, score it, and let the gate decide.

    Same prompt, render and post-processing as the incumbent: the weights are
    the only change. infra/modal_train.py trains it; infra/modal_small.py
    serves it.
    """
    from evalgate.scorer import prompt_hash
    with tx() as conn:
        if one(conn, "SELECT id FROM model_versions WHERE name = ?", (name,)):
            raise SystemExit(f"model version {name!r} already exists; pick a new --name")
        version_id = insert(
            conn, "model_versions", name=name, base_model=settings.small_model,
            adapter_ref=adapter, status="candidate",
            notes=json.dumps({"kind": "lora", "note": note, "prompt": prompt_hash()}),
        )
    print(f"registered candidate {name!r} (model_version {version_id}, adapter {adapter}): {note}")
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
    before = scorer.latest_eval(version_id)
    # The answers THIS version's eval was computed from, not whatever the
    # current prompt/config would name: those may belong to another version.
    if not before or not before.get("prompt") or not scorer.answers_for(before, model).exists():
        raise SystemExit(f"no recorded answers for {version['name']}; run the evaluate stage")
    print(f"re-scoring {version['name']} ({model}) from recorded answers...")
    result = scorer.score_model(version_id, model, progress=True, recorded=before)
    if before:
        print(f"  before: valid_rate {before['valid_rate']:.1%}   field_f1 {before['field_f1']:.4f}")
    print(f"  now:    valid_rate {result['valid_rate']:.1%}   field_f1 {result['field_f1']:.4f}")
    runlog.save_result({"score": result, "previous_eval": before})
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Score pipeline versions and let the gate decide")
    ap.add_argument("--stage", required=True,
                    choices=["baseline", "evaluate", "candidate", "adapter", "rescore"])
    ap.add_argument("--version", type=int, help="model_version id (evaluate, rescore)")
    ap.add_argument("--name", default=None, help="name for a candidate version")
    ap.add_argument("--note", default=None, help="what the candidate changes")
    ap.add_argument("--adapter", default=None,
                    help="adapter stage: the LoRA's served name, e.g. form4-lora-v1")
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
    elif args.stage in ("evaluate", "rescore"):
        if not args.version:
            raise SystemExit(f"--version is required for the {args.stage} stage")
        (stage_evaluate if args.stage == "evaluate" else stage_rescore)(args.version)
    elif args.stage == "candidate":
        if not args.name:
            raise SystemExit("--name is required for the candidate stage")
        stage_candidate(args.name, args.note or "", args.replay_from)
    elif args.stage == "adapter":
        if not (args.name and args.adapter):
            raise SystemExit("--name and --adapter are required for the adapter stage")
        stage_adapter(args.name, args.adapter, args.note or "")


if __name__ == "__main__":
    main()
