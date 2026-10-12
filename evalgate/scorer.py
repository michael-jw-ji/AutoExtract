"""Frozen-holdout scorer.

Two metrics, deliberately:
  * valid_rate -- fraction that passes the schema at all
  * field_f1   -- micro-averaged field-level F1 against gold

valid_rate alone is far too coarse at n~100. It moves in whole-percent steps,
so noise swamps genuine improvement and the gate ends up promoting on coin
flips. field_f1 moves continuously and is what the promotion margin is
measured on.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.config import settings
from core.db import insert, one, rows, tx
from core.freeze import current_hash, holdout_ids
from serve.extract import extract_text
from verify.compare import counts, drop_fields, f1, field_hits
from verify.validate import validate


def holdout_docs() -> list[dict]:
    ids = holdout_ids()
    with tx() as conn:
        return [
            r for r in rows(
                conn, "SELECT id, text, gold_json, ambiguous_json FROM documents "
                      "WHERE split='holdout'"
            ) if r["id"] in ids
        ]


def prompt_hash() -> str:
    """Short hash of the serving system prompt (the 'p...' in cache names)."""
    from serve.extract import system_prompt
    return hashlib.sha256(system_prompt().encode()).hexdigest()[:8]


def answers_for(ev: dict, model_ref: str) -> Path:
    """The recorded answers an eval row was computed from."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", model_ref)
    return settings.data_dir / "eval_cache" / (
        f"{name}__{ev['eval_set_hash']}__{ev['config']}__p{ev['prompt']}.jsonl")


def per_doc_counts(path: Path) -> dict[int, tuple[int, int, int]]:
    """(tp, n_pred, n_gold) per holdout document, from recorded answers,
    scored exactly as score_model scores them (ambiguous fields dropped)."""
    docs = {d["id"]: d for d in holdout_docs()}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        doc = docs.get(rec["doc_id"])
        if doc is None:
            continue
        pred = validate(rec["raw"]).scorable
        gold = json.loads(doc["gold_json"]) if doc["gold_json"] else {}
        skip = json.loads(doc.get("ambiguous_json") or "[]")
        if skip:
            pred, gold = drop_fields(pred, skip), drop_fields(gold, skip)
        out[rec["doc_id"]] = counts(pred, gold) if gold else (0, 0, 0)
    return out


def cache_path(model_ref: str) -> Path:
    """Where this model's holdout answers are recorded, one JSON line each.

    Keyed by everything that changes an answer: model, eval set, pipeline
    config, and the system prompt itself. Change any of them and you get a
    fresh file, never a stale reuse.
    """
    prompt = prompt_hash()
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", model_ref)
    return settings.data_dir / "eval_cache" / (
        f"{name}__{current_hash()}__{config_label()}__p{prompt}.jsonl")


def _collect(docs: list[dict], model_ref: str, workers: int, progress: bool) -> dict[int, str]:
    """Raw model answers for every holdout doc, resumable.

    Each answer is appended to the cache the moment it arrives, so a crash or
    Ctrl+C loses nothing: the next run only asks for what is missing. The
    cache is also the "recorded responses" a free, deterministic CI eval needs.
    """
    path = cache_path(model_ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    answers: dict[int, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            answers[rec["doc_id"]] = rec["raw"]
    todo = [d for d in docs if d["id"] not in answers]
    if progress:
        print(f"  {len(answers)} cached, {len(todo)} to run, {workers} at a time "
              f"-> {path.name}", flush=True)

    lock = threading.Lock()
    failed: list[tuple[int, str]] = []
    started = time.monotonic()

    def run(doc: dict) -> None:
        try:
            raw, latency_ms, _ = extract_text(doc["text"], model_ref)
        except Exception as exc:  # noqa: BLE001 -- reported below, never scored
            with lock:
                failed.append((doc["id"], f"{type(exc).__name__}: {exc}"[:200]))
            return
        with lock:
            answers[doc["id"]] = raw
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"doc_id": doc["id"], "raw": raw,
                                     "latency_ms": latency_ms}) + "\n")
            if progress:
                done = len(answers)
                rate = (time.monotonic() - started) / max(1, done - (len(docs) - len(todo)))
                left = (len(docs) - done) * rate / 60
                print(f"  [{done}/{len(docs)}] {latency_ms / 1000:5.1f}s  ~{left:.0f} min left",
                      flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(run, todo))

    if failed:
        # Never score a missing answer as a wrong one: that would understate the
        # model and nothing downstream could tell. Stop, keep the cache, resume.
        for doc_id, err in failed[:5]:
            print(f"  ! doc {doc_id}: {err}", flush=True)
        raise RuntimeError(
            f"{len(failed)} of {len(docs)} documents got no answer. Everything else "
            f"is cached in {path}; re-run the same command to retry only those.")
    return answers


