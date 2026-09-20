"""Copy model versions, evals and gate decisions between databases.

Forking a database for an experiment (scripts/fork_db.py) clears the per-run
tables, which is right for measurement and wrong for presentation: the
training lineage ends up in one file and the current pipeline's numbers in
another, and neither tells the whole story.

This merges the lineage across. It is only valid because every version was
scored against the SAME frozen holdout -- the merge refuses if the eval-set
hashes disagree, because scores from different exams are not comparable and
putting them in one table would imply they are.

    python scripts/merge_versions.py --from autoextract.db --into autoextract.optimal.db
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from core.config import ROOT


def connect(p: Path) -> sqlite3.Connection:
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    return c


def main() -> None:
    ap = argparse.ArgumentParser(description="Merge model lineage between DBs")
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--into", dest="dst", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src_p = Path(args.src) if Path(args.src).is_absolute() else ROOT / args.src
    dst_p = Path(args.dst) if Path(args.dst).is_absolute() else ROOT / args.dst
    for p in (src_p, dst_p):
        if not p.exists():
            raise SystemExit(f"not found: {p}")

    src, dst = connect(src_p), connect(dst_p)
    try:
        # Refuse to merge scores measured against different eval sets.
        hashes = {
            r["eval_set_hash"]
            for db in (src, dst)
            for r in db.execute("SELECT DISTINCT eval_set_hash FROM evals")
        }
        if len(hashes) > 1:
            raise SystemExit(
                f"eval sets differ across databases: {sorted(hashes)}.\n"
                "Scores from different holdouts are not comparable; merging "
                "them into one table would imply they are."
            )

        existing = {r["name"] for r in dst.execute("SELECT name FROM model_versions")}
        versions = [dict(r) for r in src.execute("SELECT * FROM model_versions ORDER BY id")]
        todo = [v for v in versions if v["name"] not in existing]
        if not todo:
            print("nothing to merge — all versions already present")
            return

        print(f"merging {len(todo)} version(s) from {src_p.name} into {dst_p.name}:")
        idmap: dict[int, int] = {}
        for v in todo:
            print(f"   [{v['track']}] {v['name']}  ({v['status']})")
            if args.dry_run:
                continue
            cur = dst.execute(
                "INSERT INTO model_versions "
                "(name, base_model, adapter_ref, status, track, parent_id, notes, "
                " created_at, promoted_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (v["name"], v["base_model"], v["adapter_ref"], v["status"],
                 v.get("track", "baseten"), idmap.get(v["parent_id"] or -1),
                 v["notes"], v["created_at"], v["promoted_at"]),
            )
            idmap[v["id"]] = int(cur.lastrowid)

        if args.dry_run:
            print("\n(dry run — nothing written)")
            return

        for old, new in idmap.items():
            for e in src.execute("SELECT * FROM evals WHERE model_version_id=?", (old,)):
                dst.execute(
                    "INSERT INTO evals (model_version_id, eval_set_hash, valid_rate, "
                    "field_f1, metric, n_docs, per_field_json, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (new, e["eval_set_hash"], e["valid_rate"], e["field_f1"],
                     e["metric"] if "metric" in e.keys() else "field_f1",
                     e["n_docs"], e["per_field_json"], e["created_at"]),
                )
            for p in src.execute("SELECT * FROM promotions WHERE candidate_id=?", (old,)):
                dst.execute(
                    "INSERT INTO promotions (candidate_id, incumbent_id, decision, "
                    "margin, required, reason, created_at) VALUES (?,?,?,?,?,?,?)",
                    (new, idmap.get(p["incumbent_id"] or -1), p["decision"],
                     p["margin"], p["required"], p["reason"], p["created_at"]),
                )
            for t in src.execute("SELECT * FROM training_runs WHERE model_version_id=?", (old,)):
                dst.execute(
                    "INSERT INTO training_runs (model_version_id, baseten_job_id, "
                    "dataset_path, dataset_size, repair_count, replay_count, status, "
                    "started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (new, t["baseten_job_id"], t["dataset_path"], t["dataset_size"],
                     t["repair_count"], t["replay_count"], t["status"],
                     t["started_at"], t["finished_at"]),
                )
        dst.commit()
        print(f"\nmerged. {dst_p.name} now has "
              f"{dst.execute('SELECT COUNT(*) n FROM model_versions').fetchone()['n']} versions")
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    main()
