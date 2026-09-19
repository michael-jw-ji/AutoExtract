"""Recompute stored failure signatures from their saved error lists.

Signatures are derived data. When the derivation changes -- as it did when
prefix-duplicate collapsing was added -- existing rows keep the old spelling
and clusters fragment across both. This recomputes them from errors_json, with
no API calls and no re-serving.

    python scripts/backfill_signatures.py
"""

from __future__ import annotations

import json

from core.db import rows, tx
from verify.validate import signature_of


def main() -> None:
    with tx() as conn:
        failures = rows(conn, "SELECT id, signature, errors_json FROM failures")
        changed = 0
        for row in failures:
            try:
                errors = json.loads(row["errors_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            new_sig = signature_of(errors)
            if new_sig != row["signature"]:
                conn.execute(
                    "UPDATE failures SET signature = ? WHERE id = ?",
                    (new_sig, row["id"]),
                )
                # Keep the extraction row consistent with its failure.
                conn.execute(
                    "UPDATE extractions SET signature = ? WHERE id = "
                    "(SELECT extraction_id FROM failures WHERE id = ?)",
                    (new_sig, row["id"]),
                )
                changed += 1

    print(f"recomputed {changed} of {len(failures)} signatures")
    with tx() as conn:
        for r in rows(
            conn,
            "SELECT signature, COUNT(*) n FROM failures "
            "GROUP BY signature ORDER BY n DESC LIMIT 10",
        ):
            print(f"  {r['n']:>4}x  {r['signature'][:96]}")


if __name__ == "__main__":
    main()
