"""AI pre-review of holdout failures: suggest a theme, fix layer and root cause
for every failing filing, and flag the ones a human must decide.

    python scripts/prereview_failures.py            # baseline answers
    python scripts/prereview_failures.py --model X  # another model's answers

Writes data/form4/failure_review/<answers>/<accession>.json, which
scripts/failures_form4.py shows next to each filing.

The root causes encoded below were established by measuring the baseline, not
assumed (numbers from the 2026-10-11 baseline):
  * 137 of 139 wrong transaction codes equal that row's own A/D letter
  * 63 filings contain an invented row for an empty table
  * owner checkbox flags disagree with an explicit [X] on the page
  * 26 Table II rows have a null price where column 8 shows one
They are rules over the row-by-row diff from evalgate/failures.py, so the
same review can be re-run after each fix and compared.

A suggestion is "confirm" only when every theme in the filing is one of the
measured, mechanical causes. Anything about scoring policy, possibly-wrong
gold, or an unexplained pattern is "check": a person decides those.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3

from core.config import settings
from evalgate import scorer
from evalgate.failures import analyze

REVIEWER = "claude-opus-5-5 pre-review"

#: theme -> (fix layer, root cause). Order is priority for the primary theme.
THEMES: dict[str, tuple[str, str]] = {
    "runaway generation": ("model capability",
        "The model kept generating until the JSON broke, on a filing it otherwise reads fine."),
    "placeholder row for an empty table": ("prompt",
        "A table with no transactions still gets an invented row (e.g. 'Derivative Security', 0 shares); "
        "the prompt asks for rows from both tables and never says an empty table means none."),
    "code copied from the A/D column": ("prompt",
        "The transaction code is filled with the row's acquired/disposed letter (A or D) instead of the "
        "code column; both are single letters and A/D are valid codes, so validation cannot catch it."),
    "Table II row read as Table I": ("text rendering",
        "A derivative row is reported as non-derivative, usually with the UNDERLYING security as its title; "
        "nothing in the flattened text says which table a row came from."),
    "holdings row extracted": ("prompt",
        "A holdings line (no date, no code) is reported as a transaction."),
    "owner checkboxes misread": ("enrichment (code)",
        "Director/officer/10% flags disagree with explicit [X]/[ ] boxes on the page; those boxes are "
        "machine-readable, so code can derive them."),
    "price missed": ("prompt",
        "Price is null although the price column shows a value, very often $0 (grants, gifts, and column 8 "
        "of Table II); the prompt only says 'the Price column' and to use null when it is blank."),
    "code from the V column": ("prompt",
        "The code is taken from the 'V' (voluntary) column next to it instead of the code column."),
    "equity-swap code suffix": ("enrichment (code)",
        "The page prints a code as 'S/K' (sale with the equity-swap flag); the model copies it verbatim and "
        "enrichment does not strip the suffix, so it fails the code enum."),
    "number from another column": ("model capability",
        "A field holds a number that belongs to a different column of the same row (a column shift)."),
    "price where the page shows none": ("gold / eval set",
        "The model gives a price where the price column is blank or footnote-only; it may have read a "
        "footnote or the exercise price. Convention question, like the ambiguous labels."),
    "ownership text in a number field": ("model capability",
        "The nature-of-ownership text (e.g. 'By ... LP') lands in a numeric field: a column shift."),
    "conversion direction": ("prompt",
        "A/D is wrong on a conversion (code C), where direction depends on the table."),
    "Table II row duplicated as its underlying stock": ("text rendering",
        "A derivative transaction is reported twice: as itself, and again as a row in the underlying stock "
        "(e.g. phantom units plus an extra 'Common Stock' row with the underlying amount)."),
    "footnote-marked amount skipped": ("text rendering",
        "An amount printed with a glued footnote marker (e.g. 18.324(1)) is skipped for another number in "
        "the row; marked amounts are wrong 10% of the time vs 3% unmarked."),
    "decimals dropped": ("prompt",
        "A fractional share amount is truncated to a whole number (67,446.241 -> 67446)."),
    "amount misread": ("model capability",
        "A share amount is replaced by a different number with no marker or decimal explanation."),
    "row duplicated under another title": ("prompt",
        "A real transaction is reported a second time under a derivative-sounding title (Restricted Stock, "
        "RSUs, PSUs, Warrants), usually a name taken from a footnote."),
    "Table II holdings row extracted": ("prompt",
        "A Table II holdings line (exercise and expiry dates, no transaction) is reported as a transaction, "
        "with the exercisable date as its 'transaction date'."),
    "row invented from a footnote": ("prompt",
        "A row is built from a number that appears only in a footnote (e.g. 'Includes 471 unvested RSUs')."),
    "unexplained extra row": ("model capability",
        "Extra rows that match neither a placeholder nor a holdings line; read the page to see where they came from."),
    "missing row": ("model capability", "A transaction row on the page is missing from the answer."),
    "officer title from remarks": ("gold / eval set",
        "Gold says 'See Remarks' while the model gave the actual title from the Remarks; the model may be right."),
    "issuer name from memory": ("model capability",
        "The issuer name differs in CONTENT from the page (e.g. page 'SailPoint, Inc.', answer 'SailPoint "
        "Technologies Holdings, Inc.'): the model wrote a name it knows instead of the one printed."),
    "owner name misread": ("model capability", "A reporting owner's name differs in content from the page."),
    "officer title from another box": ("prompt",
        "officer_title holds text from another part of the form (the 'Other (specify)' line or the "
        "ownership column) for someone who is not an officer."),
    "security title misread": ("model capability",
        "A Table I row's security title names a different security than the page shows."),
    "title wording": ("gold / eval set",
        "A title differs only by wording such as a defined-term suffix ('(\"Common Stock\")'); the "
        "names policy does not cover titles, so this is a scoring decision."),
    "other": ("model capability", "Did not match any measured pattern; needs reading."),
}

#: Themes a human must decide (policy, possibly-wrong gold, or not yet explained).
NEEDS_HUMAN = {"runaway generation", "conversion direction", "amount misread",
               "unexplained extra row", "missing row", "officer title from remarks",
               "price where the page shows none", "title wording", "other"}

#: One scoring-policy decision covers all of these; it is asked once, not per filing.
POLICY = {"name formatting"}


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _shown(value) -> str:
    """How the page prints a gold amount: 67,446.241 / 6,397."""
    n = _num(value)
    if n is None:
        return str(value)
    return f"{int(n):,}" if n == int(n) else f"{n:,.6f}".rstrip("0")


def _amount_theme(pred, gold, text: str) -> str:
    p, g = _num(pred), _num(gold)
    if p is not None and g is not None and g != int(g) and p == int(g):
        return "decimals dropped"
    if re.search(re.escape(_shown(gold)) + r"\(\d+\)", text):
        return "footnote-marked amount skipped"
    return "amount misread"


def _in_table_ii(row: dict, text: str) -> bool:
    """Does an extra row's amount appear on a Table II line of the page?"""
    part = text.split("Table II", 1)[1] if "Table II" in text else ""
    forms = [_shown(row.get(k)) for k in ("shares", "shares_owned_after") if _num(row.get(k))]
    return any(f in line for line in part.splitlines()[1:] for f in forms if f not in {"0"})


