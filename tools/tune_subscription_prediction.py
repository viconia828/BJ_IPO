from __future__ import annotations

import argparse
import csv
from datetime import datetime
import itertools
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable


ROOT_DIR = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import subscription_predictor
import subscription_ladder_labels
import config_loader
import sync_offline_tuning_dataset as offline_tuning_sync


DEFAULT_HISTORY_PATH = ROOT_DIR / "data" / "offline_tuning" / "subscription_history_sample.csv"
DEFAULT_DATASET_PATH = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_LADDER_LABEL_PATH = ROOT_DIR / "data" / "offline_tuning" / "subscription_ladder_labels.csv"
DEFAULT_PARAMS_PATH = ROOT_DIR / "策略参数.txt"
DEFAULT_AUTO_RECORD_PATH = ROOT_DIR / "自动调参记录.txt"

DEFAULT_ACCOUNT_POOL_THRESHOLDS_WAN = [300, 500, 800, 1000, 1500, 2000]

DEFAULT_BASELINE_PARAMS = {
    "subscription_prediction_sample_decay_half_life_days": 5,
    "subscription_prediction_cap_factor_direction": "median_over_target",
    "subscription_prediction_cap_factor_exponent": 0.30,
    "subscription_prediction_issue_factor_direction": "median_over_target",
    "subscription_prediction_issue_factor_exponent": 0.45,
    "subscription_prediction_lock_factor_exponent": 0.0,
    "subscription_prediction_multiple_scale": 1.0,
}

DEFAULT_SEARCH_GRID = {
    "subscription_prediction_sample_decay_half_life_days": [5, 10, 20, 40],
    "subscription_prediction_cap_factor_direction": ["target_over_median", "median_over_target"],
    "subscription_prediction_cap_factor_exponent": [0.0, 0.15, 0.25, 0.30, 0.45],
    "subscription_prediction_issue_factor_direction": ["target_over_median", "median_over_target"],
    "subscription_prediction_issue_factor_exponent": [0.0, 0.15, 0.20, 0.30, 0.45],
    "subscription_prediction_lock_factor_exponent": [0.0, 0.20, 0.35],
    "subscription_prediction_multiple_scale": [0.85, 1.0, 1.15],
}


DEFAULT_CORE_COARSE_BLOCK_GRIDS = (
    ("core_decay_coarse", {"subscription_prediction_sample_decay_half_life_days": [5, 10, 20, 40]}),
    (
        "core_cap_shape_coarse",
        {
            "subscription_prediction_cap_factor_direction": ["target_over_median", "median_over_target"],
            "subscription_prediction_cap_factor_exponent": [0.0, 0.25, 0.45],
        },
    ),
    (
        "core_issue_shape_coarse",
        {
            "subscription_prediction_issue_factor_direction": ["target_over_median", "median_over_target"],
            "subscription_prediction_issue_factor_exponent": [0.0, 0.25, 0.45],
        },
    ),
    (
        "core_lock_scale_coarse",
        {
            "subscription_prediction_lock_factor_exponent": [0.0, 0.20, 0.35],
            "subscription_prediction_multiple_scale": [0.85, 1.0, 1.15],
        },
    ),
)

SIMILAR_TOP_APPLY_FROZEN_PARAM_KEYS = (
    "subscription_prediction_similar_top_apply_frozen_enabled",
    "subscription_prediction_similar_top_apply_frozen_weight",
    "subscription_prediction_similar_top_apply_frozen_recent_samples",
    "subscription_prediction_similar_top_apply_frozen_min_samples",
    "subscription_prediction_similar_top_apply_frozen_half_life_samples",
    "subscription_prediction_similar_top_apply_frozen_max_relative_distance",
    "subscription_prediction_similar_top_apply_frozen_bandwidth",
)

DEFAULT_SIMILAR_TOP_APPLY_FROZEN_PARAMS = {
    "subscription_prediction_similar_top_apply_frozen_enabled": True,
    "subscription_prediction_similar_top_apply_frozen_weight": 0.65,
    "subscription_prediction_similar_top_apply_frozen_recent_samples": 24,
    "subscription_prediction_similar_top_apply_frozen_min_samples": 1,
    "subscription_prediction_similar_top_apply_frozen_half_life_samples": 8.0,
    "subscription_prediction_similar_top_apply_frozen_max_relative_distance": 0.35,
    "subscription_prediction_similar_top_apply_frozen_bandwidth": 0.18,
}

DEFAULT_SIMILAR_TOP_APPLY_FROZEN_COARSE_GRID = {
    "subscription_prediction_similar_top_apply_frozen_enabled": [False, True],
    "subscription_prediction_similar_top_apply_frozen_weight": [0.0, 0.5, 0.65, 0.85, 1.0],
    "subscription_prediction_similar_top_apply_frozen_recent_samples": [12, 24, 36],
    "subscription_prediction_similar_top_apply_frozen_min_samples": [1, 3],
    "subscription_prediction_similar_top_apply_frozen_half_life_samples": [4.0, 8.0, 12.0],
    "subscription_prediction_similar_top_apply_frozen_max_relative_distance": [0.20, 0.35, 0.50],
    "subscription_prediction_similar_top_apply_frozen_bandwidth": [0.10, 0.18, 0.28],
}


DEFAULT_SIMILAR_TOP_APPLY_FROZEN_COARSE_BLOCK_GRIDS = (
    (
        "similar_top_apply_frozen_enabled_weight_coarse",
        {
            "subscription_prediction_similar_top_apply_frozen_enabled": [False, True],
            "subscription_prediction_similar_top_apply_frozen_weight": [0.0, 0.5, 0.65, 0.85, 1.0],
        },
    ),
    (
        "similar_top_apply_frozen_window_coarse",
        {
            "subscription_prediction_similar_top_apply_frozen_enabled": [True],
            "subscription_prediction_similar_top_apply_frozen_recent_samples": [12, 24, 36],
            "subscription_prediction_similar_top_apply_frozen_min_samples": [1, 3],
            "subscription_prediction_similar_top_apply_frozen_half_life_samples": [4.0, 8.0, 12.0],
        },
    ),
    (
        "similar_top_apply_frozen_distance_coarse",
        {
            "subscription_prediction_similar_top_apply_frozen_enabled": [True],
            "subscription_prediction_similar_top_apply_frozen_max_relative_distance": [0.20, 0.35, 0.50],
            "subscription_prediction_similar_top_apply_frozen_bandwidth": [0.10, 0.18, 0.28],
        },
    ),
)

