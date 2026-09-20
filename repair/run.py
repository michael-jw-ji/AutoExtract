"""Repair orchestration: mechanical first, distillation second, verify always.

`verified` is the gate that protects the training set. A repair is verified
only if it is schema-valid AND (where we have gold) matches gold exactly.
Unverified repairs are recorded for the dashboard but never trained on.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from buffer import store
from core.db import insert, tx
from repair import distill, mechanical
from verify.compare import matches_gold
from verify.validate import strip_fences, validate


def _verify(candidate: dict, gold_json: str | None) -> tuple[bool, dict | None]:
    outcome = validate(json.dumps(candidate, default=str))
    if not outcome.valid or outcome.parsed is None:
        return False, None
    if gold_json:
        try:
            gold = json.loads(gold_json)
        except json.JSONDecodeError:
            return True, outcome.parsed
        return matches_gold(outcome.parsed, gold), outcome.parsed
    return True, outcome.parsed


def repair_one(failure: dict, *, allow_distill: bool = True) -> dict:
    """Repair a single buffered failure. Returns a small result record."""
    errors = json.loads(failure["errors_json"])
    attempt_text = strip_fences(failure["raw_output"])
    try:
        attempt = json.loads(attempt_text)
    except json.JSONDecodeError:
        attempt = {}

    # --- Pass 1: mechanical ---
    #
    # Short-circuit ONLY on a verified result. A mechanical repair that is
    # schema-valid but does not match gold is not good enough to stop at: the
    # distillation pass sees the registry and would often get it right. An
    # earlier version returned on any parseable result, which silently capped
    # the verified yield -- and JSON mode made it worse, because more
    # documents then produced output mechanical could force into validity
    # while still being wrong.
    fallback = None
    if attempt:
        candidate = mechanical.repair(attempt)
        verified, parsed = _verify(candidate, failure.get("gold_json"))
        if parsed is not None:
            if verified:
                _persist(failure, "mechanical", parsed, True)
                store.mark(failure["id"], "repaired")
                return {"failure_id": failure["id"], "method": "mechanical",
                        "verified": True}
            fallback = parsed        # keep it, but try harder first

    # --- Pass 2: distillation ---
    if allow_distill:
        candidate = distill.repair(failure["doc_text"], attempt_text, errors)
        if candidate is not None:
            candidate = mechanical.repair(candidate)  # cheap normalisation on top
            verified, parsed = _verify(candidate, failure.get("gold_json"))
            if parsed is not None and (verified or fallback is None):
                _persist(failure, "distill", parsed, verified)
                store.mark(failure["id"], "repaired" if verified else "unrepairable")
                return {"failure_id": failure["id"], "method": "distill",
                        "verified": verified}

    # Nothing verified. Record the mechanical attempt so the dashboard can see
    # it, but it stays unverified and never reaches a training set.
    if fallback is not None:
        _persist(failure, "mechanical", fallback, False)
        store.mark(failure["id"], "unrepairable")
        return {"failure_id": failure["id"], "method": "mechanical",
                "verified": False}

    store.mark(failure["id"], "unrepairable")
    return {"failure_id": failure["id"], "method": None, "verified": False}


def _persist(failure: dict, method: str, parsed: dict, verified: bool) -> None:
    with tx() as conn:
        insert(
            conn,
            "repairs",
            failure_id=failure["id"],
            doc_id=failure["doc_id"],
            method=method,
            repaired_json=json.dumps(parsed, default=str),
            verified=int(verified),
        )


def run(limit: int = 200, *, allow_distill: bool = True, workers: int = 10) -> dict:
    """Drain the failure buffer. Returns counts for the dashboard."""
    pending = store.pending(limit)
    if not pending:
        return {"processed": 0, "verified": 0, "mechanical": 0, "distill": 0}

    with tx() as conn:
        gold = {
            r["id"]: r["gold_json"]
            for r in conn.execute(
                "SELECT id, gold_json FROM documents WHERE id IN "
                f"({','.join('?' * len(pending))})",
                [f["doc_id"] for f in pending],
            ).fetchall()
        }

    for failure in pending:
        failure["gold_json"] = gold.get(failure["doc_id"])

    # Distillation is a network call per failure, so this is I/O bound.
    stats = {"processed": 0, "verified": 0, "mechanical": 0, "distill": 0}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(repair_one, f, allow_distill=allow_distill) for f in pending
        ]
        for fut in as_completed(futures):
            try:
                result = fut.result()
            except Exception as exc:
                print(f"  ! repair failed: {type(exc).__name__}: {exc}")
                stats["processed"] += 1
                continue
            stats["processed"] += 1
            if result["verified"]:
                stats["verified"] += 1
            if result["method"]:
                stats[result["method"]] += 1
    return stats
