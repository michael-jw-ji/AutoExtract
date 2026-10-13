"""Validate the officer_title judge (evalgate/judge.py) before trusting it.

Items are (filing, person, candidate title, true verdict). The truth comes
from human-confirmed labels, never from a model:

  real        every distinct title any recorded answer gave for a person in
              the 44 hand-labelled filings, plus the label itself. True
              verdict: PASS iff it equals the label after normalising case,
              punctuation and spacing.
  remarks     persons whose box-5 title only says "See Remarks" (1 labelled +
              6 more holdout filings): the title stated in the Remarks
              (REMARKS_TITLES, read off the page; listed for confirmation),
              every recorded candidate, the pointer itself and null.
  paraphrase  the label rewritten with standard abbreviations (CEO, EVP...):
              synthetic, true PASS. Exact matching in code fails these.
  distractor  another labelled person's title, and null for a titled
              person: synthetic, true FAIL.

Reports true positive rate (PASS when truth is PASS) and true negative rate,
overall and per kind, against code's exact match on the same items. Calls the
large model (Modal H100); verdicts are cached in data/judge_cache/.

    python scripts/judge_eval.py
"""

from __future__ import annotations

import glob
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import runlog  # noqa: E402
from core.config import settings  # noqa: E402
from core.db import init_db, rows, tx  # noqa: E402
from evalgate.judge import judge_title  # noqa: E402
from evalgate.scorer import holdout_docs, model_input  # noqa: E402
from verify.compare import pair_rows  # noqa: E402
from verify.validate import validate  # noqa: E402

#: Titles stated in the Remarks for persons whose box 5 says "See Remarks".
#: Read off the page; doc 471 is hand-labelled (its title marked ambiguous),
#: the other six are holdout filings outside the labelled set.
REMARKS_TITLES = {
    (9, "Graff Jeremy R."): "President and Chief Development Officer",
    (10, "MARTINEZ MARIA D."): "SVP, Chief Human Resources Officer",
    (16, "Nursey Michael E."): "SEVP, Chief Domestic Banking Officer",
    (112, "Galloway Jesse Charles"): "Executive Vice President and General Counsel",
    (179, "McCulloch Kevin"): "President and Chief Operating Officer",
    (197, "Pelant John Edward"): ("Executive Vice President, Traveler Experience and "
                                  "US Defense and Government"),
    (471, "Kilgore Joshua Allen"): "Executive Chairman",
}

_ABBREV = [("Chief Executive Officer", "CEO"), ("Chief Financial Officer", "CFO"),
           ("Chief Operating Officer", "COO"), ("Chief Accounting Officer", "CAO"),
           ("Chief Technology Officer", "CTO"), ("Executive Vice President", "EVP"),
           ("Senior Vice President", "SVP"), ("Vice President", "VP"), (" and ", " & ")]


def norm(t: str | None) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (t or "").lower()).split())


def paraphrase(t: str) -> str | None:
    out = t
    for long, short in _ABBREV:
        out = re.sub(re.escape(long), short, out, flags=re.I)
    return out if out != t else None


