"""CI eval: replay recorded model answers through the CURRENT code. Free, offline.

The model is not called in CI. A fixture (data/ci/form4_ci.jsonl, tracked)
holds 30 holdout filings: raw HTML, gold, and the 7B's answer exactly as the
model wrote it, BEFORE post-processing. With the model held fixed, any change
in score comes from the code under review, so the bar can be strict:

  1. input check   re-render each filing and rebuild the prompt; if the text
                   the model would read changed, the recorded answers no
                   longer apply -> FAIL, "needs a live eval".
  2. replay        post-process each recorded answer with the current enrich()
                   and score it with the scorer's own code (score_answers).
  3. compare       against data/ci/baseline.json: FAIL if any field drops more
                   than --tolerance points (default 0.5), or valid_rate drops.

    python scripts/ci_eval.py                     # check (what CI runs)
    python scripts/ci_eval.py --update-baseline   # accept the current scores
    python scripts/ci_eval.py --build             # record the fixture (calls the 7B; needs the DB, raw HTML, Modal)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domains import get_domain  # noqa: E402
from domains.form4_io import html_to_text  # noqa: E402
from evalgate.scorer import score_answers  # noqa: E402
from serve.extract import apply_enrichment, system_prompt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "ci" / "form4_ci.jsonl"
BASELINE = ROOT / "data" / "ci" / "baseline.json"

#: How many filings to take per difficulty tag, in priority order. Rare tags
#: first so each is represented; 30 in all.
PICK = [("multi_owner", 5), ("amendment", 3), ("no_transactions", 2), ("many_rows", 2),
        ("ambiguous", 2), ("blank_price", 4), ("derivative", 6), ("hand_labeled", 3),
        ("plain", 3)]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _input(rec: dict) -> str:
    """What the model reads under the current code: the same as
    Form4Domain.model_input, from the fixture's HTML instead of the disk."""
    return f"{rec['cik_line']}\n{html_to_text(rec['html'], get_domain().render_version)}"


def build() -> None:
    from core.config import settings
    from core.db import init_db
    from evalgate.scorer import holdout_docs, model_input, skip_fields  # noqa: F401
    from core.db import tx, rows
    from serve.extract import extract_text

    init_db()
    docs = {d["id"]: d for d in holdout_docs()}
    with tx() as conn:
        tags = {r["id"]: (r["messiness"] or "").split(",")
                for r in rows(conn, "SELECT id, messiness FROM documents")}
    order = sorted(docs, key=lambda i: _sha(docs[i]["ext_id"]))   # fixed, not cherry-picked
    chosen: list[int] = []
    for tag, k in PICK:
        chosen += [i for i in order if tag in tags[i] and i not in chosen][:k]
    print(f"recording {len(chosen)} filings from {settings.small_model}...")

    out = []
    for n, i in enumerate(chosen, 1):
        d = docs[i]
        text = model_input(d)
        html = (settings.data_dir / "form4" / "raw" / f"{d['ext_id']}.html").read_text(
            encoding="utf-8", errors="replace")
        model_raw, ms, used = extract_text(text, settings.small_model, enrich=False)
        rec = {"doc_id": i, "accession": d["ext_id"], "tags": tags[i],
               "cik_line": d["text"].split("\n", 1)[0], "html": html,
               "input_sha": _sha(text), "gold_json": d["gold_json"],
               "ambiguous_json": d["ambiguous_json"], "model": used, "model_raw": model_raw}
        assert _input(rec) == text, "fixture render differs from the scorer's input"
        out.append(rec)
        print(f"  [{n}/{len(chosen)}] {d['ext_id']} {ms / 1000:.1f}s", flush=True)

    meta = {"prompt_sha": _sha(system_prompt()), "render_version": get_domain().render_version,
            "enrich_version": get_domain().enrich_version, "model": settings.small_model}
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text("\n".join([json.dumps({"meta": meta})] + [json.dumps(r) for r in out])
                       + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE.relative_to(ROOT)}")


def load() -> tuple[dict, list[dict]]:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    return json.loads(lines[0])["meta"], [json.loads(x) for x in lines[1:]]


def replay(records: list[dict]) -> dict:
    answers = {r["doc_id"]: apply_enrichment(r["model_raw"], _input(r)) for r in records}
    return score_answers([{**r, "id": r["doc_id"]} for r in records], answers)


def check(tolerance: float) -> int:
    meta, records = load()
    if _sha(system_prompt()) != meta["prompt_sha"]:
        print("FAIL: the system prompt changed, so the recorded answers no longer apply.\n"
              "      Run a live eval (run_cycle.py --stage candidate), then "
              "`ci_eval.py --build` to re-record.")
        return 1
    changed = [r["accession"] for r in records if _sha(_input(r)) != r["input_sha"]]
    if changed:
        print(f"FAIL: the rendered input changed for {len(changed)} of {len(records)} filings "
              f"(e.g. {changed[0]}), so the recorded answers no longer apply.\n"
              "      Rendering is part of the model's input: run a live eval, then "
              "`ci_eval.py --build`.")
        return 1

    now = replay(records)
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    print(f"replayed {len(records)} recorded answers (no model calls)")
    print(f"  field_f1   {base['field_f1']:.4f} -> {now['field_f1']:.4f}")
    print(f"  valid_rate {base['valid_rate']:.4f} -> {now['valid_rate']:.4f}")
    failures = []
    for field, was in sorted(base["per_field"].items()):
        is_ = now["per_field"].get(field, 0.0)
        delta = (is_ - was) * 100
        mark = ""
        if delta < -tolerance:
            failures.append(f"{field} {delta:+.1f} pts")
            mark = "  <-- REGRESSION"
        if delta or mark:
            print(f"  {field:42s} {was:.4f} -> {is_:.4f} {delta:+.1f}{mark}")
    if now["valid_rate"] < base["valid_rate"]:
        failures.append(f"valid_rate {(now['valid_rate'] - base['valid_rate']) * 100:+.1f} pts")
    if failures:
        print(f"FAIL: {'; '.join(failures)} (tolerance {tolerance} pts)")
        return 1
    print("PASS: no field dropped more than", tolerance, "pts")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--tolerance", type=float, default=0.5)
    args = ap.parse_args()
    if args.build:
        build()
        args.update_baseline = True
    if args.update_baseline:
        _, records = load()
        BASELINE.write_text(json.dumps(replay(records), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {BASELINE.relative_to(ROOT)}")
        return
    sys.exit(check(args.tolerance))


if __name__ == "__main__":
    main()
