"""Inference client for any OpenAI-compatible server (Ollama by default)."""

from __future__ import annotations

import sys
import time
from functools import lru_cache

from openai import DefaultHttpxClient, OpenAI

from core.config import settings


@lru_cache(maxsize=2)
def client(large: bool = False) -> OpenAI:
    """The OpenAI client for the small or the large model's server."""
    key = settings.require_key()
    return OpenAI(
        api_key=settings.large_llm_api_key if large else key,
        base_url=settings.large_llm_base_url if large else settings.llm_base_url,
        timeout=settings.llm_timeout,
        # The SDK retries 429s with its own backoff. Bumped from the default
        # because a rate-limited call that surfaces as an exception used to
        # fall back to a template document and quietly contaminate the corpus.
        max_retries=6,
        # Modal answers any request still running after 150s (a cold start, or
        # a long 30-row generation) with a 303 to a polling URL. The SDK treats
        # 303 as an error and does not retry it, so follow it instead.
        http_client=DefaultHttpxClient(follow_redirects=True, timeout=settings.llm_timeout),
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

    extra: dict = {}
    if settings.json_mode if json_mode is None else json_mode:
        extra["response_format"] = {"type": "json_object"}

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    # An empty response is a real failure mode, not a hypothetical: the same
    # document at temperature 0 returned 740 chars, then 0 chars, then 432.
    # An empty string cannot be parsed, so it used to surface as a json_decode
    # failure and get sent all the way to the large model for "repair" -- when
    # simply asking again fixes it.
    text = ""
    for attempt in range(3):
        resp = client(model == settings.large_model).chat.completions.create(
            model=model, messages=messages,
            temperature=temperature, max_tokens=max_tokens, **extra,
        )
        text = resp.choices[0].message.content or ""
        if text.strip():
            break
    # A truncated answer is invalid JSON and would be scored as a model
    # failure. Say so loudly instead of letting it pass as one.
    if resp.choices[0].finish_reason == "length":
        print(f"  ! {model}: output hit max_tokens={max_tokens}; answer truncated",
              file=sys.stderr, flush=True)
    latency_ms = int((time.perf_counter() - started) * 1000)
    return text, latency_ms


def serving_model(adapter_ref: str | None = None) -> str:
    """Which model id to serve with. A promoted LoRA overrides the base."""
    return adapter_ref or settings.small_model