DEFAULT_ACCOUNT_POOL_PRIOR_BASE_PARAMS = {
    "subscription_prediction_sample_decay_half_life_days": 5,
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

MAIN_TUNABLE_PARAM_KEYS = tuple(DEFAULT_SEARCH_GRID.keys()) + SIMILAR_TOP_APPLY_FROZEN_PARAM_KEYS
PRIOR_TUNABLE_PARAM_KEYS = (
    "subscription_prediction_account_pool_prior_weight",
    "subscription_prediction_account_pool_recent_samples",
    "subscription_prediction_account_pool_half_life_samples",
    "subscription_prediction_account_pool_prior_min_uplift_ratio",
    "subscription_prediction_account_pool_prior_min_source_samples",
)


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


def _resolve_subscription_base_params(
    strategy_params: dict[str, Any] | None,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_defaults = dict(defaults or DEFAULT_BASELINE_PARAMS)
    merged = dict(base_defaults)
    for key, value in DEFAULT_SIMILAR_TOP_APPLY_FROZEN_PARAMS.items():
        merged.setdefault(key, value)
    if strategy_params:
        merged.update(strategy_params)
    for key, value in base_defaults.items():
        merged.setdefault(key, value)
    for key, value in DEFAULT_SIMILAR_TOP_APPLY_FROZEN_PARAMS.items():
        merged.setdefault(key, value)
    return merged


def _main_tunable_snapshot(params: dict[str, Any]) -> dict[str, Any]:
    return {key: params.get(key) for key in MAIN_TUNABLE_PARAM_KEYS if key in params}


def _prior_tunable_snapshot(params: dict[str, Any]) -> dict[str, Any]:
    return {key: params.get(key) for key in PRIOR_TUNABLE_PARAM_KEYS if key in params}


def _values_differ(old_value: Any, new_value: Any) -> bool:
    old_number = _safe_float(old_value)
    new_number = _safe_float(new_value)
    if old_number is not None and new_number is not None:
        return abs(old_number - new_number) > 1e-9
    return str(old_value) != str(new_value)


def _render_param_file_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _write_param_updates(params_file: str | Path, updates: dict[str, Any]) -> Path:
    path = Path(params_file)
    text = path.read_text(encoding="utf-8-sig")
    for key, value in updates.items():
        rendered = _render_param_file_value(value)
        pattern = re.compile(rf"(?m)^(\s*{re.escape(key)}\s*=\s*)([^#\r\n]*?)(\s*(?:#.*)?$)")
        if pattern.search(text):
            text = pattern.sub(lambda match: f"{match.group(1)}{rendered}{match.group(3)}", text, count=1)
            continue

        subscription_section = re.search(r"(?m)^# === 申购资金预测：正股/碎股门槛模型 ===\s*$", text)
        insert_line = f"{key} = {rendered}\n"
        if subscription_section:
            line_end = text.find("\n", subscription_section.end())
            insert_at = len(text) if line_end < 0 else line_end + 1
            text = text[:insert_at] + insert_line + text[insert_at:]
        else:
            industry_section = re.search(r"(?m)^\[industry_mapping\]\s*$", text)
            if industry_section:
                text = text[: industry_section.start()] + insert_line + text[industry_section.start() :]
            else:
                if not text.endswith("\n"):
                    text += "\n"
                text += insert_line
    path.write_text(text, encoding="utf-8")
    return path


def _refresh_subscription_history_before_tuning(
    args: argparse.Namespace,
    strategy_params: dict[str, Any],
) -> dict[str, Any] | None:
    if os.environ.get("BSE_TUNING_NO_AUTO_REFRESH") == "1" or args.no_auto_refresh_history:
        return None

    print("申购调参前调用统一样本同步入口...", flush=True)
    try:
        result = offline_tuning_sync.sync_offline_tuning_dataset(
            args,
            strategy_params,
            progress_callback=None,
            verbose=True,
        )
        return dict(result.get("history_summary") or {})
    except Exception as exc:
        print(f"申购调参前统一样本同步失败：{exc}", flush=True)
        if not args.history_path.exists():
            raise
        print("本次将继续使用旧申购资金历史样本集。", flush=True)
        return None

def _changed_main_tunable_params(base_params: dict[str, Any], candidate_params: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    fallback = _resolve_subscription_base_params(base_params)
    for key in MAIN_TUNABLE_PARAM_KEYS:
        if key not in candidate_params:
            continue
        old_value = fallback.get(key)
        new_value = candidate_params.get(key)
        if _values_differ(old_value, new_value):
            updates[key] = new_value
    return updates


def _changed_prior_tunable_params(base_params: dict[str, Any], candidate_params: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for key in PRIOR_TUNABLE_PARAM_KEYS:
        if key not in candidate_params:
            continue
        if key not in base_params or _values_differ(base_params.get(key), candidate_params.get(key)):
            updates[key] = candidate_params.get(key)
    return updates


def _changed_subscription_auto_params(
    base_params: dict[str, Any],
    candidate_params: dict[str, Any],
    *,
    include_prior: bool = False,
) -> dict[str, Any]:
    updates = _changed_main_tunable_params(base_params, candidate_params)
    if include_prior:
        updates.update(_changed_prior_tunable_params(base_params, candidate_params))
    return updates


def _load_history_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        return [dict(row) for row in csv.DictReader(file_obj)]


def _row_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("apply_date") or row.get("listing_date") or ""),
        str(row.get("security_code") or ""),
    )


def _is_eligible_row(row: dict[str, Any]) -> bool:
    return (
        _parse_bool(row.get("model_ready")) and _parse_bool(row.get("allocation_fit_usable_for_tuning"))
    ) or _parse_bool(row.get("manual_ladder_label_ready"))


def _prepare_rows_with_ladder_labels(
    rows: list[dict[str, Any]],
    label_path: Path = DEFAULT_LADDER_LABEL_PATH,
    *,
    sync_missing_rows: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if sync_missing_rows:
        sync_summary = subscription_ladder_labels.sync_label_rows(rows, label_path)
    else:
        label_rows = subscription_ladder_labels.load_label_rows(label_path)
        sync_summary = {
            "path": str(label_path),
            "row_count": len(label_rows),
            "filled_count": sum(1 for row in label_rows if str(row.get("manual_ladder") or "").strip()),
            "added_codes": [],
        }
    label_rows = subscription_ladder_labels.load_label_rows(label_path)
    merged_rows = subscription_ladder_labels.apply_labels_to_history_rows(
        rows,
        label_rows,
        apply_threshold_overrides=False,
    )
    sync_summary["merged_row_count"] = len(merged_rows)
    sync_summary["manual_ladder_ready_count"] = sum(
        1 for row in merged_rows if _parse_bool(row.get("manual_ladder_label_ready"))
    )
    return merged_rows, sync_summary


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
    manual_ladder_row_count = 0
    manual_ladder_amount_abs_errors: list[float] = []
    manual_ladder_amount_pct_errors: list[float] = []
    manual_ladder_time_priority_total = 0
    manual_ladder_time_priority_correct = 0
    manual_ladder_time_priority_misses: list[str] = []

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
        manual_ladder_items = subscription_ladder_labels.parse_manual_ladder(
            row.get("manual_ladder"),
            row.get("top_apply_amount_wan"),
        )
        if manual_ladder_items:
            manual_ladder_row_count += 1
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

        predicted_ladder_by_lot = {
            int(item.get("lots") or 0): item
            for item in (prediction.get("lot_thresholds") or [])
            if isinstance(item, dict) and int(item.get("fractional_lots") or 0) == 0
        }
        predicted_ladder_by_key = {
            (int(item.get("regular_lots") or 0), int(item.get("fractional_lots") or 0)): item
            for item in (prediction.get("lot_thresholds") or [])
            if isinstance(item, dict)
        }
        manual_ladder_errors: list[dict[str, Any]] = []
        for label_item in manual_ladder_items:
            label_lots = int(label_item.get("total_lots") or 0)
            actual_ladder_amount = _safe_float(label_item.get("threshold_amount_wan"))
            label_key = (
                int(label_item.get("regular_lots") or 0),
                int(label_item.get("fractional_lots") or 0),
            )
            predicted_ladder_item = predicted_ladder_by_key.get(label_key) or predicted_ladder_by_lot.get(label_lots) or {}
            predicted_ladder_amount = _safe_float(predicted_ladder_item.get("threshold_amount_wan"))
            ladder_abs_error = None
            ladder_pct_error = None
            if actual_ladder_amount and predicted_ladder_amount:
                ladder_abs_error = abs(predicted_ladder_amount - actual_ladder_amount)
                ladder_pct_error = ladder_abs_error / actual_ladder_amount
                manual_ladder_amount_abs_errors.append(ladder_abs_error)
                manual_ladder_amount_pct_errors.append(ladder_pct_error)
            label_time_required = bool(label_item.get("time_priority_required"))
            predicted_time_required = bool(predicted_ladder_item.get("time_priority_required"))
            time_priority_match = None
            if label_time_required:
                manual_ladder_time_priority_total += 1
                time_priority_match = predicted_time_required is True
                if time_priority_match:
                    manual_ladder_time_priority_correct += 1
                else:
                    manual_ladder_time_priority_misses.append(
                        f"{row.get('security_code')}:{label_lots}手"
                    )
            manual_ladder_errors.append(
                {
                    "regular_lots": label_item.get("regular_lots"),
                    "fractional_lots": label_item.get("fractional_lots"),
                    "total_lots": label_lots,
                    "actual_threshold_amount_wan": actual_ladder_amount,
                    "predicted_threshold_amount_wan": predicted_ladder_amount,
                    "threshold_kind": label_item.get("threshold_kind"),
                    "abs_error_wan": ladder_abs_error,
                    "pct_error": ladder_pct_error,
                    "label_time_priority_required": label_time_required,
                    "predicted_time_priority_required": predicted_time_required,
                    "time_priority_match": time_priority_match,
                }
            )

        account_pool_prior = prediction.get("account_pool_prior") or {}
        details.append(
            {
                "security_code": row.get("security_code"),
                "available": bool(prediction.get("available")),
                "actual_guaranteed_amount_wan": actual_amount,
                "predicted_guaranteed_amount_wan": predicted_amount,
                "predicted_subscription_multiple": _safe_float(prediction.get("subscription_multiple")),
                "guaranteed_amount_abs_error_wan": amount_abs_error,
                "guaranteed_amount_pct_error": amount_pct_error,
                "actual_top_apply_below_guaranteed": actual_top_apply,
                "predicted_top_apply_below_guaranteed": predicted_top_apply,
                "top_apply_classification_match": classification_match,
                "manual_ladder_label_count": len(manual_ladder_items),
                "manual_ladder_errors": manual_ladder_errors,
                "account_pool_prior_applied": bool(account_pool_prior.get("applied")),
                "account_pool_prior_weight": account_pool_prior.get("floor_weight"),
                "account_pool_prior_base_subscription_multiple": account_pool_prior.get("base_subscription_multiple"),
                "account_pool_prior_floor_subscription_multiple": account_pool_prior.get("floor_subscription_multiple"),
                "account_pool_prior_uplift_ratio": account_pool_prior.get("uplift_ratio"),
                "account_pool_prior_rejected_reason": account_pool_prior.get("rejected_reason"),
                "account_pool_prior_valid_subscription_shares": account_pool_prior.get("valid_subscription_shares"),
                "account_pool_prior_floor_valid_subscription_shares": account_pool_prior.get(
                    "floor_valid_subscription_shares"
                ),
                "account_pool_prior_updated_guaranteed_amount_wan": account_pool_prior.get(
                    "updated_guaranteed_threshold_amount_wan"
                ),
                "account_pool_prior_updated_guaranteed_reachable": account_pool_prior.get(
                    "updated_guaranteed_threshold_reachable"
                ),
                "account_pool_prior_source_codes": account_pool_prior.get("source_codes") or [],
                "account_pool_prior_sample_count": account_pool_prior.get("sample_count"),
                "account_pool_prior_lower_bound_sample_count": account_pool_prior.get("lower_bound_sample_count"),
            }
        )
        history.append(_recent_ipo_from_row(row))
        history_rows.append(row)

    mae = sum(amount_abs_errors) / len(amount_abs_errors) if amount_abs_errors else None
    mape = sum(amount_pct_errors) / len(amount_pct_errors) if amount_pct_errors else None
    ladder_mae = (
        sum(manual_ladder_amount_abs_errors) / len(manual_ladder_amount_abs_errors)
        if manual_ladder_amount_abs_errors
        else None
    )
    ladder_mape = (
        sum(manual_ladder_amount_pct_errors) / len(manual_ladder_amount_pct_errors)
        if manual_ladder_amount_pct_errors
        else None
    )
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
        "manual_ladder_label_rows": manual_ladder_row_count,
        "manual_ladder_amount_metric_rows": len(manual_ladder_amount_abs_errors),
        "manual_ladder_amount_mae_wan": ladder_mae,
        "manual_ladder_amount_mape": ladder_mape,
        "manual_ladder_time_priority_total": manual_ladder_time_priority_total,
        "manual_ladder_time_priority_correct": manual_ladder_time_priority_correct,
        "manual_ladder_time_priority_accuracy": (
            manual_ladder_time_priority_correct / manual_ladder_time_priority_total
            if manual_ladder_time_priority_total
            else None
        ),
        "manual_ladder_time_priority_misses": manual_ladder_time_priority_misses,
        "fit_residuals_weighted": _weighted_fit_residuals(eligible_rows),
        "details": details,
    }


def _candidate_params_from_grid(
    grid: dict[str, list[Any]],
    base_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    keys = list(grid.keys())
    candidates: list[dict[str, Any]] = []
    for values in itertools.product(*(grid[key] for key in keys)):
        params = dict(base_params or {})
        params.update(dict(zip(keys, values)))
        candidates.append(params)
    return candidates


def _normalize_param_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    number = _safe_float(value)
    if number is not None:
        return round(number, 10)
    return str(value or "").strip().lower()


def _candidate_signature(params: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
    similar_enabled = _parse_bool(params.get('subscription_prediction_similar_top_apply_frozen_enabled'))
    signature: list[tuple[str, Any]] = []
    for key in MAIN_TUNABLE_PARAM_KEYS:
        if (
            key in SIMILAR_TOP_APPLY_FROZEN_PARAM_KEYS
            and key != 'subscription_prediction_similar_top_apply_frozen_enabled'
            and not similar_enabled
        ):
            signature.append((key, None))
        else:
            signature.append((key, _normalize_param_value(params.get(key))))
    return tuple(signature)


def _unique_sorted_numeric_values(values: list[float], *, low: float | None = None, high: float | None = None, digits: int = 4) -> list[float]:
    clean: set[float] = set()
    for value in values:
        current = float(value)
        if low is not None:
            current = max(current, low)
        if high is not None:
            current = min(current, high)
        clean.add(round(current, digits))
    return sorted(clean)


def _unique_sorted_int_values(values: list[float], *, low: int = 1) -> list[int]:
    return sorted({max(int(round(value)), low) for value in values})


def _similar_top_apply_frozen_fine_grid(base_params: dict[str, Any]) -> dict[str, list[Any]]:
    defaults = DEFAULT_SIMILAR_TOP_APPLY_FROZEN_PARAMS
    enabled = _parse_bool(base_params.get("subscription_prediction_similar_top_apply_frozen_enabled"))
    weight = _safe_float(base_params.get("subscription_prediction_similar_top_apply_frozen_weight"))
    recent_samples = _safe_float(base_params.get("subscription_prediction_similar_top_apply_frozen_recent_samples"))
    min_samples = _safe_float(base_params.get("subscription_prediction_similar_top_apply_frozen_min_samples"))
    half_life = _safe_float(base_params.get("subscription_prediction_similar_top_apply_frozen_half_life_samples"))
    max_distance = _safe_float(base_params.get("subscription_prediction_similar_top_apply_frozen_max_relative_distance"))
    bandwidth = _safe_float(base_params.get("subscription_prediction_similar_top_apply_frozen_bandwidth"))

    weight = float(defaults["subscription_prediction_similar_top_apply_frozen_weight"] if weight is None else weight)
    recent_samples = float(defaults["subscription_prediction_similar_top_apply_frozen_recent_samples"] if recent_samples is None else recent_samples)
    min_samples = float(defaults["subscription_prediction_similar_top_apply_frozen_min_samples"] if min_samples is None else min_samples)
    half_life = float(defaults["subscription_prediction_similar_top_apply_frozen_half_life_samples"] if half_life is None else half_life)
    max_distance = float(defaults["subscription_prediction_similar_top_apply_frozen_max_relative_distance"] if max_distance is None else max_distance)
    bandwidth = float(defaults["subscription_prediction_similar_top_apply_frozen_bandwidth"] if bandwidth is None else bandwidth)

    return {
        "subscription_prediction_similar_top_apply_frozen_enabled": [enabled],
        "subscription_prediction_similar_top_apply_frozen_weight": _unique_sorted_numeric_values(
            [weight - 0.15, weight - 0.05, weight, weight + 0.05, weight + 0.15],
            low=0.0,
            high=1.0,
        ),
        "subscription_prediction_similar_top_apply_frozen_recent_samples": _unique_sorted_int_values(
            [recent_samples - 4, recent_samples, recent_samples + 4]
        ),
        "subscription_prediction_similar_top_apply_frozen_min_samples": _unique_sorted_int_values(
            [min_samples - 1, min_samples, min_samples + 1]
        ),
        "subscription_prediction_similar_top_apply_frozen_half_life_samples": _unique_sorted_numeric_values(
            [half_life - 2.0, half_life, half_life + 2.0],
            low=1.0,
        ),
        "subscription_prediction_similar_top_apply_frozen_max_relative_distance": _unique_sorted_numeric_values(
            [max_distance - 0.05, max_distance, max_distance + 0.05],
            low=0.0,
            high=1.0,
        ),
        "subscription_prediction_similar_top_apply_frozen_bandwidth": _unique_sorted_numeric_values(
            [bandwidth - 0.03, bandwidth, bandwidth + 0.03],
            low=0.01,
        ),
    }



def _current_numeric_param(base_params: dict[str, Any], key: str, default: float) -> float:
    value = _safe_float(base_params.get(key))
    return float(default if value is None else value)


def _current_choice_param(base_params: dict[str, Any], key: str, default: str, choices: tuple[str, ...]) -> str:
    value = str(base_params.get(key) or default)
    return value if value in choices else default


def _core_fine_block_grids(base_params: dict[str, Any]) -> tuple[tuple[str, dict[str, list[Any]]], ...]:
    decay = _current_numeric_param(base_params, "subscription_prediction_sample_decay_half_life_days", 5.0)
    cap_direction = _current_choice_param(
        base_params,
        "subscription_prediction_cap_factor_direction",
        "median_over_target",
        ("target_over_median", "median_over_target"),
    )
    cap_exponent = _current_numeric_param(base_params, "subscription_prediction_cap_factor_exponent", 0.30)
    issue_direction = _current_choice_param(
        base_params,
        "subscription_prediction_issue_factor_direction",
        "median_over_target",
        ("target_over_median", "median_over_target"),
    )
    issue_exponent = _current_numeric_param(base_params, "subscription_prediction_issue_factor_exponent", 0.45)
    lock_exponent = _current_numeric_param(base_params, "subscription_prediction_lock_factor_exponent", 0.0)
    multiple_scale = _current_numeric_param(base_params, "subscription_prediction_multiple_scale", 1.0)
    return (
        (
            "core_decay_fine",
            {
                "subscription_prediction_sample_decay_half_life_days": _unique_sorted_numeric_values(
                    [decay * 0.75, decay, decay * 1.25],
                    low=1.0,
                    digits=2,
                ),
            },
        ),
        (
            "core_cap_shape_fine",
            {
                "subscription_prediction_cap_factor_direction": [cap_direction],
                "subscription_prediction_cap_factor_exponent": _unique_sorted_numeric_values(
                    [cap_exponent - 0.10, cap_exponent - 0.05, cap_exponent, cap_exponent + 0.05, cap_exponent + 0.10],
                    low=0.0,
                    high=0.70,
                ),
            },
        ),
        (
            "core_issue_shape_fine",
            {
                "subscription_prediction_issue_factor_direction": [issue_direction],
                "subscription_prediction_issue_factor_exponent": _unique_sorted_numeric_values(
                    [issue_exponent - 0.10, issue_exponent - 0.05, issue_exponent, issue_exponent + 0.05, issue_exponent + 0.10],
                    low=0.0,
                    high=0.70,
                ),
            },
        ),
        (
            "core_lock_scale_fine",
            {
                "subscription_prediction_lock_factor_exponent": _unique_sorted_numeric_values(
                    [lock_exponent - 0.10, lock_exponent, lock_exponent + 0.10],
                    low=0.0,
                    high=0.70,
                ),
                "subscription_prediction_multiple_scale": _unique_sorted_numeric_values(
                    [multiple_scale - 0.05, multiple_scale, multiple_scale + 0.05],
                    low=0.70,
                    high=1.30,
                ),
            },
        ),
    )


def _similar_top_apply_frozen_fine_block_grids(base_params: dict[str, Any]) -> tuple[tuple[str, dict[str, list[Any]]], ...]:
    defaults = DEFAULT_SIMILAR_TOP_APPLY_FROZEN_PARAMS
    enabled = _parse_bool(base_params.get("subscription_prediction_similar_top_apply_frozen_enabled"))
    weight = _current_numeric_param(base_params, "subscription_prediction_similar_top_apply_frozen_weight", float(defaults["subscription_prediction_similar_top_apply_frozen_weight"]))
    recent_samples = _current_numeric_param(base_params, "subscription_prediction_similar_top_apply_frozen_recent_samples", float(defaults["subscription_prediction_similar_top_apply_frozen_recent_samples"]))
    min_samples = _current_numeric_param(base_params, "subscription_prediction_similar_top_apply_frozen_min_samples", float(defaults["subscription_prediction_similar_top_apply_frozen_min_samples"]))
    half_life = _current_numeric_param(base_params, "subscription_prediction_similar_top_apply_frozen_half_life_samples", float(defaults["subscription_prediction_similar_top_apply_frozen_half_life_samples"]))
    max_distance = _current_numeric_param(base_params, "subscription_prediction_similar_top_apply_frozen_max_relative_distance", float(defaults["subscription_prediction_similar_top_apply_frozen_max_relative_distance"]))
    bandwidth = _current_numeric_param(base_params, "subscription_prediction_similar_top_apply_frozen_bandwidth", float(defaults["subscription_prediction_similar_top_apply_frozen_bandwidth"]))
    return (
        (
            "similar_top_apply_frozen_weight_fine",
            {
                "subscription_prediction_similar_top_apply_frozen_enabled": [enabled],
                "subscription_prediction_similar_top_apply_frozen_weight": _unique_sorted_numeric_values(
                    [weight - 0.15, weight - 0.05, weight, weight + 0.05, weight + 0.15],
                    low=0.0,
                    high=1.0,
                ),
            },
        ),
        (
            "similar_top_apply_frozen_window_fine",
            {
                "subscription_prediction_similar_top_apply_frozen_enabled": [enabled],
                "subscription_prediction_similar_top_apply_frozen_recent_samples": _unique_sorted_int_values(
                    [recent_samples - 4, recent_samples, recent_samples + 4]
                ),
                "subscription_prediction_similar_top_apply_frozen_min_samples": _unique_sorted_int_values(
                    [min_samples - 1, min_samples, min_samples + 1]
                ),
                "subscription_prediction_similar_top_apply_frozen_half_life_samples": _unique_sorted_numeric_values(
                    [half_life - 2.0, half_life, half_life + 2.0],
                    low=1.0,
                ),
            },
        ),
        (
            "similar_top_apply_frozen_distance_fine",
            {
                "subscription_prediction_similar_top_apply_frozen_enabled": [enabled],
                "subscription_prediction_similar_top_apply_frozen_max_relative_distance": _unique_sorted_numeric_values(
                    [max_distance - 0.05, max_distance, max_distance + 0.05],
                    low=0.0,
                    high=1.0,
                ),
                "subscription_prediction_similar_top_apply_frozen_bandwidth": _unique_sorted_numeric_values(
                    [bandwidth - 0.03, bandwidth, bandwidth + 0.03],
                    low=0.01,
                ),
            },
        ),
    )


def _params_from_summary(base_params: dict[str, Any], summary: dict[str, Any] | None) -> dict[str, Any]:
    params = dict(base_params)
    if summary and summary.get('params'):
        params.update(dict(summary.get('params') or {}))
    return params


def _candidate_rank_key(summary: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
    false_negative_count = len(summary.get("top_apply_false_negative_codes") or [])
    false_positive_count = len(summary.get("top_apply_false_positive_codes") or [])
    ladder_mape = _safe_float(summary.get("manual_ladder_amount_mape"))
    mape = _safe_float(summary.get("guaranteed_amount_mape"))
    mae = _safe_float(summary.get("guaranteed_amount_mae_wan"))
    metric_rows = _safe_float(summary.get("guaranteed_amount_metric_rows")) or 0.0
    return (
        float(false_negative_count),
        float(false_positive_count),
        ladder_mape if ladder_mape is not None else 999.0,
        mape if mape is not None else 999.0,
        mae if mae is not None else 999999.0,
        -metric_rows,
    )


def _summary_rank_key(summary: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
    rank_key = summary.get("rank_key")
    if isinstance(rank_key, (list, tuple)) and rank_key:
        return tuple(float(value) for value in rank_key)
    return _candidate_rank_key(summary)


def _account_pool_prior_minimal_trigger_rank_key(summary: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
    false_negative_count = len(summary.get("top_apply_false_negative_codes") or [])
    false_positive_count = len(summary.get("top_apply_false_positive_codes") or [])
    applied_count = _safe_float(summary.get("account_pool_prior_applied_count")) or 0.0
    mape = _safe_float(summary.get("guaranteed_amount_mape"))
    mae = _safe_float(summary.get("guaranteed_amount_mae_wan"))
    metric_rows = _safe_float(summary.get("guaranteed_amount_metric_rows")) or 0.0
    return (
        float(false_negative_count),
        float(false_positive_count),
        applied_count,
        mape if mape is not None else 999.0,
        mae if mae is not None else 999999.0,
        -metric_rows,
    )


def _compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in summary.items() if key != "details"}


def _prior_trigger_explanations(summary: dict[str, Any]) -> list[dict[str, Any]]:
    explanations: list[dict[str, Any]] = []
    for detail in summary.get("details") or []:
        if not detail.get("account_pool_prior_applied"):
            continue
        explanations.append(
            {
                "security_code": detail.get("security_code"),
                "base_subscription_multiple": detail.get("account_pool_prior_base_subscription_multiple"),
                "prior_subscription_multiple": detail.get("account_pool_prior_floor_subscription_multiple"),
                "uplift_ratio": detail.get("account_pool_prior_uplift_ratio"),
                "updated_guaranteed_amount_wan": detail.get("account_pool_prior_updated_guaranteed_amount_wan"),
                "updated_guaranteed_reachable": detail.get("account_pool_prior_updated_guaranteed_reachable"),
                "actual_guaranteed_amount_wan": detail.get("actual_guaranteed_amount_wan"),
                "source_codes": detail.get("account_pool_prior_source_codes") or [],
                "source_sample_count": detail.get("account_pool_prior_sample_count"),
                "lower_bound_sample_count": detail.get("account_pool_prior_lower_bound_sample_count"),
            }
        )
    return explanations


def _attach_account_pool_prior_rollup(summary: dict[str, Any]) -> dict[str, Any]:
    explanations = _prior_trigger_explanations(summary)
    applied_codes = [str(item.get("security_code") or "") for item in explanations]
    summary["account_pool_prior_applied_codes"] = [code for code in applied_codes if code]
    summary["account_pool_prior_applied_count"] = len(summary["account_pool_prior_applied_codes"])
    summary["account_pool_prior_trigger_explanations"] = explanations
    return summary


def _account_pool_prior_params(
    base_params: dict[str, Any],
    *,
    weight: float,
    recent_samples: int,
    half_life_samples: float,
    min_uplift_ratio: float | None = None,
    min_source_samples: int | None = None,
) -> dict[str, Any]:
    params = dict(base_params)
    params.update(
        {
            "subscription_prediction_account_pool_prior_weight": weight,
            "subscription_prediction_account_pool_recent_samples": recent_samples,
            "subscription_prediction_account_pool_half_life_samples": half_life_samples,
        }
    )
    if min_uplift_ratio is not None:
        params["subscription_prediction_account_pool_prior_min_uplift_ratio"] = min_uplift_ratio
    if min_source_samples is not None:
        params["subscription_prediction_account_pool_prior_min_source_samples"] = min_source_samples
    return params


def evaluate_candidate_grid(
    rows: list[dict[str, Any]],
    *,
    min_history_samples: int = 3,
    max_history_samples: int | None = None,
    top_n: int = 5,
    max_candidates: int | None = None,
    base_params: dict[str, Any] | None = None,
    search_profile: str = "coarse_fine",
    fine_rounds: int = 2,
    progress_callback: Callable[[int, int, dict[str, Any] | None], None] | None = None,
) -> dict[str, Any]:
    resolved_base_params = _resolve_subscription_base_params(base_params)
    baseline = evaluate_subscription_prediction(
        rows,
        min_history_samples=min_history_samples,
        max_history_samples=max_history_samples,
        params=resolved_base_params,
    )
    baseline["params"] = _main_tunable_snapshot(resolved_base_params)
    baseline["rank_key"] = _candidate_rank_key(baseline)

    normalized_profile = str(search_profile or "coarse_fine").replace("-", "_").lower()
    use_coarse_fine = normalized_profile in {"coarse_fine", "coarsefine", "multi_round", "multiround"}
    fine_round_count = max(int(fine_rounds), 0) if use_coarse_fine else 0

    ranked: list[dict[str, Any]] = []
    round_summaries: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, Any], ...]] = set()
    best_so_far: dict[str, Any] | None = None
    evaluated_total = 0

    def _planned_block_count(blocks: tuple[tuple[str, dict[str, list[Any]]], ...], base: dict[str, Any]) -> int:
        return sum(len(_candidate_params_from_grid(grid, base)) for _, grid in blocks)

    if use_coarse_fine:
        planning_base = resolved_base_params
        planned_total = _planned_block_count(DEFAULT_CORE_COARSE_BLOCK_GRIDS, planning_base)
        for _ in range(fine_round_count):
            planned_total += _planned_block_count(_core_fine_block_grids(planning_base), planning_base)
        planned_total += _planned_block_count(DEFAULT_SIMILAR_TOP_APPLY_FROZEN_COARSE_BLOCK_GRIDS, planning_base)
        for _ in range(fine_round_count):
            planned_total += _planned_block_count(_similar_top_apply_frozen_fine_block_grids(planning_base), planning_base)
    else:
        planned_total = len(_candidate_params_from_grid(DEFAULT_SEARCH_GRID, resolved_base_params))
    if max_candidates is not None:
        planned_total = min(planned_total, max(max_candidates, 0))

    def _remaining_budget() -> int | None:
        if max_candidates is None:
            return None
        return max(max(max_candidates, 0) - evaluated_total, 0)

    def _run_round(name: str, candidates: list[dict[str, Any]]) -> bool:
        nonlocal best_so_far, evaluated_total
        before_key = tuple(best_so_far.get("rank_key") or ()) if best_so_far else None
        remaining = _remaining_budget()
        if remaining is not None:
            candidates = candidates[:remaining]
        if not candidates:
            return False
        round_count = 0
        round_best: dict[str, Any] | None = None
        for params in candidates:
            if max_candidates is not None and evaluated_total >= max(max_candidates, 0):
                break
            signature = _candidate_signature(params)
            if signature in seen:
                continue
            seen.add(signature)
            summary = evaluate_subscription_prediction(
                rows,
                min_history_samples=min_history_samples,
                max_history_samples=max_history_samples,
                params=params,
            )
            summary["params"] = _main_tunable_snapshot(params)
            summary["rank_key"] = _candidate_rank_key(summary)
            compact_summary = _compact_summary(summary)
            compact_summary["search_round"] = name
            ranked.append(compact_summary)
            round_count += 1
            evaluated_total += 1
            if best_so_far is None or tuple(compact_summary.get("rank_key") or ()) < tuple(best_so_far.get("rank_key") or ()):
                best_so_far = compact_summary
            if round_best is None or tuple(compact_summary.get("rank_key") or ()) < tuple(round_best.get("rank_key") or ()):
                round_best = compact_summary
            if progress_callback:
                total_for_progress = planned_total if planned_total > 0 else evaluated_total
                progress_interval = max(1, min(200, max(total_for_progress // 20, 1)))
                if evaluated_total == total_for_progress or evaluated_total % progress_interval == 0:
                    progress_callback(evaluated_total, total_for_progress, best_so_far)
        if round_count:
            round_summaries.append(
                {
                    "name": name,
                    "candidate_count": round_count,
                    "best": round_best,
                }
            )
        after_key = tuple(best_so_far.get("rank_key") or ()) if best_so_far else None
        return bool(after_key is not None and (before_key is None or after_key < before_key))

    def _budget_available() -> bool:
        return max_candidates is None or evaluated_total < max(max_candidates, 0)

    if progress_callback:
        progress_callback(0, planned_total, None)

    if use_coarse_fine:
        for name, grid in DEFAULT_CORE_COARSE_BLOCK_GRIDS:
            if not _budget_available():
                break
            round_base = _params_from_summary(resolved_base_params, best_so_far)
            _run_round(name, _candidate_params_from_grid(grid, round_base))

        for round_index in range(1, fine_round_count + 1):
            if not _budget_available():
                break
            pass_improved = False
            for group_index in range(len(_core_fine_block_grids(_params_from_summary(resolved_base_params, best_so_far)))):
                if not _budget_available():
                    break
                round_base = _params_from_summary(resolved_base_params, best_so_far)
                suffix, grid = _core_fine_block_grids(round_base)[group_index]
                pass_improved = _run_round(
                    f"core_fine_{round_index}_{suffix}",
                    _candidate_params_from_grid(grid, round_base),
                ) or pass_improved
            if not pass_improved:
                break

        for name, grid in DEFAULT_SIMILAR_TOP_APPLY_FROZEN_COARSE_BLOCK_GRIDS:
            if not _budget_available():
                break
            round_base = _params_from_summary(resolved_base_params, best_so_far)
            _run_round(name, _candidate_params_from_grid(grid, round_base))

        for round_index in range(1, fine_round_count + 1):
            if not _budget_available():
                break
            pass_improved = False
            for group_index in range(len(_similar_top_apply_frozen_fine_block_grids(_params_from_summary(resolved_base_params, best_so_far)))):
                if not _budget_available():
                    break
                round_base = _params_from_summary(resolved_base_params, best_so_far)
                suffix, grid = _similar_top_apply_frozen_fine_block_grids(round_base)[group_index]
                pass_improved = _run_round(
                    f"similar_top_apply_frozen_fine_{round_index}_{suffix}",
                    _candidate_params_from_grid(grid, round_base),
                ) or pass_improved
            if not pass_improved:
                break
    else:
        _run_round("core_exhaustive", _candidate_params_from_grid(DEFAULT_SEARCH_GRID, resolved_base_params))

    if progress_callback and evaluated_total > 0 and evaluated_total != planned_total:
        progress_callback(evaluated_total, evaluated_total, best_so_far)

    ranked.sort(key=lambda item: tuple(item.get("rank_key") or ()))
    return {
        "candidate_count": evaluated_total,
        "planned_candidate_count": planned_total,
        "search_profile": normalized_profile,
        "fine_rounds": fine_round_count,
        "rounds": round_summaries,
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
    fields = MAIN_TUNABLE_PARAM_KEYS
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
    base_params: dict[str, Any] | None = None,
    account_pool_prior_weights: list[float] | None = None,
    account_pool_prior_recent_samples: int = 8,
    account_pool_prior_half_life_samples: float = 4.0,
    account_pool_prior_min_uplift_ratio: float = 0.0,
    account_pool_prior_min_source_samples: int = 1,
) -> dict[str, Any]:
    min_values = min_history_values or [3, 5, 8]
    windows = history_windows or [None, 8, 12, 16]
    prior_weights = account_pool_prior_weights or [1.0, 1.1, 1.2]
    prior_recent_samples = max(account_pool_prior_recent_samples, 1)
    prior_half_life = max(account_pool_prior_half_life_samples, 1.0)
    prior_min_uplift_ratio = max(account_pool_prior_min_uplift_ratio, 0.0)
    prior_min_source_samples = max(account_pool_prior_min_source_samples, 1)
    resolved_base_params = _resolve_subscription_base_params(base_params)
    search = evaluate_candidate_grid(
        rows,
        min_history_samples=3,
        top_n=max(top_n, 1),
        base_params=resolved_base_params,
        search_profile='base',
    )
    best_params = dict(resolved_base_params)
    best_params.update(dict((search.get("best") or {}).get("params") or {}))

    cases: list[dict[str, Any]] = []
    best_wins = 0
    comparable_cases = 0
    prior_win_counts = {str(weight): 0 for weight in prior_weights}
    prior_improves_best_counts = {str(weight): 0 for weight in prior_weights}
    for min_history in min_values:
        for window in windows:
            baseline = evaluate_subscription_prediction(
                rows,
                min_history_samples=min_history,
                max_history_samples=window,
                params=resolved_base_params,
            )
            best = evaluate_subscription_prediction(
                rows,
                min_history_samples=min_history,
                max_history_samples=window,
                params=best_params,
            )
            baseline_rank = _candidate_rank_key(baseline)
            best_rank = _candidate_rank_key(best)
            prior_results: list[dict[str, Any]] = []
            for weight in prior_weights:
                prior_params = _account_pool_prior_params(
                    best_params,
                    weight=weight,
                    recent_samples=prior_recent_samples,
                    half_life_samples=prior_half_life,
                    min_uplift_ratio=prior_min_uplift_ratio,
                    min_source_samples=prior_min_source_samples,
                )
                prior = evaluate_subscription_prediction(
                    rows,
                    min_history_samples=min_history,
                    max_history_samples=window,
                    params=prior_params,
                )
                _attach_account_pool_prior_rollup(prior)
                prior["params"] = prior_params
                prior["rank_key"] = _candidate_rank_key(prior)
                prior_rank = _candidate_rank_key(prior)
                weight_key = str(weight)
                if baseline.get("evaluated_rows") and prior.get("evaluated_rows") and prior_rank < baseline_rank:
                    prior_win_counts[weight_key] += 1
                if best.get("evaluated_rows") and prior.get("evaluated_rows") and prior_rank < best_rank:
                    prior_improves_best_counts[weight_key] += 1
                prior_compact = _compact_summary(prior)
                prior_compact["beats_baseline"] = bool(prior_rank < baseline_rank)
                prior_compact["beats_best"] = bool(prior_rank < best_rank)
                prior_results.append(prior_compact)

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
                    "account_pool_prior": prior_results,
                }
            )

    return {
        "selected_best_params": _main_tunable_snapshot(best_params),
        "search_best": search.get("best") or {},
        "top_candidate_cluster": _candidate_cluster(search.get("top_candidates") or []),
        "account_pool_prior_weights": prior_weights,
        "account_pool_prior_recent_samples": prior_recent_samples,
        "account_pool_prior_half_life_samples": prior_half_life,
        "account_pool_prior_min_uplift_ratio": prior_min_uplift_ratio,
        "account_pool_prior_min_source_samples": prior_min_source_samples,
        "case_count": len(cases),
        "comparable_cases": comparable_cases,
        "best_win_count": best_wins,
        "prior_win_counts": prior_win_counts,
        "prior_improves_best_counts": prior_improves_best_counts,
        "cases": cases,
    }


def evaluate_account_pool_prior(
    rows: list[dict[str, Any]],
    *,
    min_history_samples: int = 3,
    max_history_samples: int | None = None,
    base_params: dict[str, Any] | None = None,
    weights: list[float] | None = None,
    recent_sample_values: list[int] | None = None,
    half_life_values: list[float] | None = None,
    min_uplift_ratio_values: list[float] | None = None,
    min_source_sample_values: list[int] | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    base_params = _resolve_subscription_base_params(base_params, DEFAULT_ACCOUNT_POOL_PRIOR_BASE_PARAMS)
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
    min_uplift_values = min_uplift_ratio_values or [0.0]
    min_source_values = min_source_sample_values or [1]
    ranked: list[dict[str, Any]] = []
    for weight, recent_samples, half_life, min_uplift, min_source in itertools.product(
        weight_values,
        recent_values,
        half_values,
        min_uplift_values,
        min_source_values,
    ):
        params = _account_pool_prior_params(
            base_params,
            weight=weight,
            recent_samples=recent_samples,
            half_life_samples=half_life,
            min_uplift_ratio=min_uplift,
            min_source_samples=min_source,
        )
        summary = evaluate_subscription_prediction(
            rows,
            min_history_samples=min_history_samples,
            max_history_samples=max_history_samples,
            params=params,
        )
        _attach_account_pool_prior_rollup(summary)
        summary["params"] = params
        summary["rank_key"] = _candidate_rank_key(summary)
        ranked.append(_compact_summary(summary))

    ranked.sort(key=lambda item: tuple(item.get("rank_key") or ()))
    minimal_trigger_ranked = sorted(ranked, key=_account_pool_prior_minimal_trigger_rank_key)
    return {
        "candidate_count": len(ranked),
        "top_n": max(top_n, 1),
        "min_history_samples": min_history_samples,
        "max_history_samples": max_history_samples,
        "min_uplift_ratio_values": min_uplift_values,
        "min_source_sample_values": min_source_values,
        "base_params": _main_tunable_snapshot(base_params),
        "baseline": _compact_summary(baseline),
        "best": ranked[0] if ranked else {},
        "minimal_trigger_best": minimal_trigger_ranked[0] if minimal_trigger_ranked else {},
        "top_candidates": ranked[: max(top_n, 1)],
    }


def evaluate_auto_with_prior_branch(
    rows: list[dict[str, Any]],
    *,
    min_history_samples: int = 3,
    max_history_samples: int | None = None,
    top_n: int = 5,
    max_candidates: int | None = None,
    base_params: dict[str, Any] | None = None,
    prior_weights: list[float] | None = None,
    prior_recent_sample_values: list[int] | None = None,
    prior_half_life_values: list[float] | None = None,
    prior_min_uplift_ratio_values: list[float] | None = None,
    prior_min_source_sample_values: list[int] | None = None,
    search_profile: str = 'coarse_fine',
    fine_rounds: int = 2,
    progress_callback: Callable[[int, int, dict[str, Any] | None], None] | None = None,
) -> dict[str, Any]:
    result = evaluate_candidate_grid(
        rows,
        min_history_samples=min_history_samples,
        max_history_samples=max_history_samples,
        top_n=top_n,
        max_candidates=max_candidates,
        base_params=base_params,
        search_profile=search_profile,
        fine_rounds=fine_rounds,
        progress_callback=progress_callback,
    )
    baseline = result.get("baseline") or {}
    main_best = result.get("best") or {}
    baseline_rank = _summary_rank_key(baseline)
    main_best_rank = _summary_rank_key(main_best) if main_best else baseline_rank

    prior_base_params = _resolve_subscription_base_params(base_params)
    if main_best.get("params"):
        prior_base_params.update(dict(main_best.get("params") or {}))
    prior_result = evaluate_account_pool_prior(
        rows,
        min_history_samples=min_history_samples,
        max_history_samples=max_history_samples,
        base_params=prior_base_params,
        weights=prior_weights,
        recent_sample_values=prior_recent_sample_values,
        half_life_values=prior_half_life_values,
        min_uplift_ratio_values=prior_min_uplift_ratio_values,
        min_source_sample_values=prior_min_source_sample_values,
        top_n=top_n,
    )

    prior_best = prior_result.get("best") or {}
    prior_best_rank = _summary_rank_key(prior_best) if prior_best else baseline_rank
    prior_minimal = prior_result.get("minimal_trigger_best") or {}
    if prior_best:
        prior_best["beats_baseline"] = bool(prior_best_rank < baseline_rank)
        prior_best["beats_main_best"] = bool(prior_best_rank < main_best_rank)
    if prior_minimal:
        prior_minimal_rank = _summary_rank_key(prior_minimal)
        prior_minimal["beats_baseline"] = bool(prior_minimal_rank < baseline_rank)
        prior_minimal["beats_main_best"] = bool(prior_minimal_rank < main_best_rank)

    main_beats_baseline = bool(main_best and main_best_rank < baseline_rank)
    selected_branch = "main_grid" if main_beats_baseline else "none"
    selected = main_best if main_beats_baseline else {}
    selected_rank = main_best_rank if main_beats_baseline else baseline_rank
    if prior_best and prior_best_rank < baseline_rank and prior_best_rank < selected_rank:
        selected_branch = "account_pool_prior"
        selected = prior_best
        selected_rank = prior_best_rank

    prior_result["base_branch"] = "main_grid_best"
    prior_result["base_params"] = _main_tunable_snapshot(prior_base_params)
    result["account_pool_prior_branch"] = prior_result
    result["prior_candidate_count"] = prior_result.get("candidate_count", 0)
    result["total_candidate_count"] = int(result.get("candidate_count") or 0) + int(
        result.get("prior_candidate_count") or 0
    )
    result["main_best_beats_baseline"] = main_beats_baseline
    result["prior_best_beats_baseline"] = bool(prior_best and prior_best_rank < baseline_rank)
    result["prior_best_beats_main_best"] = bool(prior_best and prior_best_rank < main_best_rank)
    result["selected_branch"] = selected_branch
    result["selected"] = selected
    result["selected_rank_key"] = list(selected_rank)
    return result


def _large_account_count_from_row(row: dict[str, Any], threshold_wan: float) -> dict[str, Any] | None:
    fit = _parse_json_object(row.get("allocation_fit_json"))
    buckets = fit.get("buckets")
    if not isinstance(buckets, list):
        return None

    top_apply_amount = _safe_float(row.get("top_apply_amount_wan"))
    manual_thresholds = _manual_ladder_thresholds_by_lot(row)
    confidence = _safe_float(row.get("allocation_fit_confidence")) or _safe_float(fit.get("fit_confidence")) or 0.0
    account_count = 0.0
    manual_override_count = 0
    for item in buckets:
        if not isinstance(item, dict):
            continue
        amount = _safe_float(item.get("threshold_amount_wan"))
        allocated_lots = int(_safe_float(item.get("allocated_lots")) or 0)
        manual_amount = manual_thresholds.get(allocated_lots)
        if manual_amount is not None:
            amount = manual_amount
            manual_override_count += 1
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
        "manual_ladder_override_count": manual_override_count,
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


def _format_code_list(codes: list[Any]) -> str:
    return ", ".join(str(code) for code in codes if code) or "-"


def _format_prior_trigger_explanation(item: dict[str, Any]) -> str:
    reachable = item.get("updated_guaranteed_reachable")
    reachable_text = "-" if reachable is None else ("是" if reachable else "否")
    return (
        f"{item.get('security_code') or '-'}: "
        f"base倍数={_format_float(item.get('base_subscription_multiple'), 2)} 倍, "
        f"prior倍数={_format_float(item.get('prior_subscription_multiple'), 2)} 倍, "
        f"上调比例={_format_float(item.get('uplift_ratio'), 3)}, "
        f"上调后正股门槛={_format_float(item.get('updated_guaranteed_amount_wan'), 4)} 万元(可达={reachable_text}), "
        f"实际正股门槛={_format_float(item.get('actual_guaranteed_amount_wan'), 4)} 万元, "
        f"来源样本={_format_code_list(item.get('source_codes') or [])}"
    )


def format_summary(summary: dict[str, Any]) -> str:
    residuals = summary.get("fit_residuals_weighted") or {}
    residual_avgs = residuals.get("averages") or {}
    accuracy = summary.get("top_apply_classification_accuracy")
    ladder_accuracy = summary.get("manual_ladder_time_priority_accuracy")
    lines = [
        "申购配售预测 baseline 回放",
        f"- 样本总数: {summary.get('total_rows', 0)}",
        f"- 可调样本: {summary.get('eligible_rows', 0)}",
        f"- 历史样本不足跳过: {summary.get('skipped_for_history', 0)}",
        f"- 实际评估样本: {summary.get('evaluated_rows', 0)}",
        f"- 正股门槛 MAE: {_format_float(summary.get('guaranteed_amount_mae_wan'), 4)} 万元",
        f"- 正股门槛 MAPE: {_format_float((summary.get('guaranteed_amount_mape') or 0) * 100 if summary.get('guaranteed_amount_mape') is not None else None, 2)}%",
        f"- 手工分档样本: {summary.get('manual_ladder_label_rows', 0)} 只，分档误差样本 {summary.get('manual_ladder_amount_metric_rows', 0)} 档",
        f"- 手工分档 MAE: {_format_float(summary.get('manual_ladder_amount_mae_wan'), 4)} 万元",
        f"- 手工分档 MAPE: {_format_float((summary.get('manual_ladder_amount_mape') or 0) * 100 if summary.get('manual_ladder_amount_mape') is not None else None, 2)}%",
        "- 手工分档抢时间命中: "
        f"{summary.get('manual_ladder_time_priority_correct', 0)}/{summary.get('manual_ladder_time_priority_total', 0)}"
        f" ({_format_float((ladder_accuracy or 0) * 100 if ladder_accuracy is not None else None, 2)}%)",
        f"- 手工分档抢时间漏判: {', '.join(summary.get('manual_ladder_time_priority_misses') or []) or '-'}",
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
        f"分档MAPE={_format_float((item.get('manual_ladder_amount_mape') or 0) * 100 if item.get('manual_ladder_amount_mape') is not None else None, 2)}%, "
        f"漏判={len(false_negative_codes)} [{', '.join(false_negative_codes) or '-'}], "
        f"误判={len(false_positive_codes)} [{', '.join(false_positive_codes) or '-'}], "
        f"params: {_format_params(item.get('params') or {})}"
    )


def _format_prior_params(params: dict[str, Any]) -> str:
    fields = (
        "subscription_prediction_account_pool_prior_weight",
        "subscription_prediction_account_pool_recent_samples",
        "subscription_prediction_account_pool_half_life_samples",
        "subscription_prediction_account_pool_prior_min_uplift_ratio",
        "subscription_prediction_account_pool_prior_min_source_samples",
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
        f"prior_applied={len(applied_codes)} [{_format_code_list(applied_codes)}], "
        f"params: {_format_prior_params(item.get('params') or {})}"
    )


def format_search_summary(result: dict[str, Any]) -> str:
    baseline = result.get("baseline") or {}
    best = result.get("best") or {}
    rounds = result.get("rounds") or []
    round_text = "；".join(
        f"{item.get('name')}={item.get('candidate_count', 0)}" for item in rounds
    ) or "-"
    lines = [
        "申购配售预测候选搜索",
        f"- 搜索模式: {result.get('search_profile') or 'base'}；细搜轮数 {result.get('fine_rounds', 0)}",
        f"- 候选组数: {result.get('candidate_count', 0)} / 计划 {result.get('planned_candidate_count', result.get('candidate_count', 0))}",
        f"- 搜索轮次: {round_text}",
        "- baseline: "
        f"MAE={_format_float(baseline.get('guaranteed_amount_mae_wan'), 4)} 万元, "
        f"MAPE={_format_float((baseline.get('guaranteed_amount_mape') or 0) * 100 if baseline.get('guaranteed_amount_mape') is not None else None, 2)}%, "
        f"分档MAPE={_format_float((baseline.get('manual_ladder_amount_mape') or 0) * 100 if baseline.get('manual_ladder_amount_mape') is not None else None, 2)}%, "
        f"漏判={len(baseline.get('top_apply_false_negative_codes') or [])}, "
        f"误判={len(baseline.get('top_apply_false_positive_codes') or [])}",
        "- best: "
        f"MAE={_format_float(best.get('guaranteed_amount_mae_wan'), 4)} 万元, "
        f"MAPE={_format_float((best.get('guaranteed_amount_mape') or 0) * 100 if best.get('guaranteed_amount_mape') is not None else None, 2)}%, "
        f"分档MAPE={_format_float((best.get('manual_ladder_amount_mape') or 0) * 100 if best.get('manual_ladder_amount_mape') is not None else None, 2)}%, "
        f"漏判={len(best.get('top_apply_false_negative_codes') or [])}, "
        f"误判={len(best.get('top_apply_false_positive_codes') or [])}",
        "",
        "Top candidates:",
    ]
    for index, item in enumerate(result.get("top_candidates") or [], start=1):
        lines.append(_format_candidate_line(index, item))
    return "\n".join(lines)


def _candidate_progress_printer(title: str) -> Callable[[int, int, dict[str, Any] | None], None]:
    def _print_progress(current: int, total: int, best_so_far: dict[str, Any] | None) -> None:
        if total <= 0:
            print(f"{title}：没有可评估的候选参数。", flush=True)
            return
        if current <= 0:
            print(f"{title}：开始评估候选参数，最多 {total} 组。", flush=True)
            return
        percent = current / total * 100
        if best_so_far:
            print(
                f"{title}进度：{current}/{total} ({percent:.1f}%)，当前最好 {_format_metric_brief(best_so_far)}",
                flush=True,
            )
        else:
            print(f"{title}进度：{current}/{total} ({percent:.1f}%)", flush=True)

    return _print_progress


def format_account_pool_prior_summary(result: dict[str, Any]) -> str:
    baseline = result.get("baseline") or {}
    best = result.get("best") or {}
    minimal_trigger_best = result.get("minimal_trigger_best") or {}
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
        "- minimal-trigger with prior: "
        f"MAE={_format_float(minimal_trigger_best.get('guaranteed_amount_mae_wan'), 4)} 万元, "
        f"MAPE={_format_float((minimal_trigger_best.get('guaranteed_amount_mape') or 0) * 100 if minimal_trigger_best.get('guaranteed_amount_mape') is not None else None, 2)}%, "
        f"漏判={len(minimal_trigger_best.get('top_apply_false_negative_codes') or [])}, "
        f"误判={len(minimal_trigger_best.get('top_apply_false_positive_codes') or [])}, "
        f"prior_applied={minimal_trigger_best.get('account_pool_prior_applied_count', 0)}, "
        f"params: {_format_prior_params(minimal_trigger_best.get('params') or {})}",
        "",
        "Top candidates:",
    ]
    for index, item in enumerate(result.get("top_candidates") or [], start=1):
        lines.append(_format_account_pool_prior_line(index, item))
    if best.get("account_pool_prior_trigger_explanations"):
        lines.append("")
        lines.append("Best prior 触发解释:")
        for item in best.get("account_pool_prior_trigger_explanations") or []:
            lines.append(f"- {_format_prior_trigger_explanation(item)}")
    return "\n".join(lines)


def _format_metric_brief(summary: dict[str, Any]) -> str:
    return (
        f"MAE={_format_float(summary.get('guaranteed_amount_mae_wan'), 4)} 万元, "
        f"MAPE={_format_float((summary.get('guaranteed_amount_mape') or 0) * 100 if summary.get('guaranteed_amount_mape') is not None else None, 2)}%, "
        f"分档MAPE={_format_float((summary.get('manual_ladder_amount_mape') or 0) * 100 if summary.get('manual_ladder_amount_mape') is not None else None, 2)}%, "
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
        f"- account-pool prior weights: {_format_code_list(result.get('account_pool_prior_weights') or [])}",
        "- account-pool prior window: "
        f"recent_samples={result.get('account_pool_prior_recent_samples')}, "
        f"half_life={result.get('account_pool_prior_half_life_samples')}",
        "- account-pool prior guards: "
        f"min_uplift_ratio={result.get('account_pool_prior_min_uplift_ratio')}, "
        f"min_source_samples={result.get('account_pool_prior_min_source_samples')}",
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
        for prior in item.get("account_pool_prior") or []:
            params = prior.get("params") or {}
            weight = params.get("subscription_prediction_account_pool_prior_weight")
            applied_codes = prior.get("account_pool_prior_applied_codes") or []
            lines.append(
                f"  prior weight={weight}: "
                f"[{_format_metric_brief(prior)}], "
                f"prior_applied={len(applied_codes)} [{_format_code_list(applied_codes)}], "
                f"beats_baseline={prior.get('beats_baseline')}, "
                f"beats_best={prior.get('beats_best')}"
            )
    trigger_lines: list[str] = []
    for case in result.get("cases") or []:
        min_history = case.get("min_history_samples")
        window = _window_label(case.get("max_history_samples"))
        for prior in case.get("account_pool_prior") or []:
            params = prior.get("params") or {}
            weight = params.get("subscription_prediction_account_pool_prior_weight")
            for explanation in prior.get("account_pool_prior_trigger_explanations") or []:
                trigger_lines.append(
                    f"- min_history={min_history}, history_window={window}, weight={weight}: "
                    f"{_format_prior_trigger_explanation(explanation)}"
                )
    if trigger_lines:
        lines.append("")
        lines.append("Prior 触发解释:")
        lines.extend(trigger_lines)
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


def _format_subscription_change_lines(base_params: dict[str, Any], updates: dict[str, Any]) -> list[str]:
    resolved_base = _resolve_subscription_base_params(base_params)
    lines: list[str] = []
    for key, new_value in updates.items():
        old_text = _render_param_file_value(resolved_base.get(key))
        new_text = _render_param_file_value(new_value)
        lines.append(f"{key}: {old_text} -> {new_text}")
    return lines


def _prompt_accept_subscription_auto(can_accept: bool) -> bool:
    print("")
    print("请选择下一步：")
    if can_accept:
        print("1. 接受本次申购资金最优参数，并写入 策略参数.txt")
    else:
        print("1. 当前未产生可写入的参数修改")
    print("2. 暂不写入并退出")
    try:
        raw_value = input("请输入选项 [默认 2]：").strip()
    except EOFError:
        return False
    choice = raw_value or "2"
    return can_accept and choice.lower() in {"1", "y", "yes", "是", "接受"}


def _prepend_subscription_auto_record(
    record_path: str | Path,
    result: dict[str, Any],
    base_params: dict[str, Any],
    updates: dict[str, Any],
    params_path: str | Path,
) -> Path:
    output_path = Path(record_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    baseline = result.get("baseline") or {}
    best = result.get("best") or {}
    selected = result.get("selected") or best
    prior_branch = result.get("account_pool_prior_branch") or {}
    prior_best = prior_branch.get("best") or {}
    change_lines = _format_subscription_change_lines(base_params, updates) or ["无参数变化"]
    record_lines = [
        f"## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 申购资金自动调参（已接受）",
        "",
        f"- 参数文件：{Path(params_path)}",
        f"- 历史样本：{result.get('history_path') or DEFAULT_HISTORY_PATH}",
        f"- 主网格候选组数：{result.get('candidate_count', 0)} / 计划 "
        f"{result.get('planned_candidate_count', result.get('candidate_count', 0))}",
        f"- 主网格搜索模式：{result.get('search_profile') or 'base'}；细搜轮数 {result.get('fine_rounds', 0)}",
        f"- prior 分支候选组数：{result.get('prior_candidate_count', 0)}",
        f"- 选中分支：{result.get('selected_branch') or 'main_grid'}",
        f"- baseline：{_format_metric_brief(baseline)}",
        f"- 主网格最优：{_format_metric_brief(best)}",
        f"- prior 分支最优：{_format_metric_brief(prior_best)}",
        f"- 新参数：{_format_metric_brief(selected)}",
        "",
        "修改参数：",
        *[f"- {line}" for line in change_lines],
        "",
    ]
    old_text = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
    output_path.write_text("\n".join(record_lines) + ("\n" + old_text if old_text else ""), encoding="utf-8")
    return output_path


def _print_subscription_auto_summary(result: dict[str, Any], *, best_beats_baseline: bool) -> None:
    baseline = result.get("baseline") or {}
    best = result.get("best") or {}
    prior_branch = result.get("account_pool_prior_branch") or {}
    prior_best = prior_branch.get("best") or {}
    prior_minimal = prior_branch.get("minimal_trigger_best") or {}
    selected = result.get("selected") or {}
    print("")
    print("申购资金自动调参结果：")
    print(
        f"- 候选组数：主网格 {result.get('candidate_count', 0)} / 计划 "
        f"{result.get('planned_candidate_count', result.get('candidate_count', 0))}，"
        f"prior 分支 {result.get('prior_candidate_count', 0)}"
    )
    print(f"- 搜索模式：{result.get('search_profile') or 'base'}；细搜轮数 {result.get('fine_rounds', 0)}")
    print(f"- 当前参数：{_format_metric_brief(baseline)}")
    print(f"- 主网格最优：{_format_metric_brief(best)}")
    if prior_best:
        print(
            f"- prior 分支最优：{_format_metric_brief(prior_best)}，"
            f"prior_applied={prior_best.get('account_pool_prior_applied_count', 0)}，"
            f"beats_main={prior_best.get('beats_main_best')}"
        )
        print(f"  prior 参数：{_format_prior_params(prior_best.get('params') or {})}")
    if prior_minimal:
        print(
            f"- prior 最小触发：{_format_metric_brief(prior_minimal)}，"
            f"prior_applied={prior_minimal.get('account_pool_prior_applied_count', 0)}"
        )
    print(f"- 选中分支：{result.get('selected_branch') or 'none'}；{_format_metric_brief(selected)}")
    if not best_beats_baseline:
        print("- 结论：最优候选未优于当前参数。")


def _run_auto_mode(
    args: argparse.Namespace,
    rows: list[dict[str, Any]],
    strategy_params: dict[str, Any],
) -> int:
    print("开始申购资金自动调参：按正股门槛、手工分档误差和抢时间漏判排序候选参数。", flush=True)
    result = evaluate_auto_with_prior_branch(
        rows,
        min_history_samples=max(args.min_history_samples, 1),
        max_history_samples=args.max_history_samples,
        top_n=max(args.top_n, 1),
        max_candidates=args.max_candidates,
        base_params=strategy_params,
        prior_weights=_parse_float_values(args.account_pool_prior_weights),
        prior_recent_sample_values=_parse_int_values(args.account_pool_prior_recent_samples),
        prior_half_life_values=_parse_float_values(args.account_pool_prior_half_lives),
        prior_min_uplift_ratio_values=_parse_float_values(args.account_pool_prior_min_uplift_ratios),
        prior_min_source_sample_values=_parse_int_values(args.account_pool_prior_min_source_samples),
        search_profile=args.search_profile,
        fine_rounds=max(args.fine_rounds, 0),
        progress_callback=_candidate_progress_printer("申购资金自动调参"),
    )
    result["history_path"] = str(args.history_path)

    baseline = result.get("baseline") or {}
    selected = result.get("selected") or {}
    baseline_rank = _summary_rank_key(baseline)
    selected_rank = _summary_rank_key(selected) if selected else baseline_rank
    selected_params = dict(selected.get("params") or {})
    best_beats_baseline = bool(selected and selected_rank < baseline_rank)
    updates = (
        _changed_subscription_auto_params(
            strategy_params,
            selected_params,
            include_prior=result.get("selected_branch") == "account_pool_prior",
        )
        if best_beats_baseline
        else {}
    )
    _print_subscription_auto_summary(result, best_beats_baseline=best_beats_baseline)

    if updates:
        print("")
        print("本次建议修改的申购资金参数：")
        for line in _format_subscription_change_lines(strategy_params, updates):
            print(f"- {line}")
    else:
        print("")
        print("本轮未找到需要写回的申购资金参数。")

    if not _prompt_accept_subscription_auto(bool(updates)):
        print("已退出申购资金自动调参，未写入参数。")
        return 0

    params_path = _write_param_updates(args.params_file, updates)
    config_loader.load_params(params_path)
    record_path = _prepend_subscription_auto_record(
        args.auto_record_path,
        result,
        strategy_params,
        updates,
        params_path,
    )
    print(f"已写入参数文件：{params_path}")
    print(f"已更新自动调参记录：{record_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate baseline subscription allocation prediction metrics.")
    parser.add_argument("--dataset-path", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--history-path", type=Path, default=DEFAULT_HISTORY_PATH)
    parser.add_argument("--ladder-label-path", type=Path, default=DEFAULT_LADDER_LABEL_PATH)
    parser.add_argument("--params-file", type=Path, default=DEFAULT_PARAMS_PATH)
    parser.add_argument("--auto-record-path", type=Path, default=DEFAULT_AUTO_RECORD_PATH)
    parser.add_argument("--rebuild-dataset", action="store_true")
    parser.add_argument("--no-auto-refresh-dataset", action="store_true")
    parser.add_argument("--no-auto-refresh-history", action="store_true")
    parser.add_argument("--no-download-missing-announcements", action="store_true")
    parser.add_argument("--download-retries", type=int, default=1)
    parser.add_argument("--download-delay-seconds", type=float, default=0.0)
    parser.add_argument("--parse-prospectus", action="store_true")
    parser.add_argument("--months", type=int, default=None)
    parser.add_argument("--page-size", type=int, default=None)
    parser.add_argument("--no-ladder-labels", action="store_true")
    parser.add_argument("--min-history-samples", type=int, default=3)
    parser.add_argument("--max-history-samples", type=int, default=None)
    parser.add_argument(
        "--mode",
        choices=("baseline", "search", "robustness", "account-pool", "account-pool-prior", "auto"),
        default="baseline",
    )
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--max-candidates", type=int, default=None)
    parser.add_argument("--search-profile", choices=("base", "coarse-fine"), default="coarse-fine")
    parser.add_argument("--fine-rounds", type=int, default=2)
    parser.add_argument("--robust-min-history-samples", default="3,5,8")
    parser.add_argument("--robust-history-windows", default="all,8,12,16")
    parser.add_argument("--robust-account-pool-prior-weights", default="1.0,1.1,1.2")
    parser.add_argument("--robust-account-pool-prior-recent-samples", type=int, default=8)
    parser.add_argument("--robust-account-pool-prior-half-life", type=float, default=4.0)
    parser.add_argument("--robust-account-pool-prior-min-uplift-ratio", type=float, default=0.0)
    parser.add_argument("--robust-account-pool-prior-min-source-samples", type=int, default=1)
    parser.add_argument("--account-pool-thresholds", default="300,500,800,1000,1500,2000")
    parser.add_argument("--account-pool-recent-samples", type=int, default=12)
    parser.add_argument("--account-pool-half-life", type=float, default=4.0)
    parser.add_argument("--account-pool-prior-weights", default="0.8,1.0,1.1,1.2")
    parser.add_argument("--account-pool-prior-recent-samples", default="8,12")
    parser.add_argument("--account-pool-prior-half-lives", default="4")
    parser.add_argument("--account-pool-prior-min-uplift-ratios", default="0")
    parser.add_argument("--account-pool-prior-min-source-samples", default="1")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of text summary.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    strategy_params = config_loader.load_params(args.params_file)
    _refresh_subscription_history_before_tuning(args, strategy_params)
    rows = _load_history_rows(args.history_path)
    ladder_summary: dict[str, Any] = {}
    if not args.no_ladder_labels:
        rows, ladder_summary = _prepare_rows_with_ladder_labels(rows, args.ladder_label_path)
    if args.mode == "auto":
        return _run_auto_mode(args, rows, strategy_params)
    if args.mode == "search":
        summary = evaluate_candidate_grid(
            rows,
            min_history_samples=max(args.min_history_samples, 1),
            max_history_samples=args.max_history_samples,
            top_n=max(args.top_n, 1),
            max_candidates=args.max_candidates,
            base_params=strategy_params,
            search_profile=args.search_profile,
            fine_rounds=max(args.fine_rounds, 0),
            progress_callback=_candidate_progress_printer("申购资金候选搜索"),
        )
        summary["history_path"] = str(args.history_path)
    elif args.mode == "robustness":
        summary = evaluate_robustness(
            rows,
            min_history_values=_parse_int_values(args.robust_min_history_samples),
            history_windows=_parse_history_windows(args.robust_history_windows),
            top_n=max(args.top_n, 1),
            base_params=strategy_params,
            account_pool_prior_weights=_parse_float_values(args.robust_account_pool_prior_weights),
            account_pool_prior_recent_samples=args.robust_account_pool_prior_recent_samples,
            account_pool_prior_half_life_samples=args.robust_account_pool_prior_half_life,
            account_pool_prior_min_uplift_ratio=args.robust_account_pool_prior_min_uplift_ratio,
            account_pool_prior_min_source_samples=args.robust_account_pool_prior_min_source_samples,
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
            base_params=strategy_params,
            weights=_parse_float_values(args.account_pool_prior_weights),
            recent_sample_values=_parse_int_values(args.account_pool_prior_recent_samples),
            half_life_values=_parse_float_values(args.account_pool_prior_half_lives),
            min_uplift_ratio_values=_parse_float_values(args.account_pool_prior_min_uplift_ratios),
            min_source_sample_values=_parse_int_values(args.account_pool_prior_min_source_samples),
            top_n=max(args.top_n, 1),
        )
        summary["history_path"] = str(args.history_path)
    else:
        summary = evaluate_subscription_prediction(
            rows,
            min_history_samples=max(args.min_history_samples, 1),
            max_history_samples=args.max_history_samples,
            params=_resolve_subscription_base_params(strategy_params),
        )
        summary["params"] = _main_tunable_snapshot(_resolve_subscription_base_params(strategy_params))
        summary["history_path"] = str(args.history_path)
    if ladder_summary:
        summary["ladder_label_summary"] = ladder_summary
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
