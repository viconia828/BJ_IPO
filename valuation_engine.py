from __future__ import annotations

import statistics
from datetime import date
from typing import Any

from trend_scorer import get_trend_factor


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


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    return str(params.get("sample_weight_mode", "static")).strip().lower() or "static"


def _get_sample_half_life_days(params: dict[str, Any]) -> float:
    return max(float(params.get("sample_decay_half_life_days", 20)), 1.0)


def _resolve_reference_date(target_listing_date: str | date | None, records: list[dict[str, Any]]) -> date:
    target_date = _parse_date(target_listing_date)
    if target_date:
        return target_date

    sample_dates = [_parse_date(item.get("LISTING_DATE")) for item in records]
    sample_dates = [item for item in sample_dates if item is not None]
    if sample_dates:
        return max(sample_dates)
    return date.today()


def _get_sample_weight(sample_date: date | None, reference_date: date, params: dict[str, Any]) -> float:
    if sample_date is None:
        return 0.0
    if _get_sample_weight_mode(params) != "time_decay":
        return 1.0

    day_gap = max((reference_date - sample_date).days, 0)
    half_life_days = _get_sample_half_life_days(params)
    return 0.5 ** (day_gap / half_life_days)


def _summarize_change_stat(
    records: list[dict[str, Any]],
    params: dict[str, Any],
    reference_date: date,
) -> tuple[float | None, str]:
    value_weight_pairs: list[tuple[float, float]] = []
    for item in records:
        change_pct = _safe_float(item.get("LD_CLOSE_CHANGE"))
        if change_pct is None:
            continue
        sample_date = _parse_date(item.get("LISTING_DATE"))
        value_weight_pairs.append((change_pct, _get_sample_weight(sample_date, reference_date, params)))

    if not value_weight_pairs:
        return None, "中位数"

    if _get_sample_weight_mode(params) == "time_decay":
        value = _weighted_median(value_weight_pairs)
        label = f"时间衰减中位数（半衰期 {_get_sample_half_life_days(params):.0f} 天）"
        return value, label

    values = [value for value, _ in value_weight_pairs]
    return statistics.median(values), "中位数"


def method1_comparable(
    issue_price: float | None,
    issue_pe: float | None,
    comparable_data: list[dict[str, Any]],
    params: dict[str, Any],
) -> dict[str, Any]:
    if not issue_price or not issue_pe:
        return {"available": False, "reason": "发行价或发行 PE 缺失，无法计算方法一。"}

    clean_pe_values: list[float] = []
    for item in comparable_data:
        pe_value = _safe_float(item.get("pe_ttm"))
        if pe_value and pe_value > 0:
            clean_pe_values.append(pe_value)

    if not clean_pe_values:
        return {"available": False, "reason": "当前未获取到有效可比公司 PE 数据，方法一已跳过。"}

    eps = issue_price / issue_pe
    comp_pe = _median_or_mean(clean_pe_values, str(params.get("comparable_pe_stat", "median")))
    target_pe = comp_pe * float(params.get("bse_discount_factor", 0.75))
    target_price = eps * target_pe
    change_pct = (target_price / issue_price - 1) * 100

    return {
        "available": True,
        "eps": eps,
        "comp_pe": comp_pe,
        "target_pe": target_pe,
        "target_price": target_price,
        "change_pct": change_pct,
        "sample_count": len(clean_pe_values),
    }


