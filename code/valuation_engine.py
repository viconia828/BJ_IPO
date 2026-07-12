from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from typing import Any



def _median_or_mean(values: list[float], stat_name: str) -> float:
    if stat_name == "mean":
        return statistics.fmean(values)
    return statistics.median(values)


def _weighted_median(value_weight_pairs: list[tuple[float, float]]) -> float | None:
    valid_pairs = sorted(
        ((value, weight) for value, weight in value_weight_pairs if weight > 0),
        key=lambda item: item[0],
    )
    if not valid_pairs:
        return None

    total_weight = sum(weight for _, weight in valid_pairs)
    threshold = total_weight / 2
    cumulative = 0.0
    for value, weight in valid_pairs:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return valid_pairs[-1][0]


def _weighted_mean(value_weight_pairs: list[tuple[float, float]]) -> float | None:
    valid_pairs = [(value, weight) for value, weight in value_weight_pairs if weight > 0]
    if not valid_pairs:
        return None
    total_weight = sum(weight for _, weight in valid_pairs)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in valid_pairs) / total_weight


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_enabled(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off", "否", "关闭"}


def _parse_date(value: Any) -> date | None:
    if value in (None, "", "--"):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.split(" ", 1)[0].replace("/", "-")
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _get_sample_weight_mode(params: dict[str, Any]) -> str:
    return str(params.get("method2_weight_mode", params.get("sample_weight_mode", "static"))).strip().lower() or "static"


def _get_sample_half_life_days(params: dict[str, Any]) -> float:
    return max(float(params.get("method2_decay_half_life_days", params.get("sample_decay_half_life_days", 90))), 1.0)


def _get_sentiment_half_life_days(params: dict[str, Any]) -> float:
    return max(float(params.get("sentiment_decay_half_life_days", 5)), 1.0)


def _get_recent_window_days(params: dict[str, Any]) -> int:
    raw_days = params.get("recent_days")
    if raw_days not in (None, ""):
        return max(int(float(raw_days)), 1)
    raw_months = params.get("recent_months", 3)
    return max(int(float(raw_months)) * 30, 1)


def _resolve_reference_date(target_listing_date: str | date | None, records: list[dict[str, Any]]) -> date:
    target_date = _parse_date(target_listing_date)
    if target_date:
        return target_date

    sample_dates = [_parse_date(item.get("LISTING_DATE")) for item in records]
    sample_dates = [item for item in sample_dates if item is not None]
    if sample_dates:
        return max(sample_dates)
    return date.today()


def _filter_samples_by_recent_days(
    records: list[dict[str, Any]],
    params: dict[str, Any],
    reference_date: date,
) -> list[dict[str, Any]]:
    recent_days = _get_recent_window_days(params)
    cutoff = reference_date - timedelta(days=recent_days)
    filtered: list[dict[str, Any]] = []
    for item in records:
        sample_date = _parse_date(item.get("LISTING_DATE"))
        if sample_date is None:
            continue
        if cutoff <= sample_date <= reference_date:
            filtered.append(item)
    return filtered


def _get_sample_weight(sample_date: date | None, reference_date: date, params: dict[str, Any]) -> float:
    if sample_date is None:
        return 0.0
    if _get_sample_weight_mode(params) != "time_decay":
        return 1.0

    day_gap = max((reference_date - sample_date).days, 0)
    half_life_days = _get_sample_half_life_days(params)
    return 0.5 ** (day_gap / half_life_days)


def _get_sentiment_weight(sample_date: date | None, reference_date: date, params: dict[str, Any]) -> float:
    if sample_date is None:
        return 0.0
    day_gap = max((reference_date - sample_date).days, 0)
    half_life_days = _get_sentiment_half_life_days(params)
    return 0.5 ** (day_gap / half_life_days)


def _sample_first_day_change_pct(item: dict[str, Any]) -> float | None:
    average_change = _safe_float(item.get("LD_AVERAGE_CHANGE"))
    if average_change is not None:
        return average_change
    return _safe_float(item.get("LD_CLOSE_CHANGE"))


def _quantile(values: list[float], q: float) -> float | None:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    position = (len(clean) - 1) * q
    low = int(position)
    high = min(low + 1, len(clean) - 1)
    fraction = position - low
    return clean[low] * (1 - fraction) + clean[high] * fraction


def _change_from_listing_close(first_day_change: float | None, later_change: float | None) -> float | None:
    if first_day_change is None or later_change is None:
        return None
    listing_base = 1 + first_day_change / 100
    later_base = 1 + later_change / 100
    if listing_base <= 0:
        return None
    return (later_base / listing_base - 1) * 100


def _sample_post_listing_profit_effect_pct(item: dict[str, Any]) -> float | None:
    direct_value = _safe_float(item.get("POST_LISTING_PROFIT_EFFECT_PCT"))
    if direct_value is not None:
        return direct_value

    direct_values = [
        _safe_float(item.get("NEXT_DAY_FROM_LISTING_CLOSE_PCT")),
        _safe_float(item.get("THIRD_DAY_FROM_LISTING_CLOSE_PCT")),
    ]
    direct_values = [value for value in direct_values if value is not None]
    if direct_values:
        return statistics.fmean(direct_values)

    first_day_close_change = _safe_float(item.get("LD_CLOSE_CHANGE"))
    next_change = None
    for key in ("NEXT_DAY_CLOSE_CHANGE", "NEXT_DAY_CHANGE", "D2_CLOSE_CHANGE"):
        next_change = _safe_float(item.get(key))
        if next_change is not None:
            break
    third_change = None
    for key in ("THIRD_DAY_CLOSE_CHANGE", "THIRD_DAY_CHANGE", "D3_CLOSE_CHANGE"):
        third_change = _safe_float(item.get(key))
        if third_change is not None:
            break

    computed_values = [
        _change_from_listing_close(first_day_close_change, next_change),
        _change_from_listing_close(first_day_close_change, third_change),
    ]
    computed_values = [value for value in computed_values if value is not None]
    if computed_values:
        return statistics.fmean(computed_values)
    return None


def _build_change_entries(
    records: list[dict[str, Any]],
    params: dict[str, Any],
    reference_date: date,
) -> list[tuple[dict[str, Any], float, float]]:
    entries: list[tuple[dict[str, Any], float, float]] = []
    for item in records:
        change_pct = _sample_first_day_change_pct(item)
        if change_pct is None:
            continue
        sample_date = _parse_date(item.get("LISTING_DATE"))
        entries.append((item, change_pct, _get_sample_weight(sample_date, reference_date, params)))
    return entries


def _summarize_change_entries(
    entries: list[tuple[dict[str, Any], float, float]],
    params: dict[str, Any],
) -> tuple[float | None, str]:
    if not entries:
        return None, "中位数"

    if _get_sample_weight_mode(params) == "time_decay":
        value = _weighted_median([(change_pct, weight) for _, change_pct, weight in entries])
        label = f"时间衰减中位数（半衰期 {_get_sample_half_life_days(params):.0f} 天）"
        return value, label

    return statistics.median([change_pct for _, change_pct, _ in entries]), "中位数"


def _robust_filter_change_entries(
    entries: list[tuple[dict[str, Any], float, float]],
    params: dict[str, Any],
) -> tuple[list[tuple[dict[str, Any], float, float]], list[tuple[dict[str, Any], float, float]]]:
    min_samples = max(int(params.get("robust_median_min_samples", 4)), 1)
    if len(entries) < min_samples:
        return entries, []

    values = [change_pct for _, change_pct, _ in entries]
    median_value = statistics.median(values)
    deviations = [abs(value - median_value) for value in values]
    mad = statistics.median(deviations)
    epsilon = 1e-9
    if mad <= epsilon:
        filtered = [entry for entry in entries if abs(entry[1] - median_value) <= epsilon]
        if filtered and len(filtered) < len(entries):
            removed = [entry for entry in entries if abs(entry[1] - median_value) > epsilon]
            return filtered, removed
        return entries, []

    multiplier = max(float(params.get("robust_mad_multiplier", 3.0)), 0.1)
    robust_sigma = 1.4826 * mad
    limit = multiplier * robust_sigma
    filtered = [entry for entry in entries if abs(entry[1] - median_value) <= limit]
    if not filtered:
        return entries, []
    removed = [entry for entry in entries if abs(entry[1] - median_value) > limit]
    return filtered, removed


def _summarize_change_records(
    records: list[dict[str, Any]],
    params: dict[str, Any],
    reference_date: date,
    *,
    robust: bool = False,
) -> tuple[float | None, str, list[dict[str, Any]], list[dict[str, Any]]]:
    entries = _build_change_entries(records, params, reference_date)
    removed_entries: list[tuple[dict[str, Any], float, float]] = []
    if robust:
        entries, removed_entries = _robust_filter_change_entries(entries, params)

    value, label = _summarize_change_entries(entries, params)
    if robust:
        label = f"MAD去极值{label}" if removed_entries else f"稳健{label}"
    return value, label, [entry[0] for entry in entries], [entry[0] for entry in removed_entries]


def _summarize_change_stat(
    records: list[dict[str, Any]],
    params: dict[str, Any],
    reference_date: date,
) -> tuple[float | None, str]:
    value, label, _, _ = _summarize_change_records(records, params, reference_date)
    return value, label


def method1_comparable(
    issue_price: float | None,
    issue_pe: float | None,
    comparable_data: list[dict[str, Any]],
    params: dict[str, Any],
    industry_pe: float | None = None,
    float_shares: float | None = None,
) -> dict[str, Any]:
    if not issue_price or not issue_pe:
        return {"available": False, "reason": "发行价或发行 PE 缺失，无法计算方法一。"}

    clean_pe_values: list[float] = []
    for item in comparable_data:
        pe_value = _safe_float(item.get("pe_ttm"))
        if pe_value and pe_value > 0:
            clean_pe_values.append(pe_value)

    anchor_source = "prospectus_comparables"
    confidence_multiplier = 1.0
    if clean_pe_values:
        comp_pe = _median_or_mean(clean_pe_values, str(params.get("comparable_pe_stat", "median")))
    else:
        valid_industry_pe = _safe_float(industry_pe)
        fallback_enabled = _is_enabled(params.get("method1_industry_fallback_enabled"), False)
        if not fallback_enabled or valid_industry_pe is None or valid_industry_pe <= 0:
            return {"available": False, "reason": "当前未获取到有效可比公司 PE 或行业 PE 数据，方法一已跳过。"}
        comp_pe = valid_industry_pe
        anchor_source = "industry_pe_fallback"
        confidence_multiplier = max(float(params.get("method1_industry_fallback_confidence", 0.5)), 0.0)

    eps = issue_price / issue_pe
    base_target_pe = comp_pe * float(params.get("bse_discount_factor", 0.75))

    pe_ratio = None
    pe_factor = 1.0
    valid_industry_pe = _safe_float(industry_pe)
    factors_enabled = _is_enabled(params.get("method1_pe_float_factors_enabled"), False)
    if factors_enabled and valid_industry_pe is not None and valid_industry_pe > 0:
        pe_ratio = issue_pe / valid_industry_pe
        low_threshold = float(params.get("pe_low_threshold", 0.30))
        high_threshold = float(params.get("pe_high_threshold", 0.65))
        if high_threshold <= low_threshold:
            high_threshold = low_threshold + 0.01
        low_factor = 1 + float(params.get("pe_discount_boost", 0.10))
        high_factor = 1 + float(params.get("pe_premium_drag", -0.10))
        if pe_ratio <= low_threshold:
            pe_factor = low_factor
        elif pe_ratio >= high_threshold:
            pe_factor = high_factor
        else:
            position = (pe_ratio - low_threshold) / (high_threshold - low_threshold)
            pe_factor = low_factor + (high_factor - low_factor) * position
        pe_factor = max(pe_factor, 0.1)

    float_factor = 1.0
    valid_float_shares = _safe_float(float_shares)
    float_threshold = float(params.get("float_size_threshold", 2000))
    if factors_enabled and valid_float_shares is not None and valid_float_shares >= 0 and float_threshold > 0:
        if valid_float_shares < float_threshold:
            size_gap = 1 - valid_float_shares / float_threshold
            float_factor += max(float(params.get("small_cap_premium", 0.10)), 0.0) * size_gap

    target_pe = base_target_pe * pe_factor * float_factor
    target_price = eps * target_pe
    change_pct = (target_price / issue_price - 1) * 100

    return {
        "available": True,
        "eps": eps,
        "comp_pe": comp_pe,
        "anchor_source": anchor_source,
        "confidence_multiplier": confidence_multiplier,
        "base_target_pe": base_target_pe,
        "pe_ratio": pe_ratio,
        "pe_factor": pe_factor,
        "float_shares": valid_float_shares,
        "float_factor": float_factor,
        "target_pe": target_pe,
        "target_price": target_price,
        "change_pct": change_pct,
        "sample_count": len(clean_pe_values),
    }


def _filter_samples_by_year_to_date(
    records: list[dict[str, Any]],
    reference_date: date,
) -> list[dict[str, Any]]:
    start_date = date(reference_date.year, 1, 1)
    filtered: list[dict[str, Any]] = []
    for item in records:
        sample_date = _parse_date(item.get("LISTING_DATE"))
        if sample_date is None:
            continue
        if start_date <= sample_date <= reference_date:
            filtered.append(item)
    return filtered


def _method2_sample_confidence(raw_sample_count: int, params: dict[str, Any]) -> tuple[float, str]:
    if not _is_enabled(params.get("method2_sample_confidence_enabled", True), default=True):
        return 1.0, "disabled"
    if raw_sample_count <= 1:
        key, tier = "method2_confidence_1_sample", "1"
    elif raw_sample_count == 2:
        key, tier = "method2_confidence_2_samples", "2"
    elif raw_sample_count == 3:
        key, tier = "method2_confidence_3_samples", "3"
    else:
        key, tier = "method2_confidence_4plus_samples", "4+"
    defaults = {
        "method2_confidence_1_sample": 0.25,
        "method2_confidence_2_samples": 0.50,
        "method2_confidence_3_samples": 0.75,
        "method2_confidence_4plus_samples": 1.00,
    }
    return _clamp(float(params.get(key, defaults[key])), 0.0, 1.0), tier


def _pick_secondary_industry_samples(
    industry: dict[str, Any],
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    valid_records = [item for item in records if _sample_first_day_change_pct(item) is not None]
    secondary = str(industry.get("secondary") or "").strip()
    if not secondary or secondary == "未分类":
        return [], "未映射"
    return [item for item in valid_records if item.get("industry_secondary") == secondary], "二级行业"


def _filter_historical_samples(
    recent_ipos: list[dict[str, Any]],
    target_code: str | None,
    target_listing_date: str | date | None,
) -> list[dict[str, Any]]:
    target_date = _parse_date(target_listing_date)
    filtered: list[dict[str, Any]] = []
    for item in recent_ipos:
        code = str(item.get("SECURITY_CODE", "")).strip()
        sample_date = _parse_date(item.get("LISTING_DATE"))
        if target_code and code == target_code:
            continue
        if target_date and sample_date and sample_date >= target_date:
            continue
        if target_date and sample_date is None:
            continue
        filtered.append(item)

    filtered.sort(key=lambda item: _parse_date(item.get("LISTING_DATE")) or date.min, reverse=True)
    return filtered


def method2_industry_momentum(
    issue_price: float | None,
    issue_pe: float | None,
    industry_pe: float | None,
    float_shares: float | None,
    industry: dict[str, Any],
    recent_ipos: list[dict[str, Any]],
    params: dict[str, Any],
    target_code: str | None = None,
    target_listing_date: str | date | None = None,
) -> dict[str, Any]:
    _ = (issue_pe, industry_pe, float_shares)
    if not issue_price:
        return {"available": False, "reason": "发行价缺失，无法计算方法二。"}
    if not recent_ipos:
        return {"available": False, "reason": "近期新股样本为空，无法计算方法二。"}

    secondary = str(industry.get("secondary") or "").strip()
    if not secondary or secondary == "未分类":
        return {"available": False, "reason": "标的未完成二级行业映射，方法二不再回退一级行业或全市场。"}

    historical_ipos = _filter_historical_samples(recent_ipos, target_code, target_listing_date)
    if not historical_ipos:
        return {"available": False, "reason": "不存在早于标的上市日的历史样本，无法计算方法二。"}

    reference_date = _resolve_reference_date(target_listing_date, historical_ipos)
    year_records = _filter_samples_by_year_to_date(historical_ipos, reference_date)
    if not year_records:
        return {"available": False, "reason": f"{reference_date.year}年内不存在早于标的上市日的历史样本，无法计算方法二。"}

    samples, sample_scope = _pick_secondary_industry_samples(industry, year_records)
    if not samples:
        return {"available": False, "reason": f"{reference_date.year}年内没有同二级行业历史新股样本，方法二不可用。"}

    base_chg, base_stat_label, clean_samples, removed_samples = _summarize_change_records(
        samples,
        params,
        reference_date,
        robust=True,
    )
    if not clean_samples or base_chg is None:
        return {"available": False, "reason": "同行业样本缺少首日涨幅字段，无法计算方法二。"}

    target_price = issue_price * (1 + base_chg / 100)
    start_date = date(reference_date.year, 1, 1)
    confidence_multiplier, confidence_tier = _method2_sample_confidence(len(samples), params)
    return {
        "available": True,
        "method": "industry_first_day_change",
        "issue_price": issue_price,
        "base_chg": base_chg,
        "target_price": target_price,
        "change_pct": base_chg,
        "sample_count": len(clean_samples),
        "raw_sample_count": len(samples),
        "confidence_multiplier": confidence_multiplier,
        "confidence_tier": confidence_tier,
        "confidence_sample_count": len(samples),
        "confidence_basis": "raw_sample_count_before_outlier_removal",
        "sample_scope": sample_scope,
        "sample_window_label": f"{reference_date.year}年内",
        "sample_window_start": start_date.isoformat(),
        "sample_window_end": reference_date.isoformat(),
        "sample_codes": [item.get("SECURITY_CODE", "") for item in clean_samples],
        "raw_sample_codes": [item.get("SECURITY_CODE", "") for item in samples],
        "removed_sample_codes": [item.get("SECURITY_CODE", "") for item in removed_samples],
        "removed_sample_count": len(removed_samples),
        "historical_sample_count": len(year_records),
        "recent_days": None,
        "base_stat_label": base_stat_label,
        "float_factor": 1.0,
        "float_note": "方法二仅使用同行业首日涨幅，流通盘修正不在本方法内处理。",
        "pe_ratio": None,
        "pe_factor": 1.0,
        "pe_note": "PE 估值已由方法一处理，方法二不再叠加 PE 因子。",
        "trend_factor": 1.0,
        "trend_note": "近期市场情绪已拆分到方法三，方法二不再叠加走势情绪因子。",
        "industry_trend_factor": 1.0,
        "market_trend_factor": 1.0,
        "industry_trend_score_median": None,
        "market_trend_score_median": None,
        "industry_trend_sample_codes": [],
        "market_trend_sample_codes": [],
        "adj_factor": 1.0,
    }


def _build_sentiment_entries(
    records: list[dict[str, Any]],
    params: dict[str, Any],
    reference_date: date,
    value_getter,
) -> list[tuple[dict[str, Any], float, float]]:
    entries: list[tuple[dict[str, Any], float, float]] = []
    for item in records:
        value = value_getter(item)
        if value is None:
            continue
        sample_date = _parse_date(item.get("LISTING_DATE"))
        weight = _get_sentiment_weight(sample_date, reference_date, params)
        if weight <= 0:
            continue
        entries.append((item, value, weight))
    return entries


def _clamp(value: float, floor_value: float, cap_value: float) -> float:
    lower = min(floor_value, cap_value)
    upper = max(floor_value, cap_value)
    return min(max(value, lower), upper)


def method3_recent_sentiment(
    issue_price: float | None,
    recent_ipos: list[dict[str, Any]],
    params: dict[str, Any],
    target_code: str | None = None,
    target_listing_date: str | date | None = None,
) -> dict[str, Any]:
    if not issue_price:
        return {"available": False, "reason": "Missing issue price; method3 cannot be calculated."}
    if not recent_ipos:
        return {"available": False, "reason": "Recent IPO sample pool is empty; method3 cannot be calculated."}

    historical_ipos = _filter_historical_samples(recent_ipos, target_code, target_listing_date)
    if not historical_ipos:
        return {"available": False, "reason": "No historical samples before target listing date; method3 cannot be calculated."}

    reference_date = _resolve_reference_date(target_listing_date, historical_ipos)
    recent_records = _filter_samples_by_recent_days(historical_ipos, params, reference_date)
    recent_days = _get_recent_window_days(params)
    if not recent_records:
        return {
            "available": False,
            "reason": f"No historical samples within the latest {recent_days} days before target listing date; method3 cannot be calculated.",
        }

    first_day_entries = _build_sentiment_entries(recent_records, params, reference_date, _sample_first_day_change_pct)
    post_listing_entries = _build_sentiment_entries(recent_records, params, reference_date, _sample_post_listing_profit_effect_pct)
    first_day_factor = _weighted_mean([(value, weight) for _, value, weight in first_day_entries])
    post_listing_factor = _weighted_mean([(value, weight) for _, value, weight in post_listing_entries])
    if first_day_factor is None:
        return {"available": False, "reason": "Recent samples lack first-day change fields; method3 cannot be calculated."}

    first_day_baseline = float(params.get("sentiment_first_day_baseline_pct", 100.0))
    first_day_scale = float(params.get("sentiment_first_day_scale", params.get("market_sentiment_weight", 0.15)))
    post_listing_scale = float(params.get("sentiment_post_listing_scale", params.get("market_sentiment_weight", 0.15)))
    premium_cap = float(params.get("sentiment_premium_cap_pct", 35.0))
    premium_floor = float(params.get("sentiment_premium_floor_pct", -20.0))

    first_day_signal = (first_day_factor - first_day_baseline) * first_day_scale
    post_listing_signal = (post_listing_factor or 0.0) * post_listing_scale
    raw_premium_pct = first_day_signal + post_listing_signal
    premium_pct = _clamp(raw_premium_pct, premium_floor, premium_cap)
    premium_price = issue_price * premium_pct / 100
    half_life_days = _get_sentiment_half_life_days(params)
    return {
        "available": True,
        "method": "recent_sentiment_premium",
        "sentiment_premium_pct": premium_pct,
        "raw_sentiment_premium_pct": raw_premium_pct,
        "premium_price": premium_price,
        "change_pct": premium_pct,
        "first_day_factor_pct": first_day_factor,
        "first_day_baseline_pct": first_day_baseline,
        "first_day_signal_pct": first_day_signal,
        "post_listing_factor_pct": post_listing_factor,
        "post_listing_signal_pct": post_listing_signal,
        "sample_count": len(first_day_entries),
        "first_day_sample_count": len(first_day_entries),
        "post_listing_sample_count": len(post_listing_entries),
        "raw_sample_count": len(recent_records),
        "sample_scope": "recent_market_sentiment",
        "sample_window_label": f"latest {recent_days} days",
        "sample_codes": [item.get("SECURITY_CODE", "") for item, _, _ in first_day_entries],
        "post_listing_sample_codes": [item.get("SECURITY_CODE", "") for item, _, _ in post_listing_entries],
        "raw_sample_codes": [item.get("SECURITY_CODE", "") for item in recent_records],
        "historical_sample_count": len(historical_ipos),
        "recent_days": recent_days,
        "base_stat_label": f"时间衰减均值（半衰期 {half_life_days:.0f} 天）",
        "decay_half_life_days": half_life_days,
        "sentiment_first_day_scale": first_day_scale,
        "sentiment_post_listing_scale": post_listing_scale,
        "sentiment_premium_cap_pct": premium_cap,
        "sentiment_premium_floor_pct": premium_floor,
    }


def _normalize_available_method_weights(candidates: list[tuple[str, dict[str, Any], float]]) -> dict[str, float]:
    if not candidates:
        return {}
    total_weight = sum(max(weight, 0.0) for _, _, weight in candidates)
    if total_weight <= 0:
        return {key: 1 / len(candidates) for key, _, _ in candidates}
    return {key: max(weight, 0.0) / total_weight for key, _, weight in candidates}


def composite_valuation(
    method1: dict[str, Any] | None,
    method2: dict[str, Any] | None,
    params: dict[str, Any],
    method3: dict[str, Any] | None = None,
) -> dict[str, Any]:
    width = float(params.get("price_range_width", 0.15))
    raw_weights = {
        "method1": float(params.get("weight_comparable", 0.5)),
        "method2": float(params.get("weight_industry_momentum", 0.5)),
    }
    candidates: list[tuple[str, dict[str, Any], float]] = []
    for key, result in (("method1", method1), ("method2", method2)):
        if result and result.get("available"):
            confidence = max(float(result.get("confidence_multiplier", 1.0)), 0.0)
            candidates.append((key, result, raw_weights.get(key, 0.0) * confidence))

    if not candidates:
        return {"available": False, "reason": "No available base valuation result; composite valuation cannot be calculated."}
    if (
        len(candidates) == 1
        and candidates[0][0] == "method1"
        and candidates[0][1].get("anchor_source") == "industry_pe_fallback"
    ):
        return {
            "available": False,
            "reason": "Industry PE fallback cannot support the final valuation without an independent industry-new-share anchor.",
        }

    normalized = _normalize_available_method_weights(candidates)
    confidence_residual_anchor_price = 0.0
    confidence_residual_weight = 0.0
    if len(candidates) == 1 and candidates[0][0] == "method2":
        method2_result = candidates[0][1]
        method2_confidence = _clamp(float(method2_result.get("confidence_multiplier", 1.0)), 0.0, 1.0)
        issue_price = _safe_float(method2_result.get("issue_price"))
        if issue_price is not None and method2_confidence < 1.0:
            confidence_residual_weight = 1.0 - method2_confidence
            confidence_residual_anchor_price = issue_price
            normalized = {"method2": method2_confidence}
    base_target_price = (
        sum(float(result["target_price"]) * normalized[key] for key, result, _ in candidates)
        + confidence_residual_anchor_price * confidence_residual_weight
    )

    sentiment_premium_price = 0.0
    sentiment_premium_pct = 0.0
    if method3 and method3.get("available"):
        sentiment_premium_price = float(method3.get("premium_price") or 0.0)
        sentiment_premium_pct = float(method3.get("sentiment_premium_pct") or 0.0)

    target_price = base_target_price + sentiment_premium_price
    return {
        "available": True,
        "target_price": target_price,
        "base_target_price": base_target_price,
        "sentiment_premium_price": sentiment_premium_price,
        "sentiment_premium_pct": sentiment_premium_pct,
        "range_low": target_price * (1 - width),
        "range_high": target_price * (1 + width),
        "weight_comparable": normalized.get("method1", 0.0),
        "weight_industry_momentum": normalized.get("method2", 0.0),
        "weight_recent_sentiment": 0.0,
        "weight_method1": normalized.get("method1", 0.0),
        "weight_method2": normalized.get("method2", 0.0),
        "weight_method3": 0.0,
        "weight_confidence_residual": confidence_residual_weight,
        "confidence_residual_anchor_price": confidence_residual_anchor_price if confidence_residual_weight > 0 else None,
        "method3_available": bool(method3 and method3.get("available")),
        "available_methods": [key for key, _, _ in candidates],
    }


def _local_center_industry_keys(item: dict[str, Any]) -> tuple[str, str]:
    return (
        str(item.get("industry_primary") or item.get("INDUSTRY") or "").strip(),
        str(item.get("industry_secondary") or "").strip(),
    )


def _local_center_float_shares(item: dict[str, Any]) -> float | None:
    direct = _safe_float(item.get("float_shares"))
    if direct is not None:
        return direct
    issue_shares = _safe_float(item.get("TOTAL_ISSUE_NUM"))
    old_shares = _safe_float(item.get("old_shares")) or 0.0
    return issue_shares + old_shares if issue_shares is not None else None


def _local_center_raw_row(item: dict[str, Any], completed: list[dict[str, Any]]) -> dict[str, Any]:
    issue_price = _safe_float(item.get("ISSUE_PRICE"))
    float_shares = _local_center_float_shares(item)
    issue_pe = _safe_float(item.get("AFTER_ISSUE_PE"))
    industry_pe = _safe_float(item.get("INDUSTRY_PE_NEW"))
    old_shares = _safe_float(item.get("old_shares"))
    primary, secondary = _local_center_industry_keys(item)
    changes = [
        float(row["actual_change_pct"])
        for row in completed
        if _safe_float(row.get("actual_change_pct")) is not None
    ]
    same_industry = []
    for row in completed:
        row_primary = str(row.get("industry_primary") or "")
        row_secondary = str(row.get("industry_secondary") or "")
        if secondary and row_secondary == secondary:
            same_industry.append(row)
        elif primary and row_primary == primary:
            same_industry.append(row)
    same_changes = [
        float(row["actual_change_pct"])
        for row in same_industry[-5:]
        if _safe_float(row.get("actual_change_pct")) is not None
    ]
    return {
        "code": str(item.get("SECURITY_CODE") or "").strip(),
        "listing_date": str(item.get("LISTING_DATE") or "")[:10],
        "industry_primary": primary,
        "industry_secondary": secondary,
        "issue_price": issue_price,
        "float_market_cap_yi": (
            issue_price * float_shares / 10000
            if issue_price is not None and float_shares is not None
            else None
        ),
        "old_share_ratio": (
            old_shares / float_shares
            if old_shares is not None and float_shares not in (None, 0)
            else None
        ),
        "after_issue_pe": issue_pe,
        "pe_to_industry": (
            issue_pe / industry_pe
            if issue_pe is not None and industry_pe not in (None, 0)
            else None
        ),
        "recent5_median_change": statistics.median(changes[-5:]) if changes else None,
        "same_industry_recent_median": statistics.median(same_changes) if same_changes else None,
        "top_apply_marketcap": _safe_float(item.get("TOP_APPLY_MARKETCAP")),
        "online_issue_num": _safe_float(item.get("ONLINE_ISSUE_NUM")),
        "actual_change_pct": _sample_first_day_change_pct(item),
    }


def _local_center_thresholds(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    fields = (
        "float_market_cap_yi",
        "top_apply_marketcap",
        "online_issue_num",
    )
    result: dict[str, dict[str, float | None]] = {}
    for field in fields:
        values = [
            float(value)
            for value in (_safe_float(row.get(field)) for row in rows)
            if value is not None
        ]
        result[field] = {
            "p25": _quantile(values, 0.25),
            "p50": _quantile(values, 0.50),
            "p75": _quantile(values, 0.75),
        }
    return result


def _local_center_proxy_score(
    row: dict[str, Any],
    thresholds: dict[str, dict[str, float | None]],
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    issue_price = _safe_float(row.get("issue_price"))
    float_cap = _safe_float(row.get("float_market_cap_yi"))
    pe = _safe_float(row.get("after_issue_pe"))
    pe_ratio = _safe_float(row.get("pe_to_industry"))
    recent5 = _safe_float(row.get("recent5_median_change"))
    same_industry = _safe_float(row.get("same_industry_recent_median"))
    old_ratio = _safe_float(row.get("old_share_ratio"))
    top_apply = _safe_float(row.get("top_apply_marketcap"))
    online_issue = _safe_float(row.get("online_issue_num"))

    float_q = thresholds.get("float_market_cap_yi") or {}
    if float_cap is not None:
        if float_q.get("p25") is not None and float_cap <= float(float_q["p25"]):
            score += 2
            reasons.append("small_float_cap")
        elif float_q.get("p50") is not None and float_cap <= float(float_q["p50"]):
            score += 1
            reasons.append("mid_small_float_cap")
        elif float_q.get("p75") is not None and float_cap >= float(float_q["p75"]):
            score -= 1
            reasons.append("large_float_cap")
    if issue_price is not None:
        if issue_price <= 15:
            score += 1
            reasons.append("low_issue_price")
        elif issue_price >= 30:
            score -= 1
            reasons.append("high_issue_price")

    if pe_ratio is not None:
        if pe_ratio <= 0.55:
            score += 2
            reasons.append("deep_issue_pe_discount")
        elif pe_ratio <= 0.80:
            score += 1
            reasons.append("issue_pe_discount")
        elif pe_ratio >= 1.50:
            score -= 2
            reasons.append("high_issue_pe_premium")
        elif pe_ratio >= 1.15:
            score -= 1
            reasons.append("issue_pe_premium")
    if pe is not None:
        if pe <= 15:
            score += 1
            reasons.append("low_issue_pe")
        elif pe >= 35:
            score -= 1
            reasons.append("high_issue_pe")

    if recent5 is not None:
        if recent5 >= 180:
            score += 3
            reasons.append("very_strong_recent_mood")
        elif recent5 >= 120:
            score += 2
            reasons.append("strong_recent_mood")
        elif recent5 >= 70:
            score += 1
            reasons.append("positive_recent_mood")
        elif recent5 < 20:
            score -= 2
            reasons.append("weak_recent_mood")
        elif recent5 < 50:
            score -= 1
            reasons.append("soft_recent_mood")
    if same_industry is not None:
        if same_industry >= 160:
            score += 2
            reasons.append("strong_sector_mood")
        elif same_industry >= 90:
            score += 1
            reasons.append("positive_sector_mood")
        elif same_industry < 30:
            score -= 1
            reasons.append("weak_sector_mood")

    top_q = thresholds.get("top_apply_marketcap") or {}
    online_q = thresholds.get("online_issue_num") or {}
    if top_apply is not None and top_q.get("p25") is not None and top_q.get("p75") is not None:
        if top_apply <= float(top_q["p25"]):
            score += 1
            reasons.append("low_top_apply")
        elif top_apply >= float(top_q["p75"]):
            score -= 0.5
            reasons.append("high_top_apply")
    if online_issue is not None and online_q.get("p25") is not None and online_issue <= float(online_q["p25"]):
        score += 0.5
        reasons.append("small_online_issue")
    if old_ratio is not None:
        if old_ratio >= 0.25:
            score -= 2
            reasons.append("high_old_share_overhang")
        elif old_ratio >= 0.12:
            score -= 1
            reasons.append("old_share_overhang")
    return score, reasons


def _local_center_linear_prediction(
    previous: list[dict[str, Any]],
    score: float,
    params: dict[str, Any],
) -> tuple[float | None, int]:
    min_history = max(int(float(params.get("local_center_min_history", 8))), 1)
    history_window = max(int(float(params.get("local_center_history_window", 20))), 0)
    actual_cap = float(params.get("local_center_actual_cap_pct", 900.0))
    slope_cap = max(float(params.get("local_center_slope_cap", 25.0)), 0.0)
    history = previous[-history_window:] if history_window > 0 else previous
    pairs = [
        (float(row["proxy_score"]), min(float(row["actual_change_pct"]), actual_cap))
        for row in history
        if _safe_float(row.get("proxy_score")) is not None
        and _safe_float(row.get("actual_change_pct")) is not None
    ]
    if len(pairs) < min_history:
        return None, len(pairs)
    if len(pairs) < 5:
        similar = [y for x, y in pairs if abs(x - score) <= 2.5]
        values = similar if len(similar) >= 2 else [y for _, y in pairs]
        return statistics.median(values), len(pairs)

    xs = [x for x, _ in pairs]
    ys = [y for _, y in pairs]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    variance = sum((x - mean_x) ** 2 for x in xs)
    if variance <= 1e-9:
        return statistics.median(ys), len(pairs)
    beta = sum((x - mean_x) * (y - mean_y) for x, y in pairs) / variance
    beta = max(-20.0, min(slope_cap, beta))
    intercept = mean_y - beta * mean_x
    return max(-50.0, min(actual_cap, intercept + beta * score)), len(pairs)


def apply_local_center_overlay(
    final: dict[str, Any],
    *,
    issue_price: float | None,
    issue_pe: float | None,
    industry_pe: float | None,
    float_shares: float | None,
    old_shares: float | None,
    industry: dict[str, Any],
    recent_ipos: list[dict[str, Any]],
    params: dict[str, Any],
    target_code: str | None = None,
    target_listing_date: str | date | None = None,
    online_issue_num: float | None = None,
    top_apply_marketcap: float | None = None,
) -> dict[str, Any]:
    if not _is_enabled(params.get("local_center_overlay_enabled"), False):
        return final
    if not final.get("available") or issue_price in (None, 0):
        return final

    reference_date = _parse_date(target_listing_date) or date.today()
    normalized_target_code = str(target_code or "").strip()
    historical = []
    for item in recent_ipos:
        code = str(item.get("SECURITY_CODE") or "").strip()
        listing_date = _parse_date(item.get("LISTING_DATE"))
        if normalized_target_code and code == normalized_target_code:
            continue
        if listing_date is None or listing_date >= reference_date:
            continue
        if _sample_first_day_change_pct(item) is None:
            continue
        historical.append(dict(item))
    historical.sort(key=lambda item: (_parse_date(item.get("LISTING_DATE")) or date.min, str(item.get("SECURITY_CODE") or "")))

    target_item = {
        "SECURITY_CODE": normalized_target_code,
        "LISTING_DATE": reference_date.isoformat(),
        "ISSUE_PRICE": issue_price,
        "AFTER_ISSUE_PE": issue_pe,
        "INDUSTRY_PE_NEW": industry_pe,
        "float_shares": float_shares,
        "old_shares": old_shares,
        "industry_primary": str(industry.get("primary") or ""),
        "industry_secondary": str(industry.get("secondary") or ""),
        "ONLINE_ISSUE_NUM": online_issue_num,
        "TOP_APPLY_MARKETCAP": top_apply_marketcap,
    }
    sequence = [*historical, target_item]
    completed: list[dict[str, Any]] = []
    target_row: dict[str, Any] | None = None
    index = 0
    while index < len(sequence):
        group_date = str(sequence[index].get("LISTING_DATE") or "")[:10]
        group_items: list[dict[str, Any]] = []
        while index < len(sequence) and str(sequence[index].get("LISTING_DATE") or "")[:10] == group_date:
            group_items.append(sequence[index])
            index += 1
        raw_group = [_local_center_raw_row(item, completed) for item in group_items]
        threshold_rows = completed if len(completed) >= 4 else completed + raw_group
        thresholds = _local_center_thresholds(threshold_rows)
        for row in raw_group:
            row["proxy_score"], row["proxy_reasons"] = _local_center_proxy_score(row, thresholds)
            if row.get("code") == normalized_target_code and group_date == reference_date.isoformat():
                target_row = row
        completed.extend(row for row in raw_group if _safe_float(row.get("actual_change_pct")) is not None)

    if target_row is None:
        return {**final, "local_center_overlay_applied": False, "local_center_overlay_reason": "target feature row unavailable"}
    rolling_change, history_count = _local_center_linear_prediction(
        completed,
        float(target_row.get("proxy_score") or 0.0),
        params,
    )
    if rolling_change is None:
        return {
            **final,
            "local_center_overlay_applied": False,
            "local_center_overlay_reason": "insufficient completed history",
            "local_center_history_count": history_count,
            "local_center_proxy_score": target_row.get("proxy_score"),
        }

    base_target = _safe_float(final.get("target_price"))
    if base_target is None:
        return final
    base_change = (base_target / float(issue_price) - 1) * 100
    alpha = min(max(float(params.get("local_center_alpha", 0.50)), 0.0), 1.0)
    blended_change = base_change * (1 - alpha) + rolling_change * alpha
    target_price = float(issue_price) * (1 + blended_change / 100)
    width = float(params.get("price_range_width", 0.10))
    return {
        **final,
        "target_price": target_price,
        "range_low": target_price * (1 - width),
        "range_high": target_price * (1 + width),
        "pre_local_center_target_price": base_target,
        "pre_local_center_change_pct": base_change,
        "local_center_overlay_applied": True,
        "local_center_overlay_reason": "walk_forward_local_proxy_blend",
        "local_center_alpha": alpha,
        "local_center_proxy_score": target_row.get("proxy_score"),
        "local_center_proxy_reasons": target_row.get("proxy_reasons") or [],
        "local_center_history_count": history_count,
        "local_center_rolling_change_pct": rolling_change,
        "local_center_blended_change_pct": blended_change,
    }


def generate_notes(data_dict: dict[str, Any], params: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    ipo = data_dict.get("ipo_info", {})
    issue_pe = _safe_float(ipo.get("AFTER_ISSUE_PE"))
    industry_pe = _safe_float(ipo.get("INDUSTRY_PE_NEW"))
    float_shares = _safe_float(data_dict.get("float_shares"))
    industry = data_dict.get("industry", {})
    method1 = data_dict.get("method1", {})
    method2 = data_dict.get("method2", {})
    old_shares_meta = data_dict.get("old_shares_meta", {}) or {}
    comparable_codes = data_dict.get("comparable_codes", []) or []
    wind_summary = data_dict.get("wind_summary", {}) or {}

    if industry.get("primary") == "未分类" or industry.get("secondary") == "未分类":
        notes.append("当前标的尚未完成二级行业映射，方法二不再回退一级行业或全市场。建议补充 `stock_industry` 或行业映射。")

    if issue_pe and industry_pe and industry_pe > 0:
        ratio = issue_pe / industry_pe
        if ratio < float(params.get("pe_low_threshold", 0.3)):
            notes.append("发行 PE 显著低于行业 PE，定价具备一定折价优势。")
        elif ratio > float(params.get("pe_high_threshold", 0.6)):
            notes.append("发行 PE 相对行业偏高，需关注上市首日估值兑现压力。")

    if float_shares is not None and float_shares < float(params.get("float_size_threshold", 2000)):
        notes.append("首日流通盘偏小，方法一已按流通盘差距连续计入交易结构溢价。")

    if method1.get("anchor_source") == "industry_pe_fallback":
        notes.append("有效上市可比 PE 缺失，方法一改用行业 PE 低置信度兜底。")

    if not method1.get("available"):
        if str(wind_summary.get("channel", "disabled")).strip().lower() == "disabled":
            notes.append("当前处于 Wind 禁用模式，但方法一仍会优先尝试使用东方财富可比快照；本次未形成有效可比公司 PE，综合估值默认以方法二为主。")
        else:
            notes.append("当前未获取到有效 Wind 可比公司快照，方法一已跳过，综合估值默认以方法二为主。")
        if comparable_codes:
            notes.append(f"已从公告文件提取到可比公司代码：{', '.join(comparable_codes)}。")
    elif str(wind_summary.get("channel", "disabled")).strip().lower() == "disabled":
        notes.append(
            f"方法一当前直接使用东方财富可比快照，共纳入 {len(wind_summary.get('returned_codes', []))} 只可比公司。"
        )
    elif wind_summary.get("api_calls"):
        notes.append(
            f"Wind 本次实际请求 {wind_summary.get('api_calls', 0)} 次，"
            f"其中固定字段新增 {len(wind_summary.get('api_fetched_fixed', []))} 只，"
            f"可变字段刷新 {len(wind_summary.get('api_fetched_variable', []))} 只。"
        )

    if comparable_codes and wind_summary.get("channel") not in {"", "disabled"}:
        if wind_summary.get("local_computed_codes"):
            notes.append(
                "方法一当前优先使用 Wind 原料字段本地计算 PE/市值："
                + ", ".join(wind_summary.get("local_computed_codes", []))
                + "。"
            )
        if comparable_codes and not wind_summary.get("api_calls") and wind_summary.get("returned_codes"):
            notes.append("可比公司估值本次优先命中本地 Wind 缓存，未新增 API 请求。")
        if wind_summary.get("eastmoney_fallback_used"):
            notes.append(
                "Wind 不可用或字段缺失时，以下代码使用了东方财富补充数据："
                + ", ".join(wind_summary.get("eastmoney_fallback_used", []))
                + "。"
            )
        if wind_summary.get("cross_validated_codes"):
            notes.append(
                "已完成 Wind / 东方财富交叉验证："
                + ", ".join(wind_summary.get("cross_validated_codes", []))
                + "。"
            )
        if wind_summary.get("cross_validation_warnings"):
            notes.append(
                "跨源口径差异提醒："
                + "；".join(wind_summary.get("cross_validation_warnings", []))
                + "。"
            )
        if wind_summary.get("stale_variable_used"):
            notes.append(
                "Wind quota 或通道限制下，以下可比公司沿用了本地旧快照："
                + ", ".join(wind_summary.get("stale_variable_used", []))
                + "。"
            )
        if wind_summary.get("skipped_due_quota"):
            notes.append(
                "由于 Wind quota 限制，以下代码本次未刷新："
                + ", ".join(wind_summary.get("skipped_due_quota", []))
                + "。"
            )
        if wind_summary.get("eastmoney_api_calls"):
            notes.append(f"东方财富本次补充抓取 {wind_summary.get('eastmoney_api_calls', 0)} 次，用于备选或交叉验证。")
        if wind_summary.get("reason") and wind_summary.get("reason") != "Wind 当前处于禁用状态。":
            notes.append(f"Wind 提示：{wind_summary.get('reason')}")

    if method2.get("available"):
        base_stat_label = str(method2.get("base_stat_label", "")).strip()
        if base_stat_label and base_stat_label != "中位数":
            notes.append(f"方法二同行业首日涨幅统计使用{base_stat_label}，并限定在标的上市年内同二级行业样本。")

    old_shares_desc = data_dict.get("old_shares_desc", "")
    if "待确认" in old_shares_desc:
        notes.append("首日流通老股数据暂未确认，当前首日流通盘按仅新增发行量估算。")
    elif old_shares_meta.get("fallback_used"):
        notes.append("首日流通老股未从上市公告书提取到有效结果，本次已回退到招股文件口径。")
    elif old_shares_meta.get("source_file_type") == "招股文件" and not old_shares_meta.get("listing_pdf_found", True):
        notes.append("当前未找到上市公告书，首日流通老股暂按招股文件口径估算。")

    return notes
