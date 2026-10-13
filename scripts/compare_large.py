"""Does a bigger model fix what the served one still gets wrong? (PLAN, Milestone 2)

Runs the frozen holdout through LARGE_MODEL (Qwen2.5-32B on Modal) under the
incumbent's exact pipeline -- same prompt, same render, same post-processing
-- so the model is the only thing that differs. A REFERENCE measurement, not a
candidate: nothing is registered, so the gate cannot promote a model this
project does not serve.

Answers are cached like any eval (data/eval_cache/, resumable). Output: the
paired bootstrap vs the incumbent, per-field accuracy for both, and per filing
whether the 32B fixed, kept or introduced each error, written with the run log
to results/runs/.

    python scripts/compare_large.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import runlog  # noqa: E402
from core.config import settings  # noqa: E402
from core.db import incumbent, init_db, tx  # noqa: E402
from evalgate import scorer  # noqa: E402
from evalgate.stats import paired_bootstrap  # noqa: E402
from verify.compare import pair_rows  # noqa: E402
from verify.validate import validate  # noqa: E402


def rows_summary(path: Path, docs: dict) -> dict[int, dict]:
    """Per filing: matched / missing / extra rows and rows in the wrong table."""
    out = {}
    for rec in map(json.loads, path.read_text(encoding="utf-8").splitlines()):
        gold = json.loads(docs[rec["doc_id"]]["gold_json"])["transactions"]
        pred = validate(rec["raw"]).scorable
        pred = (pred.get("transactions") if isinstance(pred, dict) else None) or []
        s = Counter()
        for p, g in pair_rows("transactions", pred, gold):
            if p is not None and g is not None:
                s["matched"] += 1
                s["wrong_table"] += pred[p].get("is_derivative") != gold[g]["is_derivative"]
            else:
                s["missing" if g is not None else "extra"] += 1
        out[rec["doc_id"]] = dict(s)
    return out


def main() -> None:
    init_db()
    with tx() as conn:
        inc = incumbent(conn)
    inc_eval = scorer.latest_eval(inc["id"])
    if scorer.config_label() != inc_eval["config"] or scorer.prompt_hash() != inc_eval["prompt"]:
        raise SystemExit("the current pipeline is not the incumbent's; the model would not be "
                         "the only difference")
    small = scorer.answers_for(inc_eval, inc["adapter_ref"] or inc["base_model"])

    print(f"incumbent {inc['name']}: f1 {inc_eval['field_f1']:.4f}, valid {inc_eval['valid_rate']:.1%}")
    print(f"running the holdout through {settings.large_model}...")
    large_score = scorer.score_model(None, settings.large_model, persist=False, progress=True,
                                     workers=settings.large_llm_concurrency)
    large = scorer.cache_path(settings.large_model)
    print(f"  {settings.large_model}: f1 {large_score['field_f1']:.4f}, "
          f"valid {large_score['valid_rate']:.1%}")

    delta, lo, hi = paired_bootstrap(scorer.per_doc_counts(large), scorer.per_doc_counts(small),
                                     resamples=settings.gate_resamples)
    print(f"  32B minus 7B: {delta:+.2f} pts, 95% CI [{lo:+.2f}, {hi:+.2f}]")

    docs = {d["id"]: d for d in scorer.holdout_docs()}
    s_rows, l_rows = rows_summary(small, docs), rows_summary(large, docs)
    s_counts, l_counts = scorer.per_doc_counts(small), scorer.per_doc_counts(large)

    def perfect(c):
        return c[0] == c[1] == c[2]

    outcome = Counter()
    per_doc = []
    for i, doc in docs.items():
        a, b = perfect(s_counts[i]), perfect(l_counts[i])
        kind = ("both right" if a and b else "32B fixes" if b else
                "32B breaks" if a else "both wrong")
        outcome[kind] += 1
        per_doc.append({"doc_id": i, "accession": doc["ext_id"], "outcome": kind,
                        "small": {"counts": s_counts[i], "rows": s_rows[i]},
                        "large": {"counts": l_counts[i], "rows": l_rows[i]}})
    print("  per filing:", dict(outcome))
    for key in ("missing", "extra", "wrong_table"):
        print(f"  rows {key:11s} 7B {sum(r.get(key, 0) for r in s_rows.values()):4d}   "
              f"32B {sum(r.get(key, 0) for r in l_rows.values()):4d}")

    runlog.save_result({
        "incumbent": {"name": inc["name"], "answers": small.name, "f1": inc_eval["field_f1"],
                      "valid_rate": inc_eval["valid_rate"],
                      "per_field": json.loads(inc_eval["per_field_json"])},
        "large": {"model": settings.large_model, "answers": large.name, **large_score},
        "bootstrap_large_minus_small": {"delta": delta, "lo": lo, "hi": hi},
        "outcomes": dict(outcome),
        "per_doc": per_doc,
    })


if __name__ == "__main__":
    with runlog.run("compare_large"):
        main()
