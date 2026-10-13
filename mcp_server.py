"""MCP server: Form 4 extraction and the eval record, as tools for AI agents.

Tools
  extract_form4(url_or_text)  extract one SEC Form 4 with the served pipeline
                              (render v3 -> Qwen2.5-7B -> post-processing ->
                              schema + rules). Given an EDGAR URL it also
                              fetches the filing's own XML and reports how the
                              extraction compares to it, field by field.
  get_eval_summary()          the incumbent's scores on the frozen holdout, the
                              gate's decisions, and cost/latency.

Run (stdio, for Claude Desktop and other MCP clients):
    python mcp_server.py
Needs LLM_BASE_URL / LLM_API_KEY (the 7B) and, for URLs, SEC_USER_AGENT in .env.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from core.config import settings  # noqa: E402
from domains import get_domain  # noqa: E402
from domains.form4_io import html_to_text, parse_xml  # noqa: E402
from serve.client import chat  # noqa: E402
from serve.extract import apply_enrichment, system_prompt  # noqa: E402
from verify.compare import counts, f1, field_hits  # noqa: E402
from verify.validate import validate  # noqa: E402

# httpx logs every request URL at INFO (Modal call ids, SEC paths): noise in
# the client's server log, so only warnings and up.
logging.getLogger("httpx").setLevel(logging.WARNING)

server = MCPServer(
    name="autoextract-form4",
    instructions=("Extracts SEC Form 4 insider-transaction filings into JSON with a small "
                  "model and code checks, and reports the pipeline's measured accuracy. "
                  "Pass an EDGAR filing URL to also get a check against the filing's XML."),
)

_FOLDER = re.compile(r"https?://(?:www\.)?sec\.gov/Archives/edgar/data/(\d+)/(\d{18})", re.I)
_ACCESSION = re.compile(r"(\d{10})-?(\d{2})-?(\d{6})")


def _fetch_filing(url: str) -> tuple[str, dict]:
    """(rendered page text with the issuer CIK line, gold parsed from the XML)."""
    from fetch_form4 import XSL_DIRS, Fetcher, pick_primary_xml

    m = _FOLDER.search(url)
    if not m:
        raise ValueError("expected an EDGAR filing URL like "
                         "https://www.sec.gov/Archives/edgar/data/<cik>/<accession>/...")
    agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not agent:
        raise ValueError("SEC_USER_AGENT is not set in .env (SEC requires a contact email)")
    folder = f"https://www.sec.gov/Archives/edgar/data/{m.group(1)}/{m.group(2)}"
    f = Fetcher(agent)
    idx = f.get(f"{folder}/index.json")
    name = pick_primary_xml(idx.json()) if idx is not None else None
    if not name:
        raise ValueError(f"no Form 4 XML found in {folder}")
    xml = f.get(f"{folder}/{name}")
    html = next((r for r in (f.get(f"{folder}/{x}/{name}") for x in XSL_DIRS) if r), None)
    if xml is None or html is None:
        raise ValueError(f"could not fetch the filing's XML and rendered page from {folder}")
    dom = get_domain()
    gold = parse_xml(xml.content)
    # Same steps as the loader: the XML's values, normalised the same way.
    gold = json.loads(dom.Schema.model_validate(dom.enrich(gold)[0]).model_dump_json())
    text = html_to_text(html.content, dom.render_version)
    return f"Issuer CIK: {gold['issuer_cik']}\n{text}", gold


def _compare(pred: dict, gold: dict) -> dict:
    tp, n_pred, n_gold = counts(pred, gold)
    wrong = sorted(k for k, (hit, total) in field_hits(pred, gold).items() if hit < total)
    return {"field_f1": round(f1(tp, n_pred, n_gold), 4), "fields_with_differences": wrong,
            "xml_transactions": len(gold.get("transactions", [])),
            "extracted_transactions": len(pred.get("transactions") or [])}


@server.tool()
def extract_form4(url_or_text: str) -> dict:
    """Extract an SEC Form 4 filing into structured JSON.

    Pass an EDGAR filing URL (any URL inside the filing's folder, e.g.
    https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/...)
    to fetch the filing and also compare the result with the filing's own
    XML; or pass the filing's HTML or plain text. Returns the extraction,
    whether it passed the schema and rule checks, and (for URLs) the
    field-level comparison with the XML.
    """
    # stdio carries the MCP protocol on stdout; anything printed goes to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        gold = None
        source = url_or_text.strip()
        if source.lower().startswith("http"):
            text, gold = _fetch_filing(source)
        elif "<html" in source[:2000].lower() or "<table" in source[:5000].lower():
            text = html_to_text(source, get_domain().render_version)
        else:
            text = source
        model_raw, latency_ms = chat(settings.small_model, system_prompt(), text, max_tokens=8192)
        raw = apply_enrichment(model_raw, text)
        outcome = validate(raw)
        result = {
            "extraction": outcome.parsed if outcome.valid else outcome.scorable,
            "valid": outcome.valid,
            "errors": [{"field": ".".join(map(str, e.get("loc", ()))), "problem": e.get("msg")}
                       for e in outcome.errors][:10],
            "model": settings.small_model,
            "latency_ms": latency_ms,
            "pipeline": {"render": get_domain().render_version,
                         "enrich": get_domain().enrich_version},
        }
        if gold is not None and isinstance(result["extraction"], dict):
            result["check_against_filing_xml"] = _compare(result["extraction"], gold)
        elif gold is None:
            result["note"] = ("No URL, so no check against the filing's XML. Without the "
                              "'Issuer CIK:' line EDGAR adds, issuer_cik may be missing.")
        return result


def _summary_from_db() -> dict | None:
    from core.db import incumbent, rows, tx
    from evalgate.scorer import latest_eval
    if not settings.db_path.exists():
        return None
    with tx() as conn:
        inc = incumbent(conn)
        decisions = rows(conn, "SELECT c.name AS candidate, p.decision, p.margin, p.reason "
                               "FROM promotions p JOIN model_versions c ON c.id = p.candidate_id "
                               "ORDER BY p.id")
    if inc is None:
        return None
    ev = latest_eval(inc["id"])
    return {"incumbent": inc["name"], "field_f1": ev["field_f1"], "valid_rate": ev["valid_rate"],
            "holdout": {"documents": ev["n_docs"], "hash": ev["eval_set_hash"]},
            "per_field_recall": json.loads(ev["per_field_json"]),
            "gate_decisions": [dict(d) for d in decisions]}


def _summary_from_records() -> dict:
    """Fallback for a fresh clone (no database): the tracked run records."""
    runs = sorted((ROOT / "results" / "runs").glob("*_candidate.json"))
    decisions, last = [], None
    for p in runs:
        r = json.loads(p.read_text(encoding="utf-8"))["result"]
        gate = r.get("gate") or {}
        if gate:
            decisions.append({"candidate": gate.get("candidate"), "decision": gate.get("decision"),
                              "margin": gate.get("margin_pp"), "reason": gate.get("reason")})
            if gate.get("decision") == "promoted":
                last = r
    s = (last or {}).get("score", {})
    return {"incumbent": decisions and next((d["candidate"] for d in reversed(decisions)
                                             if d["decision"] == "promoted"), None),
            "field_f1": s.get("field_f1"), "valid_rate": s.get("valid_rate"),
            "holdout": {"documents": s.get("n_docs"), "hash": s.get("eval_set_hash")},
            "per_field_recall": s.get("per_field"), "gate_decisions": decisions}


def _cost_latency() -> dict | None:
    """From the most recent traced holdout run (scripts/trace_report.py)."""
    from trace_report import report
    traces = sorted((ROOT / "results" / "runs").glob("*_candidate.traces.jsonl"))
    if not traces:
        return None
    r = report(traces[-1])
    return {"model": r["model"], "gpu": r["gpu"], "usd_per_filing": r["gpu_cost_per_doc_usd"],
            "latency_p50_s": r["latency_s"]["p50"], "latency_p99_s": r["latency_s"]["p99"],
            "from_run": traces[-1].name}


@server.tool()
def get_eval_summary() -> dict:
    """How accurate is the extraction pipeline, and how do we know?

    Returns the promoted pipeline's scores on the frozen 189-filing holdout
    (field F1, schema-valid rate, per-field recall), every promotion-gate
    decision made so far (promoted and rejected, with the reason), the
    measured cost and latency, and where the full write-up lives.
    """
    with contextlib.redirect_stdout(sys.stderr):
        summary = _summary_from_db() or _summary_from_records()
        summary["cost_latency"] = _cost_latency()
        summary["gate_rule"] = ("promote only if the paired-bootstrap 95% interval of the F1 "
                                "gain is above zero, no field drops > 2 pts, valid rate holds")
        summary["details"] = "RESULTS.md and FAILURES.md in the repository"
        return summary


if __name__ == "__main__":
    server.run("stdio")
