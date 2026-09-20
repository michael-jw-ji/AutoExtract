"""Score a local model on the frozen holdout and write a comparable snapshot.

Writes the same snapshot format scripts/selfheal_test.py produces, so the
existing `compare` command works across the local track unchanged:

    COMPACT_PROMPT=1 python scripts/score_local.py --label local-base
    COMPACT_PROMPT=1 python scripts/score_local.py --label local-lora --adapter models/lora-<stamp>
    python scripts/selfheal_test.py compare --before local-base --after local-lora

Base and adapter MUST be scored with the same COMPACT_PROMPT setting used to
build the training data, or the comparison is meaningless.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from core.config import ROOT, settings
from core.db import init_db
from core.freeze import current_hash
from evalgate.scorer import config_label, holdout_docs
from serve.extract import apply_enrichment, system_prompt
from verify.compare import counts, f1
from verify.validate import validate

SNAP_DIR = ROOT / "data" / "snapshots"


def save(args, base: str, records: list[dict]) -> dict:
    scored = [r["f1"] for r in records if r["f1"] is not None]
    suffix = f" + {Path(args.adapter).name}" if args.adapter else " (base)"
    payload = {
        "label": args.label,
        "model": f"{base}{suffix}",
        "eval_set_hash": current_hash(),
        "config": config_label(),
        "n_docs": len(records),
        "valid_rate": round(sum(r["valid"] for r in records) / len(records), 4)
        if records else 0.0,
        "mean_f1": round(sum(scored) / len(scored), 4) if scored else 0.0,
        "records": records,
    }
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    (SNAP_DIR / f"{args.label}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Score a local model on the holdout")
    ap.add_argument("--label", required=True)
    ap.add_argument("--adapter", default=None, help="LoRA dir; omit to score the base")
    ap.add_argument("--base", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-new-tokens", type=int, default=700)
    args = ap.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    init_db()
    docs = holdout_docs()
    if args.limit:
        docs = docs[: args.limit]

    base = args.base or settings.local_base_model
    tok = AutoTokenizer.from_pretrained(args.adapter or base)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    print(f"base    : {base}")
    print(f"adapter : {args.adapter or '(none — scoring the base model)'}")
    print(f"prompt  : {'compact' if settings.compact_prompt else 'full JSON Schema'} "
          f"({len(system_prompt())} chars)")
    print(f"enrich  : {'on' if settings.enrich else 'OFF'}")

    model = AutoModelForCausalLM.from_pretrained(
        base, torch_dtype=torch.bfloat16, device_map={"": 0},
    )
    if args.adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, args.adapter)
        model = model.merge_and_unload()   # faster inference, no adapter overhead
    model.eval()

    system = system_prompt()
    records = []
    print(f"\nscoring {len(docs)} holdout documents...")

    for i, doc in enumerate(docs, 1):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": doc["text"]},
        ]
        prompt = tok.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tok(prompt, return_tensors="pt").to(model.device)
        t0 = time.perf_counter()
        with torch.no_grad():
            out = model.generate(
                **inputs, max_new_tokens=args.max_new_tokens,
                do_sample=False, pad_token_id=tok.pad_token_id,
            )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        raw = tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)

        # Apply the SAME serve-time enrichment the hosted pipeline uses, or the
        # comparison is rigged: one side would get code-derived business rules
        # and the other would not.
        if settings.enrich:
            raw = apply_enrichment(raw)

        outcome = validate(raw)
        gold = json.loads(doc["gold_json"]) if doc["gold_json"] else {}
        records.append({
            "doc_id": doc["id"],
            "valid": outcome.valid,
            "signature": outcome.signature,
            "error_types": sorted({e["type"] for e in outcome.errors}),
            "f1": f1(*counts(outcome.scorable, gold)) if gold else None,
            "latency_ms": latency_ms,
        })
        if i % 10 == 0 or i == len(docs):
            ok = sum(r["valid"] for r in records)
            print(f"  [{i}/{len(docs)}]  valid {ok}  ({ok / i:.0%})", flush=True)
            # Checkpoint as we go. Generating 120 documents on a laptop GPU
            # takes long enough that losing the whole run to an interruption
            # is a real cost -- a partial snapshot is still comparable, since
            # `compare` intersects on doc_id.
            save(args, base, records)

    payload = save(args, base, records)
    print(f"\nvalid_rate {payload['valid_rate']:.1%}   mean_f1 {payload['mean_f1']:.4f}")
    print(f"wrote {SNAP_DIR / (args.label + '.json')}")


if __name__ == "__main__":
    main()
