"""Download recent Form 4 filings from EDGAR: rendered HTML plus official XML.

    SEC_USER_AGENT="Your Name you@example.com" python scripts/fetch_form4.py --n 200

For each filing this writes
    data/form4/raw/<accession>.html   the SEC-rendered view (model input)
    data/form4/xml/<accession>.xml    the filing's own XML (source of gold)
and appends one line per filing to data/form4/manifest.jsonl. It does NOT parse
gold or touch the database; that needs the Form 4 schema (domains/form4.py).

Re-running is safe: filings already on disk are skipped, so --n is a target for
the total. A filing that cannot be fetched after retries is DROPPED and counted
in the final summary, never replaced by anything synthetic.

SEC fair access: <= 10 requests/sec and a descriptive User-Agent. This stays
at ~8/sec across all workers combined.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import httpx

from core.config import settings

ARCHIVES = "https://www.sec.gov/Archives/edgar"
MAX_RPS = 8.0
ATTEMPTS = 5

#: Newest first. The rendered-view directory name tracks the XSL version SEC
#: used when it accepted the filing, and it is not listed in index.json.
XSL_DIRS = ("xslF345X05", "xslF345X06", "xslF345X04", "xslF345X03", "xslF345X02")

#: form.idx rows are fixed-width but widths drift; parse from the right instead:
#: <form type> <company name> <cik> <YYYYMMDD> <edgar/data/CIK/ACCESSION.txt>
_ROW = re.compile(
    r"^(?P<form>\S+)\s+(?P<company>.+?)\s+(?P<cik>\d+)\s+(?P<date>\d{8})\s+"
    r"(?P<path>edgar/data/\d+/(?P<acc>\d{10}-\d{2}-\d{6})\.txt)\s*$"
)


@dataclass(frozen=True)
class Filing:
    form: str
    company: str
    cik: str
    filed: str  # YYYYMMDD
    accession: str  # with dashes

    @property
    def folder(self) -> str:
        return f"{ARCHIVES}/data/{int(self.cik)}/{self.accession.replace('-', '')}"


def parse_daily_index(text: str, forms: set[str]) -> list[Filing]:
    out = []
    for line in text.splitlines():
        m = _ROW.match(line.rstrip())
        if m and m["form"] in forms:
            out.append(Filing(m["form"], m["company"].strip(), m["cik"],
                              m["date"], m["acc"]))
    return out


def pick_primary_xml(index: dict) -> str | None:
    """The filing's own ownership XML: a top-level .xml, not an XSL rendering."""
    items = index.get("directory", {}).get("item", [])
    names = [i["name"] for i in items
             if i.get("name", "").lower().endswith(".xml")
             and not i["name"].lower().startswith("xsl")]
    return names[0] if names else None


class Fetcher:
    """One shared client with a global rate limit and bounded retries."""

    def __init__(self, user_agent: str) -> None:
        self.http = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=30.0, follow_redirects=True,
        )
        self._lock = threading.Lock()
        self._next = 0.0

    def _wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(self._next, now)
            self._next = slot + 1.0 / MAX_RPS
        if slot > now:
            time.sleep(slot - now)

    def get(self, url: str) -> httpx.Response | None:
        """Response, or None for a definitive 404. Raises after ATTEMPTS."""
        for attempt in range(ATTEMPTS):
            self._wait()
            try:
                r = self.http.get(url)
            except httpx.TransportError:
                r = None
            if r is not None:
                if r.status_code == 404:
                    return None
                if r.status_code < 400:
                    return r
                if r.status_code not in (403, 429) and r.status_code < 500:
                    r.raise_for_status()
            time.sleep(2 ** attempt)
        raise RuntimeError(f"giving up on {url} after {ATTEMPTS} attempts")


def business_days_back(start: date):
    d = start
    while True:
        if d.weekday() < 5:
            yield d
        d -= timedelta(days=1)


def quarter(d: date) -> int:
    return (d.month - 1) // 3 + 1


