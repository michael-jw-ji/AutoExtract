"""Failure clustering.

Deliberately NOT embeddings. A pydantic ValidationError already hands us a
precise, deterministic description of what went wrong; the cluster key is just
the failure signature. This is instant, reproducible, and renders in the
dashboard as a readable ranked list rather than a blob of dots.

Embeddings would only earn their keep for output that is schema-valid but
semantically wrong. That is a different and much harder problem, and it is
explicitly out of scope.
"""

from __future__ import annotations

from core.db import rows, tx
from verify.validate import normalise_loc


def clusters(limit: int = 20, status: str | None = None) -> list[dict]:
    """Failure signatures ranked by frequency."""
    where = "WHERE status = ?" if status else ""
    params: tuple = (status,) if status else ()
    with tx() as conn:
        found = rows(
            conn,
            f"SELECT signature, COUNT(*) AS count, "
            f"       MIN(id) AS first_id, MAX(created_at) AS last_seen "
            f"FROM failures {where} "
            f"GROUP BY signature ORDER BY count DESC LIMIT ?",
            params + (limit,),
        )
    for c in found:
        c["labels"] = label_signature(c["signature"])
    return found


def label_signature(signature: str) -> list[str]:
    """Turn `arithmetic_subtotal:|missing:line_items.*.unit_price` into prose."""
    out = []
    for part in signature.split("|"):
        if not part:
            continue
        kind, _, loc = part.partition(":")
        out.append(f"{kind} at {loc}" if loc else kind)
    return out


def top_signature() -> str | None:
    found = clusters(limit=1, status="new")
    return found[0]["signature"] if found else None


def summary() -> dict:
    with tx() as conn:
        total = conn.execute("SELECT COUNT(*) FROM extractions").fetchone()[0]
        bad = conn.execute("SELECT COUNT(*) FROM extractions WHERE valid=0").fetchone()[0]
    return {
        "extractions": total,
        "failures": bad,
        "failure_rate": round(bad / total, 4) if total else 0.0,
        "distinct_signatures": len(clusters(limit=1000)),
    }


__all__ = ["clusters", "label_signature", "top_signature", "summary", "normalise_loc"]
