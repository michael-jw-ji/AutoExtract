"""FastAPI app: the extraction endpoint plus read-only views for the dashboard.

The dashboard NEVER computes anything. Every number it shows is a row that
some stage of the loop already wrote, which is why the demo survives a dead
component: the audit trail is the product.

    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from buffer import cluster, store
from core.db import connect, incumbent, init_db, insert, one, rows, tx
from core.config import settings
from serve.extract import extract_text
from verify.validate import strip_fences, validate

app = FastAPI(title="AutoExtract", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


class ExtractRequest(BaseModel):
    text: str
    persist: bool = True


@app.get("/api/config")
def config() -> dict:
    """Which domain and which switches are live. The dashboard shows these
    because every number on it is only meaningful relative to them."""
    from domains import available, get_domain
    d = get_domain()
    return {
        "domain": d.name,
        "available_domains": available(),
        "schema": d.Schema.__name__,
        "json_mode": settings.json_mode,
        "enrich": settings.enrich,
        "serving_model": settings.small_model,
        "db": settings.db_path.name,
    }


@app.post("/extract")
def extract(req: ExtractRequest) -> dict:
    """Extract, validate, and (by default) buffer the failure if it fails.

    Returns BOTH the model's raw answer and the enriched one, so the caller
    can see exactly which fields the model got and which were derived by
    code. That distinction is the whole architecture.
    """
    from serve.client import chat
    from serve.extract import apply_enrichment, system_prompt
    from domains import get_domain

    raw_model, latency_ms = chat(settings.small_model, system_prompt(), req.text)
    model_used = settings.small_model
    before = validate(raw_model)

    report: dict = {}
    raw = raw_model
    if settings.enrich:
        try:
            payload = json.loads(strip_fences(raw_model))
            if isinstance(payload, dict):
                enriched, report = get_domain().enrich(payload)
                raw = json.dumps(enriched, default=str)
        except json.JSONDecodeError:
            report = {"skipped": "model output was not parseable"}

    outcome = validate(raw)

    if req.persist:
        with tx() as conn:
            inc = incumbent(conn)
            doc_id = insert(
                conn, "documents", ext_id=f"api-{abs(hash(req.text)) % 10**10}",
                text=req.text, gold_json=None, split="live", messiness="api",
            )
            extraction_id = insert(
                conn, "extractions", doc_id=doc_id,
                model_version_id=inc["id"] if inc else None, raw_output=raw,
                parsed_json=json.dumps(outcome.parsed) if outcome.parsed else None,
                valid=int(outcome.valid), signature=outcome.signature,
                error_count=outcome.error_count, latency_ms=latency_ms,
            )
            if not outcome.valid:
                insert(
                    conn, "failures", extraction_id=extraction_id, doc_id=doc_id,
                    signature=outcome.signature, error_count=outcome.error_count,
                    errors_json=json.dumps(outcome.errors, default=str),
                )

    return {
        "valid": outcome.valid,
        "data": outcome.parsed,
        "errors": outcome.errors if not outcome.valid else [],
        "signature": outcome.signature,
        "model": model_used,
        "latency_ms": latency_ms,
        # What changed between the model's answer and the final one.
        #
        # error_list matters as much as the count. With ENRICH=1 the final
        # answer is usually valid and `errors` above is empty, so a caller
        # that only sees the count learns that something was fixed but never
        # WHAT -- and "it invented VND-00001 for Northwind" is the entire
        # point. changed[] pairs the model's value against the derived one.
        "before": {
            "valid": before.valid,
            "errors": len(before.errors),
            "signature": before.signature,
            "error_list": before.errors[:10],
        },
        "changed": _changed_fields(raw_model, raw),
        "enrichment": report,
        "enrich_enabled": settings.enrich,
    }


def _changed_fields(raw_model: str, raw_final: str) -> list[dict]:
    """Scalar fields whose value differs after enrichment, plus line-item
    categories -- which live in a list and would otherwise never surface."""
    def parse(text: str) -> dict:
        try:
            value = json.loads(strip_fences(text))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}

    def same(b, a) -> bool:
        """Treat 24435.03 and '24435.03' as unchanged.

        Enrichment normalises money to 2-decimal strings, so every numeric
        field 'changes' on every document. Listing those buries the handful
        of real corrections -- which are the only reason this exists.
        """
        if b == a:
            return True
        try:
            return Decimal(str(b)) == Decimal(str(a))
        except (InvalidOperation, TypeError, ValueError):
            return False

    before, after = parse(raw_model), parse(raw_final)
    if not before or not after:
        return []

    out: list[dict] = []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if isinstance(b, (dict, list)) or isinstance(a, (dict, list)):
            continue
        if not same(b, a):
            out.append({"field": key, "model": b, "derived": a})

    for idx, (bi, ai) in enumerate(
        zip(before.get("line_items") or [], after.get("line_items") or [])
    ):
        if not isinstance(bi, dict) or not isinstance(ai, dict):
            continue
        if bi.get("category") != ai.get("category"):
            out.append({
                "field": f"line_items[{idx}].category",
                "label": str(ai.get("description", ""))[:22],
                "model": bi.get("category"),
                "derived": ai.get("category"),
            })
    return out


@app.get("/api/stats")
def stats() -> dict:
    with tx() as conn:
        inc = incumbent(conn)
        totals = one(
            conn,
            "SELECT COUNT(*) AS extractions, "
            "SUM(CASE WHEN valid=1 THEN 1 ELSE 0 END) AS passed, "
            "AVG(latency_ms) AS avg_latency FROM extractions",
        ) or {}
        versions = one(conn, "SELECT COUNT(*) AS n FROM model_versions") or {}
        promos = rows(
            conn,
            "SELECT decision, COUNT(*) AS n FROM promotions GROUP BY decision",
        )
    total = totals.get("extractions") or 0
    passed = totals.get("passed") or 0
    return {
        "incumbent": inc["name"] if inc else None,
        "extractions": total,
        "passed": passed,
        "failed": total - passed,
        "valid_rate": round(passed / total, 4) if total else 0.0,
        "avg_latency_ms": round(totals.get("avg_latency") or 0),
        "buffer_depth": store.depth(),
        "model_versions": versions.get("n", 0),
        "promotions": {r["decision"]: r["n"] for r in promos},
    }


@app.get("/api/clusters")
def clusters(limit: int = 15, status: str | None = None) -> list[dict]:
    return cluster.clusters(limit=limit, status=status)


@app.get("/api/versions")
def versions() -> list[dict]:
    """Model lineage with each version's latest eval and gate verdict."""
    with tx() as conn:
        result = rows(
            conn,
            "SELECT v.*, "
            "  (SELECT field_f1 FROM evals e WHERE e.model_version_id=v.id "
            "   ORDER BY e.id DESC LIMIT 1) AS field_f1, "
            "  (SELECT valid_rate FROM evals e WHERE e.model_version_id=v.id "
            "   ORDER BY e.id DESC LIMIT 1) AS valid_rate, "
            "  (SELECT metric FROM evals e WHERE e.model_version_id=v.id "
            "   ORDER BY e.id DESC LIMIT 1) AS metric, "
            "  (SELECT config FROM evals e WHERE e.model_version_id=v.id "
            "   ORDER BY e.id DESC LIMIT 1) AS config, "
            "  (SELECT decision FROM promotions p WHERE p.candidate_id=v.id "
            "   ORDER BY p.id DESC LIMIT 1) AS decision, "
            "  (SELECT margin FROM promotions p WHERE p.candidate_id=v.id "
            "   ORDER BY p.id DESC LIMIT 1) AS margin, "
            "  (SELECT reason FROM promotions p WHERE p.candidate_id=v.id "
            "   ORDER BY p.id DESC LIMIT 1) AS reason, "
            "  (SELECT dataset_size FROM training_runs t "
            "   WHERE t.model_version_id=v.id ORDER BY t.id DESC LIMIT 1) AS dataset_size "
            "FROM model_versions v ORDER BY v.id",
        )
    for r in result:
        if r.get("notes"):
            try:
                r["notes"] = json.loads(r["notes"])
            except json.JSONDecodeError:
                pass
    return result