def fetch_filing(f: Fetcher, filing: Filing, raw: Path, xml: Path) -> dict | str:
    """Save one filing. Returns a manifest row, or a string reason it was dropped."""
    idx = f.get(f"{filing.folder}/index.json")
    if idx is None:
        return "no index.json"
    name = pick_primary_xml(idx.json())
    if name is None:
        return "no primary xml"
    xml_resp = f.get(f"{filing.folder}/{name}")
    if xml_resp is None:
        return "xml missing"
    html_resp, used = None, None
    for xsl in XSL_DIRS:
        html_resp = f.get(f"{filing.folder}/{xsl}/{name}")
        if html_resp is not None:
            used = xsl
            break
    if html_resp is None:
        return "no rendered html"
    acc = filing.accession
    (raw / f"{acc}.html").write_bytes(html_resp.content)
    (xml / f"{acc}.xml").write_bytes(xml_resp.content)
    return {"accession": acc, "form": filing.form, "company": filing.company,
            "cik": filing.cik, "filed": filing.filed, "xml_name": name,
            "xsl": used, "url": filing.folder}


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch recent Form 4 filings from EDGAR")
    ap.add_argument("--n", type=int, default=200, help="target total on disk")
    ap.add_argument("--forms", default="4", help="comma list, e.g. 4,4/A")
    ap.add_argument("--per-day", type=int, default=20,
                    help="max filings sampled per business day, for spread")
    ap.add_argument("--max-days", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1337)
    args = ap.parse_args()

    ua = os.getenv("SEC_USER_AGENT", "").strip()
    if "@" not in ua:
        sys.exit('Set SEC_USER_AGENT to something like "Your Name you@example.com" '
                 "(SEC requires a contact in the User-Agent).")

    out = settings.data_dir / "form4"
    raw, xml = out / "raw", out / "xml"
    raw.mkdir(parents=True, exist_ok=True)
    xml.mkdir(parents=True, exist_ok=True)
    manifest = out / "manifest.jsonl"
    have = {p.stem for p in raw.glob("*.html")} & {p.stem for p in xml.glob("*.xml")}
    forms = {x.strip() for x in args.forms.split(",") if x.strip()}
    rng = random.Random(args.seed)
    f = Fetcher(ua)
    dropped: dict[str, int] = {}
    saved = 0

    print(f"{len(have)} filings already on disk; target {args.n}")
    days = business_days_back(date.today())
    for _ in range(args.max_days):
        if len(have) >= args.n:
            break
        d = next(days)
        url = (f"{ARCHIVES}/daily-index/{d.year}/QTR{quarter(d)}/"
               f"form.{d:%Y%m%d}.idx")
        resp = f.get(url)
        if resp is None:  # holiday, or not published yet
            continue
        # One filing is indexed once per filer (issuer and each owner), so
        # the same accession can appear several times in a day's index.
        todo = list({x.accession: x for x in parse_daily_index(resp.text, forms)
                     if x.accession not in have}.values())
        rng.shuffle(todo)
        for filing in todo[: args.per_day]:
            if len(have) >= args.n:
                break
            try:
                result = fetch_filing(f, filing, raw, xml)
            except Exception as exc:  # noqa: BLE001 -- recorded, never swallowed
                result = f"error: {exc}"
            if isinstance(result, str):
                dropped[result] = dropped.get(result, 0) + 1
                continue
            with manifest.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(result) + "\n")
            have.add(filing.accession)
            saved += 1
        print(f"  {d}: {len(have)}/{args.n}")

    print(f"\nsaved {saved} new, {len(have)} total in {out}")
    if dropped:
        print("DROPPED (not replaced with anything):")
        for why, n in sorted(dropped.items(), key=lambda kv: -kv[1]):
            print(f"  {n:4d}  {why}")
    if len(have) < args.n:
        print(f"short of target by {args.n - len(have)}; raise --max-days or --per-day")


if __name__ == "__main__":
    main()
