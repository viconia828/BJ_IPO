from __future__ import annotations

from datetime import date
import math
import statistics
from typing import Any


LOT_SIZE = 100
DEFAULT_GUARANTEED_BUFFER_MIN_WAN = 50.0
DEFAULT_GUARANTEED_BUFFER_MAX_WAN = 100.0
DEFAULT_FROZEN_FUNDS_FLOOR_RECENT_SAMPLES = 20
DEFAULT_FROZEN_FUNDS_FLOOR_QUANTILE = 0.0
DEFAULT_FROZEN_FUNDS_FLOOR_WEIGHT = 0.95
DEFAULT_LOT_THRESHOLD_MAX_LOTS = 20


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    if value in (None, "", "--"):
        return None
    text = str(value).strip().split(" ", 1)[0].replace("/", "-")
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _fmt_number(value: Any, digits: int = 2, fallback: str = "-") -> str:
    number = _safe_float(value)
    if number is None:
        return fallback
    return f"{number:.{digits}f}"


def _fmt_amount_wan(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    if number >= 10000:
        return f"{number / 10000:.2f} 亿元"
    return f"{number:.2f} 万元"


def _fmt_buffer_wan(min_value: Any, max_value: Any) -> str:
    min_amount = _safe_float(min_value)
    max_amount = _safe_float(max_value)
    if min_amount is None or max_amount is None:
        return "-"
    if abs(min_amount - max_amount) < 1e-9:
        return f"{min_amount:.0f} 万元"
    return f"{min_amount:.0f}-{max_amount:.0f} 万元"


def _fmt_protected_amount_range(min_value: Any, max_value: Any) -> str:
    min_amount = _safe_float(min_value)
    max_amount = _safe_float(max_value)
    if min_amount is None or max_amount is None:
        return "-"
    if abs(min_amount - max_amount) < 1e-9:
        return _fmt_amount_wan(min_amount)
    return f"{min_amount:.2f}-{max_amount:.2f} 万元"


def _resolve_guaranteed_buffer_range(settings: dict[str, Any]) -> tuple[float, float]:
    min_buffer = _safe_float(settings.get("subscription_prediction_guaranteed_buffer_min_wan"))
    max_buffer = _safe_float(settings.get("subscription_prediction_guaranteed_buffer_max_wan"))
    if min_buffer is None:
        min_buffer = DEFAULT_GUARANTEED_BUFFER_MIN_WAN
    if max_buffer is None:
        max_buffer = DEFAULT_GUARANTEED_BUFFER_MAX_WAN
    min_buffer = max(min_buffer, 0.0)
    max_buffer = max(max_buffer, min_buffer)
    return min_buffer, max_buffer


def _resolve_lot_threshold_max_lots(settings: dict[str, Any]) -> int:
    raw_value = _safe_float(settings.get("subscription_prediction_lot_threshold_max_lots"))
    if raw_value is None:
        raw_value = DEFAULT_LOT_THRESHOLD_MAX_LOTS
    return max(int(raw_value), 1)


def _fmt_shares(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    if number >= 10000:
        return f"{number / 10000:.2f} 万股"
    return f"{number:.0f} 股"


def _fmt_accounts(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    if number >= 10000:
        return f"{number / 10000:.2f} 万户"
    return f"{number:.0f} 户"


def _fmt_yi(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    return f"{number:.2f} 亿元"


def _ceil_to_lot(shares: float, lot_size: int = LOT_SIZE) -> int:
    return int(math.ceil(shares / lot_size) * lot_size)


def _floor_to_lot(shares: float, lot_size: int = LOT_SIZE) -> int:
    return int(math.floor(shares / lot_size) * lot_size)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _is_enabled(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off", "否", "关闭"}


def _round_metric(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    rounded = round(float(value), digits)
    return 0.0 if rounded == -0.0 else rounded


def _weighted_median(values: list[tuple[float, float]]) -> float | None:
    clean = sorted((value, max(weight, 0.0)) for value, weight in values if value > 0 and weight > 0)
    if not clean:
        return None
    total_weight = sum(weight for _, weight in clean)
    cursor = 0.0
    for value, weight in clean:
        cursor += weight
        if cursor >= total_weight / 2:
            return value
    return clean[-1][0]


def _quantile(values: list[float], quantile: float) -> float | None:
    clean = sorted(value for value in values if value > 0)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    q = _clamp(float(quantile), 0.0, 1.0)
    position = (len(clean) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return clean[lower]
    ratio = position - lower
    return clean[lower] * (1 - ratio) + clean[upper] * ratio


def _money_to_shares(amount_wan: float | None, issue_price: float | None) -> int | None:
    if amount_wan is None or amount_wan <= 0 or issue_price is None or issue_price <= 0:
        return None
    return _floor_to_lot(amount_wan * 10000 / issue_price)


def _shares_to_amount_wan(shares: float | None, issue_price: float | None) -> float | None:
    if shares is None or issue_price is None or issue_price <= 0:
        return None
    return shares * issue_price / 10000


def _resolve_online_issue_shares(record: dict[str, Any]) -> tuple[float | None, str]:
    online_issue = _safe_float(record.get("ONLINE_ISSUE_NUM"))
    if online_issue and online_issue > 0:
        return online_issue, "ONLINE_ISSUE_NUM"
    total_issue_wan = _safe_float(record.get("TOTAL_ISSUE_NUM"))
    if total_issue_wan and total_issue_wan > 0:
        return total_issue_wan * 10000, "TOTAL_ISSUE_NUM fallback"
    issue_num_wan = _safe_float(record.get("ISSUE_NUM"))
    if issue_num_wan and issue_num_wan > 0:
        return issue_num_wan * 10000, "ISSUE_NUM fallback"
    return None, ""


def _resolve_top_apply_amount_wan(record: dict[str, Any], issue_price: float | None) -> tuple[float | None, str]:
    for field_name in ("TOP_APPLY_MARKETCAP", "APPLY_AMT_UPPER"):
        amount = _safe_float(record.get(field_name))
        if amount and amount > 0:
            return amount, field_name
    limit_wan_shares = _safe_float(record.get("SUBSCRIPTION_LIMIT_WAN_SHARES"))
    if limit_wan_shares and limit_wan_shares > 0 and issue_price and issue_price > 0:
        return limit_wan_shares * issue_price, "SUBSCRIPTION_LIMIT_WAN_SHARES"
    online_apply_upper = _safe_float(record.get("ONLINE_APPLY_UPPER"))
    if online_apply_upper and online_apply_upper > 0 and issue_price and issue_price > 0:
        return online_apply_upper * issue_price / 10000, "ONLINE_APPLY_UPPER"
    return None, ""


def _resolve_valid_subscription_shares(
    record: dict[str, Any],
    online_issue_shares: float | None,
    issue_price: float | None,
) -> tuple[float | None, str]:
    valid_shares = _safe_float(record.get("ONLINE_VA_SHARES"))
    if valid_shares and valid_shares > 0:
        return valid_shares, "ONLINE_VA_SHARES"

    frozen_yi = _safe_float(record.get("FROZEN_FUNDS_YI"))
    if frozen_yi and frozen_yi > 0 and issue_price and issue_price > 0:
        return frozen_yi * 100000000 / issue_price, "FROZEN_FUNDS_YI"

    lwr_pct = _safe_float(record.get("ONLINE_ISSUE_LWR"))
    if lwr_pct and lwr_pct > 0 and online_issue_shares and online_issue_shares > 0:
        return online_issue_shares * 100 / lwr_pct, "ONLINE_ISSUE_LWR"

    multiple = _safe_float(record.get("ONLINE_ES_MULTIPLE"))
    if multiple and multiple > 0 and online_issue_shares and online_issue_shares > 0:
        return online_issue_shares * multiple, "ONLINE_ES_MULTIPLE"

    return None, ""


def _resolve_allocated_accounts(record: dict[str, Any]) -> tuple[float | None, str]:
    allocated_accounts = _safe_float(record.get("ONLINE_ALLOCATED_ACCOUNTS"))
    if allocated_accounts and allocated_accounts > 0:
        return allocated_accounts, "ONLINE_ALLOCATED_ACCOUNTS"
    return None, ""


def _resolve_lock_days(record: dict[str, Any]) -> tuple[int | None, str]:
    apply_date = _parse_date(record.get("APPLY_DATE"))
    result_date = _parse_date(record.get("ISSUE_RESULT_DATE") or record.get("BALLOT_NUM_DATE"))
    if apply_date is None or result_date is None:
        return None, ""
    return max((result_date - apply_date).days, 1), "APPLY_DATE->ISSUE_RESULT_DATE"


def _historical_sample(record: dict[str, Any]) -> dict[str, float] | None:
    issue_price = _safe_float(record.get("ISSUE_PRICE"))
    online_issue_shares, _ = _resolve_online_issue_shares(record)
    valid_shares, _ = _resolve_valid_subscription_shares(record, online_issue_shares, issue_price)
    if not issue_price or issue_price <= 0 or not online_issue_shares or online_issue_shares <= 0:
        return None
    if not valid_shares or valid_shares <= online_issue_shares:
        return None

    top_apply_wan, _ = _resolve_top_apply_amount_wan(record, issue_price)
    lock_days, _ = _resolve_lock_days(record)
    issue_amount_yi = online_issue_shares * issue_price / 100000000
    return {
        "security_code": str(record.get("SECURITY_CODE") or ""),
        "subscription_multiple": valid_shares / online_issue_shares,
        "frozen_funds_yi": valid_shares * issue_price / 100000000,
        "top_apply_wan": top_apply_wan or 0.0,
        "issue_amount_yi": issue_amount_yi,
        "lock_days": float(lock_days or 3),
    }


def _frozen_funds_floor_from_samples(
    samples: list[dict[str, float]],
    issue_price: float,
    online_issue_shares: float,
    params: dict[str, Any],
) -> dict[str, Any] | None:
    if not _is_enabled(params.get("subscription_prediction_frozen_funds_floor_enabled"), default=True):
        return None
    recent_sample_count = int(
        float(
            params.get(
                "subscription_prediction_frozen_funds_floor_recent_samples",
                DEFAULT_FROZEN_FUNDS_FLOOR_RECENT_SAMPLES,
            )
        )
    )
    floor_samples = samples[:recent_sample_count] if recent_sample_count > 0 else list(samples)
    min_samples = int(float(params.get("subscription_prediction_frozen_funds_floor_min_samples", 3)))
    frozen_values = [float(sample["frozen_funds_yi"]) for sample in floor_samples if sample.get("frozen_funds_yi", 0) > 0]
    if len(frozen_values) < min_samples:
        return None

    quantile = float(
        params.get(
            "subscription_prediction_frozen_funds_floor_quantile",
            DEFAULT_FROZEN_FUNDS_FLOOR_QUANTILE,
        )
    )
    raw_floor_yi = _quantile(frozen_values, quantile)
    if raw_floor_yi is None or raw_floor_yi <= 0:
        return None

    weight = max(
        float(params.get("subscription_prediction_frozen_funds_floor_weight", DEFAULT_FROZEN_FUNDS_FLOOR_WEIGHT)),
        0.0,
    )
    floor_yi = raw_floor_yi * weight
    floor_valid_shares = floor_yi * 100000000 / issue_price
    return {
        "raw_floor_frozen_funds_yi": raw_floor_yi,
        "floor_frozen_funds_yi": floor_yi,
        "floor_weight": weight,
        "floor_quantile": _clamp(quantile, 0.0, 1.0),
        "floor_recent_samples": recent_sample_count,
        "source_sample_count": len(frozen_values),
        "source_codes": [str(sample.get("security_code") or "") for sample in floor_samples if sample.get("frozen_funds_yi", 0) > 0],
        "floor_valid_subscription_shares": floor_valid_shares,
        "floor_subscription_multiple": floor_valid_shares / online_issue_shares,
    }


def _estimate_valid_subscription_shares(
    target: dict[str, Any],
    recent_ipos: list[dict[str, Any]],
    issue_price: float,
    online_issue_shares: float,
    top_apply_wan: float | None,
    lock_days: int | None,
    params: dict[str, Any],
) -> dict[str, Any]:
    samples = [sample for item in recent_ipos if (sample := _historical_sample(item))]
    min_samples = int(float(params.get("subscription_prediction_min_samples", 3)))
    if len(samples) < min_samples:
        return {
            "valid_subscription_shares": None,
            "sample_count": len(samples),
            "reason": f"历史有效申购样本不足 {min_samples} 个",
        }

    today_weight = 1.0
    half_life = max(
        float(
            params.get(
                "subscription_prediction_sample_decay_half_life_days",
                params.get("sample_decay_half_life_days", 20),
            )
        ),
        1.0,
    )
    weighted_multiples: list[tuple[float, float]] = []
    for index, sample in enumerate(samples):
        weight = today_weight * (0.5 ** (index / half_life))
        weighted_multiples.append((sample["subscription_multiple"], weight))
    median_multiple = _weighted_median(weighted_multiples) or statistics.median(sample["subscription_multiple"] for sample in samples)

    median_top_apply = statistics.median(sample["top_apply_wan"] for sample in samples if sample["top_apply_wan"] > 0) if any(sample["top_apply_wan"] > 0 for sample in samples) else None
    median_issue_amount = statistics.median(sample["issue_amount_yi"] for sample in samples if sample["issue_amount_yi"] > 0)
    median_lock_days = statistics.median(sample["lock_days"] for sample in samples if sample["lock_days"] > 0)
    target_issue_amount = online_issue_shares * issue_price / 100000000

    cap_exponent = max(float(params.get("subscription_prediction_cap_factor_exponent", 0.25)), 0.0)
    cap_direction = str(params.get("subscription_prediction_cap_factor_direction", "target_over_median"))
    cap_factor = 1.0
    if top_apply_wan and median_top_apply:
        cap_ratio = median_top_apply / top_apply_wan if cap_direction == "median_over_target" else top_apply_wan / median_top_apply
        cap_factor = _clamp(
            cap_ratio**cap_exponent,
            float(params.get("subscription_prediction_cap_factor_min", 0.75)),
            float(params.get("subscription_prediction_cap_factor_max", 1.30)),
        )

    issue_exponent = max(float(params.get("subscription_prediction_issue_factor_exponent", 0.20)), 0.0)
    issue_direction = str(params.get("subscription_prediction_issue_factor_direction", "target_over_median"))
    issue_factor = 1.0
    if median_issue_amount:
        issue_ratio = (
            median_issue_amount / target_issue_amount
            if issue_direction == "median_over_target"
            else target_issue_amount / median_issue_amount
        )
        issue_factor = _clamp(
            issue_ratio**issue_exponent,
            float(params.get("subscription_prediction_issue_factor_min", 0.80)),
            float(params.get("subscription_prediction_issue_factor_max", 1.25)),
        )
    lock_factor = 1.0
    lock_exponent = max(float(params.get("subscription_prediction_lock_factor_exponent", 0.35)), 0.0)
    if lock_days and median_lock_days:
        lock_factor = _clamp(
            (median_lock_days / lock_days) ** lock_exponent,
            float(params.get("subscription_prediction_lock_factor_min", 0.75)),
            float(params.get("subscription_prediction_lock_factor_max", 1.25)),
        )

    multiple_scale = max(float(params.get("subscription_prediction_multiple_scale", 1.0)), 0.01)
    predicted_multiple = max(median_multiple * cap_factor * issue_factor * lock_factor * multiple_scale, 1.01)
    base_valid_shares = online_issue_shares * predicted_multiple
    base_frozen_funds_yi = base_valid_shares * issue_price / 100000000
    frozen_floor = _frozen_funds_floor_from_samples(samples, issue_price, online_issue_shares, params)
    frozen_floor_applied = False
    valid_subscription_shares = base_valid_shares
    if frozen_floor:
        floor_valid_shares = float(frozen_floor.get("floor_valid_subscription_shares") or 0.0)
        if floor_valid_shares > valid_subscription_shares:
            valid_subscription_shares = floor_valid_shares
            predicted_multiple = valid_subscription_shares / online_issue_shares
            frozen_floor_applied = True
    if frozen_floor:
        frozen_floor["applied"] = frozen_floor_applied
        frozen_floor["base_frozen_funds_yi"] = base_frozen_funds_yi
        frozen_floor["base_valid_subscription_shares"] = base_valid_shares
        frozen_floor["base_subscription_multiple"] = base_valid_shares / online_issue_shares
        frozen_floor["uplift_ratio"] = (
            valid_subscription_shares / base_valid_shares if base_valid_shares > 0 else None
        )
    return {
        "valid_subscription_shares": valid_subscription_shares,
        "sample_count": len(samples),
        "median_subscription_multiple": median_multiple,
        "cap_factor": cap_factor,
        "cap_factor_direction": cap_direction,
        "issue_factor": issue_factor,
        "issue_factor_direction": issue_direction,
        "lock_factor": lock_factor,
        "multiple_scale": multiple_scale,
        "predicted_subscription_multiple": predicted_multiple,
        "base_predicted_subscription_multiple": base_valid_shares / online_issue_shares,
        "base_predicted_frozen_funds_yi": base_frozen_funds_yi,
        "frozen_funds_floor": frozen_floor,
        "reason": "",
    }


def _allocation_fit_residuals(
    *,
    buckets: list[dict[str, Any]],
    allocated_accounts: int,
    total_lots: int,
    valid_subscription_shares: float,
    allocated_min_shares: float,
    unallocated_accounts: int,
    unallocated_avg_shares: float | None,
    unallocated_cap_shares: float | None,
) -> dict[str, Any]:
    bucket_account_total = sum(int(item.get("accounts") or 0) for item in buckets)
    bucket_lot_total = sum(
        int(item.get("accounts") or 0) * int(item.get("allocated_lots") or 0)
        for item in buckets
    )
    if unallocated_avg_shares is None:
        reconstructed_subscription_shares = allocated_min_shares
    else:
        reconstructed_subscription_shares = allocated_min_shares + unallocated_avg_shares * unallocated_accounts

    over_cap_shares = None
    cap_utilization = None
    if unallocated_avg_shares is not None and unallocated_cap_shares and unallocated_cap_shares > 0:
        over_cap_shares = max(unallocated_avg_shares - unallocated_cap_shares, 0.0)
        cap_utilization = unallocated_avg_shares / unallocated_cap_shares

    under_zero_shares = max(-(unallocated_avg_shares or 0.0), 0.0) if unallocated_avg_shares is not None else None
    return {
        "allocated_account_residual": bucket_account_total - allocated_accounts,
        "allocated_lot_residual": bucket_lot_total - total_lots,
        "valid_subscription_balance_residual_shares": _round_metric(
            valid_subscription_shares - reconstructed_subscription_shares,
        ),
        "unallocated_avg_over_cap_shares": _round_metric(over_cap_shares),
        "unallocated_avg_under_zero_shares": _round_metric(under_zero_shares),
        "unallocated_cap_utilization": _round_metric(cap_utilization),
    }


def _allocation_fit_confidence(
    method: str,
    fit_quality: str,
    residuals: dict[str, Any],
    *,
    allocated_accounts: int,
    total_lots: int,
    valid_subscription_shares: float,
    unallocated_cap_shares: float | None,
) -> float:
    if method == "top_apply_below_guaranteed":
        base = 0.95 if fit_quality == "time_priority_label" else 0.85
    elif fit_quality == "rough_lot_account_fit":
        base = 0.70
    elif fit_quality == "weak_residual_over_cap":
        base = 0.40
    elif fit_quality == "weak_residual_under_zero":
        base = 0.35
    else:
        base = 0.55

    account_penalty = abs(float(residuals.get("allocated_account_residual") or 0.0)) / max(allocated_accounts, 1)
    lot_penalty = abs(float(residuals.get("allocated_lot_residual") or 0.0)) / max(total_lots, 1)
    balance_penalty = abs(float(residuals.get("valid_subscription_balance_residual_shares") or 0.0)) / max(
        valid_subscription_shares,
        1.0,
    )
    cap_penalty = 0.0
    if unallocated_cap_shares and unallocated_cap_shares > 0:
        cap_penalty += float(residuals.get("unallocated_avg_over_cap_shares") or 0.0) / unallocated_cap_shares
        cap_penalty += float(residuals.get("unallocated_avg_under_zero_shares") or 0.0) / unallocated_cap_shares
    penalty = min(account_penalty * 2 + lot_penalty * 2 + balance_penalty + cap_penalty, 0.8)
    return round(_clamp(base - penalty, 0.0, 1.0), 4)


def _fit_allocation_buckets(
    *,
    issue_price: float,
    online_issue_shares: float,
    valid_subscription_shares: float,
    valid_accounts: float | None,
    allocated_accounts: float | None,
    top_apply_shares: float | None,
    allocation_ratio: float,
    guaranteed_shares: int,
    guaranteed_reachable: bool,
) -> dict[str, Any] | None:
    if (
        not valid_accounts
        or valid_accounts <= 0
        or not allocated_accounts
        or allocated_accounts <= 0
        or not top_apply_shares
        or top_apply_shares <= 0
        or allocation_ratio <= 0
    ):
        return None

    allocated_accounts_int = int(round(allocated_accounts))
    valid_accounts_int = int(round(valid_accounts))
    total_lots = int(round(online_issue_shares / LOT_SIZE))
    if allocated_accounts_int <= 0 or valid_accounts_int < allocated_accounts_int or total_lots <= 0:
        return None

    top_apply_shares_int = int(top_apply_shares)
    if not guaranteed_reachable:
        bucket_accounts = min(allocated_accounts_int, total_lots)
        unallocated_accounts = max(valid_accounts_int - bucket_accounts, 0)
        unallocated_avg_shares = (
            max(valid_subscription_shares - bucket_accounts * top_apply_shares_int, 0) / unallocated_accounts
            if unallocated_accounts
            else None
        )
        buckets = [
            {
                "allocated_lots": 1,
                "accounts": bucket_accounts,
                "threshold_shares": top_apply_shares_int,
                "threshold_amount_wan": _shares_to_amount_wan(top_apply_shares_int, issue_price),
                "basis": "top_apply_time_priority",
            }
        ]
        allocated_min_shares = bucket_accounts * top_apply_shares_int
        fit_quality = "time_priority_label" if allocated_accounts_int == total_lots else "time_priority_label_with_account_gap"
        residuals = _allocation_fit_residuals(
            buckets=buckets,
            allocated_accounts=allocated_accounts_int,
            total_lots=total_lots,
            valid_subscription_shares=valid_subscription_shares,
            allocated_min_shares=allocated_min_shares,
            unallocated_accounts=unallocated_accounts,
            unallocated_avg_shares=unallocated_avg_shares,
            unallocated_cap_shares=top_apply_shares_int,
        )
        fit_confidence = _allocation_fit_confidence(
            "top_apply_below_guaranteed",
            fit_quality,
            residuals,
            allocated_accounts=allocated_accounts_int,
            total_lots=total_lots,
            valid_subscription_shares=valid_subscription_shares,
            unallocated_cap_shares=top_apply_shares_int,
        )
        return {
            "available": True,
            "method": "top_apply_below_guaranteed",
            "fit_quality": fit_quality,
            "fit_confidence": fit_confidence,
            "fit_usable_for_tuning": fit_confidence >= 0.55,
            "fit_residuals": residuals,
            "allocated_accounts": allocated_accounts_int,
            "allocated_lots_total": total_lots,
            "average_lots_per_allocated_account": total_lots / allocated_accounts_int,
            "unallocated_accounts": unallocated_accounts,
            "unallocated_avg_shares": unallocated_avg_shares,
            "unallocated_avg_amount_wan": _shares_to_amount_wan(unallocated_avg_shares, issue_price),
            "unallocated_cap_shares": top_apply_shares_int,
            "top_apply_below_guaranteed": True,
            "buckets": buckets,
        }

    if total_lots < allocated_accounts_int:
        return None

    extra_lots = total_lots - allocated_accounts_int
    max_full_lots = max(_floor_to_lot(top_apply_shares_int * allocation_ratio) // LOT_SIZE, 1)
    max_lots = max_full_lots + 1
    max_lots = max(1, min(int(max_lots), max(total_lots, 1)))

    buckets: list[dict[str, Any]] = []
    remaining_accounts = allocated_accounts_int
    remaining_extra_lots = extra_lots
    for lots in range(max_lots, 1, -1):
        if remaining_extra_lots <= 0 or remaining_accounts <= 0:
            break
        account_count = min(remaining_accounts, remaining_extra_lots // (lots - 1))
        if account_count <= 0:
            continue
        threshold_shares = min(top_apply_shares_int, _ceil_to_lot(lots * LOT_SIZE / allocation_ratio))
        buckets.append(
            {
                "allocated_lots": lots,
                "accounts": int(account_count),
                "threshold_shares": threshold_shares,
                "threshold_amount_wan": _shares_to_amount_wan(threshold_shares, issue_price),
                "basis": "compressed_extra_lots",
            }
        )
        remaining_accounts -= int(account_count)
        remaining_extra_lots -= int(account_count) * (lots - 1)

    if remaining_extra_lots > 0 and remaining_accounts > 0:
        lots = remaining_extra_lots + 1
        threshold_shares = min(top_apply_shares_int, _ceil_to_lot(lots * LOT_SIZE / allocation_ratio))
        buckets.append(
            {
                "allocated_lots": int(lots),
                "accounts": 1,
                "threshold_shares": threshold_shares,
                "threshold_amount_wan": _shares_to_amount_wan(threshold_shares, issue_price),
                "basis": "residual_extra_lots",
            }
        )
        remaining_accounts -= 1
        remaining_extra_lots = 0

    fractional_cutoff_shares = max(LOT_SIZE, min(top_apply_shares_int, guaranteed_shares - LOT_SIZE))
    if remaining_accounts > 0:
        buckets.append(
            {
                "allocated_lots": 1,
                "accounts": int(remaining_accounts),
                "threshold_shares": fractional_cutoff_shares,
                "threshold_amount_wan": _shares_to_amount_wan(fractional_cutoff_shares, issue_price),
                "basis": "fractional_cutoff_estimate",
            }
        )

    allocated_min_shares = sum(
        float(item["accounts"]) * float(item["threshold_shares"])
        for item in buckets
        if item.get("accounts") and item.get("threshold_shares")
    )
    unallocated_accounts = max(valid_accounts_int - allocated_accounts_int, 0)
    remaining_applied_shares = valid_subscription_shares - allocated_min_shares
    unallocated_avg_shares = remaining_applied_shares / unallocated_accounts if unallocated_accounts > 0 else None
    residual_over_cap_shares = None
    residual_under_zero_shares = None
    if unallocated_avg_shares is not None:
        residual_over_cap_shares = max(unallocated_avg_shares - fractional_cutoff_shares, 0.0)
        residual_under_zero_shares = max(-unallocated_avg_shares, 0.0)
    fit_quality = "rough"
    if residual_under_zero_shares and residual_under_zero_shares > 0:
        fit_quality = "weak_residual_under_zero"
    elif residual_over_cap_shares and residual_over_cap_shares > 0:
        fit_quality = "weak_residual_over_cap"
    elif remaining_extra_lots == 0:
        fit_quality = "rough_lot_account_fit"
    sorted_buckets = sorted(buckets, key=lambda item: int(item.get("allocated_lots") or 0), reverse=True)
    residuals = _allocation_fit_residuals(
        buckets=sorted_buckets,
        allocated_accounts=allocated_accounts_int,
        total_lots=total_lots,
        valid_subscription_shares=valid_subscription_shares,
        allocated_min_shares=allocated_min_shares,
        unallocated_accounts=unallocated_accounts,
        unallocated_avg_shares=unallocated_avg_shares,
        unallocated_cap_shares=fractional_cutoff_shares,
    )
    fit_confidence = _allocation_fit_confidence(
        "account_lot_equation",
        fit_quality,
        residuals,
        allocated_accounts=allocated_accounts_int,
        total_lots=total_lots,
        valid_subscription_shares=valid_subscription_shares,
        unallocated_cap_shares=fractional_cutoff_shares,
    )

    return {
        "available": True,
        "method": "account_lot_equation",
        "fit_quality": fit_quality,
        "fit_confidence": fit_confidence,
        "fit_usable_for_tuning": fit_confidence >= 0.55,
        "fit_residuals": residuals,
        "allocated_accounts": allocated_accounts_int,
        "allocated_lots_total": total_lots,
        "average_lots_per_allocated_account": total_lots / allocated_accounts_int,
        "unallocated_accounts": unallocated_accounts,
        "unallocated_avg_shares": unallocated_avg_shares,
        "unallocated_avg_amount_wan": _shares_to_amount_wan(unallocated_avg_shares, issue_price),
        "unallocated_cap_shares": fractional_cutoff_shares,
        "residual_over_cap_shares": residual_over_cap_shares,
        "top_apply_below_guaranteed": False,
        "buckets": sorted_buckets,
    }


def _distribution_cutoff(
    distribution: Any,
    allocation_ratio: float,
    online_issue_shares: float,
) -> dict[str, Any] | None:
    if not isinstance(distribution, list) or not distribution:
        return None
    rows: list[tuple[float, int]] = []
    for item in distribution:
        if not isinstance(item, dict):
            continue
        apply_shares = _safe_float(item.get("apply_shares"))
        accounts = _safe_float(item.get("accounts"))
        if apply_shares and apply_shares > 0 and accounts and accounts > 0:
            rows.append((apply_shares, int(round(accounts))))
    if not rows:
        return None

    full_allocated = 0
    for apply_shares, accounts in rows:
        full_allocated += _floor_to_lot(apply_shares * allocation_ratio) * accounts
    leftover_lots = max(int(round((online_issue_shares - full_allocated) / LOT_SIZE)), 0)
    if leftover_lots <= 0:
        return {
            "fractional_threshold_shares": None,
            "fractional_time_priority_required": False,
            "leftover_lots": 0,
            "cutoff_fill_rate": None,
            "basis": "distribution_table",
        }

    for apply_shares, accounts in sorted(rows, key=lambda row: row[0], reverse=True):
        if leftover_lots > accounts:
            leftover_lots -= accounts
            continue
        return {
            "fractional_threshold_shares": apply_shares,
            "fractional_time_priority_required": leftover_lots < accounts,
            "leftover_lots": leftover_lots,
            "cutoff_accounts": accounts,
            "cutoff_fill_rate": leftover_lots / accounts if accounts else None,
            "basis": "distribution_table",
        }

    return {
        "fractional_threshold_shares": min(apply_shares for apply_shares, _ in rows),
        "fractional_time_priority_required": False,
        "leftover_lots": leftover_lots,
        "cutoff_fill_rate": 1.0,
        "basis": "distribution_table",
    }


def _build_lot_thresholds(
    *,
    allocation_ratio: float,
    issue_price: float,
    top_apply_shares: int | None,
    fractional_shares: int | None,
    fractional_basis: str,
    fractional_time_required: bool,
    max_lots_limit: int = 20,
) -> list[dict[str, Any]]:
    if allocation_ratio <= 0 or issue_price <= 0:
        return []

    rows: list[dict[str, Any]] = []
    limit = max(int(max_lots_limit), 1)
    for lots in range(1, limit + 1):
        guaranteed_shares = _ceil_to_lot(lots * LOT_SIZE / allocation_ratio)
        candidates = [
            {
                "threshold_shares": guaranteed_shares,
                "basis": "guaranteed_lot",
                "time_priority_required": False,
            }
        ]
        if fractional_shares and fractional_shares > 0:
            previous_guaranteed_shares = _ceil_to_lot((lots - 1) * LOT_SIZE / allocation_ratio) if lots > 1 else 0
            fractional_candidate_shares = _ceil_to_lot(max(previous_guaranteed_shares, fractional_shares))
            candidates.append(
                {
                    "threshold_shares": fractional_candidate_shares,
                    "basis": fractional_basis,
                    "time_priority_required": bool(fractional_time_required),
                }
            )

        selected = min(candidates, key=lambda item: float(item["threshold_shares"]))
        threshold_shares = int(selected["threshold_shares"])
        if top_apply_shares is not None and threshold_shares > top_apply_shares:
            break
        rows.append(
            {
                "lots": lots,
                "threshold_shares": threshold_shares,
                "threshold_amount_wan": _shares_to_amount_wan(threshold_shares, issue_price),
                "basis": selected["basis"],
                "time_priority_required": bool(selected["time_priority_required"]),
            }
        )
    return rows


def build_subscription_prediction(
    ipo_info: dict[str, Any],
    recent_ipos: list[dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = params or {}
    recent_items = recent_ipos or []
    issue_price = _safe_float(ipo_info.get("ISSUE_PRICE"))
    online_issue_shares, online_issue_source = _resolve_online_issue_shares(ipo_info)
    if not issue_price or issue_price <= 0 or not online_issue_shares or online_issue_shares <= 0:
        return {
            "available": False,
            "mode": "insufficient_data",
            "reason": "缺少发行价或网上发行数量",
            "table_rows": [["模型状态", "缺少发行价或网上发行数量", "-"]],
        }

    top_apply_wan, top_apply_source = _resolve_top_apply_amount_wan(ipo_info, issue_price)
    top_apply_shares = _money_to_shares(top_apply_wan, issue_price)
    lock_days, lock_source = _resolve_lock_days(ipo_info)
    valid_shares, valid_source = _resolve_valid_subscription_shares(ipo_info, online_issue_shares, issue_price)
    allocated_accounts, allocated_accounts_source = _resolve_allocated_accounts(ipo_info)
    estimate: dict[str, Any] = {}
    mode = "actual"
    if not valid_shares or valid_shares <= online_issue_shares:
        estimate = _estimate_valid_subscription_shares(
            ipo_info,
            recent_items,
            issue_price,
            online_issue_shares,
            top_apply_wan,
            lock_days,
            settings,
        )
        valid_shares = _safe_float(estimate.get("valid_subscription_shares"))
        valid_source = "history_estimate" if valid_shares else ""
        mode = "estimated"

    if not valid_shares or valid_shares <= online_issue_shares:
        return {
            "available": False,
            "mode": "insufficient_data",
            "reason": estimate.get("reason") or "缺少有效申购数量，且历史样本不足以估算",
            "table_rows": [
                ["网上发行数量", _fmt_shares(online_issue_shares), online_issue_source or "-"],
                ["顶格申购金额", _fmt_amount_wan(top_apply_wan), top_apply_source or "-"],
                ["模型状态", estimate.get("reason") or "缺少有效申购数量，且历史样本不足以估算", "-"],
            ],
        }

    allocation_ratio = online_issue_shares / valid_shares
    subscription_multiple = valid_shares / online_issue_shares
    allocation_rate_pct = allocation_ratio * 100
    frozen_funds_yi = _safe_float(ipo_info.get("FROZEN_FUNDS_YI")) or valid_shares * issue_price / 100000000
    valid_accounts = _safe_float(ipo_info.get("ONLINE_VA_NUM"))

    raw_guaranteed = LOT_SIZE / allocation_ratio
    guaranteed_shares = _ceil_to_lot(raw_guaranteed)
    guaranteed_reachable = top_apply_shares is None or guaranteed_shares <= top_apply_shares
    guaranteed_amount_wan = _shares_to_amount_wan(guaranteed_shares, issue_price)
    top_apply_below_guaranteed = bool(top_apply_shares is not None and guaranteed_shares > top_apply_shares)
    top_apply_gap_shares = (guaranteed_shares - top_apply_shares) if top_apply_below_guaranteed else None
    top_apply_gap_amount_wan = _shares_to_amount_wan(top_apply_gap_shares, issue_price)
    buffer_min_wan, buffer_max_wan = _resolve_guaranteed_buffer_range(settings)
    protected_amount_min_wan = guaranteed_amount_wan + buffer_min_wan
    protected_amount_max_wan = guaranteed_amount_wan + buffer_max_wan
    protected_threshold_reachable = bool(
        top_apply_wan is None or protected_amount_max_wan <= top_apply_wan
    )
    protected_threshold_exceeds_top_apply = bool(
        top_apply_wan is not None and protected_amount_max_wan > top_apply_wan
    )
    protected_gap_amount_wan = (
        protected_amount_max_wan - top_apply_wan if protected_threshold_exceeds_top_apply and top_apply_wan is not None else None
    )

    distribution_result = _distribution_cutoff(
        ipo_info.get("SUBSCRIPTION_AMOUNT_DISTRIBUTION"),
        allocation_ratio,
        online_issue_shares,
    )
    direct_fractional_shares = _safe_float(ipo_info.get("FRACTIONAL_THRESHOLD_SHARES"))
    direct_time_required = ipo_info.get("FRACTIONAL_TIME_PRIORITY_REQUIRED")
    if direct_fractional_shares:
        fractional_shares = _ceil_to_lot(direct_fractional_shares)
        fractional_basis = "issue_result_threshold"
        time_required = bool(direct_time_required)
        cutoff_fill_rate = None
        leftover_lots = None
    elif distribution_result:
        fractional_raw = distribution_result.get("fractional_threshold_shares")
        fractional_shares = _ceil_to_lot(float(fractional_raw)) if fractional_raw else None
        fractional_basis = str(distribution_result.get("basis") or "distribution_table")
        time_required = bool(distribution_result.get("fractional_time_priority_required"))
        cutoff_fill_rate = distribution_result.get("cutoff_fill_rate")
        leftover_lots = distribution_result.get("leftover_lots")
    else:
        if guaranteed_reachable:
            extra_lot_shares = _ceil_to_lot((2 * LOT_SIZE) / allocation_ratio)
            if top_apply_shares is not None and extra_lot_shares <= top_apply_shares:
                fractional_shares = extra_lot_shares
                time_required = True
                fractional_basis = "extra_lot_estimate_without_distribution"
            else:
                fractional_shares = None
                time_required = False
                fractional_basis = "extra_lot_unreachable_without_distribution"
        else:
            fractional_shares = top_apply_shares
            time_required = True
            fractional_basis = "top_apply_below_guaranteed_all_time_priority"
        cutoff_fill_rate = None
        leftover_lots = None

    time_priority_scope = "none"
    if time_required:
        time_priority_scope = "all_top_apply_accounts" if top_apply_below_guaranteed else "fractional_cutoff"
    top_apply_time_priority_required = top_apply_below_guaranteed
    if top_apply_time_priority_required:
        top_apply_time_priority_note = "必须抢时间（顶格仍不足正股）"
    elif protected_threshold_exceeds_top_apply:
        top_apply_time_priority_note = "可能需要抢时间（保护后建议金额超过顶格）"
    else:
        top_apply_time_priority_note = "否"

    if top_apply_below_guaranteed:
        fractional_time_priority_note = "必须抢时间（顶格账户正股/碎股均按时间优先）"
    elif time_required:
        fractional_time_priority_note = "可能需要抢时间多获配一手碎股"
    else:
        fractional_time_priority_note = "否"

    allocation_fit = _fit_allocation_buckets(
        issue_price=issue_price,
        online_issue_shares=online_issue_shares,
        valid_subscription_shares=valid_shares,
        valid_accounts=valid_accounts,
        allocated_accounts=allocated_accounts,
        top_apply_shares=top_apply_shares,
        allocation_ratio=allocation_ratio,
        guaranteed_shares=guaranteed_shares,
        guaranteed_reachable=guaranteed_reachable,
    )

    fractional_amount_wan = _shares_to_amount_wan(fractional_shares, issue_price)
    lot_thresholds = _build_lot_thresholds(
        allocation_ratio=allocation_ratio,
        issue_price=issue_price,
        top_apply_shares=top_apply_shares,
        fractional_shares=fractional_shares,
        fractional_basis=fractional_basis,
        fractional_time_required=time_required,
        max_lots_limit=_resolve_lot_threshold_max_lots(settings),
    )
    protected_amount_text = (
        f"测算 {_fmt_amount_wan(guaranteed_amount_wan)} + 保护 {_fmt_buffer_wan(buffer_min_wan, buffer_max_wan)} = "
        f"{_fmt_protected_amount_range(protected_amount_min_wan, protected_amount_max_wan)}"
    )
    if protected_threshold_exceeds_top_apply and top_apply_wan is not None:
        protected_amount_text = (
            f"{protected_amount_text}；高于顶格 {_fmt_amount_wan(top_apply_wan)}，建议顶格并关注时间优先"
        )
    table_rows = [
        ["网上发行数量", _fmt_shares(online_issue_shares), online_issue_source or "-"],
        ["顶格申购金额", _fmt_amount_wan(top_apply_wan), top_apply_source or "-"],
        ["有效申购户数", _fmt_accounts(valid_accounts), "ONLINE_VA_NUM" if valid_accounts else "-"],
        ["网上获配户数", _fmt_accounts(allocated_accounts), allocated_accounts_source or "-"],
        ["有效申购股数", _fmt_shares(valid_shares), valid_source or "-"],
        ["冻结资金", _fmt_yi(frozen_funds_yi), "FROZEN_FUNDS_YI/derived"],
        ["申购冻结天数", f"{lock_days} 天" if lock_days else "-", lock_source or "-"],
        ["申购倍数", f"{subscription_multiple:.2f} 倍", valid_source or "-"],
        ["配售比例", f"{allocation_rate_pct:.4f}%", valid_source or "-"],
        ["正股获配门槛", _fmt_amount_wan(guaranteed_amount_wan) if guaranteed_reachable else f">{_fmt_amount_wan(top_apply_wan)}", "rule:floor_100"],
        ["正股保护阈值", _fmt_buffer_wan(buffer_min_wan, buffer_max_wan), "manual:safety_buffer"],
        ["正股建议申购金额", protected_amount_text, "guaranteed_threshold+safety_buffer"],
        ["碎股获配门槛", _fmt_amount_wan(fractional_amount_wan), fractional_basis],
        ["碎股是否抢时间", "是" if time_required else "否", fractional_basis],
        ["顶格抢时间提示", top_apply_time_priority_note, "top_apply_vs_guaranteed"],
        ["碎股加配抢时间提示", fractional_time_priority_note, fractional_basis],
        [
            "时间优先场景",
            "顶格仍不足正股，全员抢碎股" if top_apply_below_guaranteed else ("碎股边界抢时间" if time_required else "否"),
            fractional_basis,
        ],
    ]
    for item in lot_thresholds:
        lot_label = f"{int(item.get('lots') or 0)}手建议申购门槛"
        source = str(item.get("basis") or "-")
        if item.get("time_priority_required"):
            source = f"{source}:time_priority"
        table_rows.append([lot_label, _fmt_amount_wan(item.get("threshold_amount_wan")), source])
    if allocation_fit:
        table_rows.append(
            [
                "获配户数拟合",
                f"{allocation_fit.get('allocated_accounts')} 户 / {allocation_fit.get('allocated_lots_total')} 手",
                str(allocation_fit.get("fit_quality") or allocation_fit.get("method") or "-"),
            ]
        )
    if mode == "estimated":
        table_rows.append(["估算样本数", str(int(estimate.get("sample_count") or 0)), "recent_ipos"])
        frozen_floor = estimate.get("frozen_funds_floor") or {}
        if frozen_floor:
            floor_source = (
                "recent_frozen_floor"
                if frozen_floor.get("applied")
                else "recent_frozen_floor:not_applied"
            )
            table_rows.append(
                [
                    "冻结资金下限",
                    _fmt_yi(frozen_floor.get("floor_frozen_funds_yi")),
                    floor_source,
                ]
            )

    return {
        "available": True,
        "mode": mode,
        "reason": "",
        "issue_price": issue_price,
        "online_issue_shares": online_issue_shares,
        "top_apply_amount_wan": top_apply_wan,
        "top_apply_shares": top_apply_shares,
        "valid_subscription_shares": valid_shares,
        "valid_accounts": valid_accounts,
        "allocated_accounts": allocated_accounts,
        "frozen_funds_yi": frozen_funds_yi,
        "lock_days": lock_days,
        "subscription_multiple": subscription_multiple,
        "allocation_rate_pct": allocation_rate_pct,
        "guaranteed_threshold_shares": guaranteed_shares,
        "guaranteed_threshold_amount_wan": guaranteed_amount_wan if guaranteed_reachable else None,
        "guaranteed_threshold_raw_amount_wan": guaranteed_amount_wan,
        "guaranteed_threshold_reachable": guaranteed_reachable,
        "guaranteed_safety_buffer_min_wan": buffer_min_wan,
        "guaranteed_safety_buffer_max_wan": buffer_max_wan,
        "protected_guaranteed_amount_min_wan": protected_amount_min_wan,
        "protected_guaranteed_amount_max_wan": protected_amount_max_wan,
        "protected_guaranteed_threshold_reachable": protected_threshold_reachable,
        "protected_guaranteed_threshold_exceeds_top_apply": protected_threshold_exceeds_top_apply,
        "protected_guaranteed_gap_amount_wan": protected_gap_amount_wan,
        "top_apply_below_guaranteed": top_apply_below_guaranteed,
        "top_apply_time_priority_required": top_apply_time_priority_required,
        "top_apply_time_priority_note": top_apply_time_priority_note,
        "top_apply_gap_shares": top_apply_gap_shares,
        "top_apply_gap_amount_wan": top_apply_gap_amount_wan,
        "fractional_threshold_shares": fractional_shares,
        "fractional_threshold_amount_wan": fractional_amount_wan,
        "fractional_time_priority_required": time_required,
        "fractional_time_priority_note": fractional_time_priority_note,
        "time_priority_scope": time_priority_scope,
        "fractional_cutoff_fill_rate": cutoff_fill_rate,
        "leftover_lots": leftover_lots,
        "lot_thresholds": lot_thresholds,
        "allocation_fit": allocation_fit,
        "table_rows": table_rows,
        "estimate": estimate,
        "frozen_funds_floor": estimate.get("frozen_funds_floor") if mode == "estimated" else None,
    }
