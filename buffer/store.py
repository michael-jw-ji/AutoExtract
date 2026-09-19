"""The failure buffer: reads over the `failures` table."""

from __future__ import annotations

from core.db import rows, tx


def pending(limit: int = 500) -> list[dict]:
    """Failures that have not yet been repaired or written off."""
    with tx() as conn:
        return rows(
            conn,
            "SELECT f.*, d.text AS doc_text, e.raw_output "
            "FROM failures f "
            "JOIN documents d ON d.id = f.doc_id "
            "JOIN extractions e ON e.id = f.extraction_id "
            "WHERE f.status = 'new' AND d.split = 'live' "
            "ORDER BY f.id LIMIT ?",
            (limit,),
        )


def mark(failure_id: int, status: str) -> None:
    with tx() as conn:
        conn.execute("UPDATE failures SET status = ? WHERE id = ?", (status, failure_id))


def depth() -> int:
    with tx() as conn:
        return int(
            conn.execute("SELECT COUNT(*) FROM failures WHERE status='new'").fetchone()[0]
        )
