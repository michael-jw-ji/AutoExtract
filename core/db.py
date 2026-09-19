"""SQLite access. One connection factory, thin helpers, no ORM."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from core.config import settings

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """Transaction scope. Commits on success, rolls back on exception."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> bool:
    """Add a column if it is missing. Returns True if it was added.

    schema.sql uses CREATE TABLE IF NOT EXISTS, so an existing database never
    picks up new columns. This is the migration path for the ones added after
    a DB already exists.
    """
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column in cols:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return True


MIGRATIONS = [
    # Which training path a version came from. The local track (a LoRA on
    # Qwen2.5-1.5B) must never be gate-compared against the Baseten track's
    # inkling-small incumbent -- different base models, two variables.
    ("model_versions", "track", "TEXT NOT NULL DEFAULT 'baseten'"),
    # Which metric the score is. The Baseten track records micro-averaged
    # field_f1; the local track records per-document mean_f1. Recording which
    # is which beats silently putting one in a column named for the other.
    ("evals", "metric", "TEXT NOT NULL DEFAULT 'field_f1'"),
]


def init_db() -> None:
    with tx() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        for table, column, decl in MIGRATIONS:
            ensure_column(conn, table, column, decl)


def insert(conn: sqlite3.Connection, table: str, **cols: Any) -> int:
    keys = list(cols)
    placeholders = ", ".join("?" for _ in keys)
    sql = f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders})"
    cur = conn.execute(sql, [_encode(cols[k]) for k in keys])
    return int(cur.lastrowid)


def _encode(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    if isinstance(v, bool):
        return int(v)
    return v


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def incumbent(conn: sqlite3.Connection, track: str = "baseten") -> dict | None:
    """The currently-serving model version for a track.

    Track-scoped on purpose: a promoted LOCAL LoRA (Qwen on a laptop GPU) is
    not servable through the Baseten endpoint, so it must never become what
    serve/extract.py reaches for. The local track exists to produce evidence,
    not to serve traffic.
    """
    return one(
        conn,
        "SELECT * FROM model_versions WHERE status = 'promoted' AND track = ? "
        "ORDER BY promoted_at DESC, id DESC LIMIT 1",
        (track,),
    )