@app.get("/api/runs")
def runs() -> list[dict]:
    with tx() as conn:
        return rows(
            conn,
            "SELECT t.*, v.name AS version_name FROM training_runs t "
            "LEFT JOIN model_versions v ON v.id = t.model_version_id "
            "ORDER BY t.id DESC",
        )


@app.get("/api/promotions")
def promotions() -> list[dict]:
    with tx() as conn:
        return rows(
            conn,
            "SELECT p.*, c.name AS candidate_name, i.name AS incumbent_name "
            "FROM promotions p "
            "LEFT JOIN model_versions c ON c.id = p.candidate_id "
            "LEFT JOIN model_versions i ON i.id = p.incumbent_id "
            "ORDER BY p.id DESC",
        )


@app.get("/api/failures")
def failures(limit: int = 50, signature: str | None = None) -> list[dict]:
    where = "WHERE f.signature = ?" if signature else ""
    params: tuple = (signature,) if signature else ()
    with tx() as conn:
        return rows(
            conn,
            f"SELECT f.id, f.signature, f.error_count, f.status, f.created_at, "
            f"       substr(d.text, 1, 400) AS doc_excerpt, d.messiness "
            f"FROM failures f JOIN documents d ON d.id = f.doc_id {where} "
            f"ORDER BY f.id DESC LIMIT ?",
            params + (limit,),
        )


