"""OpenTelemetry tracing for model calls (docs/PLAN.md, Milestone 3).

Every chat completion is a span carrying the GenAI semantic-convention
attributes (gen_ai.request.model, gen_ai.usage.input_tokens,
gen_ai.usage.output_tokens, ...). The scorer wraps each document in a parent
span, so a document's latency includes retries and redirects.

Spans go to a JSON-lines file: run_cycle.py points it at
results/runs/<stamp>_<stage>.traces.jsonl (tracked, next to the run log), and
scripts/trace_report.py turns it into cost and p50/p99 latency per document.
Set OTEL_EXPORTER_OTLP_ENDPOINT and install opentelemetry-exporter-otlp to
send the same spans to a collector (Jaeger, Honeycomb, ...) as well.
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

_provider = TracerProvider(resource=Resource.create({"service.name": "autoextract"}))
trace.set_tracer_provider(_provider)
tracer = trace.get_tracer("autoextract")


class JsonlExporter(SpanExporter):
    """One JSON object per finished span. Swappable at runtime (per run)."""

    def __init__(self) -> None:
        self.path: Path | None = None
        self._lock = threading.Lock()

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        if self.path is None:
            return SpanExportResult.SUCCESS
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            for s in spans:
                fh.write(json.dumps({
                    "name": s.name,
                    "trace_id": f"{s.context.trace_id:032x}",
                    "span_id": f"{s.context.span_id:016x}",
                    "parent_id": f"{s.parent.span_id:016x}" if s.parent else None,
                    "start_ns": s.start_time, "end_ns": s.end_time,
                    "duration_ms": round((s.end_time - s.start_time) / 1e6, 1),
                    "status": s.status.status_code.name,
                    "attributes": dict(s.attributes or {}),
                }) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.path = None


_jsonl = JsonlExporter()
_provider.add_span_processor(SimpleSpanProcessor(_jsonl))

if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
    try:  # optional: pip install opentelemetry-exporter-otlp
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    except ImportError:
        print("  ! OTEL_EXPORTER_OTLP_ENDPOINT is set but opentelemetry-exporter-otlp "
              "is not installed; spans go to the JSONL file only")


@contextmanager
def to_file(path: Path):
    """Write every span finished inside the block to `path`."""
    _jsonl.path = path
    try:
        yield path
    finally:
        _provider.force_flush()
        _jsonl.path = None
