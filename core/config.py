"""Central config. Everything reads from here, nothing reads os.environ directly."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


class Settings:
    baseten_api_key: str = os.getenv("BASETEN_API_KEY", "")
    baseten_base_url: str = os.getenv(
        "BASETEN_BASE_URL", "https://inference.baseten.co/v1"
    )
    small_model: str = os.getenv("SMALL_MODEL", "thinkingmachines/inkling-small")
    large_model: str = os.getenv("LARGE_MODEL", "moonshotai/Kimi-K2.6")

    # The HF base model that training actually fine-tunes. This is NOT
    # necessarily SMALL_MODEL: Baseten's Model APIs serve a fixed catalogue,
    # while Training Jobs can fine-tune any HF model and deploy it separately.
    train_base_model: str = os.getenv("TRAIN_BASE_MODEL", "Qwen/Qwen3-4B")

    # Local training track (8GB laptop GPU). bf16 + LoRA with no quantization,
    # which avoids bitsandbytes entirely -- the painful Windows dependency.
    local_base_model: str = os.getenv("LOCAL_BASE_MODEL", "Qwen/Qwen2.5-1.5B-Instruct")
    #: Swap the full JSON Schema prompt for a terse field list. Must be set
    #: identically when building a dataset and when scoring, or the LoRA is
    #: tuned against a prompt the scorer never sends.
    #:
    #: .strip() is load-bearing: `set COMPACT_PROMPT=1 && cmd` in cmd.exe
    #: assigns "1 " WITH a trailing space, because cmd takes everything up to
    #: the &&. Without the strip this reads as False and scoring silently
    #: falls back to the full-schema prompt -- which would look exactly like
    #: "the fine-tune didn't work".
    compact_prompt: bool = os.getenv("COMPACT_PROMPT", "0").strip() in {"1", "true", "yes"}

    #: Send response_format={"type":"json_object"} on extraction calls.
    #: Verified accepted by Baseten for inkling-small. Off by default: it
    #: changes the failure distribution, so enable it for a FRESH cycle rather
    #: than partway through one whose baseline was measured without it.
    json_mode: bool = os.getenv("JSON_MODE", "0").strip() in {"1", "true", "yes"}

    promotion_margin: float = _f("PROMOTION_MARGIN", 2.0)
    repair_fraction: float = _f("REPAIR_FRACTION", 0.7)
    max_cluster_share: float = _f("MAX_CLUSTER_SHARE", 0.25)

    db_path: Path = ROOT / "autoextract.db"
    data_dir: Path = ROOT / "data"
    dataset_dir: Path = ROOT / "data" / "datasets"

    @classmethod
    def require_key(cls) -> str:
        if not cls.baseten_api_key:
            raise RuntimeError(
                "BASETEN_API_KEY is not set.\n"
                "  1. cp .env.example .env\n"
                "  2. paste your key from https://app.baseten.co/settings/api_keys\n"
            )
        return cls.baseten_api_key


settings = Settings()
