from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import subscription_ladder_labels
import subscription_funding_model
import subscription_funding_hypotheses
import subscription_account_funding
import subscription_predictor
import subscription_replay_timeline as replay_timeline

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_HISTORY_PATH = ROOT_DIR / "data/offline_tuning/subscription_history_sample.csv"
DEFAULT_LABEL_PATH = ROOT_DIR / "data/offline_tuning/subscription_ladder_labels.csv"


def load_history_rows(
    history_path: Path = DEFAULT_HISTORY_PATH, label_path: Path = DEFAULT_LABEL_PATH,
) -> list[dict[str, Any]]:
    """Read the same local, labelled history used for offline funding tuning."""
    if not history_path.exists():
        return []
    with history_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    return subscription_ladder_labels.apply_labels_to_history_rows(
        rows, subscription_ladder_labels.load_label_rows(label_path),
        apply_threshold_overrides=False,
    )


def target_row_from_ipo(ipo_info: dict[str, Any]) -> dict[str, Any]:
    price = _safe_float(ipo_info.get("ISSUE_PRICE"))
    shares, _ = subscription_predictor._resolve_online_issue_shares(ipo_info)
    cap, _ = subscription_predictor._resolve_top_apply_amount_wan(ipo_info, price or 0)
    return {
        "security_code": str(ipo_info.get("SECURITY_CODE") or ""),
        "apply_date": replay_timeline.normalized_date(ipo_info.get("APPLY_DATE")),
        "issue_result_date": replay_timeline.normalized_date(
            ipo_info.get("ISSUE_RESULT_DATE") or ipo_info.get("BALLOT_NUM_DATE")
            or ipo_info.get("RESULT_NOTICE_DATE")),
        "issue_price": price, "online_issue_shares": shares,
        "top_apply_amount_wan": cap,
    }


def known_history(
    rows: list[dict[str, Any]], target: dict[str, Any], *,
    as_of_date: str | date | None = None, max_history_samples: int | None = None,
) -> list[dict[str, Any]]:
    as_of = replay_timeline.normalized_date(as_of_date) if as_of_date is not None else ""
    if as_of_date is not None and not as_of:
        raise ValueError("Invalid subscription as-of date")
    known = [row for row in sorted(rows, key=_row_sort_key)
             if _is_eligible_row(row) and replay_timeline.known_before(row, target)
             and (not as_of or replay_timeline.result_available_date(row) <= as_of)]
    return known[-max_history_samples:] if max_history_samples else known


