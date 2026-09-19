"""Does it actually self-heal? Evidence, not vibes.

"field_f1 went up" is weak evidence. A model can improve for reasons that have
nothing to do with the repairs it was trained on. These tests isolate the
claim:

  snapshot  record per-document outcomes for one model on the frozen holdout
  compare   diff two snapshots -- which failure signatures HEALED, which
            PERSISTED, and which REGRESSED (the forgetting check)
  control   build a same-size dataset containing ZERO repairs, for the
            negative-control training run

The decisive experiment is the control. Train one LoRA on repairs and one on
the same number of already-passing examples. If the repair-trained model does
not beat the control, the gain came from fine-tuning in general, not from the
repairs, and the self-healing claim is unsupported.

    python scripts/selfheal_test.py snapshot --label before
    # ... run the loop, train, deploy ...
    python scripts/selfheal_test.py snapshot --label after --model <adapter_ref>
    python scripts/selfheal_test.py compare --before before --after after
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from core.config import settings
from core.db import init_db
from core.freeze import current_hash
from evalgate.scorer import holdout_docs
from serve.extract import extract_text
from train.dataset import build_control_dataset
from verify.compare import counts, f1
from verify.validate import validate

SNAP_DIR = settings.data_dir / "snapshots"


def snapshot(label: str, model_ref: str | None, workers: int = 10) -> dict:
    docs = holdout_docs()
    if not docs:
        raise SystemExit("Holdout is empty. Generate documents and freeze first.")

    print(f"snapshotting {label} on {len(docs)} holdout documents...")

    def one(doc: dict) -> dict:
        raw, latency_ms, _ = extract_text(doc["text"], model_ref)
        outcome = validate(raw)
        gold = json.loads(doc["gold_json"]) if doc["gold_json"] else {}
        return {
            "doc_id": doc["id"],
            "valid": outcome.valid,
            "signature": outcome.signature,
            "error_types": sorted({e["type"] for e in outcome.errors}),
            "f1": f1(*counts(outcome.scorable, gold)) if gold else None,
            "latency_ms": latency_ms,
        }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(one, docs))
    print(f"  {sum(r['valid'] for r in records)}/{len(records)} valid")

    valid_rate = sum(r["valid"] for r in records) / len(records)
    scored = [r["f1"] for r in records if r["f1"] is not None]
    payload = {
        "label": label,
        "model": model_ref or settings.small_model,
        "eval_set_hash": current_hash(),
        "n_docs": len(records),
        "valid_rate": round(valid_rate, 4),
        "mean_f1": round(sum(scored) / len(scored), 4) if scored else 0.0,
        "records": records,
    }

    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAP_DIR / f"{label}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nvalid_rate {payload['valid_rate']:.1%}   mean_f1 {payload['mean_f1']:.4f}")
    print(f"wrote {path}")
    return payload


def load(label: str) -> dict:
    path = SNAP_DIR / f"{label}.json"
    if not path.exists():
        raise SystemExit(f"no snapshot {label!r} -- run `snapshot --label {label}` first")
    return json.loads(path.read_text(encoding="utf-8"))


def compare(before_label: str, after_label: str) -> dict:
    before, after = load(before_label), load(after_label)

    if before["eval_set_hash"] != after["eval_set_hash"]:
        raise SystemExit(
            f"eval sets differ ({before['eval_set_hash']} vs "
            f"{after['eval_set_hash']}). These snapshots are not comparable."
        )

    b = {r["doc_id"]: r for r in before["records"]}
    a = {r["doc_id"]: r for r in after["records"]}
    shared = sorted(set(b) & set(a))

    healed, regressed, still_failing, still_passing = [], [], [], []
    healed_types: Counter[str] = Counter()
    regressed_types: Counter[str] = Counter()

    for doc_id in shared:
        was, now = b[doc_id], a[doc_id]
        if not was["valid"] and now["valid"]:
            healed.append(doc_id)
            healed_types.update(was["error_types"])
        elif was["valid"] and not now["valid"]:
            regressed.append(doc_id)
            regressed_types.update(now["error_types"])
        elif not was["valid"]:
            still_failing.append(doc_id)
            healed_types.update([])
        else:
            still_passing.append(doc_id)

    vr_delta = (after["valid_rate"] - before["valid_rate"]) * 100
    f1_delta = (after["mean_f1"] - before["mean_f1"]) * 100

    print("=" * 66)
    print(f"SELF-HEAL COMPARISON   {before_label} -> {after_label}")
    print(f"  {before['model']}")
    print(f"  {after['model']}")
    print("=" * 66)
    print(f"  valid_rate   {before['valid_rate']:.1%} -> {after['valid_rate']:.1%}"
          f"   ({vr_delta:+.2f}pp)")
    print(f"  mean_f1      {before['mean_f1']:.4f} -> {after['mean_f1']:.4f}"
          f"   ({f1_delta:+.2f}pp)")
    print(f"\n  healed          {len(healed):>4}   (failed before, pass now)")
    print(f"  regressed       {len(regressed):>4}   (passed before, fail now)")
    print(f"  still failing   {len(still_failing):>4}")
    print(f"  still passing   {len(still_passing):>4}")

    if healed_types:
        print("\n  error types healed:")
        for t, n in healed_types.most_common(10):
            print(f"    {n:>4}x  {t}")
    if regressed_types:
        print("\n  error types INTRODUCED (forgetting check):")
        for t, n in regressed_types.most_common(10):
            print(f"    {n:>4}x  {t}")

    print("\n" + "-" * 66)
    if not shared:
        verdict = "INCONCLUSIVE -- snapshots share no documents"
    elif len(healed) == 0:
        verdict = "NO HEALING -- nothing that failed before passes now"
    elif len(regressed) > len(healed):
        verdict = (
            f"NET NEGATIVE -- healed {len(healed)} but broke {len(regressed)}. "
            "Raise the replay share or lower the learning rate."
        )
    elif len(regressed) > 0:
        verdict = (
            f"HEALED {len(healed)}, but {len(regressed)} regressed. Replay is "
            "not fully protecting prior behaviour."
        )
    else:
        verdict = f"CLEAN HEAL -- {len(healed)} fixed, zero regressions."
    print(verdict)
    print("-" * 66)
    print(
        "\nReminder: this shows the candidate improved. It does NOT yet show the\n"
        "REPAIRS caused it. For that, train a control LoRA on a repair-free\n"
        "dataset of the same size and compare against it:\n"
        "  python scripts/selfheal_test.py control --size <n>"
    )

    return {
        "valid_rate_delta_pp": round(vr_delta, 3),
        "mean_f1_delta_pp": round(f1_delta, 3),
        "healed": healed,
        "regressed": regressed,
        "verdict": verdict,
    }


def control(size: int) -> dict:
    info = build_control_dataset(size=size)
    print(f"control dataset: {info['path']}")
    print(f"  {info['size']} examples, 0 repairs "
          f"({info['available_passing']} passing examples available)")
    print(
        "\nTrain this exactly like the real one, then:\n"
        "  python scripts/selfheal_test.py snapshot --label control --model <ref>\n"
        "  python scripts/selfheal_test.py compare --before before --after control\n"
        "\nThe repair-trained model must beat the control. If it does not, the\n"
        "gain is from fine-tuning in general, not from the repairs."
    )
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description="Prove (or disprove) self-healing")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="record holdout outcomes for one model")
    s.add_argument("--label", required=True)
    s.add_argument("--model", default=None, help="model id or adapter ref")
    s.add_argument("--workers", type=int, default=10, help="parallel API calls")

    c = sub.add_parser("compare", help="diff two snapshots")
    c.add_argument("--before", required=True)
    c.add_argument("--after", required=True)

    k = sub.add_parser("control", help="build a repair-free control dataset")
    k.add_argument("--size", type=int, default=200)

    args = ap.parse_args()
    init_db()

    if args.cmd == "snapshot":
        snapshot(args.label, args.model, args.workers)
    elif args.cmd == "compare":
        compare(args.before, args.after)
    elif args.cmd == "control":
        control(args.size)


if __name__ == "__main__":
    main()
