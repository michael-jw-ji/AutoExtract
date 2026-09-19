"""Training orchestration: dataset -> Baseten job -> candidate version row."""

from __future__ import annotations

import json
from datetime import datetime

from core.config import settings
from core.db import incumbent, insert, tx
from train import backend
from train.dataset import build_dataset


def launch_retrain(*, max_examples: int = 800, **train_kwargs) -> dict:
    """Build a dataset and push a training job. Returns run + version info."""
    manifest = build_dataset(max_examples=max_examples)
    if manifest["size"] == 0:
        raise RuntimeError(
            "Dataset is empty -- no verified repairs yet. Run the repair pass."
        )

    run_name = f"autoextract-{datetime.now():%Y%m%d-%H%M%S}"
    job = backend.launch(manifest["path"], run_name, **train_kwargs)

    with tx() as conn:
        inc = incumbent(conn)
        version_id = insert(
            conn,
            "model_versions",
            name=run_name,
            base_model=settings.train_base_model,
            adapter_ref=None,
            status="candidate",
            parent_id=inc["id"] if inc else None,
            notes=json.dumps({"signatures": manifest["signatures"],
                              "dry_run": job["dry_run"]}),
        )
        run_id = insert(
            conn,
            "training_runs",
            model_version_id=version_id,
            baseten_job_id=job["job_id"],
            dataset_path=manifest["path"],
            dataset_size=manifest["size"],
            repair_count=manifest["repair_count"],
            replay_count=manifest["replay_count"],
            status="running",
        )

    return {
        "training_run_id": run_id,
        "model_version_id": version_id,
        "job_id": job["job_id"],
        "run_name": run_name,
        "dry_run": job["dry_run"],
        "manifest": manifest,
    }


def finalize(training_run_id: int, model_version_id: int, job_id: str) -> dict:
    """Deploy the checkpoint and attach the adapter ref to the version."""
    deployed = backend.deploy_checkpoint(job_id)
    with tx() as conn:
        conn.execute(
            "UPDATE training_runs SET status='succeeded', "
            "finished_at=datetime('now') WHERE id = ?",
            (training_run_id,),
        )
        conn.execute(
            "UPDATE model_versions SET adapter_ref = ? WHERE id = ?",
            (deployed["adapter_ref"], model_version_id),
        )
    return deployed


def mark_failed(training_run_id: int, error: str) -> None:
    with tx() as conn:
        conn.execute(
            "UPDATE training_runs SET status='failed', finished_at=datetime('now') "
            "WHERE id = ?",
            (training_run_id,),
        )
        conn.execute(
            "UPDATE model_versions SET notes = ? WHERE id = "
            "(SELECT model_version_id FROM training_runs WHERE id = ?)",
            (json.dumps({"error": error[:500]}), training_run_id),
        )