def build() -> list[dict]:
    docs = {d["id"]: d for d in holdout_docs()}
    with tx() as conn:
        labelled = {r["id"] for r in rows(conn, "SELECT id, messiness FROM documents")
                    if "hand_labeled" in (r["messiness"] or "")}
    # Every title any recorded answer gave, per (doc, gold owner index).
    seen: dict[tuple[int, int], set] = defaultdict(set)
    for f in sorted(glob.glob(str(settings.data_dir / "eval_cache" / "*.jsonl"))):
        for rec in map(json.loads, Path(f).read_text(encoding="utf-8").splitlines()):
            if rec["doc_id"] not in docs:
                continue
            gold = json.loads(docs[rec["doc_id"]]["gold_json"])["reporting_owners"]
            pred = validate(rec["raw"]).scorable
            pred = (pred.get("reporting_owners") if isinstance(pred, dict) else None) or []
            for p, g in pair_rows("reporting_owners", pred, gold):
                if p is not None and g is not None:
                    seen[rec["doc_id"], g].add(pred[p].get("officer_title") or None)

    items, titles = [], []
    for doc_id in sorted(labelled | {d for d, _ in REMARKS_TITLES}):
        for g, owner in enumerate(json.loads(docs[doc_id]["gold_json"])["reporting_owners"]):
            name, label = owner["name"], owner.get("officer_title") or None
            base = {"doc_id": doc_id, "name": name}
            if (doc_id, name) in REMARKS_TITLES:
                truth = REMARKS_TITLES[doc_id, name]
                cands = seen[doc_id, g] | {truth, label, None}
                items += [{**base, "kind": "remarks", "candidate": c,
                           "truth": "PASS" if norm(c) == norm(truth) else "FAIL"} for c in cands]
                continue
            if doc_id not in labelled:
                continue
            if label:
                titles.append(label)
            items += [{**base, "kind": "real", "candidate": c,
                       "truth": "PASS" if norm(c) == norm(label) else "FAIL"}
                      for c in seen[doc_id, g] | {label}]
            if label and (alt := paraphrase(label)):
                items.append({**base, "kind": "paraphrase", "candidate": alt, "truth": "PASS"})
            if label:
                items.append({**base, "kind": "distractor", "candidate": None, "truth": "FAIL"})
    # Another person's title, chosen deterministically, for every labelled person.
    others = sorted(set(titles), key=norm)
    labelled_people = {(i["doc_id"], i["name"]) for i in items if i["kind"] == "real"}
    for n, (doc_id, name) in enumerate(sorted(labelled_people)):
        label = next(i["candidate"] for i in items if (i["doc_id"], i["name"]) == (doc_id, name)
                     and i["kind"] == "real" and i["truth"] == "PASS")
        pick = [t for t in others if norm(t) != norm(label)]
        items.append({"doc_id": doc_id, "name": name, "kind": "distractor",
                      "candidate": pick[n % len(pick)], "truth": "FAIL"})
    return items


def rates(items: list[dict], key: str) -> dict:
    tp = sum(i[key] == "PASS" and i["truth"] == "PASS" for i in items)
    tn = sum(i[key] == "FAIL" and i["truth"] == "FAIL" for i in items)
    pos = sum(i["truth"] == "PASS" for i in items)
    neg = len(items) - pos
    return {"n": len(items), "pos": pos, "neg": neg,
            "tpr": round(tp / pos, 3) if pos else None, "tnr": round(tn / neg, 3) if neg else None}


def main() -> None:
    init_db()
    items = build()
    docs = {d["id"]: d for d in holdout_docs()}
    print(f"{len(items)} items: {dict(Counter(i['kind'] for i in items))}; "
          f"truth {dict(Counter(i['truth'] for i in items))}")
    print(f"judging with {settings.large_model}...")

    def gold_title(it: dict) -> str | None:
        gold = json.loads(docs[it["doc_id"]]["gold_json"])["reporting_owners"]
        return next(o.get("officer_title") for o in gold if o["name"] == it["name"])

    def run(it: dict) -> None:
        r = judge_title(model_input(docs[it["doc_id"]]), it["name"], it["candidate"])
        it["judge"], it["reason"] = r["verdict"], r.get("reason", "")
        # What code can do: exact match with the recorded gold after
        # normalising case, punctuation and spacing ("See Remarks" included).
        it["code"] = "PASS" if norm(it["candidate"]) == norm(gold_title(it)) else "FAIL"

    with ThreadPoolExecutor(max_workers=settings.large_llm_concurrency) as pool:
        list(pool.map(run, items))

    summary = {"overall": {"judge": rates(items, "judge"), "code": rates(items, "code")}}
    for kind in ("real", "remarks", "paraphrase", "distractor"):
        sub = [i for i in items if i["kind"] == kind]
        summary[kind] = {"judge": rates(sub, "judge"), "code": rates(sub, "code")}
    print(f"{'':12s} {'n':>4s} {'pos':>4s} {'neg':>4s}   judge TPR  TNR    code TPR  TNR")
    for k, v in summary.items():
        j, c = v["judge"], v["code"]
        print(f"{k:12s} {j['n']:4d} {j['pos']:4d} {j['neg']:4d}   {j['tpr']!s:>8s} {j['tnr']!s:>5s}"
              f"   {c['tpr']!s:>8s} {c['tnr']!s:>5s}")
    wrong = [i for i in items if i["judge"] != i["truth"]]
    print(f"\njudge disagrees with the label on {len(wrong)} items:")
    for i in wrong:
        print(f"  doc {i['doc_id']} {i['name']!r} [{i['kind']}] candidate={i['candidate']!r} "
              f"truth={i['truth']} judge={i['judge']}: {i['reason']}")
    runlog.save_result({"summary": summary, "items": items,
                        "remarks_titles": {f"{d}|{n}": t for (d, n), t in REMARKS_TITLES.items()}})


if __name__ == "__main__":
    with runlog.run("judge_eval"):
        main()
