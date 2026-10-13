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
    #: Any OpenAI-compatible endpoint. Defaults to a local Ollama server, which
    #: ignores the key but whose client requires a non-empty one.
    llm_api_key: str = os.getenv("LLM_API_KEY", "ollama")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
    #: The LARGE model (repairs) may live on a different server than the small
    #: one -- e.g. Modal for large, local Ollama for small. Unset, both use the
    #: endpoint above.
    large_llm_base_url: str = os.getenv("LARGE_LLM_BASE_URL", "").strip() or llm_base_url
    large_llm_api_key: str = os.getenv("LARGE_LLM_API_KEY", "").strip() or llm_api_key
    #: Requests to send at once. Local Ollama runs ONE at a time (Parallel:1);
    #: more than that only queues, and queue time counts against the timeout.
    llm_concurrency: int = int(os.getenv("LLM_CONCURRENCY", "1"))
    #: Modal's vLLM serves several at once (MAX_CONCURRENT in infra/modal_llm.py).
    large_llm_concurrency: int = int(os.getenv("LARGE_LLM_CONCURRENCY", "4"))
    #: Seconds per request. A 30-row filing can take minutes to generate locally.
    llm_timeout: float = float(os.getenv("LLM_TIMEOUT", "600"))
    small_model: str = os.getenv("SMALL_MODEL", "qwen2.5:7b")
    large_model: str = os.getenv("LARGE_MODEL", "qwen2.5:14b")
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
    #: ON by default: it took json_decode failures from 61/300 to 0 and lifted
    #: field_f1 0.6571 -> 0.7165 at zero cost. Set JSON_MODE=0 only to
    #: reproduce pre-measurement numbers.
    json_mode: bool = os.getenv("JSON_MODE", "1").strip() in {"1", "true", "yes"}

    #: Run deterministic enrichment (lookups, arithmetic, formats) at SERVE
    #: time rather than only during repair: the model reads the page, code
    #: derives everything that has a right answer.
    #:
    #: ON by default. Measured on the frozen holdout:
    #:   off -> valid_rate  0.0%, field_f1 0.6571, 300/300 live failures
    #:   on  -> valid_rate 95.0%, field_f1 0.9738,   6/300 live failures
    #: That also beats registry-in-prompt (0.9269), because code fixes
    #: arithmetic and formats that prompting leaves to the model to execute.
    #: Set ENRICH=0 only to reproduce pre-measurement numbers.
    enrich: bool = os.getenv("ENRICH", "1").strip() in {"1", "true", "yes"}

    promotion_margin: float = _f("PROMOTION_MARGIN", 2.0)
    #: How the gate decides (decided 2026-10-11). "bootstrap": promote only if
    #: the paired-bootstrap 95% interval of the F1 gain is above zero, no
    #: scored field drops more than GATE_FIELD_DROP points, and the valid rate
    #: does not drop. "margin": the original fixed +PROMOTION_MARGIN rule,
    #: kept so earlier decisions can be reproduced.
    gate_policy: str = os.getenv("GATE_POLICY", "bootstrap").strip().lower()
    gate_field_drop: float = _f("GATE_FIELD_DROP", 2.0)
    gate_resamples: int = int(os.getenv("GATE_RESAMPLES", "2000"))

    #: DB_PATH lets an experiment run against a forked database instead of
    #: destroying one that already holds a complete result. Relative paths
    #: resolve against the repo root.
    db_path: Path = (
        (ROOT / os.getenv("DB_PATH", "form4.db")).resolve()
        if not os.path.isabs(os.getenv("DB_PATH", "form4.db"))
        else Path(os.getenv("DB_PATH", "form4.db"))
    )
    data_dir: Path = ROOT / "data"

    @classmethod
    def require_key(cls) -> str:
        if not cls.llm_api_key:
            raise RuntimeError(
                "LLM_API_KEY is empty. Unset it to use the Ollama default, or "
                "set it to your provider's key (and LLM_BASE_URL to its URL)."
            )
        return cls.llm_api_key


settings = Settings()