def build_prediction(
    ipo_info: dict[str, Any], *, history_rows: list[dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None, as_of_date: str | date | None = None,
    max_history_samples: int | None = None,
    funding_only: bool = False,
) -> dict[str, Any]:
    """Shared production/replay forecast; sentiment windows never enter here.

    An explicit sample cap is reserved for offline robustness diagnostics.
    Production and ordinary tuning leave it unset and use all known history.
    funding_only omits fractional interpolation/formatting for funding search;
    the selected parameters must also be checked through the full output path.
    """
    rows = history_rows if history_rows is not None else load_history_rows()
    settings = dict(params or {})
    settings.setdefault("subscription_prediction_unified_recency_enabled", True)
    settings.setdefault("subscription_prediction_min_samples", 3)
    funding_model = str(settings.get("subscription_prediction_funding_model", "multiple"))
    if funding_model not in {"multiple", *subscription_funding_model.MODELS}:
        raise ValueError(f"Unknown subscription funding model: {funding_model}")
    if funding_model in subscription_funding_model.MODELS:
        settings["subscription_prediction_unified_recency_enabled"] = True
    target = target_row_from_ipo(ipo_info)
    known = known_history(rows, target, as_of_date=as_of_date,
                          max_history_samples=max_history_samples)
    pool_rows = settings.get("subscription_prediction_account_pool_rows")
    if not isinstance(pool_rows, list):
        pool_rows, _ = subscription_predictor._load_account_pool_rows(settings)
        settings["subscription_prediction_account_pool_rows"] = pool_rows
    cache = settings.setdefault(subscription_predictor.ACCOUNT_POOL_RUNTIME_CACHE_KEY, {})
    timeline_key = (
        subscription_predictor._account_pool_input_signature(settings),
        tuple((str(row.get("security_code") or ""), str(row.get("apply_date") or ""),
               replay_timeline.result_available_date(row)) for row in rows),
    )
    contexts = cache.setdefault("replay_contexts", {})
    if contexts.get("key") != timeline_key:
        contexts.clear()
        contexts.update({"key": timeline_key,
                         "timeline": replay_timeline.account_pool_timeline(pool_rows, rows), "targets": {}})
    cutoff = target["apply_date"]
    as_of = replay_timeline.normalized_date(as_of_date) if as_of_date is not None else ""
    if as_of and cutoff:
        cutoff = min(cutoff, (date.fromisoformat(as_of) + timedelta(days=1)).isoformat())
    target_key = (cutoff, target["security_code"])
    if target_key not in contexts["targets"]:
        contexts["targets"][target_key] = {
            "subscription_prediction_account_pool_rows": replay_timeline.account_pool_before(
                contexts["timeline"], {**target, "apply_date": cutoff}),
            subscription_predictor.ACCOUNT_POOL_RUNTIME_CACHE_KEY: {},
        }
    row_settings = {**settings, **contexts["targets"][target_key]}
    recent_ipos = [_recent_ipo_from_row(row) for row in known]
    lock_basis = settings.get("subscription_prediction_lock_days_basis", "result_date")
    if lock_basis == "t_plus_2_calendar_2026":
        ipo_info = subscription_funding_hypotheses.with_capital_return_proxy(ipo_info)
        recent_ipos = [subscription_funding_hypotheses.with_capital_return_proxy(r) for r in recent_ipos]
    elif lock_basis != "result_date":
        raise ValueError(f"Unknown capital occupation basis: {lock_basis}")
    if subscription_predictor._unified_recency(row_settings):
        row_settings["subscription_prediction_recency_weights"] = subscription_predictor._history_recency_weights(recent_ipos, row_settings)
    funding_estimate = None
    target_valid_shares, _ = subscription_predictor._resolve_valid_subscription_shares(
        ipo_info, target["online_issue_shares"], target["issue_price"])
    needs_forecast = not target_valid_shares or target_valid_shares <= (target["online_issue_shares"] or 0)
    funding_history = known
    models = {}
    if needs_forecast and (funding_model == "account_pool_funds" or
            (funding_model == "multiple" and float(settings.get("subscription_prediction_account_pool_prior_weight") or 0) > 0)):
        funding_history, models = _repaired_account_history(
            known, contexts["timeline"], cutoff,
            float(settings.get("subscription_prediction_account_amount_position", .5)))
    if funding_model in subscription_funding_model.MODELS and needs_forecast:
        demand = _account_pool_demand_wan_from_row
        bucket_basis = settings.get("subscription_prediction_bucket_amount_basis", "threshold")
        if funding_model == "account_pool_funds":
            demand = lambda row, cap: subscription_account_funding.demand(models[str(row["security_code"])], cap)
        elif bucket_basis == "conserving_bounds":
            position = float(settings.get("subscription_prediction_bucket_amount_position", .5))
            demand = lambda row, cap: subscription_funding_hypotheses.conserving_demand(row, cap, position)
        elif bucket_basis != "threshold":
            raise ValueError(f"Unknown bucket amount basis: {bucket_basis}")
        funding_estimate = subscription_funding_model.estimate_funding(
            target, funding_history, row_settings, demand=demand)
    prediction = subscription_predictor.build_subscription_prediction(
        ipo_info, recent_ipos=recent_ipos, params=row_settings, funding_estimate=funding_estimate,
        funding_only=funding_only)
    # Published target facts retain their existing actual/manual semantics.
    # Only forecasts receive the same prior adjustment as offline tuning.
    if prediction.get("mode") == "estimated" and funding_model == "multiple" and not prediction.get("target_frozen_funds_override"):
        prediction = _apply_account_pool_prior(prediction, target, funding_history, row_settings)
    prediction["history_context"] = {
        "policy": "all_results_known_before_apply_date",
        "apply_date": target["apply_date"], "as_of_date": as_of or None,
        "history_source_codes": [str(row.get("security_code") or "") for row in known],
        "history_sample_count": len(known),
        "max_history_samples": max_history_samples,
        "recency_weights": row_settings.get("subscription_prediction_recency_weights"),
        "account_pool_snapshot_codes": [str(row.get("security_code") or "")
                                        for row in row_settings["subscription_prediction_account_pool_rows"]],
    }
    return prediction


def _repaired_account_history(history_rows, pool_timeline, cutoff, position):
    """Attach source-specific distributions only once for both funding paths."""
    source_map = {str(row.get("security_code")): (available, row)
                  for available, row in pool_timeline if available < cutoff}
    enriched_rows, models = [], {}
    for row in history_rows:
        code = str(row["security_code"])
        available, source = source_map.get(code, ("", {}))
        enriched = {**row, "source_lot_distribution_json": source.get("source_lot_distribution_json"),
                    "account_distribution_available_date": available}
        models[code] = subscription_account_funding.distribution(enriched, position)
        enriched["account_distribution_rejection"] = models[code].get("reason")
        enriched["_account_funding_distribution"] = models[code]
        enriched_rows.append(enriched)
    return enriched_rows, models


def _repaired_account_demand_wan_from_row(row, target_cap_wan):
    model = row.get("_account_funding_distribution") or subscription_account_funding.distribution(row)
    value = subscription_account_funding.demand(model, target_cap_wan)
    if value is None:
        return None
    return {**value, "security_code": row.get("security_code"), "apply_date": row.get("apply_date"),
            "distribution_available_date": row.get("account_distribution_available_date"),
            "is_lower_bound": False}


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是"}


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _row_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("apply_date") or row.get("listing_date") or ""),
        str(row.get("security_code") or ""),
    )


