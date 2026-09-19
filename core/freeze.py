"""Mechanical enforcement of the eval freeze.

The rule "the eval set never leaks into training data" is worthless as a
convention -- someone will break it at 4am. So it is enforced here: the
holdout id-set is hashed once, and any dataset build asserts against it.
"""

from __future__ import annotations

import hashlib
import json

from core.db import one, rows, tx


class EvalLeakError(RuntimeError):
    """Raised when training data overlaps the frozen holdout. Never catch this."""


class ContaminatedHoldoutError(RuntimeError):
    """Raised when the holdout contains template-rendered documents."""


#: Marker emitted by scripts/gen_docs.py::render_offline. Template documents
#: are far easier than model-rendered ones (0% failure rate in the hour-0
#: probe), so any that reach the holdout silently inflate every eval number.
#: This happened for real: a rate-limited run fell back to templates and 49%
#: of an 80-document holdout was trivial before anyone noticed.
TEMPLATE_MARKER = "===== INVOICE"


def _hash_ids(doc_ids: list[int]) -> str:
    payload = json.dumps(sorted(doc_ids), separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def count_template_docs(split: str | None = None) -> int:
    where = "WHERE text LIKE ?" + (" AND split = ?" if split else "")
    params: tuple = (f"{TEMPLATE_MARKER}%",) + ((split,) if split else ())
    with tx() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM documents {where}",
                                params).fetchone()[0])


def freeze_holdout(allow_template: bool = False) -> dict:
    """Snapshot the current holdout split. Idempotent unless the split changed."""
    contaminated = count_template_docs("holdout")
    if contaminated and not allow_template:
        raise ContaminatedHoldoutError(
            f"{contaminated} holdout document(s) are template-rendered, not "
            f"model-rendered. Templates are far easier and will inflate every "
            f"eval number measured against them.\n"
            f"This usually means gen_docs.py hit rate limits. Delete the "
            f"affected documents and regenerate with fewer --workers, or pass "
            f"allow_template=True if you genuinely intend to score against "
            f"template documents."
        )

    with tx() as conn:
        ids = [r["id"] for r in rows(conn, "SELECT id FROM documents WHERE split='holdout'")]
        if not ids:
            raise RuntimeError(
                "No holdout documents. Run scripts/gen_docs.py before freezing."
            )
        h = _hash_ids(ids)
        existing = one(conn, "SELECT * FROM eval_freeze WHERE id = 1")
        if existing and existing["set_hash"] != h:
            raise RuntimeError(
                f"Holdout changed after freeze ({existing['set_hash']} -> {h}). "
                "The eval set is frozen. Delete eval_freeze deliberately if you "
                "really mean to re-freeze, and re-run every eval."
            )
        if not existing:
            conn.execute(
                "INSERT INTO eval_freeze (id, set_hash, n_docs, doc_ids) VALUES (1,?,?,?)",
                (h, len(ids), json.dumps(sorted(ids))),
            )
        return {"set_hash": h, "n_docs": len(ids)}


def holdout_ids() -> set[int]:
    with tx() as conn:
        row = one(conn, "SELECT doc_ids FROM eval_freeze WHERE id = 1")
    if not row:
        raise RuntimeError("Holdout is not frozen. Run core.freeze.freeze_holdout() first.")
    return set(json.loads(row["doc_ids"]))


def current_hash() -> str:
    with tx() as conn:
        row = one(conn, "SELECT set_hash FROM eval_freeze WHERE id = 1")
    if not row:
        raise RuntimeError("Holdout is not frozen.")
    return str(row["set_hash"])


def assert_no_leak(training_doc_ids: list[int]) -> None:
    """Hard-fail if any training example comes from the frozen holdout."""
    overlap = set(training_doc_ids) & holdout_ids()
    if overlap:
        raise EvalLeakError(
            f"{len(overlap)} holdout document(s) found in training data: "
            f"{sorted(overlap)[:10]}. Refusing to build the dataset."
        )