def score_model(
    model_version_id: int | None,
    model_ref: str | None = None,
    *,
    persist: bool = True,
    progress: bool = False,
    workers: int | None = None,
) -> dict:
    """Run the frozen holdout through one model and score it."""
    docs = holdout_docs()
    if not docs:
        raise RuntimeError("Holdout is empty. Run scripts/fetch_form4.py then scripts/freeze_eval.py.")

    n_valid = 0
    tp_total = pred_total = gold_total = 0
    per_field_hits: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    model_ref = model_ref or settings.small_model
    # Default concurrency matches the server: local Ollama runs ONE request at
    # a time, and queueing ten against it made requests wait past the client
    # timeout, retry, and pile up -- over half failed in the first attempt.
    answers = _collect(docs, model_ref, workers or settings.llm_concurrency, progress)
    results = [(doc, validate(answers[doc["id"]])) for doc in docs]

    for doc, outcome in results:
        if outcome.valid:
            n_valid += 1

        # Best-effort payload, not just validated output -- see Outcome.scorable.
        pred = outcome.scorable
        gold = json.loads(doc["gold_json"]) if doc["gold_json"] else {}
        # Fields a human marked ambiguous have no single right answer, so
        # they are taken out of BOTH sides rather than scored either way.
        skip = json.loads(doc.get("ambiguous_json") or "[]")
        if skip:
            pred, gold = drop_fields(pred, skip), drop_fields(gold, skip)
        if gold:
            tp, n_pred, n_gold = counts(pred, gold)
            tp_total += tp
            pred_total += n_pred
            gold_total += n_gold

            for path, (hit, total) in field_hits(pred, gold).items():
                per_field_hits[path][0] += hit
                per_field_hits[path][1] += total

    result = {
        "model_version_id": model_version_id,
        "eval_set_hash": current_hash(),
        "valid_rate": round(n_valid / len(docs), 4),
        "field_f1": round(f1(tp_total, pred_total, gold_total), 4),
        "n_docs": len(docs),
        "per_field": {
            k: round(v[0] / v[1], 4) for k, v in per_field_hits.items() if v[1]
        },
    }

    if persist and model_version_id is not None:
        with tx() as conn:
            insert(
                conn,
                "evals",
                model_version_id=model_version_id,
                eval_set_hash=result["eval_set_hash"],
                valid_rate=result["valid_rate"],
                field_f1=result["field_f1"],
                n_docs=result["n_docs"],
                config=config_label(),
                prompt=prompt_hash(),
                per_field_json=json.dumps(result["per_field"]),
            )
    return result


def config_label() -> str:
    """The pipeline settings a score was measured under, e.g. 'enrich+json'.

    Two scores are only comparable if this matches. Recording it is what makes
    a `valid 0.0%` row readable: it means enrichment was off, not that the
    model collapsed.
    """
    parts = []
    if settings.enrich:
        parts.append("enrich")
    if settings.json_mode:
        parts.append("json")
    if settings.compact_prompt:
        parts.append("compact")
    return "+".join(parts) if parts else "bare"


def latest_eval(model_version_id: int) -> dict | None:
    with tx() as conn:
        return one(
            conn,
            "SELECT * FROM evals WHERE model_version_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (model_version_id,),
        )
