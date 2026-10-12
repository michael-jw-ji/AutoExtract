"""The holdout scorer: resumable, and never scores a missing answer as wrong.

No model, no database: extract_text, the holdout and the freeze hash are faked.
"""

from __future__ import annotations

import json

import pytest

from core.config import settings
from evalgate import scorer
from tests.test_form4 import gold, tx


@pytest.fixture
def fake(tmp_path, monkeypatch):
    g = gold(tx())
    docs = [{"id": i, "text": f"doc {i}", "gold_json": json.dumps(g),
             "ambiguous_json": None} for i in range(1, 6)]
    calls: list[int] = []
    state = {"fail": set()}

    def extract(text, model_ref=None):
        doc_id = int(text.split()[1])
        calls.append(doc_id)
        if doc_id in state["fail"]:
            raise TimeoutError("model timed out")
        return json.dumps(g), 10, "fake"

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(scorer, "holdout_docs", lambda: docs)
    monkeypatch.setattr(scorer, "current_hash", lambda: "testhash")
    monkeypatch.setattr(scorer, "extract_text", extract)
    monkeypatch.setattr(scorer, "model_input", lambda d: d["text"])
    return calls, state


def test_scores_every_document(fake):
    result = scorer.score_model(None, "fake-model", persist=False)
    assert result["n_docs"] == 5 and result["valid_rate"] == 1.0
    assert result["field_f1"] == 1.0


def test_a_failed_document_stops_scoring_instead_of_counting_as_wrong(fake):
    calls, state = fake
    state["fail"] = {3}
    with pytest.raises(RuntimeError, match="1 of 5 documents got no answer"):
        scorer.score_model(None, "fake-model", persist=False)


def test_a_rerun_resumes_and_only_asks_for_what_is_missing(fake):
    calls, state = fake
    state["fail"] = {3}
    with pytest.raises(RuntimeError):
        scorer.score_model(None, "fake-model", persist=False)
    assert sorted(calls) == [1, 2, 3, 4, 5]
    calls.clear()
    state["fail"] = set()
    result = scorer.score_model(None, "fake-model", persist=False)
    assert calls == [3]                      # only the missing one
    assert result["valid_rate"] == 1.0


def test_a_different_model_gets_its_own_cache(fake):
    calls, _ = fake
    scorer.score_model(None, "model-a", persist=False)
    calls.clear()
    scorer.score_model(None, "model-b", persist=False)
    assert len(calls) == 5
