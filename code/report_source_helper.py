from __future__ import annotations

from typing import Any


SUPPLEMENT_FIELD_LABELS = {
    "APPLY_DATE": "申购日期",
    "PRICE_WAY": "定价方式",
    "ISSUE_PRICE": "发行价格",
    "AFTER_ISSUE_PE": "发行市盈率",
    "TOTAL_ISSUE_NUM": "发行总量",
    "TOP_APPLY_MARKETCAP": "顶格打新金额",
    "ONLINE_VA_NUM": "有效申购户数",
    "ONLINE_ALLOCATED_ACCOUNTS": "网上获配户数",
    "ONLINE_ISSUE_LWR": "中签率",
    "INDUSTRY_PE_NEW": "行业 PE",
    "INDUSTRY": "所属行业",
    "INDUSTRY_CODE": "行业代码",
    "SW_INDUSTRY": "申万行业",
    "MAIN_BUSINESS": "主营业务",
    "TOTAL_SHARE_CAPITAL_AFTER_ISSUE": "发行后总股本",
    "SUBSCRIPTION_LIMIT_WAN_SHARES": "申购上限",
    "ONLINE_ISSUE_NUM": "网上发行数量",
    "ONLINE_VA_SHARES": "有效申购股数",
    "ONLINE_ES_MULTIPLE": "申购倍数",
    "FROZEN_FUNDS_YI": "冻结资金",
    "FRACTIONAL_THRESHOLD_SHARES": "碎股门槛",
    "FRACTIONAL_TIME_PRIORITY_REQUIRED": "碎股时间优先",
    "SUBSCRIPTION_AMOUNT_DISTRIBUTION": "申购金额梯度",
}

DISPLAY_ONLY_SUPPLEMENT_FIELDS = frozenset(
    {
        "PRICE_WAY",
        "ONLINE_VA_NUM",
        "MAIN_BUSINESS",
    }
)


def normalize_supplemented_fields(fields: Any) -> list[str]:
    normalized: list[str] = []
    for item in fields or []:
        name = str(item or "").strip()
        if name and name not in normalized:
            normalized.append(name)
    order_map = {name: index for index, name in enumerate(SUPPLEMENT_FIELD_LABELS)}
    return sorted(
        normalized,
        key=lambda name: (
            order_map.get(name, len(order_map)),
            normalized.index(name),
        ),
    )


def get_supplemented_field_labels(fields: Any) -> list[str]:
    labels: list[str] = []
    for field_name in normalize_supplemented_fields(fields):
        labels.append(SUPPLEMENT_FIELD_LABELS.get(field_name, field_name))
    return labels


def supplemented_fields_are_display_only(fields: Any) -> bool:
    normalized = normalize_supplemented_fields(fields)
    return bool(normalized) and set(normalized).issubset(DISPLAY_ONLY_SUPPLEMENT_FIELDS)


def build_ipo_source_text(all_data: dict[str, Any]) -> str:
    ipo_summary = all_data.get("ipo_data_summary") or {}
    provider = str(ipo_summary.get("provider") or all_data.get("params", {}).get("ipo_data_source", "eastmoney")).strip().lower() or "eastmoney"
    prospectus_labels = get_supplemented_field_labels(ipo_summary.get("prospectus_supplemented_fields"))
    prospectus_suffix = f"+ 招股说明书（字段补充：{'、'.join(prospectus_labels)}）" if prospectus_labels else ""
    issue_announcement_labels = get_supplemented_field_labels(ipo_summary.get("issue_announcement_supplemented_fields"))
    issue_announcement_suffix = (
        f"+ 发行公告（字段补充：{'、'.join(issue_announcement_labels)}）" if issue_announcement_labels else ""
    )
    issue_result_labels = get_supplemented_field_labels(ipo_summary.get("issue_result_supplemented_fields"))
    issue_result_suffix = f"+ 发行结果公告（字段补充：{'、'.join(issue_result_labels)}）" if issue_result_labels else ""
    document_suffix = f"{prospectus_suffix}{issue_announcement_suffix}{issue_result_suffix}"
    if provider != "tushare":
        return f"东方财富（IPO 信息与近期样本）{document_suffix}"

    tushare_label = "Tushare（IPO 关键字段与近期样本）"
    if ipo_summary.get("industry_pe_source") == "tushare_sw_daily":
        tushare_label = "Tushare（IPO 关键字段、行业 PE 与近期样本）"

    supplemented_labels = get_supplemented_field_labels(ipo_summary.get("supplemented_fields"))
    extra_parts: list[str] = []
    if ipo_summary.get("target_fallback_used"):
        extra_parts.append("目标股回退")
    if ipo_summary.get("eastmoney_recent_fallback_used"):
        extra_parts.append("部分近期样本回退")
    if ipo_summary.get("eastmoney_supplement_used"):
        if supplemented_labels:
            qualifier = "仅展示补充" if supplemented_fields_are_display_only(ipo_summary.get("supplemented_fields")) else "字段补充"
            extra_parts.append(f"{qualifier}：{'、'.join(supplemented_labels)}")
        else:
            extra_parts.append("报告字段补充")
    if extra_parts:
        return f"{tushare_label}+ 东方财富（{'；'.join(extra_parts)}）{document_suffix}"
    return f"{tushare_label}{document_suffix}"


