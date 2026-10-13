"""Export the results page's data from the tracked run records.

    python scripts/site_data.py         # writes web/src/data.json for the web/ app

No model calls and no database: everything comes from results/runs/, so a
fresh clone builds the same page. The shape is `SiteData` in web/src/types.ts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from trace_report import report  # noqa: E402

RUNS = ROOT / "results" / "runs"
OUT = ROOT / "web" / "src" / "data.json"


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _note(argv: list[str]) -> str:
    return argv[argv.index("--note") + 1] if "--note" in argv else ""


def collect() -> dict:
    gated = sorted([*RUNS.glob("*_candidate.json"), *RUNS.glob("*_adapter.json")], key=lambda p: p.name)
    versions, scores = [], {}
    for p in gated:
        rec = _load(p)
        gate, score, run = rec["result"].get("gate") or {}, rec["result"]["score"], rec["run"]
        if not gate:
            continue
        scores[gate["candidate"]] = score
        versions.append({
            "name": gate["candidate"], "stage": run["stage"], "date": run["started_utc"][:10],
            "note": _note(run["argv"]), "decision": gate["decision"], "incumbent": gate["incumbent"],
            "before": gate["incumbent_f1"], "after": gate["candidate_f1"], "gain_pp": gate["margin_pp"],
            "valid": score["valid_rate"], "reason": gate["reason"], "record": p.name,
            "commit": run["commit"],
        })
    current = next(v for v in reversed(versions) if v["decision"] == "promoted")
    now, prev = scores[current["name"]], scores.get(current["incumbent"])

    traces = RUNS / current["record"].replace(".json", ".traces.jsonl")
    cost = None
    if traces.exists():
        r = report(traces)
        cost = {"model": r["model"], "gpu": r["gpu"], "usd_per_filing": r["gpu_cost_per_doc_usd"],
                "p50_s": r["latency_s"]["p50"], "p99_s": r["latency_s"]["p99"]}

    large = None
    compares = sorted(RUNS.glob("*_compare_large.json"))
    if compares:
        r = _load(compares[-1])["result"]
        large = {"model": r["large"]["model"], "f1": r["large"]["field_f1"], "valid": r["large"]["valid_rate"],
                 "small_name": r["incumbent"]["name"], "small_f1": r["incumbent"]["f1"],
                 "small_valid": r["incumbent"]["valid_rate"], "outcomes": r["outcomes"],
                 "ci_pp": r["bootstrap_large_minus_small"]}

    return {
        "current": {"name": current["name"], "f1": now["field_f1"], "valid": now["valid_rate"],
                    "commit": current["commit"]},
        "base": {"name": versions[0]["incumbent"], "f1": versions[0]["before"]},
        "holdout": {"documents": now["n_docs"], "hash": now["eval_set_hash"]},
        "fields": [{"field": f, "before": (prev or {}).get("per_field", {}).get(f), "after": v}
                   for f, v in now["per_field"].items()],
        "fields_before": current["incumbent"],
        "versions": versions, "cost": cost, "large": large,
    }


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(collect(), indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
