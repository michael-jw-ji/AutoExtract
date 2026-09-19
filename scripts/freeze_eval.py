"""Freeze the holdout set. Run once, after generating documents.

    python scripts/freeze_eval.py
"""

from __future__ import annotations

from core.db import init_db
from core.freeze import freeze_holdout


def main() -> None:
    init_db()
    info = freeze_holdout()
    print(f"holdout frozen: {info['n_docs']} documents, hash {info['set_hash']}")
    print("build_dataset() will now hard-fail if any of these leak into training.")


if __name__ == "__main__":
    main()