def _is_eligible_row(row: dict[str, Any]) -> bool:
    return (
        _parse_bool(row.get("model_ready")) and _parse_bool(row.get("allocation_fit_usable_for_tuning"))
    ) or _parse_bool(row.get("manual_ladder_label_ready"))


def _recent_ipo_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "SECURITY_CODE": row.get("security_code"),
        "APPLY_DATE": row.get("apply_date"),
        "ISSUE_RESULT_DATE": row.get("issue_result_date"),
        "RESULT_AVAILABLE_DATE": replay_timeline.result_available_date(row),
        "ISSUE_PRICE": _safe_float(row.get("issue_price")),
        "ONLINE_ISSUE_NUM": _safe_float(row.get("online_issue_shares")),
        "TOP_APPLY_MARKETCAP": _safe_float(row.get("top_apply_amount_wan")),
        "ONLINE_VA_SHARES": _safe_float(row.get("online_valid_shares")),
        "FROZEN_FUNDS_YI": _safe_float(row.get("frozen_funds_yi")),
        "ONLINE_ISSUE_LWR": _safe_float(row.get("allocation_rate_pct")),
        "ONLINE_ES_MULTIPLE": _safe_float(row.get("subscription_multiple")),
    }


def _target_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "SECURITY_CODE": row.get("security_code"),
        "APPLY_DATE": row.get("apply_date"),
        "ISSUE_RESULT_DATE": row.get("issue_result_date"),
        "ISSUE_PRICE": _safe_float(row.get("issue_price")),
        "ONLINE_ISSUE_NUM": _safe_float(row.get("online_issue_shares")),
        "TOP_APPLY_MARKETCAP": _safe_float(row.get("top_apply_amount_wan")),
    }


def _fit_unallocated_avg_amount_wan(row: dict[str, Any], fit: dict[str, Any]) -> float | None:
    amount = _safe_float(fit.get("unallocated_avg_amount_wan"))
    if amount is not None:
        return amount
    avg_shares = _safe_float(fit.get("unallocated_avg_shares"))
    issue_price = _safe_float(row.get("issue_price"))
    if avg_shares is None or issue_price is None or issue_price <= 0:
        return None
    return avg_shares * issue_price / 10000


