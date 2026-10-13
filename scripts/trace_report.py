"""Cost and p50/p99 latency per document, from a run's OpenTelemetry spans.

    python scripts/trace_report.py results/runs/<stamp>_candidate.traces.jsonl

Latency is the "extract document" span: the whole model call for one filing,
retries and Modal's 303 polling included, as seen by the client while
LLM_CONCURRENCY requests share the GPU.

Cost: Modal bills the GPU per second while a container is up, whatever the
number of requests in flight, so a document's cost is the run's GPU-seconds
divided by the documents it served (wall clock from the first request to the
last answer, times the GPU's rate). This is the GPU only: Modal's CPU and
memory charges are small and left out, and so is the idle scale-down window
after the run. A cold start that happened during the run IS included.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: Modal on-demand GPU prices, $/second (modal.com/pricing, read 2026-10-13).
GPU_PER_SECOND = {"small": ("L40S", 0.000542), "large": ("H100", 0.001097)}


def pct(values: list[float], p: float) -> float:
    """Nearest-rank percentile: the smallest value with p% of values at or below it."""
    s = sorted(values)
    return s[max(0, min(len(s) - 1, -(-len(s) * p // 100) - 1))] if s else 0.0


def report(path: Path) -> dict:
    spans = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    docs = [s for s in spans if s["name"] == "extract document"]
    calls = {s["parent_id"]: s for s in spans if s["name"].startswith("chat ")}
    if not docs:
        raise SystemExit(f"{path.name}: no document spans")
    lat = [d["duration_ms"] / 1000 for d in docs]
    tok_in = [calls[d["span_id"]]["attributes"]["gen_ai.usage.input_tokens"]
              for d in docs if d["span_id"] in calls]
    tok_out = [calls[d["span_id"]]["attributes"]["gen_ai.usage.output_tokens"]
               for d in docs if d["span_id"] in calls]
    first = next(iter(calls.values()))["attributes"]
    # Traces recorded before the rename (2026-10-13) use "autoextract.*".
    server = first.get("insider_reading.server") or first["autoextract.server"]
    gpu, rate = GPU_PER_SECOND[server]
    wall = (max(d["end_ns"] for d in docs) - min(d["start_ns"] for d in docs)) / 1e9
    model = next(iter(calls.values()))["attributes"]["gen_ai.request.model"]
    return {
        "model": model, "gpu": gpu, "documents": len(docs),
        "latency_s": {"p50": round(pct(lat, 50), 1), "p99": round(pct(lat, 99), 1),
                      "max": round(max(lat), 1), "mean": round(sum(lat) / len(lat), 1)},
        "tokens_per_doc": {"input_mean": round(sum(tok_in) / len(tok_in)),
                           "output_mean": round(sum(tok_out) / len(tok_out)),
                           "output_p99": int(pct(tok_out, 99))},
        "run_wall_s": round(wall), "gpu_cost_run_usd": round(wall * rate, 3),
        "gpu_cost_per_doc_usd": round(wall * rate / len(docs), 5),
    }


def main() -> None:
    for arg in sys.argv[1:]:
        r = report(Path(arg))
        print(f"{Path(arg).name}: {r['documents']} documents, {r['model']} on {r['gpu']}")
        print(f"  latency per document  p50 {r['latency_s']['p50']}s  p99 {r['latency_s']['p99']}s"
              f"  (max {r['latency_s']['max']}s)")
        print(f"  tokens per document   in {r['tokens_per_doc']['input_mean']}  "
              f"out {r['tokens_per_doc']['output_mean']} (p99 {r['tokens_per_doc']['output_p99']})")
        print(f"  GPU cost              ${r['gpu_cost_per_doc_usd']:.4f} per document "
              f"(${r['gpu_cost_run_usd']:.2f} for the run, {r['run_wall_s']}s wall)")
        print(json.dumps(r))


if __name__ == "__main__":
    main()
