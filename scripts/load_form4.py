"""Load fetched Form 4 filings into the database: text as input, XML as gold.

    python scripts/load_form4.py --dry-run     # validate everything, write nothing
    python scripts/load_form4.py               # insert (idempotent)

For each filing in data/form4/manifest.jsonl:
  text  = the SEC-rendered HTML flattened to plain text   (domains/form4_io.py)
  gold  = the filing's own XML, parsed, enriched and validated against the
          Form4 schema, then stored as JSON

A filing whose XML does not validate (a missing transaction code, say) is
DROPPED and reported with the reason. Gold is never repaired to fit.

Split: a stable hash of the accession number sends ~--holdout-frac of NEW
documents to 'holdout', the rest to 'live'. Already-loaded documents are never
reassigned, and once the holdout is frozen every new document goes to 'live',
so loading more filings later cannot disturb the frozen eval set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter

from pydantic import ValidationError

from core.config import settings
from core.db import init_db, insert, one, tx
from domains import get_domain
from domains.form4_io import html_to_text, parse_xml


def _bucket(accession: str) -> float:
    """Stable value in [0, 1) for an accession number."""
    return int(hashlib.sha256(accession.encode()).hexdigest()[:8], 16) / 2**32


def _tags(gold: dict, form: str) -> str:
    rows = gold["transactions"]
    tags = []
    if form != "4":
        tags.append("amendment")
    if len(gold["reporting_owners"]) > 1:
        tags.append("multi_owner")
    if not rows:
        tags.append("no_transactions")
    if len(rows) >= 10:
        tags.append("many_rows")
    if any(r["is_derivative"] for r in rows):
        tags.append("derivative")
    if any(r["price_per_share"] is None for r in rows):
        tags.append("blank_price")
    return ",".join(tags) or "plain"


def main() -> None:
    ap = argparse.ArgumentParser(description="Load Form 4 filings into the database")
    ap.add_argument("--holdout-frac", type=float, default=0.75,
                    help="share of new documents sent to the holdout split")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh-text", action="store_true",
                    help="re-render the text of already-loaded documents (after a "
                         "change to html_to_text). Refused once the holdout is "
                         "frozen: the eval inputs must not change under a frozen set.")
    args = ap.parse_args()

    root = settings.data_dir / "form4"
    manifest = root / "manifest.jsonl"
    if not manifest.exists():
        raise SystemExit("No manifest. Run scripts/fetch_form4.py first.")

    domain = get_domain("form4")
    init_db()
    with tx() as conn:
        frozen = one(conn, "SELECT 1 AS x FROM eval_freeze WHERE id = 1") is not None
    if frozen:
        print("holdout is frozen: every new document goes to 'live'")

    dropped: Counter[str] = Counter()
    dropped_ids: list[tuple[str, str]] = []
    loaded = skipped = 0
    splits: Counter[str] = Counter()

    # Hand labels (scripts/label_form4.py) override the XML gold and force the
    # filing into the holdout, unless the holdout is already frozen.
    labels = {p.stem: json.loads(p.read_text(encoding="utf-8"))
              for p in (root / "labels").glob("*.json")}
    relabelled = refreshed = 0

    seen: set[str] = set()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        meta = json.loads(line)
        acc = meta["accession"]
        if acc in seen:  # an older fetch could list one filing twice
            continue
        seen.add(acc)
        label = labels.get(acc)
        with tx() as conn:
            existing = one(conn, "SELECT id FROM documents WHERE ext_id = ?", (acc,))
        if existing and args.refresh_text and not frozen and not args.dry_run:
            body = html_to_text((root / "raw" / f"{acc}.html").read_bytes())
            with tx() as conn:
                cik = json.loads(one(conn, "SELECT gold_json FROM documents WHERE id = ?",
                                     (existing["id"],))["gold_json"])["issuer_cik"]
                conn.execute("UPDATE documents SET text = ? WHERE id = ?",
                             (f"Issuer CIK: {cik}\n{body}", existing["id"]))
            refreshed += 1
        if existing and (label is None or frozen):
            if label is not None:
                print(f"  ! {acc} has a label but the holdout is frozen; not applied")
            skipped += 1
            continue

        try:
            raw = parse_xml((root / "xml" / f"{acc}.xml").read_bytes())
            enriched, _ = domain.enrich(raw)
            gold = json.loads(domain.Schema.model_validate(enriched).model_dump_json())
            if label is not None:
                gold = json.loads(domain.Schema.model_validate(
                    domain.enrich(label["gold"])[0]).model_dump_json())
            # The issuer CIK is in EDGAR's filing metadata but not on the
            # rendered form, so without this line the model would have to
            # invent it. It is a copy task, not an inference.
            body = html_to_text((root / "raw" / f"{acc}.html").read_bytes())
            text = f"Issuer CIK: {gold['issuer_cik']}\n{body}"
        except ValidationError as exc:
            e = exc.errors()[0]
            reason = f"{e['type']}:{'.'.join('*' if isinstance(p, int) else str(p) for p in e['loc'])}"
            dropped[reason] += 1
            dropped_ids.append((acc, reason))
            continue
        except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
            dropped[f"error:{type(exc).__name__}"] += 1
            dropped_ids.append((acc, f"{type(exc).__name__}: {exc}"))
            continue

        if label is not None and not frozen:
            split = "holdout"
        else:
            split = "live" if frozen or _bucket(acc) >= args.holdout_frac else "holdout"
        tags = _tags(gold, meta.get("form", "4"))
        ambiguous = None
        if label is not None:
            tags += ",hand_labeled" + (",ambiguous" if label["status"] == "ambiguous" else "")
            ambiguous = json.dumps(label.get("ambiguous_fields", []))
        splits[split] += 1
        if existing:
            relabelled += 1
        else:
            loaded += 1
        if args.dry_run:
            continue
        with tx() as conn:
            if existing:
                conn.execute(
                    "UPDATE documents SET text=?, gold_json=?, split=?, messiness=?, "
                    "ambiguous_json=? WHERE id=?",
                    (text, json.dumps(gold), split, tags, ambiguous, existing["id"]))
            else:
                insert(conn, "documents", ext_id=acc, text=text,
                       gold_json=json.dumps(gold), split=split,
                       messiness=tags, ambiguous_json=ambiguous)

    print(f"{'would load' if args.dry_run else 'loaded'} {loaded}, "
          f"relabelled {relabelled}, already present {skipped}, "
          f"dropped {sum(dropped.values())}")
    if args.refresh_text:
        print(f"re-rendered text of {refreshed} existing documents"
              + (" (holdout frozen: refused)" if frozen else ""))
    print(f"split: {dict(splits)}")
    if dropped:
        print("DROPPED (gold failed the schema; not repaired):")
        for reason, n in dropped.most_common():
            print(f"  {n:4d}  {reason}")
        for acc, reason in dropped_ids:
            print(f"        {acc}  {reason}")


if __name__ == "__main__":
    main()
