"""Record a local training run in the loop's own bookkeeping.

Training locally happens outside the pipeline, so without this the run leaves
no trace: no model_versions row, no training_run, no eval, no gate decision --
and the dashboard keeps showing a single version forever. This ingests the two
snapshots produced by scripts/score_local.py and writes the same rows the
Baseten path would have written.

Two deliberate differences from the Baseten track, both recorded rather than
papered over:

  * track='local'. The gate compares the LoRA against the LOCAL base, never
    against the inkling-small incumbent -- different base models would make
    the margin measure two things at once.
  * metric='mean_f1'. The local snapshots carry per-document F1, not the
    micro-averaged field_f1 the Baseten scorer computes. Both sides of the
    local comparison use the same metric, so the delta is valid; it just is
    not numerically comparable to the Baseten track's numbers.

    python scripts/register_local.py --base local-base --lora local-lora \
        --adapter models/lora-20260919-183741
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.config import ROOT, settings
from core.db import init_db, insert, one, tx
from evalgate import gate
from evalgate.scorer import config_label

SNAP_DIR = ROOT / "data" / "snapshots"


def load_snapshot(label: str) -> dict:
    path = SNAP_DIR / f"{label}.json"
    if not path.exists():
        raise SystemExit(f"no snapshot {label!r} at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def upsert_version(name: str, base_model: str, adapter: str | None,
                   parent_id: int | None, notes: dict) -> int:
    with tx() as conn:
        existing = one(conn, "SELECT id FROM model_versions WHERE name = ?", (name,))
        if existing:
            return int(existing["id"])
        return insert(
            conn, "model_versions",
            name=name, base_model=base_model, adapter_ref=adapter,
            status="candidate", parent_id=parent_id, track="local",
            notes=json.dumps(notes),
        )


def record_eval(version_id: int, snap: dict) -> None:
    with tx() as conn:
        insert(
            conn, "evals",
            model_version_id=version_id,
            eval_set_hash=snap["eval_set_hash"],
            valid_rate=snap["valid_rate"],
            field_f1=snap["mean_f1"],      # see module docstring: metric column
            metric="mean_f1",
            n_docs=snap["n_docs"],
            # Prefer the config the SNAPSHOT was taken under. Reading it from
            # current settings would label a months-old snapshot with today's
            # environment, which is worse than leaving it blank.
            config=snap.get("config") or config_label(),
            per_field_json=None,
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="Register a local training run")
    ap.add_argument("--base", default="local-base", help="base snapshot label")
    ap.add_argument("--lora", default="local-lora", help="LoRA snapshot label")
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--dataset", default=None)
    args = ap.parse_args()

    init_db()
    base_snap, lora_snap = load_snapshot(args.base), load_snapshot(args.lora)

    if base_snap["eval_set_hash"] != lora_snap["eval_set_hash"]:
        raise SystemExit(
            f"eval sets differ ({base_snap['eval_set_hash']} vs "
            f"{lora_snap['eval_set_hash']}). These are not comparable."
        )

    adapter = Path(args.adapter)
    meta = {}
    if (adapter / "meta.json").exists():
        meta = json.loads((adapter / "meta.json").read_text(encoding="utf-8"))
    dataset = args.dataset or meta.get("dataset")

    base_id = upsert_version(
        name=f"local-base ({settings.local_base_model.split('/')[-1]})",
        base_model=settings.local_base_model, adapter=None, parent_id=None,
        notes={"kind": "untuned local base", "snapshot": args.base},
    )
    record_eval(base_id, base_snap)

    # The base becomes the local incumbent so the LoRA has something to beat.
    with tx() as conn:
        conn.execute(
            "UPDATE model_versions SET status='promoted', "
            "promoted_at=COALESCE(promoted_at, datetime('now')) WHERE id=?",
            (base_id,),
        )

    lora_id = upsert_version(
        name=adapter.name, base_model=settings.local_base_model,
        adapter=str(adapter), parent_id=base_id,
        notes={"kind": "local LoRA", "snapshot": args.lora, **meta},
    )
    record_eval(lora_id, lora_snap)

    n_examples = meta.get("examples")
    with tx() as conn:
        insert(
            conn, "training_runs",
            model_version_id=lora_id,
            baseten_job_id=f"local:{adapter.name}",
            dataset_path=dataset,
            dataset_size=n_examples,
            repair_count=None, replay_count=None,
            status="succeeded", finished_at=None,
        )
        conn.execute(
            "UPDATE training_runs SET finished_at=datetime('now') "
            "WHERE baseten_job_id = ?", (f"local:{adapter.name}",),
        )

    print(f"local-base  version {base_id}  "
          f"valid {base_snap['valid_rate']:.1%}  mean_f1 {base_snap['mean_f1']:.4f}")
    print(f"local-lora  version {lora_id}  "
          f"valid {lora_snap['valid_rate']:.1%}  mean_f1 {lora_snap['mean_f1']:.4f}")

    verdict = gate.decide(lora_id, against=base_id)
    print("\n" + "=" * 62)
    print(f"GATE: {verdict['decision'].upper()}")
    print(f"  candidate  {verdict['candidate']}")
    print(f"  incumbent  {verdict['incumbent']}")
    print(f"  margin     {verdict['margin_pp']:+.2f}pp "
          f"(required +{verdict['required_pp']:.2f}pp)")
    print(f"  reason     {verdict['reason']}")
    print("=" * 62)


if __name__ == "__main__":
    main()