def _manual_ladder_thresholds_by_lot(row: dict[str, Any]) -> dict[int, float]:
    top_apply = _safe_float(row.get("top_apply_amount_wan"))
    thresholds: dict[int, float] = {}
    for item in subscription_ladder_labels.parse_manual_ladder(row.get("manual_ladder"), top_apply):
        lot_level = int(item.get("total_lots") or 0)
        amount = _safe_float(item.get("threshold_amount_wan"))
        if lot_level > 0 and amount is not None and amount > 0:
            thresholds[lot_level] = amount
    return thresholds


def _account_pool_demand_wan_from_row(row: dict[str, Any], target_cap_wan: float) -> dict[str, Any] | None:
    fit = _parse_json_object(row.get("allocation_fit_json"))
    buckets = fit.get("buckets")
    if not isinstance(buckets, list) or target_cap_wan <= 0:
        return None

    manual_thresholds = _manual_ladder_thresholds_by_lot(row)
    bucket_demand_wan = 0.0
    bucket_accounts = 0.0
    manual_override_count = 0
    for item in buckets:
        if not isinstance(item, dict):
            continue
        accounts = _safe_float(item.get("accounts"))
        amount = _safe_float(item.get("threshold_amount_wan"))
        allocated_lots = int(_safe_float(item.get("allocated_lots")) or 0)
        manual_amount = manual_thresholds.get(allocated_lots)
        if manual_amount is not None:
            amount = manual_amount
            manual_override_count += 1
        if accounts is None or amount is None or accounts <= 0 or amount <= 0:
            continue
        bucket_accounts += accounts
        bucket_demand_wan += accounts * min(amount, target_cap_wan)

    unallocated_accounts = _safe_float(fit.get("unallocated_accounts"))
    unallocated_avg_amount = _fit_unallocated_avg_amount_wan(row, fit)
    unallocated_demand_wan = 0.0
    if (
        unallocated_accounts is not None
        and unallocated_avg_amount is not None
        and unallocated_accounts > 0
        and unallocated_avg_amount > 0
    ):
        unallocated_demand_wan = unallocated_accounts * min(unallocated_avg_amount, target_cap_wan)

    total_demand_wan = bucket_demand_wan + unallocated_demand_wan
    if total_demand_wan <= 0:
        return None

    confidence = _safe_float(row.get("allocation_fit_confidence")) or _safe_float(fit.get("fit_confidence")) or 0.0
    return {
        "security_code": row.get("security_code"),
        "apply_date": row.get("apply_date"),
        "estimated_demand_wan": total_demand_wan,
        "bucket_demand_wan": bucket_demand_wan,
        "unallocated_demand_wan": unallocated_demand_wan,
        "bucket_accounts": bucket_accounts,
        "unallocated_accounts": unallocated_accounts,
        "manual_ladder_override_count": manual_override_count,
        "confidence": confidence,
        "is_lower_bound": bool(fit.get("top_apply_below_guaranteed")),
        "fit_quality": fit.get("fit_quality") or fit.get("method") or "",
        # Retained only for the explicitly named legacy bucket experiment.
        # The production multiple prior uses repaired source distributions.
        "account_count_basis": "legacy_compressed_allocation_fit",
        "is_observed_distribution": False,
    }


