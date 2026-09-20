"""Fork the database for a new experiment, keeping documents and the freeze.

Re-running the loop with a different setting (JSON mode, a new policy version)
needs a clean slate for extractions -- but destroying the current database
would take a complete recorded result with it. This copies the file and clears
only the per-run tables.

Documents and eval_freeze are PRESERVED deliberately: the comparison is only
meaningful against the same frozen holdout, and regenerating documents would
change both the corpus and the eval set at once.

    python scripts/fork_db.py --to autoextract.jsonmode.db
    $env:DB_PATH="autoextract.jsonmode.db"   # then run the loop as usual
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

from core.config import ROOT, settings

# Cleared on fork. Order matters -- children before parents (foreign keys).
PER_RUN_TABLES = [
    "promotions", "evals", "training_runs", "repairs",
    "failures", "extractions", "model_versions",
]


def main() -> None:
    ap = argparse.ArgumentParser(description="Fork the DB for a new experiment")
    ap.add_argument("--to", required=True, help="destination filename or path")
    ap.add_argument("--from", dest="src", default=None, help="source (default: current)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing fork")
    args = ap.parse_args()

    src = Path(args.src) if args.src else settings.db_path
    dst = Path(args.to)
    if not dst.is_absolute():
        dst = ROOT / dst

    if not src.exists():
        raise SystemExit(f"source database not found: {src}")
    if dst.exists() and not args.force:
        raise SystemExit(f"{dst} already exists (pass --force to overwrite)")

    # Checkpoint WAL first, or the copy can miss recent writes entirely.
    with sqlite3.connect(src) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    shutil.copyfile(src, dst)

    conn = sqlite3.connect(dst)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        cleared = {}
        for table in PER_RUN_TABLES:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            conn.execute(f"DELETE FROM {table}")
            cleared[table] = n
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN "
                     f"({','.join('?' * len(PER_RUN_TABLES))})", PER_RUN_TABLES)
        conn.commit()

        docs = conn.execute(
            "SELECT split, COUNT(*) n FROM documents GROUP BY split"
        ).fetchall()
        freeze = conn.execute("SELECT set_hash, n_docs FROM eval_freeze").fetchone()
    finally:
        conn.close()

    print(f"forked {src.name} -> {dst.name}")
    print("  cleared: " + ", ".join(f"{k} {v}" for k, v in cleared.items() if v))
    print("  kept   : " + ", ".join(f"{r['split']} {r['n']}" for r in docs))
    if freeze:
        print(f"  freeze : {freeze['n_docs']} docs, hash {freeze['set_hash']}")
    print(f'\nnext:  $env:DB_PATH="{dst.name}"')


if __name__ == "__main__":
    main()
