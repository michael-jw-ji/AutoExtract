"""Confirm the configured model IDs actually exist and respond.

Model ids in .env.example are GUESSES. Run this first -- a wrong id fails with
a 404 deep inside the loop at 3am otherwise.

    python scripts/check_models.py
"""

from __future__ import annotations

import sys

from core.config import settings
from serve.client import chat, client


def ping(label: str, model: str) -> bool:
    print(f"\n{label}: {model}")
    try:
        text, latency_ms = chat(
            model, "Reply with the single word: ok", "ping", max_tokens=8
        )
        print(f"  OK  {latency_ms}ms  -> {text.strip()[:60]!r}")
        return True
    except Exception as exc:
        print(f"  FAILED  {type(exc).__name__}: {str(exc)[:300]}")
        return False


def main() -> None:
    for label, large in (("small", False), ("large", True)):
        url = settings.large_llm_base_url if large else settings.llm_base_url
        key = settings.large_llm_api_key if large else settings.llm_api_key
        print(f"{label}: {url}  (key ...{key[-4:]})")
        try:
            available = [m.id for m in client(large).models.list().data]
            print(f"  {len(available)} models: {', '.join(sorted(available)[:8])}")
        except Exception as exc:
            print(f"  could not list models ({type(exc).__name__}) -- pinging directly")

    ok_small = ping("SMALL_MODEL (serves + gets fine-tuned)", settings.small_model)
    ok_large = ping("LARGE_MODEL (data gen + repairs)", settings.large_model)

    if not (ok_small and ok_large):
        print(
            "\nFix the failing id(s) or URL(s) in .env, then re-run. Model ids must "
            "match what the server reports above (`ollama list` for local, "
            "MODEL_NAME in infra/modal_llm.py for Modal)."
        )
        sys.exit(1)
    print("\nBoth models respond.")


if __name__ == "__main__":
    main()
