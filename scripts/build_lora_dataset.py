"""Build the Form 4 LoRA training set the way the loop intends (CLAUDE.md).

1. Serve every LIVE filing (never the frozen holdout) with the incumbent
   pipeline: same prompt, render and post-processing. Answers are cached in
   data/live_cache/ so a re-run makes no model calls.
2. A filing whose answer differs from gold is a failure. Its repair is the
   filing's own XML gold, so every repair is verified by construction (the
   rule that keeps unverified repairs out of training).
3. 70% repairs, 30% replay of filings the model already gets right (against
   forgetting). The replay share is drawn by a fixed hash, not by hand.

Each example is chat-format: system = the serving prompt, user = the page
exactly as served (render v3), assistant = the gold JSON in the shape the
model is asked for (no `value`: code derives it). The holdout is checked
mechanically with core.freeze.assert_no_leak.

    python scripts/build_lora_dataset.py          # writes data/datasets/form4_lora_v1.jsonl
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import runlog  # noqa: E402
from core.config import settings  # noqa: E402
from core.db import init_db, rows, tx  # noqa: E402
from core.freeze import assert_no_leak, holdout_ids  # noqa: E402
from evalgate.scorer import config_label, model_input, prompt_hash, skip_fields  # noqa: E402
from serve.extract import apply_enrichment, extract_text, system_prompt  # noqa: E402
from verify.compare import counts, drop_fields  # noqa: E402
from verify.validate import validate  # noqa: E402

OUT = settings.data_dir / "datasets" / "form4_lora_v1.jsonl"
REPAIR_SHARE = 0.70
NUMERIC = ("shares", "price_per_share", "shares_owned_after")


def live_docs() -> list[dict]:
    held = holdout_ids()
    with tx() as conn:
        docs = rows(conn, "SELECT id, ext_id, text, gold_json, ambiguous_json FROM documents "
                          "WHERE split = 'live'")
    return [d for d in docs if d["id"] not in held and d["gold_json"]]


def answers(docs: list[dict]) -> dict[int, str]:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", settings.small_model)
    path = settings.data_dir / "live_cache" / f"{name}__{config_label()}__p{prompt_hash()}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    got = {}
    if path.exists():
        got = {r["doc_id"]: r["raw"] for r in map(json.loads, path.read_text(encoding="utf-8").splitlines())}
    todo = [d for d in docs if d["id"] not in got]
    print(f"  {len(got)} cached, {len(todo)} to run -> {path.name}", flush=True)
    lock, failed = threading.Lock(), []

    def run(d: dict) -> None:
        text = model_input(d)
        try:
            model_raw, ms, _ = extract_text(text, settings.small_model, enrich=False)
        except Exception as exc:  # noqa: BLE001 -- reported, never guessed
            with lock:
                failed.append((d["id"], str(exc)[:120]))
            return
        raw = apply_enrichment(model_raw, text)
        with lock:
            got[d["id"]] = raw
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"doc_id": d["id"], "raw": raw, "model_raw": model_raw,
                                     "latency_ms": ms}) + "\n")
            if len(got) % 25 == 0:
                print(f"  [{len(got)}/{len(docs)}]", flush=True)

    with ThreadPoolExecutor(max_workers=settings.llm_concurrency) as pool:
        list(pool.map(run, todo))
    if failed:
        raise RuntimeError(f"{len(failed)} live filings got no answer (e.g. {failed[0]}); re-run")
    return got


def target(gold: dict) -> str:
    """Gold in the shape the prompt asks for: no `value`, plain JSON numbers."""
    def num(v):
        if v is None:
            return None
        d = Decimal(str(v))
        return int(d) if d == d.to_integral_value() else float(d)
    out = {k: gold[k] for k in ("issuer_cik", "issuer_name", "issuer_ticker", "period_of_report",
                                "reporting_owners")}
    out["transactions"] = [{**{k: v for k, v in t.items() if k != "value"},
                            **{k: num(t.get(k)) for k in NUMERIC}} for t in gold["transactions"]]
    return json.dumps(out)


def _rank(doc: dict) -> str:
    return hashlib.sha256(doc["ext_id"].encode()).hexdigest()


def main() -> None:
    init_db()
    docs = live_docs()
    print(f"serving {len(docs)} live filings with the incumbent pipeline "
          f"({settings.small_model}, {config_label()}, p{prompt_hash()})")
    got = answers(docs)

    failures, passing = [], []
    for d in docs:
        gold = json.loads(d["gold_json"])
        pred = validate(got[d["id"]]).scorable
        skip = skip_fields(d.get("ambiguous_json"), gold)
        tp, n_pred, n_gold = counts(drop_fields(pred, skip), drop_fields(gold, skip))
        (passing if tp == n_pred == n_gold else failures).append(d)
    n_replay = round(len(failures) * (1 - REPAIR_SHARE) / REPAIR_SHARE)
    replay = sorted(passing, key=_rank)[:n_replay]
    chosen = failures + replay
    assert_no_leak([d["id"] for d in chosen])

    system = system_prompt()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for d in sorted(chosen, key=_rank):
            fh.write(json.dumps({"messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": model_input(d)},
                {"role": "assistant", "content": target(json.loads(d["gold_json"]))}]}) + "\n")
    print(f"  live filings: {len(failures)} wrong, {len(passing)} right")
    print(f"  dataset: {len(failures)} verified repairs + {len(replay)} replay = {len(chosen)} "
          f"-> {OUT.relative_to(settings.data_dir.parent)}")
    runlog.save_result({"failures": [d["ext_id"] for d in failures],
                        "replay": [d["ext_id"] for d in replay],
                        "n_live": len(docs), "dataset": str(OUT.name),
                        "dataset_sha": hashlib.sha256(OUT.read_bytes()).hexdigest()[:16]})


if __name__ == "__main__":
    with runlog.run("lora_dataset"):
        main()
