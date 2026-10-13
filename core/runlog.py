"""Keep a permanent record of every loop run: the terminal log and the result.

    with runlog.run("baseline"):
        ...                              # everything printed is also saved
        runlog.save_result({...})        # the numbers, as JSON

Writes to results/runs/<UTC timestamp>_<stage>.log and .json, which are
tracked in git. The header records what the numbers depend on (commit,
models, endpoints, config, holdout hash) so a run can be understood, or
reproduced, months later. Secrets are never written: keys are not logged.
"""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from core.config import ROOT, settings

RUNS = ROOT / "results" / "runs"
_current: Path | None = None


class _Tee:
    def __init__(self, *streams) -> None:
        self.streams = streams

    def write(self, text: str) -> int:
        for s in self.streams:
            s.write(text)
        return len(text)

    def flush(self) -> None:
        for s in self.streams:
            s.flush()

    def __getattr__(self, name):  # isatty, encoding, ... from the real stream
        return getattr(self.streams[0], name)


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001 -- a missing git must not stop a run
        return "unknown"


def header(stage: str) -> dict:
    from core.freeze import current_hash
    try:
        holdout = current_hash()
    except RuntimeError:
        holdout = None
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
    return {
        "stage": stage,
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": _git("rev-parse", "--short", "HEAD") + ("+uncommitted" if dirty else ""),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "argv": sys.argv[1:],
        "small_model": settings.small_model,
        "small_endpoint": settings.llm_base_url,
        "large_model": settings.large_model,
        "large_endpoint": settings.large_llm_base_url,
        "concurrency": settings.llm_concurrency,
        "config": {"enrich": settings.enrich, "json_mode": settings.json_mode,
                   "compact_prompt": settings.compact_prompt},
        "holdout_hash": holdout,
        "db": settings.db_path.name,
    }


@contextmanager
def run(stage: str):
    """Tee stdout/stderr into results/runs/<ts>_<stage>.log for the block."""
    global _current
    RUNS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    base = RUNS / f"{stamp}_{stage}"
    log = base.with_suffix(".log").open("w", encoding="utf-8")
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _Tee(old_out, log), _Tee(old_err, log)
    _current = base
    meta = header(stage)
    print("# run " + json.dumps(meta), flush=True)
    status = "ok"
    try:
        yield base
    except BaseException as exc:
        status = f"failed: {type(exc).__name__}: {exc}"
        raise
    finally:
        print(f"# end status={status}", flush=True)
        sys.stdout, sys.stderr = old_out, old_err
        log.close()
        _current = None
        print(f"run log saved: {base.with_suffix('.log').relative_to(ROOT)}")


def save_result(data: dict) -> Path | None:
    """Write the run's numbers next to its log. No-op outside run()."""
    if _current is None:
        return None
    path = _current.with_suffix(".json")
    path.write_text(json.dumps({"run": header(_current.name.split("_", 1)[1]),
                                "result": data}, indent=2, default=str),
                    encoding="utf-8")
    return path
