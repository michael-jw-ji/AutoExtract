"""Training dataset builder.

Three invariants, all enforced here rather than trusted:

  1. No frozen-holdout document may appear. Violations raise EvalLeakError.
  2. ~70% repairs / 30% replay, so the LoRA does not forget what already works.
  3. No single failure signature exceeds MAX_CLUSTER_SHARE of the repair half.
     Without this cap the model learns one field and regresses everywhere
     else -- which is exactly the failure the eval gate would then reject.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from core.config import settings
from core.db import rows, tx
from core.freeze import assert_no_leak
from serve.extract import system_prompt
from verify.validate import strip_fences


def _example(doc_text: str, target: dict) -> dict:
    return {
        "messages": [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": doc_text},
            {"role": "assistant", "content": json.dumps(target, separators=(",", ":"),
                                                        default=str)},
        ]
    }


def _capped(repairs: list[dict], cap_share: float) -> list[dict]:
    """Limit how much any single failure signature can dominate."""
    if not repairs:
        return []
    cap = max(1, int(len(repairs) * cap_share))
    by_sig: dict[str, list[dict]] = defaultdict(list)
    for r in repairs:
        by_sig[r["signature"]].append(r)

    kept: list[dict] = []
    for sig_rows in by_sig.values():
        random.shuffle(sig_rows)
        kept.extend(sig_rows[:cap])
    random.shuffle(kept)
    return kept


def build_dataset(
    *,
    max_examples: int = 800,
    seed: int = 1337,
    out_dir: Path | None = None,
) -> dict:
    """Write a chat-format JSONL training file. Returns a manifest."""
    random.seed(seed)
    out_dir = out_dir or settings.dataset_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    with tx() as conn:
        repairs = rows(
            conn,
            "SELECT r.doc_id, r.repaired_json, f.signature, d.text AS doc_text "
            "FROM repairs r "
            "JOIN failures f ON f.id = r.failure_id "
            "JOIN documents d ON d.id = r.doc_id "
            "WHERE r.verified = 1 AND d.split = 'live' "
            "GROUP BY r.doc_id",
        )
        replay = rows(
            conn,
            "SELECT e.doc_id, e.parsed_json, d.text AS doc_text "
            "FROM extractions e "
            "JOIN documents d ON d.id = e.doc_id "
            "WHERE e.valid = 1 AND d.split = 'live' AND e.parsed_json IS NOT NULL "
            "GROUP BY e.doc_id",
        )
        # Once business rules are in the schema the base model fails nearly
        # every document, so there may be few or no passing extractions to
        # replay. Top the pool up with gold for live documents that are not
        # being used as repairs. Replay exists to preserve general behaviour;
        # gold serves that purpose just as well as a lucky pass did.
        repair_ids = {r["doc_id"] for r in repairs}
        have = {r["doc_id"] for r in replay} | repair_ids
        if len(replay) < max_examples:
            replay += [
                {"doc_id": r["id"], "parsed_json": r["gold_json"],
                 "doc_text": r["text"]}
                for r in rows(
                    conn,
                    "SELECT id, text, gold_json FROM documents "
                    "WHERE split='live' AND gold_json IS NOT NULL",
                )
                if r["id"] not in have
            ]

    repairs = _capped(repairs, settings.max_cluster_share)

    n_repair = min(len(repairs), int(max_examples * settings.repair_fraction))
    n_replay = min(len(replay), max_examples - n_repair)
    random.shuffle(replay)
    chosen_repairs, chosen_replay = repairs[:n_repair], replay[:n_replay]

    # Invariant 1. Do this BEFORE writing anything to disk.
    assert_no_leak([r["doc_id"] for r in chosen_repairs + chosen_replay])

    examples = [
        _example(r["doc_text"], json.loads(r["repaired_json"])) for r in chosen_repairs
    ] + [
        _example(r["doc_text"], json.loads(r["parsed_json"])) for r in chosen_replay
    ]
    random.shuffle(examples)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"train-{stamp}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for ex in examples:
            fh.write(json.dumps(ex, ensure_ascii=False) + "\n")

    sig_counts: dict[str, int] = defaultdict(int)
    for r in chosen_repairs:
        sig_counts[r["signature"]] += 1

    # Surface a replay shortfall instead of silently shipping a skewed mix.
    #
    # Replay is meant to be examples the model ALREADY got right, so the LoRA
    # does not forget them. At a 100% failure rate there are none, and the
    # gold top-up can only cover live documents not already used as repairs --
    # because a VERIFIED repair matches gold exactly, so the same document as
    # both a repair and a replay example is literally the same training row.
    #
    # The honest reading: in this regime the 70/30 split cannot be met, and
    # what actually protects against forgetting is signature diversity (the
    # per-cluster cap above) plus total volume. To restore real replay,
    # reserve a slice of documents that are never served, or re-run once the
    # incumbent starts passing some of them.
    achieved = len(chosen_repairs) / max(1, len(examples))
    warning = None
    if achieved > settings.repair_fraction + 0.10:
        warning = (
            f"replay shortfall: {achieved:.0%} repairs vs target "
            f"{settings.repair_fraction:.0%} -- only {len(replay)} replay "
            f"candidates existed. Forgetting risk is higher than the policy "
            f"assumes; rely on the per-signature cap and watch the regression "
            f"count in scripts/selfheal_test.py compare."
        )
        print(f"  WARNING: {warning}")

    return {
        "warning": warning,
        "path": str(path),
        "size": len(examples),
        "repair_count": len(chosen_repairs),
        "replay_count": len(chosen_replay),
        "signatures": dict(sorted(sig_counts.items(), key=lambda kv: -kv[1])),
        "available_repairs": len(repairs),
        "available_replay": len(replay),
    }


RULE_FIELDS = ("vendor_id", "payment_terms")


def build_control_dataset(
    *, size: int, seed: int = 1337, out_dir: Path | None = None
) -> dict:
    """NEGATIVE CONTROL: format-only training, with the business rules removed.

    The obvious control -- "same volume, zero repairs" -- has no valid
    construction here. It would draw from already-passing extractions, and at
    a 100% failure rate there are none; any gold-derived substitute is
    IDENTICAL to the repairs, because a verified repair matches gold exactly.

    So this control keeps everything a repair teaches EXCEPT the rule
    knowledge: same documents, same JSON shape, correct extraction of every
    field that is actually printed on the page -- but vendor_id,
    payment_terms, and each line item's category carry the model's OWN
    original (wrong) values, taken from the failed extraction.

    Train a second LoRA on this and compare. Both models see the same volume,
    the same documents, and the same output format; only the rule information
    differs. If the repair-trained model wins specifically on vendor_id and
    category, that isolates the registry knowledge in the repairs as the cause
    -- rather than "any fine-tuning helps a weak model."
    """
    random.seed(seed)
    out_dir = out_dir or settings.dataset_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    with tx() as conn:
        candidates = rows(
            conn,
            "SELECT r.doc_id, r.repaired_json, d.text AS doc_text, "
            "       e.raw_output "
            "FROM repairs r "
            "JOIN failures f ON f.id = r.failure_id "
            "JOIN extractions e ON e.id = f.extraction_id "
            "JOIN documents d ON d.id = r.doc_id "
            "WHERE r.verified = 1 AND d.split = 'live' "
            "GROUP BY r.doc_id",
        )

    random.shuffle(candidates)
    examples, used, skipped = [], [], 0

    for row in candidates:
        if len(examples) >= size:
            break
        target = json.loads(row["repaired_json"])
        try:
            original = json.loads(strip_fences(row["raw_output"]))
        except json.JSONDecodeError:
            skipped += 1          # no usable original -> cannot strip the rules
            continue
        if not isinstance(original, dict):
            skipped += 1
            continue

        # Replace rule fields with whatever the model originally said.
        for field in RULE_FIELDS:
            if field in original:
                target[field] = original[field]
        orig_items = original.get("line_items")
        if isinstance(orig_items, list):
            by_desc = {
                str(i.get("description", "")).strip().lower(): i.get("category")
                for i in orig_items if isinstance(i, dict)
            }
            for item in target.get("line_items", []):
                cat = by_desc.get(str(item.get("description", "")).strip().lower())
                if cat is not None:
                    item["category"] = cat

        examples.append(_example(row["doc_text"], target))
        used.append(row["doc_id"])

    assert_no_leak(used)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"control-{stamp}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for ex in examples:
            fh.write(json.dumps(ex, ensure_ascii=False) + "\n")

    return {
        "path": str(path),
        "size": len(examples),
        "repair_count": 0,
        "replay_count": len(examples),
        "skipped_no_original": skipped,
        "available": len(candidates),
        "kind": "format-only control (rule fields carry the model's own wrong values)",
    }
