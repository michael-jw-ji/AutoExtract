"""The promotion gate.

A candidate is promoted ONLY if it beats the incumbent by a real margin. Both
outcomes are written to `promotions` -- rejections are first-class records,
not silent no-ops, because a gate that has never rejected anything is
indistinguishable from no gate at all.
"""

from __future__ import annotations

from core.config import settings
from core.db import incumbent, insert, one, tx
from evalgate.scorer import latest_eval


def decide(candidate_id: int, against: int | None = None) -> dict:
    """Compare a scored candidate against an incumbent and record the verdict.

    `against` pins the comparison explicitly. That matters for the local
    training track: its candidate is a LoRA on Qwen2.5-1.5B, and comparing it
    to the Baseten track's inkling-small incumbent would measure two variables
    at once (learned-the-rules AND different-base-model). The only meaningful
    comparison is base vs +LoRA on the same base.
    """
    cand_eval = latest_eval(candidate_id)
    if cand_eval is None:
        raise RuntimeError(
            f"Model version {candidate_id} has no eval. Run the scorer first."
        )

    with tx() as conn:
        inc = (
            one(conn, "SELECT * FROM model_versions WHERE id = ?", (against,))
            if against is not None
            else incumbent(conn)
        )
        cand = one(conn, "SELECT * FROM model_versions WHERE id = ?", (candidate_id,))
        if against is not None and inc is None:
            raise RuntimeError(f"No model version {against} to compare against")

    if cand is None:
        raise RuntimeError(f"No model version {candidate_id}")

    inc_eval = latest_eval(inc["id"]) if inc else None
    required = settings.promotion_margin

    if inc_eval is None:
        # Nothing to beat: the first scored model becomes the incumbent. Margin
        # is 0, not the raw score -- there is no incumbent to have beaten.
        margin = 0.0
        return _record(
            cand, inc, cand_eval, inc_eval, "promoted", margin, required,
            "no incumbent -- first scored model becomes the baseline",
        )

    # The score column is named field_f1, but the local track stores mean_f1
    # in it -- so the reason text must name the metric it ACTUALLY compared.
    # Hardcoding "field_f1" produced rows reading "field_f1 +20.11pp" beside a
    # metric column saying mean_f1, which reads as a bug in the gate.
    metric = cand_eval.get("metric") or "field_f1"

    # Comparing across metrics is meaningless: field_f1 is micro-averaged over
    # every field at once, mean_f1 is per-document F1 averaged over documents,
    # and they differ on identical predictions. Refuse rather than emit a
    # number that looks authoritative and is not.
    inc_metric = inc_eval.get("metric") or "field_f1"
    if metric != inc_metric:
        reason = (
            f"cannot compare: candidate scored on {metric}, incumbent on "
            f"{inc_metric}. Re-score both with the same scorer."
        )
        return _record(cand, inc, cand_eval, inc_eval, "rejected", 0.0,
                       required, reason)

    margin = (cand_eval["field_f1"] - inc_eval["field_f1"]) * 100
    valid_delta = (cand_eval["valid_rate"] - inc_eval["valid_rate"]) * 100

    if settings.gate_policy == "bootstrap":
        return _bootstrap_decision(cand, inc, cand_eval, inc_eval, metric, valid_delta)

    if margin < required:
        reason = (
            f"{metric} +{margin:.2f}pp < required +{required:.2f}pp "
            f"({inc_eval['field_f1']:.4f} -> {cand_eval['field_f1']:.4f})"
        )
        return _record(cand, inc, cand_eval, inc_eval, "rejected", margin, required, reason)

    if valid_delta < 0:
        reason = (
            f"{metric} +{margin:.2f}pp met the bar but valid_rate regressed "
            f"{valid_delta:.2f}pp ({inc_eval['valid_rate']:.4f} -> "
            f"{cand_eval['valid_rate']:.4f})"
        )
        return _record(cand, inc, cand_eval, inc_eval, "rejected", margin, required, reason)

    reason = (
        f"{metric} +{margin:.2f}pp >= +{required:.2f}pp, "
        f"valid_rate {valid_delta:+.2f}pp"
    )
    return _record(cand, inc, cand_eval, inc_eval, "promoted", margin, required, reason)