def _pick_industry_samples(
    industry: dict[str, Any],
    recent_ipos: list[dict[str, Any]],
    min_samples: int,
) -> tuple[list[dict[str, Any]], str]:
    valid_records = [item for item in recent_ipos if _safe_float(item.get("LD_CLOSE_CHANGE")) is not None]
    secondary = industry.get("secondary")
    primary = industry.get("primary")

    if secondary and secondary != "未分类":
        secondary_records = [item for item in valid_records if item.get("industry_secondary") == secondary]
        if len(secondary_records) >= min_samples:
            return secondary_records, "二级行业"

    if primary and primary != "未分类":
        primary_records = [item for item in valid_records if item.get("industry_primary") == primary]
        if len(primary_records) >= min_samples:
            return primary_records, "一级行业"

    return valid_records, "全市场"


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
    if not issue_price:
        return {"available": False, "reason": "发行价缺失，无法计算方法二。"}
    if not recent_ipos:
        return {"available": False, "reason": "近期新股样本为空，无法计算方法二。"}

    historical_ipos = _filter_historical_samples(recent_ipos, target_code, target_listing_date)
    if not historical_ipos:
        return {"available": False, "reason": "不存在早于标的上市日的历史样本，无法计算方法二。"}

    min_samples = int(params.get("min_industry_samples", 2))
    samples, sample_scope = _pick_industry_samples(industry, historical_ipos, min_samples)
    reference_date = _resolve_reference_date(target_listing_date, historical_ipos)
    base_chg, base_stat_label = _summarize_change_stat(samples, params, reference_date)
    clean_gains = [_safe_float(item.get("LD_CLOSE_CHANGE")) for item in samples]
    clean_gains = [value for value in clean_gains if value is not None]
    if not clean_gains or base_chg is None:
        return {"available": False, "reason": "样本缺少首日涨幅字段，无法计算方法二。"}

    float_threshold = float(params.get("float_size_threshold", 2000))
    small_cap_premium = float(params.get("small_cap_premium", 0.1))
    if float_shares is not None and float_shares < float_threshold:
        float_factor = 1 + small_cap_premium
        float_note = f"{float_shares:.2f} 万股 < {float_threshold:.0f} 万股，给予小盘溢价"
    else:
        float_factor = 1.0
        float_note = "流通盘未触发小盘溢价"

    pe_low = float(params.get("pe_low_threshold", 0.3))
    pe_low_boost = float(params.get("pe_discount_boost", 0.1))
    pe_high = float(params.get("pe_high_threshold", 0.6))
    pe_high_drag = float(params.get("pe_premium_drag", -0.1))
    pe_ratio = None
    pe_factor = 1.0
    pe_note = "行业 PE 缺失，PE 因子按中性处理"
    if issue_pe and industry_pe and industry_pe > 0:
        pe_ratio = issue_pe / industry_pe
        if pe_ratio < pe_low:
            pe_factor = 1 + pe_low_boost
            pe_note = f"发行 PE 明显低于行业 PE，给予 {pe_low_boost * 100:.0f}% 加成"
        elif pe_ratio > pe_high:
            pe_factor = 1 + pe_high_drag
            pe_note = f"发行 PE 偏高，给予 {pe_high_drag * 100:.0f}% 调整"
        else:
            pe_note = "发行 PE 处于行业合理区间"

    trend = get_trend_factor(
        industry=industry,
        recent_ipos=historical_ipos,
        params=params,
        target_code=target_code,
        target_listing_date=target_listing_date,
    )
    adj_factor = float_factor * pe_factor * trend.factor
    expected_change_pct = base_chg * adj_factor
    target_price = issue_price * (1 + expected_change_pct / 100)

    return {
        "available": True,
        "base_chg": base_chg,
        "target_price": target_price,
        "change_pct": expected_change_pct,
        "sample_count": len(clean_gains),
        "sample_scope": sample_scope,
        "sample_codes": [item.get("SECURITY_CODE", "") for item in samples],
        "historical_sample_count": len(historical_ipos),
        "base_stat_label": base_stat_label,
        "float_factor": float_factor,
        "float_note": float_note,
        "pe_ratio": pe_ratio,
        "pe_factor": pe_factor,
        "pe_note": pe_note,
        "trend_factor": trend.factor,
        "trend_note": trend.note,
        "industry_trend_factor": trend.industry_factor,
        "market_trend_factor": trend.market_factor,
        "industry_trend_score_median": trend.industry_score_median,
        "market_trend_score_median": trend.market_score_median,
        "industry_trend_sample_codes": trend.industry_sample_codes,
        "market_trend_sample_codes": trend.market_sample_codes,
        "adj_factor": adj_factor,
    }


def composite_valuation(
    method1: dict[str, Any] | None,
    method2: dict[str, Any] | None,
    params: dict[str, Any],
) -> dict[str, Any]:
    width = float(params.get("price_range_width", 0.15))
    if method2 and method2.get("available") and not (method1 and method1.get("available")):
        target = float(method2["target_price"])
        return {
            "available": True,
            "target_price": target,
            "range_low": target * (1 - width),
            "range_high": target * (1 + width),
            "weight_comparable": 0.0,
            "weight_industry_momentum": 1.0,
        }

    if not (method1 and method1.get("available")) or not (method2 and method2.get("available")):
        return {"available": False, "reason": "缺少可用的估值结果，无法给出综合定价。"}

    weight_comparable = float(params.get("weight_comparable", 0.5))
    weight_industry = float(params.get("weight_industry_momentum", 0.5))
    target_price = method1["target_price"] * weight_comparable + method2["target_price"] * weight_industry
    return {
        "available": True,
        "target_price": target_price,
        "range_low": target_price * (1 - width),
        "range_high": target_price * (1 + width),
        "weight_comparable": weight_comparable,
        "weight_industry_momentum": weight_industry,
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
    comparable_codes = data_dict.get("comparable_codes", []) or []

    if industry.get("primary") == "未分类":
        notes.append("当前标的尚未完成行业映射，方法二已自动退回全市场样本。建议在 `策略参数.txt` 中填写 `stock_industry`。")

    if issue_pe and industry_pe and industry_pe > 0:
        ratio = issue_pe / industry_pe
        if ratio < float(params.get("pe_low_threshold", 0.3)):
            notes.append("发行 PE 显著低于行业 PE，定价具备一定折价优势。")
        elif ratio > float(params.get("pe_high_threshold", 0.6)):
            notes.append("发行 PE 相对行业偏高，需关注上市首日估值兑现压力。")

    if float_shares is not None and float_shares < float(params.get("float_size_threshold", 2000)):
        notes.append("首日流通盘偏小，历史上这类新股更容易获得情绪溢价。")

    if not method1.get("available"):
        notes.append("当前版本未启用 Wind 可比公司估值，方法一已跳过，综合估值默认以方法二为主。")
        if comparable_codes:
            notes.append(f"已从公告文件提取到可比公司代码：{', '.join(comparable_codes)}。待开启 Wind 后可直接用于方法一。")

    if method2.get("available") and method2.get("sample_scope") == "全市场":
        notes.append("同行业样本数量不足，方法二当前回退为全市场新股统计口径。")

    if method2.get("available"):
        base_stat_label = str(method2.get("base_stat_label", "")).strip()
        if base_stat_label and base_stat_label != "中位数":
            notes.append(f"方法二基础涨幅统计已切换为{base_stat_label}，越接近标的上市日的样本权重越高。")
        trend_note = str(method2.get("trend_note", "")).strip()
        if trend_note:
            notes.append(f"走势模块说明：{trend_note}")

    old_shares_desc = data_dict.get("old_shares_desc", "")
    if "待确认" in old_shares_desc:
        notes.append("老股转让数据暂未确认，当前首日流通盘按仅新增发行量估算。")

    return notes
