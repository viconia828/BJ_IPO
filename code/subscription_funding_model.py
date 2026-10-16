"""Small, continuous funding models using only outcomes known at prediction time.

These models estimate money directly. Issuance size is used only by the caller
to convert that money into an allocation ratio, not as a second size correction.
"""
from __future__ import annotations

import math
from typing import Any, Callable

import subscription_replay_timeline as timeline

MODELS = {"direct_funds", "bucket_funds", "account_pool_funds"}
DemandFunction = Callable[[dict[str, Any], float], dict[str, Any] | None]


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _enabled(value: Any) -> bool:
    return str(value).lower() in {"true", "1", "yes", "y", "是"}


def _funds(row: dict[str, Any]) -> float:
    value = _number(row.get("frozen_funds_yi"))
    if value is None:
        value = (_number(row.get("online_valid_shares")) or 0) * (_number(row.get("issue_price")) or 0) / 1e8
    return max(value, 0)


def _settings(params: dict[str, Any]) -> dict[str, Any]:
    model = str(params.get("subscription_prediction_funding_model", "direct_funds"))
    if model not in MODELS:
        raise ValueError(f"Unknown direct funding model: {model}")
    half = _number(params.get("subscription_prediction_sample_decay_half_life_days", 6.25))
    beta = _number(params.get("subscription_prediction_direct_cap_elasticity", .25))
    bandwidth = _number(params.get("subscription_prediction_direct_similarity_bandwidth", .35))
    if half is None or half <= 0 or bandwidth is None or bandwidth <= 0 or beta is None or not 0 <= beta <= 1:
        raise ValueError("Funding half-life/bandwidth must be positive and elasticity must be in [0, 1]")
    return {"model": model, "half": max(half, 1.0), "beta": beta, "bandwidth": bandwidth,
            "minimum": max(int(float(params.get("subscription_prediction_min_samples", 3))), 1)}


def _known(rows: list[dict[str, Any]], target: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in rows if timeline.known_before(r, target)
            and (_number(r.get("issue_price")) or 0) > 0
            and (_number(r.get("online_issue_shares")) or 0) > 0
            and _funds(r) > float(r["issue_price"]) * float(r["online_issue_shares"]) / 1e8]


def _weights(rows: list[dict[str, Any]], half: float) -> dict[str, float]:
    dates = [timeline.result_available_date(r) for r in rows]
    return {str(r["security_code"]): 0.5 ** (sum(d > dates[i] for d in dates) / half)
            for i, r in enumerate(rows)}


def _raw(target: dict[str, Any], rows: list[dict[str, Any]], cfg: dict[str, Any],
         demand: DemandFunction | None) -> dict[str, Any]:
    cap = _number(target.get("top_apply_amount_wan")) or 0
    known = _known(rows, target)
    if cap <= 0 or len(known) < cfg["minimum"]:
        return {"available": False, "reason": "缺少顶格金额或历史冻资样本不足"}
    recency = _weights(known, cfg["half"])
    samples = []
    excluded = []
    for r in known:
        source_cap = _number(r.get("top_apply_amount_wan")) or 0
        if source_cap <= 0:
            continue
        log_distance = math.log(cap / source_cap)
        weight = recency[str(r["security_code"])] * math.exp(-.5 * (log_distance / cfg["bandwidth"]) ** 2)
        if weight <= 0:
            continue
        value = _funds(r) * (cap / source_cap) ** cfg["beta"]
        detail = {}
        if cfg["model"] in {"bucket_funds", "account_pool_funds"}:
            if cfg["model"] == "account_pool_funds":
                available = str(r.get("account_distribution_available_date") or "")
                if not available or available >= timeline.normalized_date(target.get("apply_date")):
                    excluded.append({"security_code": str(r["security_code"]), "reason": "distribution_not_yet_available"})
                    continue
            own = demand(r, source_cap) if demand else None
            projected = demand(r, cap) if demand else None
            own_value = _number((own or {}).get("estimated_demand_wan")) or 0
            projected_value = _number((projected or {}).get("estimated_demand_wan")) or 0
            if own_value <= 0 or projected_value <= 0:
                excluded.append({"security_code": str(r["security_code"]),
                                 "reason": r.get("account_distribution_rejection") or "unavailable_demand"})
                continue
            # Bucket thresholds are lower bounds, not observed bucket means.
            # Reconcile within the *historical source* before transferring shape.
            value = _funds(r) * projected_value / own_value
            weight *= max(.05, min(float(projected.get("confidence") or 0), 1.0))
            detail = {"source_bucket_demand_wan": own_value,
                      "target_bucket_demand_wan": projected_value,
                      "source_reconciliation_factor": _funds(r) * 1e4 / own_value,
                      "distribution_available_date": r.get("account_distribution_available_date"),
                      "account_count_basis": projected.get("account_count_basis", "legacy_compressed_allocation_fit")}
        samples.append({"security_code": str(r["security_code"]),
                        "available_date": timeline.result_available_date(r),
                        "source_frozen_funds_yi": _funds(r), "projected_frozen_funds_yi": value,
                        "recency_weight": recency[str(r["security_code"])], "weight": weight, **detail})
    if len(samples) < cfg["minimum"]:
        if cfg["model"] in {"bucket_funds", "account_pool_funds"}:
            result = _raw(target, rows, {**cfg, "model": "direct_funds"}, demand)
            result["fallback_reason"] = "insufficient_repaired_account_sources" if cfg["model"] == "account_pool_funds" else "insufficient_bucket_sources"
            result["excluded_account_sources"] = excluded
            return result
        return {"available": False, "reason": "有效资金样本权重不足"}
    total = sum(s["weight"] for s in samples)
    return {"available": True, "basis": cfg["model"], "sample_count": len(samples),
            "frozen_funds_yi": sum(s["projected_frozen_funds_yi"] * s["weight"] for s in samples) / total,
            "effective_sample_count": total**2 / sum(s["weight"]**2 for s in samples),
            "source_codes": [s["security_code"] for s in samples], "samples": samples,
            "excluded_account_sources": excluded}