@app.get("/api/timeline")
def timeline(limit: int = 200) -> list[dict]:
    """Extraction outcomes over time, for the dashboard's pass-rate sparkline."""
    with tx() as conn:
        return rows(
            conn,
            "SELECT id, valid, signature, latency_ms, model_version_id, created_at "
            "FROM extractions ORDER BY id DESC LIMIT ?",
            (limit,),
        )


@app.get("/api/fields")
def fields() -> list[dict]:
    """Per-field accuracy from the most recent eval.

    This is the analytically useful view: valid_rate says everything failed,
    field accuracy says WHICH fields failed. The business-rule fields should
    sit near zero before training and climb after.
    """
    with tx() as conn:
        # Latest eval that actually HAS per-field data. Not simply the latest:
        # merged evals from another track carry no per_field_json, and taking
        # the newest row blanked this panel entirely.
        row = one(
            conn,
            "SELECT per_field_json, model_version_id FROM evals "
            "WHERE per_field_json IS NOT NULL AND per_field_json != '' "
            "ORDER BY id DESC LIMIT 1",
        )
    if not row or not row["per_field_json"]:
        return []
    per_field = json.loads(row["per_field_json"])
    # Fields the document does not contain -- the model must infer them from
    # rules it has never seen. These are what the LoRA is meant to teach.
    rule_fields = {"vendor_id", "payment_terms", "line_items.category"}
    return sorted(
        (
            {
                "field": k,
                "accuracy": v,
                "kind": "business_rule" if k in rule_fields else "extraction",
            }
            for k, v in per_field.items()
        ),
        key=lambda r: r["accuracy"],
    )


@app.get("/api/repair-stats")
def repair_stats() -> dict:
    """Funnel from buffered failure to verified training example."""
    with tx() as conn:
        by_method = rows(
            conn,
            "SELECT method, SUM(verified) AS verified, COUNT(*) AS total "
            "FROM repairs GROUP BY method",
        )
        by_status = rows(
            conn, "SELECT status, COUNT(*) AS n FROM failures GROUP BY status"
        )
        totals = one(
            conn,
            "SELECT COUNT(*) AS failures, "
            "(SELECT COUNT(*) FROM repairs) AS repairs, "
            "(SELECT COUNT(*) FROM repairs WHERE verified=1) AS verified "
            "FROM failures",
        ) or {}
    return {
        "by_method": by_method,
        "by_status": {r["status"]: r["n"] for r in by_status},
        "failures": totals.get("failures", 0),
        "repairs": totals.get("repairs", 0),
        "verified": totals.get("verified", 0),
    }


@app.get("/api/latency")
def latency() -> dict:
    """Latency buckets, for spotting the slow tail before a live demo."""
    with tx() as conn:
        vals = [
            r["latency_ms"]
            for r in rows(
                conn,
                "SELECT latency_ms FROM extractions WHERE latency_ms IS NOT NULL "
                "ORDER BY latency_ms",
            )
        ]
    if not vals:
        return {"buckets": [], "p50": 0, "p95": 0, "n": 0}

    edges = [0, 2000, 4000, 6000, 8000, 10000, 15000, 20000, 10**9]
    labels = ["<2s", "2-4s", "4-6s", "6-8s", "8-10s", "10-15s", "15-20s", "20s+"]
    buckets = [
        {"label": labels[i],
         "count": sum(1 for v in vals if edges[i] <= v < edges[i + 1])}
        for i in range(len(labels))
    ]
    return {
        "buckets": buckets,
        "p50": vals[len(vals) // 2],
        "p95": vals[min(len(vals) - 1, int(len(vals) * 0.95))],
        "n": len(vals),
    }


@app.get("/health")
def health() -> dict:
    try:
        conn = connect()
        conn.execute("SELECT 1")
        conn.close()
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))
