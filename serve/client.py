"""Baseten inference client. OpenAI-compatible, so we use the OpenAI SDK."""

from __future__ import annotations

import time
from functools import lru_cache

from openai import OpenAI

from core.config import settings
from serve import mock


@lru_cache(maxsize=1)
def client() -> OpenAI:
    return OpenAI(
        api_key=settings.require_key(),
        base_url=settings.baseten_base_url,
        timeout=180.0,
        # The SDK retries 429s with its own backoff. Bumped from the default
        # because a rate-limited call that surfaces as an exception used to
        # fall back to a template document and quietly contaminate the corpus.
        max_retries=6,
    )


def chat(
    model: str,
    system: str,
    user: str,
    *,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    json_mode: bool | None = None,
) -> tuple[str, int]:
    """Single-turn completion. Returns (text, latency_ms).

    json_mode sends response_format={"type":"json_object"}, which Baseten
    accepts for our serving model. That addresses the json_decode failure
    class (20% of failures on the current corpus) with no training at all.

    It is OFF by default and gated behind JSON_MODE=1 deliberately: turning it
    on changes the failure distribution, so a corpus and baseline measured
    without it are not comparable to one measured with it. Enable it for a
    FRESH cycle, not partway through an experiment.
    """
    started = time.perf_counter()

    if mock.enabled():
        return mock.respond(system, user), int((time.perf_counter() - started) * 1000)

    extra: dict = {}
    if settings.json_mode if json_mode is None else json_mode:
        extra["response_format"] = {"type": "json_object"}

    resp = client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        **extra,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    return (resp.choices[0].message.content or ""), latency_ms


def serving_model(adapter_ref: str | None = None) -> str:
    """Which model id to serve with. A promoted LoRA overrides the base."""
    return adapter_ref or settings.small_model