def _is_placeholder_row(e: dict) -> bool:
    title = str(e.get("security_title") or "")
    generic = re.match(r"^\s*(non-?)?derivative\s+securit(y|ies)\s*$", title, re.I)
    return bool(generic) or str(e.get("shares")) in {"0", "None", ""}


def classify(a: dict, gold: dict, text: str = "") -> list[str]:
    found: list[str] = []

    def add(t: str) -> None:
        if t not in found:
            found.append(t)

    auto = a["themes"]
    if "unparseable output" in auto:
        return ["runaway generation"]
    if "placeholder row for an empty table" in auto:
        add("placeholder row for an empty table")
    if "holdings row extracted" in auto:
        add("holdings row extracted")
    if "extra row" in auto:
        # Judge each unexplained extra row on its own: one filing can mix kinds.
        gold_amounts = {_num(r.get(k)) for r in gold.get("transactions", [])
                        for k in ("shares", "shares_owned_after")} - {None}
        lines = text.splitlines()
        cut = next((i for i, l in enumerate(lines) if l.startswith("Explanation of Responses")),
                   len(lines))
        tables, notes = lines[:cut], lines[cut:]
        part2 = ""
        if "Table II" in text:
            part2 = text.split("Table II", 1)[1].split("Explanation of Responses", 1)[0]
        for r in a["tables"]["transactions"]:
            if r["kind"] != "extra":
                continue
            e = {c["field"]: c["pred"] for c in r["cells"]}
            amounts = {_num(e.get(k)) for k in ("shares", "shares_owned_after")} - {None, 0.0}
            forms = [_shown(x) for x in amounts]
            if any(isinstance(e.get(k), str) and any(ch.isalpha() for ch in e[k])
                   for k in ("shares", "shares_owned_after")):
                add("ownership text in a number field")
            if amounts & gold_amounts:
                add("Table II row duplicated as its underlying stock" if _in_table_ii(e, text)
                    else "row duplicated under another title")
            elif any(f in l for l in part2.splitlines() for f in forms):
                add("Table II holdings row extracted")
            elif any(f in l and "|" in l and not re.search(r"\d{2}/\d{2}/\d{4}", l)
                     for l in tables for f in forms):
                add("holdings row extracted")
            elif any(f in l for l in notes for f in forms):
                add("row invented from a footnote")
            elif not _is_placeholder_row(e):
                add("unexplained extra row")
    if "missing row" in auto:
        add("missing row")

    for row in a["tables"]["transactions"]:
        if row["kind"] == "extra":
            for c in row["cells"]:
                if c["field"] in {"shares", "shares_owned_after", "price_per_share"} and \
                        isinstance(c["pred"], str) and any(ch.isalpha() for ch in c["pred"]):
                    add("ownership text in a number field")
        if row["kind"] != "diff":
            continue
        cells = {c["field"]: c for c in row["cells"]}
        bad = {f for f, c in cells.items() if not c["ok"] and not c.get("ambiguous")}
        pcode = str(cells.get("code", {}).get("pred") or "")
        if "code" in bad and "/K" in pcode.upper():
            add("equity-swap code suffix")
        elif "code" in bad and pcode == cells.get("acquired_disposed", {}).get("pred"):
            add("code copied from the A/D column")
        elif "code" in bad and pcode.upper() == "V":
            add("code from the V column")
        elif "code" in bad:
            add("other")
        if "is_derivative" in bad:
            add("Table II row read as Table I")
        if "price_per_share" in bad:
            p = cells["price_per_share"]
            if p["pred"] is None:
                add("price missed")
            elif p["gold"] is None:
                add("price where the page shows none")
            elif "is_derivative" not in bad:
                add("number from another column")
        if "acquired_disposed" in bad and "is_derivative" not in bad:
            gold_code = cells.get("code", {}).get("gold")
            add("conversion direction" if gold_code == "C" else
                "code copied from the A/D column" if "code" in bad else "other")
        for f in ("shares", "shares_owned_after"):
            if f in bad and "is_derivative" not in bad:
                pv = cells[f]["pred"]
                if isinstance(pv, str) and any(ch.isalpha() for ch in pv):
                    add("ownership text in a number field")
                else:
                    add(_amount_theme(pv, cells[f]["gold"], text))
        if "security_title" in bad and "is_derivative" not in bad:
            pv = re.sub(r"\s*\(.*?\)\s*$", "", str(cells["security_title"]["pred"])).lower()
            gv = re.sub(r"\s*\(.*?\)\s*$", "", str(cells["security_title"]["gold"])).lower()
            add("title wording" if pv.strip() == gv.strip() else "security title misread")

    for row in a["tables"]["reporting_owners"]:
        if row["kind"] in ("extra", "missing"):
            add("other")
            continue
        for c in row["cells"]:
            if c["ok"] or c.get("ambiguous"):
                continue
            if c["field"].startswith("is_"):
                add("owner checkboxes misread")
            elif c["field"] == "officer_title":
                add("officer title from remarks" if "remark" in str(c["gold"]).lower()
                    else "officer title from another box" if c["gold"] is None
                    else "title wording")
            elif c["field"] == "name":
                add("owner name misread")

    for s in a["scalars"]:
        if not s["ok"]:
            add("issuer name from memory" if s["field"] == "issuer_name" else "other")

    order = list(THEMES)
    return sorted(found, key=order.index) or ["other"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Pre-review holdout failures")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    answers = scorer.cache_path(args.model or settings.small_model)
    out = settings.data_dir / "form4" / "failure_review" / answers.stem
    out.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(settings.db_path)
    docs = {r[0]: r for r in con.execute(
        "SELECT id, ext_id, text, gold_json, ambiguous_json FROM documents WHERE split='holdout'")}
    counts: dict[str, int] = {}
    verdicts = {"confirm": 0, "policy": 0, "check": 0}
    for line in answers.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        _, acc, text, gold, amb = docs[rec["doc_id"]]
        g = json.loads(gold)
        a = analyze(text, g, rec["raw"], json.loads(amb or "[]"))
        if a["themes"] == ["correct"]:
            continue
        themes = classify(a, g, text)
        human = sorted(set(themes) & NEEDS_HUMAN)
        verdict = "check" if human else "policy" if set(themes) & POLICY else "confirm"
        verdicts[verdict] += 1
        for t in themes:
            counts[t] = counts.get(t, 0) + 1
        review = {
            "accession": acc, "reviewer": REVIEWER, "verdict": verdict,
            "why_check": [f"{t}: {THEMES[t][1]}" for t in human],
            "primary": themes[0],
            "themes": [{"theme": t, "fix_layer": THEMES[t][0], "root_cause": THEMES[t][1]}
                       for t in themes],
            "valid": a["valid"], "f1": a["f1"],
        }
        (out / f"{acc}.json").write_text(json.dumps(review, indent=2), encoding="utf-8")

    print(f"pre-reviewed {sum(verdicts.values())} failing filings -> {out}")
    print(f"  confirm: {verdicts['confirm']}   policy (one decision covers all): {verdicts['policy']}   "
          f"check (needs a person): {verdicts['check']}")
    for t, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        tag = "  <- needs a person" if t in NEEDS_HUMAN else "  <- one policy decision" if t in POLICY else ""
        print(f"  {n:4d}  {t:46s} [{THEMES[t][0]}]{tag}")


if __name__ == "__main__":
    main()
