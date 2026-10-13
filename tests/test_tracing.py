"""Model calls are traced with the GenAI semantic-convention attributes."""

from __future__ import annotations

import json
from types import SimpleNamespace

from serve import client as client_mod
from serve.tracing import to_file


def _resp(text, prompt_tokens, completion_tokens):
    return SimpleNamespace(
        model="test-model",
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")])


def test_a_chat_call_is_one_span_with_tokens_summed_over_retries(tmp_path, monkeypatch):
    answers = iter([_resp("", 100, 0), _resp('{"a": 1}', 100, 7)])   # empty, then retried
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: next(answers))))
    monkeypatch.setattr(client_mod, "client", lambda large=False: fake)
    path = tmp_path / "t.jsonl"
    with to_file(path):
        text, _ = client_mod.chat("test-model", "sys", "doc", max_tokens=64)
    assert text == '{"a": 1}'
    (span,) = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    a = span["attributes"]
    assert span["name"] == "chat test-model" and a["gen_ai.request.model"] == "test-model"
    assert (a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"]) == (200, 7)
    assert a["autoextract.attempts"] == 2 and a["gen_ai.request.max_tokens"] == 64


def test_no_file_means_no_output(tmp_path, monkeypatch):
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: _resp("x", 1, 1))))
    monkeypatch.setattr(client_mod, "client", lambda large=False: fake)
    client_mod.chat("m", "s", "u")
    assert list(tmp_path.iterdir()) == []
