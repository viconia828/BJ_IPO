from __future__ import annotations

import argparse
import csv
import itertools
import json
from pathlib import Path
import sys
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import subscription_predictor


DEFAULT_HISTORY_PATH = ROOT_DIR / "data" / "offline_tuning" / "subscription_history_sample.csv"

DEFAULT_ACCOUNT_POOL_THRESHOLDS_WAN = [300, 500, 800, 1000, 1500, 2000]

DEFAULT_BASELINE_PARAMS = {
    "sample_decay_half_life_days": 20,
    "subscription_prediction_cap_factor_direction": "target_over_median",
    "subscription_prediction_cap_factor_exponent": 0.25,
    "subscription_prediction_issue_factor_direction": "target_over_median",
    "subscription_prediction_issue_factor_exponent": 0.20,
    "subscription_prediction_lock_factor_exponent": 0.35,
    "subscription_prediction_multiple_scale": 1.0,
}

DEFAULT_SEARCH_GRID = {
    "sample_decay_half_life_days": [5, 10, 20, 40],
    "subscription_prediction_cap_factor_direction": ["target_over_median", "median_over_target"],
    "subscription_prediction_cap_factor_exponent": [0.0, 0.15, 0.25, 0.30, 0.45],
    "subscription_prediction_issue_factor_direction": ["target_over_median", "median_over_target"],
    "subscription_prediction_issue_factor_exponent": [0.0, 0.15, 0.20, 0.30, 0.45],
    "subscription_prediction_lock_factor_exponent": [0.0, 0.20, 0.35],
    "subscription_prediction_multiple_scale": [0.85, 1.0, 1.15],
}

DEFAULT_ACCOUNT_POOL_PRIOR_BASE_PARAMS = {
    "sample_decay_half_life_days": 5,
    "subscription_prediction_cap_factor_direction": "median_over_target",
    "subscription_prediction_cap_factor_exponent": 0.3,
    "subscription_prediction_issue_factor_direction": "median_over_target",
    "subscription_prediction_issue_factor_exponent": 0.45,
    "subscription_prediction_lock_factor_exponent": 0.0,
    "subscription_prediction_multiple_scale": 1.0,
}

DEFAULT_ACCOUNT_POOL_PRIOR_WEIGHTS = [0.8, 1.0, 1.1, 1.2]
DEFAULT_ACCOUNT_POOL_PRIOR_RECENT_SAMPLES = [8, 12]
DEFAULT_ACCOUNT_POOL_PRIOR_HALF_LIVES = [4.0]


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


def _load_history_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        return [dict(row) for row in csv.DictReader(file_obj)]


def _row_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("apply_date") or row.get("listing_date") or ""),
        str(row.get("security_code") or ""),
    )


def _is_eligible_row(row: dict[str, Any]) -> bool:
    return _parse_bool(row.get("model_ready")) and _parse_bool(row.get("allocation_fit_usable_for_tuning"))


def _recent_ipo_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "SECURITY_CODE": row.get("security_code"),
        "APPLY_DATE": row.get("apply_date"),
        "ISSUE_RESULT_DATE": row.get("issue_result_date"),
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


def _empty_metrics() -> dict[str, float]:
    return {
        "allocated_account_abs_residual": 0.0,
        "allocated_lot_abs_residual": 0.0,
        "valid_subscription_balance_abs_residual_shares": 0.0,
        "unallocated_avg_over_cap_shares": 0.0,
        "unallocated_avg_under_zero_shares": 0.0,
        "unallocated_cap_utilization": 0.0,
    }


def _weighted_fit_residuals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = _empty_metrics()
    total_weight = 0.0
    for row in rows:
        confidence = _safe_float(row.get("allocation_fit_confidence"))
        residuals = _parse_json_object(row.get("allocation_fit_residual_json"))
        if confidence is None or confidence <= 0 or not residuals:
            continue
        total_weight += confidence
        totals["allocated_account_abs_residual"] += confidence * abs(
            float(residuals.get("allocated_account_residual") or 0.0)
        )
        totals["allocated_lot_abs_residual"] += confidence * abs(float(residuals.get("allocated_lot_residual") or 0.0))
        totals["valid_subscription_balance_abs_residual_shares"] += confidence * abs(
            float(residuals.get("valid_subscription_balance_residual_shares") or 0.0)
        )
        totals["unallocated_avg_over_cap_shares"] += confidence * float(
            residuals.get("unallocated_avg_over_cap_shares") or 0.0
        )
        totals["unallocated_avg_under_zero_shares"] += confidence * float(
            residuals.get("unallocated_avg_under_zero_shares") or 0.0
        )
        totals["unallocated_cap_utilization"] += confidence * float(residuals.get("unallocated_cap_utilization") or 0.0)

    if total_weight <= 0:
        return {"weight": 0.0, "averages": _empty_metrics()}
    return {
        "weight": round(total_weight, 6),
        "averages": {key: value / total_weight for key, value in totals.items()},
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


def _account_pool_demand_wan_from_row(row: dict[str, Any], target_cap_wan: float) -> dict[str, Any] | None:
    fit = _parse_json_object(row.get("allocation_fit_json"))
    buckets = fit.get("buckets")
    if not isinstance(buckets, list) or target_cap_wan <= 0:
        return None

    bucket_demand_wan = 0.0
    bucket_accounts = 0.0
    for item in buckets:
        if not isinstance(item, dict):
            continue
        accounts = _safe_float(item.get("accounts"))
        amount = _safe_float(item.get("threshold_amount_wan"))
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
        "confidence": confidence,
        "is_lower_bound": bool(fit.get("top_apply_below_guaranteed")),
        "fit_quality": fit.get("fit_quality") or fit.get("method") or "",
    }