def _account_pool_prior_from_history(
    history_rows: list[dict[str, Any]],
    target_row: dict[str, Any],
    *,
    recent_samples: int = 8,
    half_life_samples: float = 4.0,
    recency_weights: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    target_cap_wan = _safe_float(target_row.get("top_apply_amount_wan"))
    issue_price = _safe_float(target_row.get("issue_price"))
    online_issue_shares = _safe_float(target_row.get("online_issue_shares"))
    if target_cap_wan is None or target_cap_wan <= 0 or issue_price is None or issue_price <= 0:
        return None

    recent_rows = history_rows[-recent_samples:] if recent_samples > 0 else history_rows
    samples: list[dict[str, Any]] = []
    total_weight = 0.0
    weighted_demand_wan = 0.0
    lower_bound_count = 0
    excluded = []
    for age, row in enumerate(reversed(recent_rows)):
        available = str(row.get("account_distribution_available_date") or "")
        if not replay_timeline.known_before(row, target_row) or not available or available >= target_row["apply_date"]:
            excluded.append({"security_code": row.get("security_code"), "reason": "distribution_not_yet_available"})
            continue
        sample = _repaired_account_demand_wan_from_row(row, target_cap_wan)
        if sample is None:
            excluded.append({"security_code": row.get("security_code"),
                             "reason": row.get("account_distribution_rejection") or "unavailable_repaired_distribution"})
            continue
        recency_weight = (recency_weights.get(str(row.get("security_code") or ""), 0.0)
                          if recency_weights is not None else 0.5 ** (age / max(half_life_samples, 1.0)))
        if recency_weight <= 0:
            continue
        weight = max(float(sample.get("confidence") or 0.0), 0.05) * recency_weight
        sample["weight"] = weight
        sample["recency_weight"] = recency_weight
        total_weight += weight
        weighted_demand_wan += float(sample.get("estimated_demand_wan") or 0.0) * weight
        if sample.get("is_lower_bound"):
            lower_bound_count += 1
        samples.append(sample)

    if total_weight <= 0:
        return {"available": False, "sample_count": 0, "source_codes": [], "samples": [],
                "account_count_basis": "repaired_announcement_constrained",
                "rejected_reason": "no_available_repaired_account_sources", "excluded_sources": excluded}

    demand_wan = weighted_demand_wan / total_weight
    valid_subscription_shares = demand_wan * 10000 / issue_price
    return {
        "available": True,
        "account_count_basis": "repaired_announcement_constrained",
        "excluded_sources": excluded,
        "target_top_apply_amount_wan": target_cap_wan,
        "weighted_demand_wan": demand_wan,
        "valid_subscription_shares": valid_subscription_shares,
        "subscription_multiple": (
            valid_subscription_shares / online_issue_shares
            if online_issue_shares is not None and online_issue_shares > 0
            else None
        ),
        "sample_count": len(samples),
        "recent_samples_requested": recent_samples,
        "half_life_samples": half_life_samples,
        "source_codes": [sample.get("security_code") for sample in samples],
        "lower_bound_sample_count": lower_bound_count,
        "samples": samples,
    }


def _apply_account_pool_prior(
    prediction: dict[str, Any],
    row: dict[str, Any],
    history_rows: list[dict[str, Any]],
    params: dict[str, Any],
) -> dict[str, Any]:
    weight = float(params.get("subscription_prediction_account_pool_prior_weight") or 0.0)
    if weight <= 0 or not prediction.get("available"):
        return prediction

    recent_samples = int(float(params.get("subscription_prediction_account_pool_recent_samples", 8)))
    half_life_samples = float(params.get("subscription_prediction_account_pool_half_life_samples", 4.0))
    recency_weights = None
    if subscription_predictor._unified_recency(params):
        recent_samples = len(history_rows)
        half_life_samples = subscription_predictor._recency_half_life(params)
        recency_weights = subscription_predictor._history_recency_weights(
            [_recent_ipo_from_row(item) for item in history_rows], params)
    prior = _account_pool_prior_from_history(
        history_rows,
        row,
        recent_samples=max(recent_samples, 1),
        half_life_samples=max(half_life_samples, 1.0),
        recency_weights=recency_weights,
    )
    if prior is None:
        return prediction
    if prior.get("available") is False:
        return {**prediction, "account_pool_prior": {**prior, "floor_weight": weight, "applied": False}}

    base_valid_shares = _safe_float(prediction.get("valid_subscription_shares"))
    prior_floor_shares = float(prior.get("valid_subscription_shares") or 0.0) * weight
    prior_info = dict(prior)
    prior_info.update(
        {
            "floor_weight": weight,
            "floor_valid_subscription_shares": prior_floor_shares,
            "base_valid_subscription_shares": base_valid_shares,
            "applied": False,
        }
    )

    updated = dict(prediction)
    updated["account_pool_prior"] = prior_info

    min_source_samples = int(float(params.get("subscription_prediction_account_pool_prior_min_source_samples") or 1))
    prior_info["min_source_samples"] = min_source_samples
    if int(prior_info.get("sample_count") or 0) < min_source_samples:
        prior_info["rejected_reason"] = "source_sample_count_below_min"
        return updated

    online_issue_shares = _safe_float(prediction.get("online_issue_shares") or row.get("online_issue_shares"))
    if online_issue_shares is not None and online_issue_shares > 0:
        prior_info["base_subscription_multiple"] = (
            base_valid_shares / online_issue_shares if base_valid_shares is not None else None
        )
        prior_info["floor_subscription_multiple"] = prior_floor_shares / online_issue_shares

    min_uplift_ratio = float(params.get("subscription_prediction_account_pool_prior_min_uplift_ratio") or 0.0)
    prior_info["min_uplift_ratio"] = min_uplift_ratio
    if base_valid_shares is not None and base_valid_shares > 0:
        uplift_ratio = prior_floor_shares / base_valid_shares
        prior_info["uplift_ratio"] = uplift_ratio
        if min_uplift_ratio > 0 and uplift_ratio < min_uplift_ratio:
            prior_info["rejected_reason"] = "uplift_ratio_below_min"
            return updated

    if base_valid_shares is None or prior_floor_shares <= base_valid_shares:
        prior_info["rejected_reason"] = "floor_not_above_base"
        return updated

    issue_price = _safe_float(prediction.get("issue_price") or row.get("issue_price"))
    top_apply_shares = _safe_float(prediction.get("top_apply_shares"))
    if top_apply_shares is None:
        top_apply_shares = subscription_predictor._money_to_shares(
            _safe_float(row.get("top_apply_amount_wan")),
            issue_price,
        )
    if issue_price is None or issue_price <= 0 or online_issue_shares is None or online_issue_shares <= 0:
        return updated

    allocation_ratio = online_issue_shares / prior_floor_shares
    guaranteed_shares = subscription_predictor._ceil_to_lot(subscription_predictor.LOT_SIZE / allocation_ratio)
    guaranteed_reachable = top_apply_shares is None or guaranteed_shares <= top_apply_shares
    guaranteed_amount_wan = subscription_predictor._shares_to_amount_wan(guaranteed_shares, issue_price)
    top_apply_below_guaranteed = bool(top_apply_shares is not None and guaranteed_shares > top_apply_shares)
    top_apply_gap_shares = (guaranteed_shares - top_apply_shares) if top_apply_below_guaranteed else None

    prior_info["applied"] = True
    prior_info["updated_guaranteed_threshold_amount_wan"] = guaranteed_amount_wan
    prior_info["updated_guaranteed_threshold_reachable"] = guaranteed_reachable
    prior_info["updated_top_apply_below_guaranteed"] = top_apply_below_guaranteed
    updated.update(
        {
            "valid_subscription_shares": prior_floor_shares,
            "subscription_multiple": prior_floor_shares / online_issue_shares,
            "allocation_rate_pct": allocation_ratio * 100,
            "guaranteed_threshold_shares": guaranteed_shares,
            "guaranteed_threshold_amount_wan": guaranteed_amount_wan if guaranteed_reachable else None,
            "guaranteed_threshold_reachable": guaranteed_reachable,
            "top_apply_below_guaranteed": top_apply_below_guaranteed,
            "top_apply_gap_shares": top_apply_gap_shares,
            "top_apply_gap_amount_wan": subscription_predictor._shares_to_amount_wan(top_apply_gap_shares, issue_price),
            "account_pool_prior": prior_info,
        }
    )
    # Rebuild every derived threshold from the final funding estimate. Keeping
    # the old fractional/ladder fields would evaluate two different fund sizes.
    recalculated = subscription_predictor.build_subscription_prediction(
        {**_target_from_row(row), "ONLINE_VA_SHARES": prior_floor_shares}, params=params,
        funding_only=bool(prediction.get("funding_only")),
    )
    if recalculated.get("available"):
        updated.update(recalculated)
        for key in ("mode", "estimate", "frozen_funds_floor", "frozen_funds_cap", "target_frozen_funds_override"):
            updated[key] = prediction.get(key)
        updated["account_pool_prior"] = prior_info
    return updated