def build_comparable_source_text(all_data: dict[str, Any]) -> str:
    comparable_summary = all_data.get("comparable_summary") or all_data.get("wind_summary", {}) or {}
    provider = str(comparable_summary.get("provider") or all_data.get("params", {}).get("comparable_data_source", "wind")).strip().lower() or "wind"
    provider_label = "Tushare" if provider == "tushare" else "Wind"
    channel = str(comparable_summary.get("channel", all_data.get("params", {}).get("wind_channel", "disabled"))).strip().lower()

    if provider == "tushare":
        if comparable_summary.get("eastmoney_fallback_used"):
            return (
                f"Tushare（T-1 收盘口径）+ 东方财富（补充 {len(comparable_summary.get('eastmoney_fallback_used', []))} 只，"
                f"交叉验证 {len(comparable_summary.get('cross_validated_codes', []))} 只）"
            )
        if comparable_summary.get("api_calls"):
            return (
                f"Tushare（本次请求 {comparable_summary.get('api_calls', 0)} 次，"
                f"缓存命中 {len(comparable_summary.get('variable_cache_hits', []))} 只）"
            )
        if comparable_summary.get("returned_codes"):
            return (
                f"Tushare（仅使用本地缓存，缓存命中 {len(comparable_summary.get('variable_cache_hits', []))} 只，"
                f"东方财富缓存 {len(comparable_summary.get('eastmoney_cache_hits', []))} 只）"
            )
        if comparable_summary.get("eastmoney_fetched") or comparable_summary.get("eastmoney_cache_hits"):
            return (
                f"可比快照（{len(comparable_summary.get('returned_codes', []))} 只，"
                f"实时抓取 {len(comparable_summary.get('eastmoney_fetched', []))} 只，"
                f"缓存命中 {len(comparable_summary.get('eastmoney_cache_hits', []))} 只）"
            )
        return "Tushare（当前未取到可比公司快照）"

    if channel == "disabled":
        if comparable_summary.get("returned_codes"):
            return (
                f"可比快照（{len(comparable_summary.get('returned_codes', []))} 只，"
                f"实时抓取 {len(comparable_summary.get('eastmoney_fetched', []))} 只，"
                f"缓存命中 {len(comparable_summary.get('eastmoney_cache_hits', []))} 只）"
            )
        return "可比快照（未形成有效可比公司数据）"
    if channel == "excel_only":
        return "Wind（Excel 通道预留，当前仅用本地缓存）"
    if comparable_summary.get("eastmoney_fallback_used"):
        return (
            f"{provider_label}（原料字段本地计算）+ 东方财富（补充 {len(comparable_summary.get('eastmoney_fallback_used', []))} 只，"
            f"交叉验证 {len(comparable_summary.get('cross_validated_codes', []))} 只）"
        )
    if comparable_summary.get("api_calls"):
        return (
            f"{provider_label}（本次请求 {comparable_summary.get('api_calls', 0)} 次，"
            f"本地计算 {len(comparable_summary.get('local_computed_codes', []))} 只，"
            f"缓存命中 {len(comparable_summary.get('variable_cache_hits', []))} 只）"
        )
    if comparable_summary.get("returned_codes"):
        return (
            f"{provider_label}（仅使用本地缓存，本地计算 {len(comparable_summary.get('local_computed_codes', []))} 只，"
            f"东方财富缓存 {len(comparable_summary.get('eastmoney_cache_hits', []))} 只）"
        )
    return f"{provider_label}（已开启但本次未取到可比公司快照）"