def _bootstrap_decision(cand, inc, cand_eval, inc_eval, metric, valid_delta) -> dict:
    """Promote only if the gain is real, no field regresses, and validity holds.

    Built from the RECORDED answers behind both evals, so no model is called
    and the decision can be re-derived exactly (fixed seed). Refuses to
    decide if either side's answers are missing.
    """
    import json

    from evalgate.scorer import answers_for, per_doc_counts
    from evalgate.stats import paired_bootstrap

    paths = []
    for version, ev in ((cand, cand_eval), (inc, inc_eval)):
        model = version["adapter_ref"] or version["base_model"]
        if not ev.get("prompt"):
            raise RuntimeError(f"eval {ev['id']} has no prompt hash; cannot find its answers")
        path = answers_for(ev, model)
        if not path.exists():
            raise RuntimeError(f"no recorded answers for {version['name']} at {path}")
        paths.append(path)

    delta, lo, hi = paired_bootstrap(per_doc_counts(paths[0]), per_doc_counts(paths[1]),
                                     resamples=settings.gate_resamples)
    stat = f"{metric} {delta:+.2f}pp, 95% CI [{lo:+.2f}, {hi:+.2f}]"

    old = json.loads(inc_eval.get("per_field_json") or "{}")
    new = json.loads(cand_eval.get("per_field_json") or "{}")
    drops = sorted(((new.get(k, 0.0) - v) * 100, k) for k, v in old.items())
    drops = [(d, k) for d, k in drops if d < -settings.gate_field_drop]

    if lo <= 0:
        decision, reason = "rejected", f"{stat}: the interval includes zero, so the gain may be noise"
    elif drops:
        listed = ", ".join(f"{k} {d:+.1f}" for d, k in drops[:4])
        decision, reason = "rejected", (f"{stat}, but fields regressed more than "
                                        f"{settings.gate_field_drop:.1f}pp: {listed}")
    elif valid_delta < 0:
        decision, reason = "rejected", f"{stat}, but valid_rate regressed {valid_delta:.2f}pp"
    else:
        decision, reason = "promoted", (f"{stat} excludes zero; no field down more than "
                                        f"{settings.gate_field_drop:.1f}pp; valid_rate {valid_delta:+.2f}pp")
    # `required` records the policy's bar: a lower bound above 0.
    return _record(cand, inc, cand_eval, inc_eval, decision, delta, 0.0, reason)


def _record(cand, inc, cand_eval, inc_eval, decision, margin, required, reason) -> dict:
    with tx() as conn:
        insert(
            conn,
            "promotions",
            candidate_id=cand["id"],
            incumbent_id=inc["id"] if inc else None,
            decision=decision,
            margin=round(margin, 4),
            required=required,
            reason=reason,
        )
        if decision == "promoted":
            conn.execute(
                "UPDATE model_versions SET status='promoted', "
                "promoted_at=datetime('now') WHERE id = ?",
                (cand["id"],),
            )
            if inc and inc["id"] != cand["id"]:
                conn.execute(
                    "UPDATE model_versions SET status='rejected' WHERE id = ?",
                    (inc["id"],),
                )
        else:
            conn.execute(
                "UPDATE model_versions SET status='rejected' WHERE id = ?",
                (cand["id"],),
            )

    return {
        "decision": decision,
        "candidate": cand["name"],
        "incumbent": inc["name"] if inc else None,
        "margin_pp": round(margin, 3),
        "required_pp": required,
        "reason": reason,
        "candidate_f1": cand_eval["field_f1"],
        "incumbent_f1": inc_eval["field_f1"] if inc_eval else None,
    }
