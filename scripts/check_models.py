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
    if not settings.baseten_api_key:
        raise SystemExit(
            "BASETEN_API_KEY is not set.\n"
            "  1. cp .env.example .env\n"
            "  2. paste your key from https://app.baseten.co/settings/api_keys"
        )

    print(f"base_url: {settings.baseten_base_url}")
    print(f"key:      ...{settings.baseten_api_key[-6:]}")

    try:
        available = [m.id for m in client().models.list().data]
        print(f"\n{len(available)} models visible to this key:")
        for m in sorted(available)[:40]:
            print(f"  {m}")
    except Exception as exc:
        print(f"\ncould not list models ({type(exc).__name__}) -- pinging directly")

    ok_small = ping("SMALL_MODEL (serves + gets fine-tuned)", settings.small_model)
    ok_large = ping("LARGE_MODEL (data gen + repairs)", settings.large_model)

    if not (ok_small and ok_large):
        print(
            "\nFix the failing id(s) in .env, then re-run. If you are unsure which "
            "models are available, ask the Baseten booth -- you also want to know "
            "which small model is both servable AND trainable."
        )
        sys.exit(1)
    print("\nBoth models respond. Next: python scripts/gen_docs.py --live 120 --holdout 100")


if __name__ == "__main__":
    main()