def _account_pool_prior_from_history(
    history_rows: list[dict[str, Any]],
    target_row: dict[str, Any],
    *,
    recent_samples: int = 8,
    half_life_samples: float = 4.0,
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
    for age, row in enumerate(reversed(recent_rows)):
        sample = _account_pool_demand_wan_from_row(row, target_cap_wan)
        if sample is None:
            continue
        recency_weight = 0.5 ** (age / max(half_life_samples, 1.0))
        weight = max(float(sample.get("confidence") or 0.0), 0.05) * recency_weight
        sample["weight"] = weight
        total_weight += weight
        weighted_demand_wan += float(sample.get("estimated_demand_wan") or 0.0) * weight
        if sample.get("is_lower_bound"):
            lower_bound_count += 1
        samples.append(sample)

    if total_weight <= 0:
        return None

    demand_wan = weighted_demand_wan / total_weight
    valid_subscription_shares = demand_wan * 10000 / issue_price
    return {
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
    prior = _account_pool_prior_from_history(
        history_rows,
        row,
        recent_samples=max(recent_samples, 1),
        half_life_samples=max(half_life_samples, 1.0),
    )
    if prior is None:
        return prediction

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
    if base_valid_shares is None or prior_floor_shares <= base_valid_shares:
        return updated

    issue_price = _safe_float(prediction.get("issue_price") or row.get("issue_price"))
    online_issue_shares = _safe_float(prediction.get("online_issue_shares") or row.get("online_issue_shares"))
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
    return updated


def evaluate_subscription_prediction(
    rows: list[dict[str, Any]],
    *,
    min_history_samples: int = 3,
    max_history_samples: int | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = dict(params or {})
    settings.setdefault("subscription_prediction_min_samples", min_history_samples)

    sorted_rows = sorted(rows, key=_row_sort_key)
    eligible_rows = [row for row in sorted_rows if _is_eligible_row(row)]
    history: list[dict[str, Any]] = []
    history_rows: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    skipped_history = 0
    amount_abs_errors: list[float] = []
    amount_pct_errors: list[float] = []
    classification_total = 0
    classification_correct = 0
    top_apply_false_negatives: list[str] = []
    top_apply_false_positives: list[str] = []

    for row in sorted_rows:
        if not _is_eligible_row(row):
            continue
        if len(history) < min_history_samples:
            skipped_history += 1
            history.append(_recent_ipo_from_row(row))
            history_rows.append(row)
            continue

        recent_history = history[-max_history_samples:] if max_history_samples else history
        recent_history_rows = history_rows[-max_history_samples:] if max_history_samples else history_rows
        prediction = subscription_predictor.build_subscription_prediction(
            _target_from_row(row),
            recent_ipos=recent_history,
            params=settings,
        )
        prediction = _apply_account_pool_prior(prediction, row, recent_history_rows, settings)
        actual_amount = _safe_float(row.get("guaranteed_threshold_amount_wan"))
        predicted_amount = _safe_float(prediction.get("guaranteed_threshold_amount_wan"))
        amount_abs_error = None
        amount_pct_error = None
        if prediction.get("available") and actual_amount and predicted_amount:
            amount_abs_error = abs(predicted_amount - actual_amount)
            amount_pct_error = amount_abs_error / actual_amount
            amount_abs_errors.append(amount_abs_error)
            amount_pct_errors.append(amount_pct_error)

        actual_top_apply = _parse_bool(row.get("top_apply_below_guaranteed"))
        predicted_top_apply = bool(prediction.get("top_apply_below_guaranteed")) if prediction.get("available") else None
        classification_match = predicted_top_apply == actual_top_apply if predicted_top_apply is not None else None
        if classification_match is not None:
            classification_total += 1
            if classification_match:
                classification_correct += 1
            elif actual_top_apply and predicted_top_apply is False:
                top_apply_false_negatives.append(str(row.get("security_code") or ""))
            elif not actual_top_apply and predicted_top_apply is True:
                top_apply_false_positives.append(str(row.get("security_code") or ""))

        details.append(
            {
                "security_code": row.get("security_code"),
                "available": bool(prediction.get("available")),
                "actual_guaranteed_amount_wan": actual_amount,
                "predicted_guaranteed_amount_wan": predicted_amount,
                "guaranteed_amount_abs_error_wan": amount_abs_error,
                "guaranteed_amount_pct_error": amount_pct_error,
                "actual_top_apply_below_guaranteed": actual_top_apply,
                "predicted_top_apply_below_guaranteed": predicted_top_apply,
                "top_apply_classification_match": classification_match,
                "account_pool_prior_applied": bool(
                    (prediction.get("account_pool_prior") or {}).get("applied")
                ),
                "account_pool_prior_valid_subscription_shares": (
                    (prediction.get("account_pool_prior") or {}).get("valid_subscription_shares")
                ),
                "account_pool_prior_source_codes": (
                    (prediction.get("account_pool_prior") or {}).get("source_codes") or []
                ),
            }
        )
        history.append(_recent_ipo_from_row(row))
        history_rows.append(row)

    mae = sum(amount_abs_errors) / len(amount_abs_errors) if amount_abs_errors else None
    mape = sum(amount_pct_errors) / len(amount_pct_errors) if amount_pct_errors else None
    return {
        "history_path": "",
        "total_rows": len(sorted_rows),
        "eligible_rows": len(eligible_rows),
        "min_history_samples": min_history_samples,
        "max_history_samples": max_history_samples,
        "skipped_for_history": skipped_history,
        "evaluated_rows": len(details),
        "guaranteed_amount_metric_rows": len(amount_abs_errors),
        "guaranteed_amount_mae_wan": mae,
        "guaranteed_amount_mape": mape,
        "top_apply_classification_total": classification_total,
        "top_apply_classification_correct": classification_correct,
        "top_apply_classification_accuracy": (
            classification_correct / classification_total if classification_total else None
        ),
        "top_apply_false_negative_codes": [code for code in top_apply_false_negatives if code],
        "top_apply_false_positive_codes": [code for code in top_apply_false_positives if code],
        "actual_top_apply_below_guaranteed_rows": sum(
            1 for row in eligible_rows if _parse_bool(row.get("top_apply_below_guaranteed"))
        ),
        "fit_residuals_weighted": _weighted_fit_residuals(eligible_rows),
        "details": details,
    }


def _candidate_params_from_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid.keys())
    candidates: list[dict[str, Any]] = []
    for values in itertools.product(*(grid[key] for key in keys)):
        candidates.append(dict(zip(keys, values)))
    return candidates


def _candidate_rank_key(summary: dict[str, Any]) -> tuple[float, float, float, float, float]:
    false_negative_count = len(summary.get("top_apply_false_negative_codes") or [])
    false_positive_count = len(summary.get("top_apply_false_positive_codes") or [])
    mape = _safe_float(summary.get("guaranteed_amount_mape"))
    mae = _safe_float(summary.get("guaranteed_amount_mae_wan"))
    metric_rows = _safe_float(summary.get("guaranteed_amount_metric_rows")) or 0.0
    return (
        float(false_negative_count),
        float(false_positive_count),
        mape if mape is not None else 999.0,
        mae if mae is not None else 999999.0,
        -metric_rows,
    )


def _compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in summary.items() if key != "details"}


def evaluate_candidate_grid(
    rows: list[dict[str, Any]],
    *,
    min_history_samples: int = 3,
    max_history_samples: int | None = None,
    top_n: int = 5,
    max_candidates: int | None = None,
) -> dict[str, Any]:
    baseline = evaluate_subscription_prediction(
        rows,
        min_history_samples=min_history_samples,
        max_history_samples=max_history_samples,
        params=DEFAULT_BASELINE_PARAMS,
    )
    baseline["params"] = DEFAULT_BASELINE_PARAMS

    candidates = _candidate_params_from_grid(DEFAULT_SEARCH_GRID)
    if max_candidates is not None:
        candidates = candidates[: max(max_candidates, 0)]

    ranked: list[dict[str, Any]] = []
    for params in candidates:
        summary = evaluate_subscription_prediction(
            rows,
            min_history_samples=min_history_samples,
            max_history_samples=max_history_samples,
            params=params,
        )
        summary["params"] = params
        summary["rank_key"] = _candidate_rank_key(summary)
        ranked.append(_compact_summary(summary))

    ranked.sort(key=lambda item: tuple(item.get("rank_key") or ()))
    return {
        "candidate_count": len(candidates),
        "top_n": max(top_n, 1),
        "min_history_samples": min_history_samples,
        "max_history_samples": max_history_samples,
        "baseline": _compact_summary(baseline),
        "best": ranked[0] if ranked else {},
        "top_candidates": ranked[: max(top_n, 1)],
    }


def _parse_int_values(text: str) -> list[int]:
    values: list[int] = []
    for part in str(text or "").split(","):
        token = part.strip()
        if not token:
            continue
        values.append(max(int(token), 1))
    return values


def _parse_float_values(text: str) -> list[float]:
    values: list[float] = []
    for part in str(text or "").split(","):
        token = part.strip()
        if not token:
            continue
        values.append(float(token))
    return values


def _parse_history_windows(text: str) -> list[int | None]:
    values: list[int | None] = []
    for part in str(text or "").split(","):
        token = part.strip().lower()
        if not token:
            continue
        if token in {"all", "none", "0"}:
            values.append(None)
        else:
            values.append(max(int(token), 1))
    return values


def _window_label(value: int | None) -> str:
    return "all" if value is None else str(value)


def _candidate_cluster(top_candidates: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    fields = (
        "subscription_prediction_cap_factor_direction",
        "subscription_prediction_cap_factor_exponent",
        "subscription_prediction_issue_factor_direction",
        "subscription_prediction_issue_factor_exponent",
        "subscription_prediction_lock_factor_exponent",
    )
    cluster: dict[str, dict[str, int]] = {field: {} for field in fields}
    for item in top_candidates:
        params = item.get("params") or {}
        for field in fields:
            key = str(params.get(field))
            cluster[field][key] = cluster[field].get(key, 0) + 1
    return cluster


def evaluate_robustness(
    rows: list[dict[str, Any]],
    *,
    min_history_values: list[int] | None = None,
    history_windows: list[int | None] | None = None,
    top_n: int = 8,
) -> dict[str, Any]:
    min_values = min_history_values or [3, 5, 8]
    windows = history_windows or [None, 8, 12, 16]
    search = evaluate_candidate_grid(rows, min_history_samples=3, top_n=max(top_n, 1))
    best_params = dict((search.get("best") or {}).get("params") or {})

    cases: list[dict[str, Any]] = []
    best_wins = 0
    comparable_cases = 0
    for min_history in min_values:
        for window in windows:
            baseline = evaluate_subscription_prediction(
                rows,
                min_history_samples=min_history,
                max_history_samples=window,
                params=DEFAULT_BASELINE_PARAMS,
            )
            best = evaluate_subscription_prediction(
                rows,
                min_history_samples=min_history,
                max_history_samples=window,
                params=best_params,
            )
            baseline_rank = _candidate_rank_key(baseline)
            best_rank = _candidate_rank_key(best)
            if baseline.get("evaluated_rows") and best.get("evaluated_rows"):
                comparable_cases += 1
                if best_rank < baseline_rank:
                    best_wins += 1
            cases.append(
                {
                    "min_history_samples": min_history,
                    "max_history_samples": window,
                    "baseline": _compact_summary(baseline),
                    "best": _compact_summary(best),
                    "best_beats_baseline": bool(best_rank < baseline_rank),
                }
            )

    return {
        "selected_best_params": best_params,
        "search_best": search.get("best") or {},
        "top_candidate_cluster": _candidate_cluster(search.get("top_candidates") or []),
        "case_count": len(cases),
        "comparable_cases": comparable_cases,
        "best_win_count": best_wins,
        "cases": cases,
    }


def evaluate_account_pool_prior(
    rows: list[dict[str, Any]],
    *,
    min_history_samples: int = 3,
    max_history_samples: int | None = None,
    weights: list[float] | None = None,
    recent_sample_values: list[int] | None = None,
    half_life_values: list[float] | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    base_params = dict(DEFAULT_ACCOUNT_POOL_PRIOR_BASE_PARAMS)
    baseline = evaluate_subscription_prediction(
        rows,
        min_history_samples=min_history_samples,
        max_history_samples=max_history_samples,
        params=base_params,
    )
    baseline["params"] = base_params

    weight_values = weights or DEFAULT_ACCOUNT_POOL_PRIOR_WEIGHTS
    recent_values = recent_sample_values or DEFAULT_ACCOUNT_POOL_PRIOR_RECENT_SAMPLES
    half_values = half_life_values or DEFAULT_ACCOUNT_POOL_PRIOR_HALF_LIVES
    ranked: list[dict[str, Any]] = []
    for weight, recent_samples, half_life in itertools.product(weight_values, recent_values, half_values):
        params = dict(base_params)
        params.update(
            {
                "subscription_prediction_account_pool_prior_weight": weight,
                "subscription_prediction_account_pool_recent_samples": recent_samples,
                "subscription_prediction_account_pool_half_life_samples": half_life,
            }
        )
        summary = evaluate_subscription_prediction(
            rows,
            min_history_samples=min_history_samples,
            max_history_samples=max_history_samples,
            params=params,
        )
        applied_codes = [
            str(item.get("security_code") or "")
            for item in summary.get("details") or []
            if item.get("account_pool_prior_applied")
        ]
        summary["params"] = params
        summary["account_pool_prior_applied_codes"] = [code for code in applied_codes if code]
        summary["account_pool_prior_applied_count"] = len(summary["account_pool_prior_applied_codes"])
        summary["rank_key"] = _candidate_rank_key(summary)
        ranked.append(_compact_summary(summary))

    ranked.sort(key=lambda item: tuple(item.get("rank_key") or ()))
    return {
        "candidate_count": len(ranked),
        "top_n": max(top_n, 1),
        "min_history_samples": min_history_samples,
        "max_history_samples": max_history_samples,
        "base_params": base_params,
        "baseline": _compact_summary(baseline),
        "best": ranked[0] if ranked else {},
        "top_candidates": ranked[: max(top_n, 1)],
    }


def _large_account_count_from_row(row: dict[str, Any], threshold_wan: float) -> dict[str, Any] | None:
    fit = _parse_json_object(row.get("allocation_fit_json"))
    buckets = fit.get("buckets")
    if not isinstance(buckets, list):
        return None

    top_apply_amount = _safe_float(row.get("top_apply_amount_wan"))
    confidence = _safe_float(row.get("allocation_fit_confidence")) or _safe_float(fit.get("fit_confidence")) or 0.0
    account_count = 0.0
    for item in buckets:
        if not isinstance(item, dict):
            continue
        amount = _safe_float(item.get("threshold_amount_wan"))
        accounts = _safe_float(item.get("accounts"))
        if amount is not None and accounts is not None and amount >= threshold_wan:
            account_count += accounts

    if top_apply_amount is not None and top_apply_amount < threshold_wan:
        account_count = 0.0
        basis = "above_top_apply_zero"
    elif account_count > 0:
        basis = str(fit.get("fit_quality") or fit.get("method") or "allocation_fit_bucket")
    else:
        basis = "no_bucket_above_threshold"

    return {
        "security_code": row.get("security_code"),
        "apply_date": row.get("apply_date"),
        "threshold_wan": threshold_wan,
        "estimated_accounts": account_count,
        "confidence": confidence,
        "basis": basis,
        "is_lower_bound": bool(fit.get("top_apply_below_guaranteed") and account_count > 0),
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    clean = sorted(values)
    middle = len(clean) // 2
    if len(clean) % 2 == 1:
        return clean[middle]
    return (clean[middle - 1] + clean[middle]) / 2


def evaluate_large_account_pool(
    rows: list[dict[str, Any]],
    *,
    thresholds_wan: list[float] | None = None,
    recent_samples: int = 12,
    half_life_samples: float = 4.0,
) -> dict[str, Any]:
    thresholds = thresholds_wan or DEFAULT_ACCOUNT_POOL_THRESHOLDS_WAN
    eligible_rows = [row for row in sorted(rows, key=_row_sort_key) if _is_eligible_row(row)]
    recent_rows = eligible_rows[-recent_samples:] if recent_samples > 0 else eligible_rows
    summaries: list[dict[str, Any]] = []
    for threshold in thresholds:
        samples: list[dict[str, Any]] = []
        total_weight = 0.0
        weighted_total = 0.0
        lower_bound_count = 0
        for age, row in enumerate(reversed(recent_rows)):
            sample = _large_account_count_from_row(row, threshold)
            if sample is None:
                continue
            recency_weight = 0.5 ** (age / max(half_life_samples, 1.0))
            weight = max(float(sample.get("confidence") or 0.0), 0.05) * recency_weight
            sample["weight"] = weight
            total_weight += weight
            weighted_total += float(sample.get("estimated_accounts") or 0.0) * weight
            if sample.get("is_lower_bound"):
                lower_bound_count += 1
            samples.append(sample)

        counts = [float(item.get("estimated_accounts") or 0.0) for item in samples]
        latest = samples[0] if samples else None
        summaries.append(
            {
                "threshold_wan": threshold,
                "sample_count": len(samples),
                "recent_samples_requested": recent_samples,
                "weighted_accounts": weighted_total / total_weight if total_weight > 0 else None,
                "median_accounts": _median(counts),
                "latest_accounts": latest.get("estimated_accounts") if latest else None,
                "latest_code": latest.get("security_code") if latest else "",
                "lower_bound_sample_count": lower_bound_count,
                "samples": samples,
            }
        )

    return {
        "eligible_rows": len(eligible_rows),
        "recent_rows": len(recent_rows),
        "recent_sample_codes": [row.get("security_code") for row in recent_rows],
        "thresholds_wan": thresholds,
        "half_life_samples": half_life_samples,
        "summaries": summaries,
    }


def _format_float(value: Any, digits: int = 4) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    return f"{number:.{digits}f}"


def format_summary(summary: dict[str, Any]) -> str:
    residuals = summary.get("fit_residuals_weighted") or {}
    residual_avgs = residuals.get("averages") or {}
    accuracy = summary.get("top_apply_classification_accuracy")
    lines = [
        "申购配售预测 baseline 回放",
        f"- 样本总数: {summary.get('total_rows', 0)}",
        f"- 可调样本: {summary.get('eligible_rows', 0)}",
        f"- 历史样本不足跳过: {summary.get('skipped_for_history', 0)}",
        f"- 实际评估样本: {summary.get('evaluated_rows', 0)}",
        f"- 正股门槛 MAE: {_format_float(summary.get('guaranteed_amount_mae_wan'), 4)} 万元",
        f"- 正股门槛 MAPE: {_format_float((summary.get('guaranteed_amount_mape') or 0) * 100 if summary.get('guaranteed_amount_mape') is not None else None, 2)}%",
        "- 顶格不足正股分类准确率: "
        f"{summary.get('top_apply_classification_correct', 0)}/{summary.get('top_apply_classification_total', 0)}"
        f" ({_format_float((accuracy or 0) * 100 if accuracy is not None else None, 2)}%)",
        f"- 顶格不足正股漏判: {', '.join(summary.get('top_apply_false_negative_codes') or []) or '-'}",
        f"- 顶格不足正股误判: {', '.join(summary.get('top_apply_false_positive_codes') or []) or '-'}",
        f"- 强标签样本数: {summary.get('actual_top_apply_below_guaranteed_rows', 0)}",
        f"- 残差加权样本权重: {_format_float(residuals.get('weight'), 4)}",
        f"- 加权获配户数残差: {_format_float(residual_avgs.get('allocated_account_abs_residual'), 4)}",
        f"- 加权获配手数残差: {_format_float(residual_avgs.get('allocated_lot_abs_residual'), 4)}",
        f"- 加权有效申购股数重建残差: {_format_float(residual_avgs.get('valid_subscription_balance_abs_residual_shares'), 4)} 股",
        f"- 加权未获配户均上限利用率: {_format_float(residual_avgs.get('unallocated_cap_utilization'), 4)}",
    ]
    return "\n".join(lines)


def _format_params(params: dict[str, Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in params.items())


def _format_candidate_line(index: int, item: dict[str, Any]) -> str:
    false_negative_codes = item.get("top_apply_false_negative_codes") or []
    false_positive_codes = item.get("top_apply_false_positive_codes") or []
    return (
        f"{index}. MAE={_format_float(item.get('guaranteed_amount_mae_wan'), 4)} 万元, "
        f"MAPE={_format_float((item.get('guaranteed_amount_mape') or 0) * 100 if item.get('guaranteed_amount_mape') is not None else None, 2)}%, "
        f"漏判={len(false_negative_codes)} [{', '.join(false_negative_codes) or '-'}], "
        f"误判={len(false_positive_codes)} [{', '.join(false_positive_codes) or '-'}], "
        f"params: {_format_params(item.get('params') or {})}"
    )


def _format_prior_params(params: dict[str, Any]) -> str:
    fields = (
        "subscription_prediction_account_pool_prior_weight",
        "subscription_prediction_account_pool_recent_samples",
        "subscription_prediction_account_pool_half_life_samples",
    )
    return ", ".join(f"{field}={params.get(field)}" for field in fields)


def _format_account_pool_prior_line(index: int, item: dict[str, Any]) -> str:
    false_negative_codes = item.get("top_apply_false_negative_codes") or []
    false_positive_codes = item.get("top_apply_false_positive_codes") or []
    applied_codes = item.get("account_pool_prior_applied_codes") or []
    return (
        f"{index}. MAE={_format_float(item.get('guaranteed_amount_mae_wan'), 4)} 万元, "
        f"MAPE={_format_float((item.get('guaranteed_amount_mape') or 0) * 100 if item.get('guaranteed_amount_mape') is not None else None, 2)}%, "
        f"漏判={len(false_negative_codes)} [{', '.join(false_negative_codes) or '-'}], "
        f"误判={len(false_positive_codes)} [{', '.join(false_positive_codes) or '-'}], "
        f"prior_applied={len(applied_codes)} [{', '.join(applied_codes) or '-'}], "
        f"params: {_format_prior_params(item.get('params') or {})}"
    )


def format_search_summary(result: dict[str, Any]) -> str:
    baseline = result.get("baseline") or {}
    best = result.get("best") or {}
    lines = [
        "申购配售预测候选搜索",
        f"- 候选组数: {result.get('candidate_count', 0)}",
        "- baseline: "
        f"MAE={_format_float(baseline.get('guaranteed_amount_mae_wan'), 4)} 万元, "
        f"MAPE={_format_float((baseline.get('guaranteed_amount_mape') or 0) * 100 if baseline.get('guaranteed_amount_mape') is not None else None, 2)}%, "
        f"漏判={len(baseline.get('top_apply_false_negative_codes') or [])}, "
        f"误判={len(baseline.get('top_apply_false_positive_codes') or [])}",
        "- best: "
        f"MAE={_format_float(best.get('guaranteed_amount_mae_wan'), 4)} 万元, "
        f"MAPE={_format_float((best.get('guaranteed_amount_mape') or 0) * 100 if best.get('guaranteed_amount_mape') is not None else None, 2)}%, "
        f"漏判={len(best.get('top_apply_false_negative_codes') or [])}, "
        f"误判={len(best.get('top_apply_false_positive_codes') or [])}",
        "",
        "Top candidates:",
    ]
    for index, item in enumerate(result.get("top_candidates") or [], start=1):
        lines.append(_format_candidate_line(index, item))
    return "\n".join(lines)


def format_account_pool_prior_summary(result: dict[str, Any]) -> str:
    baseline = result.get("baseline") or {}
    best = result.get("best") or {}
    lines = [
        "大户资金池 prior 检查",
        f"- 候选组数: {result.get('candidate_count', 0)}",
        "- baseline(best params, no prior): "
        f"MAE={_format_float(baseline.get('guaranteed_amount_mae_wan'), 4)} 万元, "
        f"MAPE={_format_float((baseline.get('guaranteed_amount_mape') or 0) * 100 if baseline.get('guaranteed_amount_mape') is not None else None, 2)}%, "
        f"漏判={len(baseline.get('top_apply_false_negative_codes') or [])}, "
        f"误判={len(baseline.get('top_apply_false_positive_codes') or [])}",
        "- best with prior: "
        f"MAE={_format_float(best.get('guaranteed_amount_mae_wan'), 4)} 万元, "
        f"MAPE={_format_float((best.get('guaranteed_amount_mape') or 0) * 100 if best.get('guaranteed_amount_mape') is not None else None, 2)}%, "
        f"漏判={len(best.get('top_apply_false_negative_codes') or [])}, "
        f"误判={len(best.get('top_apply_false_positive_codes') or [])}, "
        f"prior_applied={best.get('account_pool_prior_applied_count', 0)}",
        "",
        "Top candidates:",
    ]
    for index, item in enumerate(result.get("top_candidates") or [], start=1):
        lines.append(_format_account_pool_prior_line(index, item))
    return "\n".join(lines)


def _format_metric_brief(summary: dict[str, Any]) -> str:
    return (
        f"MAE={_format_float(summary.get('guaranteed_amount_mae_wan'), 4)} 万元, "
        f"MAPE={_format_float((summary.get('guaranteed_amount_mape') or 0) * 100 if summary.get('guaranteed_amount_mape') is not None else None, 2)}%, "
        f"漏判={len(summary.get('top_apply_false_negative_codes') or [])}, "
        f"误判={len(summary.get('top_apply_false_positive_codes') or [])}, "
        f"评估={summary.get('evaluated_rows', 0)}"
    )


def _format_cluster(cluster: dict[str, dict[str, int]]) -> list[str]:
    lines: list[str] = []
    for field, counts in cluster.items():
        summary = ", ".join(f"{key}:{value}" for key, value in sorted(counts.items(), key=lambda item: (-item[1], item[0])))
        lines.append(f"- {field}: {summary or '-'}")
    return lines


def format_robustness_summary(result: dict[str, Any]) -> str:
    lines = [
        "申购配售预测稳健性复核",
        f"- case 数: {result.get('case_count', 0)}",
        f"- 可比较 case 数: {result.get('comparable_cases', 0)}",
        f"- best 优于 baseline: {result.get('best_win_count', 0)}/{result.get('comparable_cases', 0)}",
        f"- selected best params: {_format_params(result.get('selected_best_params') or {})}",
        "",
        "Top candidate 参数集中度:",
    ]
    lines.extend(_format_cluster(result.get("top_candidate_cluster") or {}))
    lines.append("")
    lines.append("窗口结果:")
    for item in result.get("cases") or []:
        min_history = item.get("min_history_samples")
        window = _window_label(item.get("max_history_samples"))
        lines.append(
            f"- min_history={min_history}, history_window={window}: "
            f"baseline[{_format_metric_brief(item.get('baseline') or {})}] -> "
            f"best[{_format_metric_brief(item.get('best') or {})}], "
            f"best_win={item.get('best_beats_baseline')}"
        )
    return "\n".join(lines)


def format_large_account_pool_summary(result: dict[str, Any]) -> str:
    lines = [
        "打新大户资金池参考",
        f"- 可用样本: {result.get('eligible_rows', 0)}",
        f"- 近期样本: {result.get('recent_rows', 0)}",
        f"- 近期样本代码: {', '.join(str(code) for code in result.get('recent_sample_codes') or [])}",
        f"- 半衰期: {result.get('half_life_samples')} 个样本",
        "",
        "阈值估算:",
    ]
    for item in result.get("summaries") or []:
        lower_bound_note = ""
        if item.get("lower_bound_sample_count"):
            lower_bound_note = f"，其中 {item.get('lower_bound_sample_count')} 个样本为时间优先下限"
        lines.append(
            "- >={threshold} 万: 平滑约 {weighted} 户，中位数 {median} 户，最新样本 {latest_code} 为 {latest} 户{note}".format(
                threshold=_format_float(item.get("threshold_wan"), 0),
                weighted=_format_float(item.get("weighted_accounts"), 0),
                median=_format_float(item.get("median_accounts"), 0),
                latest_code=item.get("latest_code") or "-",
                latest=_format_float(item.get("latest_accounts"), 0),
                note=lower_bound_note,
            )
        )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate baseline subscription allocation prediction metrics.")
    parser.add_argument("--history-path", type=Path, default=DEFAULT_HISTORY_PATH)
    parser.add_argument("--min-history-samples", type=int, default=3)
    parser.add_argument("--max-history-samples", type=int, default=None)
    parser.add_argument(
        "--mode",
        choices=("baseline", "search", "robustness", "account-pool", "account-pool-prior"),
        default="baseline",
    )
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--max-candidates", type=int, default=None)
    parser.add_argument("--robust-min-history-samples", default="3,5,8")
    parser.add_argument("--robust-history-windows", default="all,8,12,16")
    parser.add_argument("--account-pool-thresholds", default="300,500,800,1000,1500,2000")
    parser.add_argument("--account-pool-recent-samples", type=int, default=12)
    parser.add_argument("--account-pool-half-life", type=float, default=4.0)
    parser.add_argument("--account-pool-prior-weights", default="0.8,1.0,1.1,1.2")
    parser.add_argument("--account-pool-prior-recent-samples", default="8,12")
    parser.add_argument("--account-pool-prior-half-lives", default="4")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of text summary.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = _load_history_rows(args.history_path)
    if args.mode == "search":
        summary = evaluate_candidate_grid(
            rows,
            min_history_samples=max(args.min_history_samples, 1),
            max_history_samples=args.max_history_samples,
            top_n=max(args.top_n, 1),
            max_candidates=args.max_candidates,
        )
        summary["history_path"] = str(args.history_path)
    elif args.mode == "robustness":
        summary = evaluate_robustness(
            rows,
            min_history_values=_parse_int_values(args.robust_min_history_samples),
            history_windows=_parse_history_windows(args.robust_history_windows),
            top_n=max(args.top_n, 1),
        )
        summary["history_path"] = str(args.history_path)
    elif args.mode == "account-pool":
        summary = evaluate_large_account_pool(
            rows,
            thresholds_wan=_parse_float_values(args.account_pool_thresholds),
            recent_samples=max(args.account_pool_recent_samples, 1),
            half_life_samples=max(args.account_pool_half_life, 1.0),
        )
        summary["history_path"] = str(args.history_path)
    elif args.mode == "account-pool-prior":
        summary = evaluate_account_pool_prior(
            rows,
            min_history_samples=max(args.min_history_samples, 1),
            max_history_samples=args.max_history_samples,
            weights=_parse_float_values(args.account_pool_prior_weights),
            recent_sample_values=_parse_int_values(args.account_pool_prior_recent_samples),
            half_life_values=_parse_float_values(args.account_pool_prior_half_lives),
            top_n=max(args.top_n, 1),
        )
        summary["history_path"] = str(args.history_path)
    else:
        summary = evaluate_subscription_prediction(
            rows,
            min_history_samples=max(args.min_history_samples, 1),
            max_history_samples=args.max_history_samples,
        )
        summary["history_path"] = str(args.history_path)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif args.mode == "search":
        print(format_search_summary(summary))
    elif args.mode == "robustness":
        print(format_robustness_summary(summary))
    elif args.mode == "account-pool":
        print(format_large_account_pool_summary(summary))
    elif args.mode == "account-pool-prior":
        print(format_account_pool_prior_summary(summary))
    else:
        print(format_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