def estimate_funding(target: dict[str, Any], history_rows: list[dict[str, Any]],
                     params: dict[str, Any], *, demand: DemandFunction | None = None) -> dict[str, Any]:
    cfg = _settings(params)
    known = _known(history_rows, target)
    base = _raw(target, known, cfg, demand)
    if not base.get("available"):
        return {"valid_subscription_shares": None, "funding_model": cfg["model"], **base}
    weights = _weights(known, cfg["half"])
    low = float(params.get("subscription_prediction_recent_market_level_adaptive_factor_min", .9))
    high = float(params.get("subscription_prediction_recent_market_level_adaptive_factor_max", 1.05))
    configured = float(params.get("subscription_prediction_recent_market_level_factor", 1))
    adaptive_weight = float(params.get("subscription_prediction_recent_market_level_adaptive_weight", .5))
    if not all(math.isfinite(v) for v in (low, high, configured, adaptive_weight)) or not 0 < low <= high or not 0 <= adaptive_weight <= 1 or configured <= 0:
        raise ValueError("Invalid funding water-level bounds or weight")
    enabled = _enabled(params.get("subscription_prediction_recent_market_level_adaptive_enabled", True))
    level = {"enabled": enabled, "available": False, "factor": max(low, min(configured, high)),
             "configured_factor": configured, "configured_base_factor": configured,
             "basis": "configured_recent_market_level", "sample_count": 0, "source_codes": [], "samples": [],
             "half_life_samples": cfg["half"], "adaptive_weight": adaptive_weight,
             "reason": "insufficient_walk_forward_samples" if enabled else "adaptive_disabled"}
    observations = []
    if enabled:
        for r in known:
            past = _raw(r, known, cfg, demand)
            if not past.get("available"):
                continue
            ratio = _funds(r) / past["frozen_funds_yi"]
            observations.append({"security_code": str(r["security_code"]),
                "prediction_source_codes": past["source_codes"], "predicted_frozen_funds_yi": past["frozen_funds_yi"],
                "prediction_distribution_dates": {s["security_code"]: s.get("distribution_available_date") for s in past["samples"] if s.get("distribution_available_date")},
                "prediction_basis": past.get("basis"), "prediction_fallback_reason": past.get("fallback_reason"),
                "actual_frozen_funds_yi": _funds(r), "raw_factor": ratio,
                "calibrated_factor": min(1.2, max(.8, ratio)), "weight": weights[str(r["security_code"])]})
        level.update(sample_count=len(observations), samples=observations,
                     source_codes=[s["security_code"] for s in observations])
        minimum = max(int(float(params.get("subscription_prediction_recent_market_level_adaptive_min_samples", 3))), 1)
        if len(observations) >= minimum:
            observed = sum(s["calibrated_factor"]*s["weight"] for s in observations) / sum(s["weight"] for s in observations)
            factor = max(low, min(high, 1 + adaptive_weight * (observed - 1)))
            level.update(available=True, factor=factor, observed_factor=observed,
                         basis="walk_forward_direct_funding_level", reason="")
    amount = base["frozen_funds_yi"] * level["factor"]
    level.update(applied=abs(level["factor"]-1) > 1e-12,
                 pre_adjustment_frozen_funds_yi=base["frozen_funds_yi"], adjusted_frozen_funds_yi=amount)
    price = _number(target.get("issue_price")) or 0
    online = _number(target.get("online_issue_shares")) or 0
    if price <= 0 or online <= 0:
        return {"valid_subscription_shares": None, "reason": "缺少发行价或网上发行数量"}
    return {"funding_model": cfg["model"], "valid_subscription_shares": amount*1e8/price,
            "predicted_subscription_multiple": amount*1e8/price/online, "sample_count": base["sample_count"],
            "base_predicted_frozen_funds_yi": base["frozen_funds_yi"],
            "raw_base_predicted_frozen_funds_yi": base["frozen_funds_yi"],
            "direct_funding": base, "recent_market_level": level, "recency_policy": "unified",
            "recency_half_life_samples": cfg["half"], "recency_weights": weights, "reason": ""}
