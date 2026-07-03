from __future__ import annotations

import html
import report_source_helper
import shutil
import statistics
import subprocess
import subscription_predictor
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


EDGE_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)
PDF_RENDER_TIMEOUT_SECONDS = 90.0
PDF_READY_STABLE_SECONDS = 2.0
PDF_READY_MIN_BYTES = 1024
DISPLAY_MISSING_TEXT = "未取到数据"


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_number(value: Any, digits: int = 2, fallback: str = "-") -> str:
    number = _safe_float(value)
    if number is None:
        return fallback
    return f"{number:.{digits}f}"


def _fmt_pct(value: Any, digits: int = 2, fallback: str = "-") -> str:
    number = _safe_float(value)
    if number is None:
        return fallback
    return f"{number:.{digits}f}%"


def _fmt_weight(value: Any, fallback: str = "-") -> str:
    number = _safe_float(value)
    if number is None:
        return fallback
    pct = number * 100
    if abs(pct - round(pct)) < 1e-9:
        return f"{pct:.0f}%"
    return f"{pct:.2f}%"


def _compress_text(text: str, limit: int = 180) -> str:
    clean = " ".join((text or "").split())
    if not clean:
        return DISPLAY_MISSING_TEXT
    if len(clean) <= limit:
        return clean
    return f"{clean[:limit].rstrip()}..."


def _display_text(value: Any, fallback: str = DISPLAY_MISSING_TEXT) -> str:
    if value in (None, "", "--"):
        return fallback
    text = str(value).strip()
    return text or fallback


def _fmt_date(value: Any, fallback: str = "-") -> str:
    if value in (None, "", "--"):
        return fallback
    text = str(value).strip()
    if " " in text:
        return text.split(" ", 1)[0]
    return text


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


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        return 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28
    if month in {4, 6, 9, 11}:
        return 30
    return 31


def _subtract_months(current_date: date, months: int) -> date:
    year = current_date.year
    month = current_date.month - months
    while month <= 0:
        year -= 1
        month += 12
    return date(year, month, min(current_date.day, _days_in_month(year, month)))


def _filter_recent_items_by_days(
    recent_ipos: list[dict[str, Any]],
    days: Any,
    reference_date: Any,
) -> list[dict[str, Any]]:
    try:
        day_count = max(int(days), 1)
    except (TypeError, ValueError):
        day_count = 90
    end_date = _parse_date(reference_date) or date.today()
    start_date = end_date - timedelta(days=day_count)

    filtered: list[dict[str, Any]] = []
    for item in recent_ipos:
        listing_date = _parse_date(item.get("LISTING_DATE"))
        if listing_date is None:
            continue
        if start_date <= listing_date <= end_date:
            filtered.append(item)

    filtered.sort(key=lambda item: _parse_date(item.get("LISTING_DATE")) or date.min, reverse=True)
    return filtered


def _build_wind_source_text(all_data: dict[str, Any]) -> str:
    wind_summary = all_data.get("wind_summary", {}) or {}
    channel = str(wind_summary.get("channel", all_data.get("params", {}).get("wind_channel", "disabled"))).strip().lower()
    if channel == "disabled":
        if wind_summary.get("returned_codes"):
            return (
                f"可比快照（{len(wind_summary.get('returned_codes', []))} 只，"
                f"实时抓取 {len(wind_summary.get('eastmoney_fetched', []))} 只，"
                f"缓存命中 {len(wind_summary.get('eastmoney_cache_hits', []))} 只）"
            )
        return "可比快照（未形成有效可比公司数据）"
    if channel == "excel_only":
        return "Wind（Excel 通道预留，当前仅用本地缓存）"
    if wind_summary.get("eastmoney_fallback_used"):
        return (
            f"Wind（原料字段本地计算）+ 东方财富（补充 {len(wind_summary.get('eastmoney_fallback_used', []))} 只，"
            f"交叉验证 {len(wind_summary.get('cross_validated_codes', []))} 只）"
        )
    if wind_summary.get("api_calls"):
        return (
            f"Wind（本次请求 {wind_summary.get('api_calls', 0)} 次，"
            f"本地计算 {len(wind_summary.get('local_computed_codes', []))} 只，"
            f"缓存命中 {len(wind_summary.get('variable_cache_hits', []))} 只）"
        )
    if wind_summary.get("returned_codes"):
        return (
            f"Wind（仅使用本地缓存，本地计算 {len(wind_summary.get('local_computed_codes', []))} 只，"
            f"东方财富缓存 {len(wind_summary.get('eastmoney_cache_hits', []))} 只）"
        )
    return "Wind（已开启但本次未取到可比公司快照）"


def _build_comparable_items(comparable_data: list[dict[str, Any]]) -> tuple[list[list[str]], str, str]:
    if not comparable_data:
        return [["暂无可比公司估值数据", "-", "-", "-", "-", "-"]], "-", "-"

    rows: list[list[str]] = []
    pe_values: list[float] = []
    pb_values: list[float] = []
    for item in comparable_data:
        pe = _safe_float(item.get("pe_ttm"))
        pb = _safe_float(item.get("pb_lf"))
        if pe and pe > 0:
            pe_values.append(pe)
        if pb and pb > 0:
            pb_values.append(pb)
        rows.append(
            [
                str(item.get("name", "-")),
                str(item.get("code", "-")),
                _fmt_number(item.get("close")),
                _fmt_number(item.get("pe_ttm")),
                _fmt_number(item.get("pb_lf")),
                _fmt_number(item.get("mkt_cap")),
            ]
        )

    pe_median = _fmt_number(statistics.median(pe_values)) if pe_values else "-"
    pb_median = _fmt_number(statistics.median(pb_values)) if pb_values else "-"
    return rows, pe_median, pb_median


def _build_recent_items(recent_ipos: list[dict[str, Any]]) -> list[list[str]]:
    if not recent_ipos:
        return [["暂无近期样本", "-", "-", "-", "-", "-", "-"]]

    rows: list[list[str]] = []
    for item in recent_ipos:
        display_price = item.get("AVERAGE_PRICE")
        if display_price in (None, "", "--"):
            display_price = item.get("CLOSE_PRICE")
        display_change = item.get("LD_AVERAGE_CHANGE")
        if display_change in (None, "", "--"):
            display_change = item.get("LD_CLOSE_CHANGE")
        rows.append(
            [
                str(item.get("SECURITY_CODE", "-")),
                str(item.get("SECURITY_NAME_ABBR", "-")),
                _fmt_date(item.get("LISTING_DATE")),
                _fmt_number(item.get("ISSUE_PRICE")),
                _fmt_number(display_price),
                _fmt_pct(display_change, 2),
                str(item.get("industry_primary", "未分类")),
            ]
        )
    return rows


def _build_overview_interval(final: dict[str, Any]) -> str:
    low_text = _fmt_number(final.get("range_low"), fallback="")
    high_text = _fmt_number(final.get("range_high"), fallback="")
    if not low_text or not high_text:
        return ""
    return f"{low_text} - {high_text}"


def _build_time_priority_text(prediction: dict[str, Any], key: str, *, overview: bool = False) -> str:
    if not prediction.get("available"):
        return "" if overview else DISPLAY_MISSING_TEXT
    value = prediction.get(key)
    if overview:
        if key == "top_apply_time_priority_required":
            if value:
                return "必须"
            return "可能" if prediction.get("protected_guaranteed_threshold_exceeds_top_apply") else "否"
        if key == "fractional_time_priority_required":
            if prediction.get("top_apply_below_guaranteed"):
                return "必须"
            if value:
                return str(prediction.get("fractional_time_priority_overview_text") or "可能")
            return "否"
        return "可能" if value else "否"
    if key == "top_apply_time_priority_required":
        return str(prediction.get("top_apply_time_priority_note") or ("可能需要" if value else "否"))
    if key == "fractional_time_priority_required":
        return str(prediction.get("fractional_time_priority_note") or ("可能需要" if value else "否"))
    return "可能需要" if value else "否"


def _build_lot_threshold_overview_items(prediction: dict[str, Any]) -> list[tuple[str, str]]:
    if not prediction.get("available"):
        return []
    items: list[tuple[str, str]] = []
    for raw_item in prediction.get("lot_thresholds") or []:
        if not isinstance(raw_item, dict):
            continue
        if raw_item.get("display") is False:
            continue
        label = str(raw_item.get("ladder_label") or "").strip()
        if not label:
            lots = int(_safe_float(raw_item.get("lots")) or 0)
            if lots <= 0:
                continue
            label = f"{lots}手"
        elif "+" not in label:
            continue
        if not label:
            continue
        amount_text = _fmt_number(raw_item.get("threshold_amount_wan"), fallback="")
        if not amount_text:
            continue
        items.append((f"{label}门槛（万元）", amount_text))
    return items


def _format_code_list(codes: list[Any], fallback: str = "无") -> str:
    items = [str(code).strip() for code in codes if str(code).strip()]
    return "、".join(items) if items else fallback


def _build_composite_lines(all_data: dict[str, Any]) -> list[str]:
    params = all_data["params"]
    method1 = all_data["method1"]
    method2 = all_data["method2"]
    final = all_data["final"]

    width = float(params.get("price_range_width", 0.15))
    width_text = _fmt_weight(width)

    if not final.get("available"):
        return [
            f"综合估值公式 = {final.get('reason', '缺少可用的估值结果，无法给出综合定价。')}",
            f"区间宽度 = ±{width_text}",
        ]

    weight_comparable = _safe_float(final.get("weight_comparable"))
    weight_industry = _safe_float(final.get("weight_industry_momentum"))

    if weight_comparable is None:
        weight_comparable = _safe_float(params.get("weight_comparable")) or 0.0
    if weight_industry is None:
        weight_industry = _safe_float(params.get("weight_industry_momentum")) or 0.0

    if method1.get("available") and method2.get("available"):
        return [
            f"权重设置 = 方法一 {_fmt_weight(weight_comparable)} + 方法二 {_fmt_weight(weight_industry)}",
            f"综合估值公式 = 方法一目标价 × {_fmt_weight(weight_comparable)} + 方法二目标价 × {_fmt_weight(weight_industry)}",
            (
                f"代入结果 = {_fmt_number(method1.get('target_price'))} × {_fmt_weight(weight_comparable)} + "
                f"{_fmt_number(method2.get('target_price'))} × {_fmt_weight(weight_industry)} = {_fmt_number(final.get('target_price'))}"
            ),
            f"区间宽度 = ±{width_text}",
        ]

    if method2.get("available"):
        return [
            "权重设置 = 方法一 0% + 方法二 100%",
            "综合估值公式 = 当前仅采用方法二结果",
            f"代入结果 = {_fmt_number(method2.get('target_price'))} = {_fmt_number(final.get('target_price'))}",
            f"区间宽度 = ±{width_text}",
        ]

    return [
        f"综合估值公式 = {final.get('reason', '缺少可用的估值结果，无法给出综合定价。')}",
        f"区间宽度 = ±{width_text}",
    ]


def _prepare_report_context(all_data: dict[str, Any]) -> dict[str, Any]:
    generated_at = datetime.now()
    analysis_date = all_data["analysis_date"]
    ipo = all_data["ipo_info"]
    industry = all_data["industry"]
    method1 = all_data["method1"]
    method2 = all_data["method2"]
    final = all_data["final"]
    params = all_data["params"]
    notes = list(all_data.get("notes") or [])
    recent_ipos = all_data.get("recent_ipos") or []
    recent_days = params.get("recent_days", int(params.get("recent_months", 3)) * 30)
    display_recent_ipos = _filter_recent_items_by_days(recent_ipos, recent_days, analysis_date)
    comparable_data = all_data.get("comparable_data") or []
    wind_source_text = report_source_helper.build_comparable_source_text(all_data)
    ipo_source_text = report_source_helper.build_ipo_source_text(all_data)

    comparable_rows, pe_median, pb_median = _build_comparable_items(comparable_data)
    recent_rows = _build_recent_items(display_recent_ipos)
    note_items = notes or ["当前未触发额外风险提示。"]

    issue_price = _safe_float(ipo.get("ISSUE_PRICE"))
    issue_pe = _safe_float(ipo.get("AFTER_ISSUE_PE"))
    industry_pe = _safe_float(ipo.get("INDUSTRY_PE_NEW"))
    price_way_text = _display_text(ipo.get("PRICE_WAY"))
    pe_ratio = (issue_pe / industry_pe * 100) if issue_pe and industry_pe else None
    discount = ((1 - issue_pe / industry_pe) * 100) if issue_pe and industry_pe else None
    subscription_prediction = all_data.get("subscription_prediction") or {}
    if not subscription_prediction:
        subscription_prediction = subscription_predictor.build_subscription_prediction(ipo, recent_ipos, params)
    subscription_prediction_rows = [
        [str(cell) for cell in row]
        for row in (subscription_prediction.get("table_rows") or [["模型状态", DISPLAY_MISSING_TEXT, "-"]])
    ]
    subscription_prediction_display_rows = [
        [*(row[:2]), *[""] * max(0, 2 - len(row[:2]))]
        for row in subscription_prediction_rows
    ]

    if method1.get("available"):
        method1_lines = [
            f"新股 EPS = {_fmt_number(method1.get('eps'), 4)} 元",
            f"可比公司 PE 统计值 = {_fmt_number(method1.get('comp_pe'))} 倍",
            f"北交所折价系数 = {_fmt_number(params.get('bse_discount_factor'))}",
            f"目标价 = {_fmt_number(method1.get('target_price'))} 元（涨幅 {_fmt_pct(method1.get('change_pct'))}）",
        ]
    else:
        method1_lines = [str(method1.get("reason", "当前未生成方法一结果。"))]

    if method2.get("available"):
        sample_label = industry["display_name"] if method2.get("sample_scope") != "全市场" else "全市场"
        base_stat_label = str(method2.get("base_stat_label", "中位数")).strip() or "中位数"
        sample_codes_text = _format_code_list(method2.get("sample_codes") or [])
        method2_lines = [
            (
                f"近{recent_days}天{sample_label}新股首日均价涨幅{base_stat_label} = "
                f"{_fmt_pct(method2.get('base_chg'))}（样本 {method2.get('sample_count', 0)} 只，{method2.get('sample_scope')}）"
            ),
            f"方法二样本范围 = 历史候选 {method2.get('historical_sample_count', 0)} 只，实际纳入 {method2.get('sample_count', 0)} 只",
            f"方法二实际样本代码 = {sample_codes_text}",
            (
                f"调节因子 = {_fmt_number(method2.get('adj_factor'), 4)}"
                f"（流通盘 {_fmt_number(method2.get('float_factor'), 2)} × PE {_fmt_number(method2.get('pe_factor'), 2)} × 走势 {_fmt_number(method2.get('trend_factor'), 2)}）"
            ),
            f"目标价 = {_fmt_number(method2.get('target_price'))} 元（涨幅 {_fmt_pct(method2.get('change_pct'))}）",
        ]
    else:
        method2_lines = [str(method2.get("reason", "当前未生成方法二结果。"))]

    basic_rows = [
        ["股票代码", str(ipo.get("SECURITY_CODE", "-"))],
        ["股票简称", str(ipo.get("SECURITY_NAME_ABBR", "-"))],
        ["发行价格", f"{_fmt_number(issue_price)} 元"],
        ["发行市盈率", f"{_fmt_number(issue_pe)} 倍"],
        ["行业市盈率", f"{_fmt_number(industry_pe)} 倍"],
        ["发行PE / 行业PE", f"{_fmt_pct(pe_ratio)}（发行折价 {_fmt_pct(discount)}）"],
        ["申购日期", _fmt_date(ipo.get("APPLY_DATE"))],
        ["上市日期", _fmt_date(ipo.get("LISTING_DATE"))],
        ["定价方式", price_way_text],
        ["发行总量", f"{_fmt_number(ipo.get('TOTAL_ISSUE_NUM'))} 万股"],
        ["首日流通盘", f"{_fmt_number(all_data.get('float_shares'))} 万股"],
        ["首日流通老股", str(all_data.get("old_shares_desc", "-"))],
        ["所属行业", _display_text(industry.get("display_name"), fallback="-")],
    ]

    valuation_rows = [
        ["可比公司估值法", _fmt_number(method1.get("target_price")), _fmt_pct(method1.get("change_pct"))],
        ["行业新股折溢价法", _fmt_number(method2.get("target_price")), _fmt_pct(method2.get("change_pct"))],
        ["综合估值", _fmt_number(final.get("target_price")), _fmt_pct(all_data.get("final_change_pct"))],
        [
            "估值区间",
            f"{_fmt_number(final.get('range_low'))} - {_fmt_number(final.get('range_high'))}",
            f"{_fmt_pct(all_data.get('range_change_low'))} ~ {_fmt_pct(all_data.get('range_change_high'))}",
        ],
    ]

    overview_listing_date = ""
    if all_data.get("listing_pdf_found", True):
        overview_listing_date = _fmt_date(ipo.get("LISTING_DATE"), fallback="")

    lot_threshold_overview_items = _build_lot_threshold_overview_items(subscription_prediction)
    overview_headers = [
        "代码",
        "名称",
        "申购日期",
        "市盈率",
        "发行价",
        "最大申购上限（万元）",
        *(header for header, _ in lot_threshold_overview_items),
        "正股抢时间",
        "碎股抢时间",
        "上市日期",
        "估价区间（元）",
    ]
    overview_row = [
        str(ipo.get("SECURITY_CODE", "") or ""),
        str(ipo.get("SECURITY_NAME_ABBR", "") or ""),
        _fmt_date(ipo.get("APPLY_DATE"), fallback=""),
        _fmt_number(issue_pe, fallback=""),
        _fmt_number(issue_price, fallback=""),
        _fmt_number(ipo.get("TOP_APPLY_MARKETCAP"), fallback=""),
        *(value for _, value in lot_threshold_overview_items),
        _build_time_priority_text(subscription_prediction, "top_apply_time_priority_required", overview=True),
        _build_time_priority_text(subscription_prediction, "fractional_time_priority_required", overview=True),
        overview_listing_date,
        _build_overview_interval(final),
    ]

    return {
        "analysis_date": analysis_date,
        "recent_days": recent_days,
        "generated_at_text": generated_at.strftime("%Y-%m-%d %H:%M:%S"),
        "file_timestamp": generated_at.strftime("%Y%m%d_%H%M%S"),
        "title": f"{ipo.get('SECURITY_NAME_ABBR', '未知')}（{ipo.get('SECURITY_CODE', '-')}）北交所新股上市首日估值分析",
        "file_stem": f"{ipo.get('SECURITY_CODE', 'unknown')}_{ipo.get('SECURITY_NAME_ABBR', '未知')}_估值_{generated_at.strftime('%Y%m%d_%H%M%S')}",
        "ipo_source_text": ipo_source_text,
        "wind_source_text": wind_source_text,
        "basic_rows": basic_rows,
        "subscription_prediction_rows": subscription_prediction_rows,
        "subscription_prediction_display_rows": subscription_prediction_display_rows,
        "company_description": _compress_text(all_data.get("company_description", "")),
        "comparable_rows": comparable_rows,
        "comparable_summary_rows": [
            ["中位数", "-", "-", pe_median, pb_median, "-"],
            ["新股发行PE", "-", _fmt_number(issue_price), _fmt_number(issue_pe), "-", "-"],
            ["发行折价", "-", "-", _fmt_pct(discount), "-", "-"],
        ],
        "method1_lines": method1_lines,
        "method2_lines": method2_lines,
        "composite_lines": _build_composite_lines(all_data),
        "valuation_rows": valuation_rows,
        "note_items": note_items,
        "recent_rows": recent_rows,
        "overview_headers": overview_headers,
        "overview_row": overview_row,
    }


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    header = "| " + " | ".join(headers) + " |"
    separator = "|" + "|".join("------" for _ in headers) + "|"
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header, separator, *body])


def build_report_markdown(all_data: dict[str, Any]) -> str:
    context = _prepare_report_context(all_data)

    comparable_table = _markdown_table(
        ["可比公司", "代码", "当前价", "PE(TTM)", "PB(LF)", "市值(亿)"],
        context["comparable_rows"],
    )
    comparable_summary = "\n".join(
        "| " + " | ".join([f"**{cell}**" if row[0] in {"中位数", "新股发行PE", "发行折价"} and idx == 0 else cell for idx, cell in enumerate(row)]) + " |"
        for row in context["comparable_summary_rows"]
    )
    valuation_table = _markdown_table(
        ["", "目标价(元)", "预期涨幅"],
        [
            row if row[0] not in {"综合估值"} else [f"**{row[0]}**", f"**{row[1]}**", f"**{row[2]}**"]
            for row in context["valuation_rows"]
        ],
    )
    notes_text = "\n".join(f"- {item}" for item in context["note_items"])
    recent_table = _markdown_table(
        ["代码", "简称", "上市日", "发行价", "首日均价", "均价涨幅", "行业"],
        context["recent_rows"],
    )

    method1_text = "\n".join(f"- {line}" for line in context["method1_lines"])
    method2_text = "\n".join(f"- {line}" for line in context["method2_lines"])
    composite_text = "\n".join(f"- {line}" for line in context["composite_lines"])

    return f"""# {context['title']}

> 分析日期：{context['analysis_date']} | 生成时间：{context['generated_at_text']} | 数据来源：{context['ipo_source_text']}，PDF（可选），{context['wind_source_text']}

---

## 一、新股基本信息

{_markdown_table(['项目', '内容'], context['basic_rows'])}

## 二、申购资金配售预测

{_markdown_table(['字段', '数值'], context['subscription_prediction_display_rows'])}

## 三、公司概况

{context['company_description']}

**可比上市公司估值对比**

{comparable_table}
{comparable_summary}

## 四、首日定价分析

### 方法一：可比公司对比估值

{method1_text}

### 方法二：行业新股综合折溢价

{method2_text}

### 综合估值

{composite_text}

{valuation_table}

## 五、关注提示

{notes_text}

---

> 近期北交所新股首日表现一览（近{context['recent_days']}天）

{recent_table}

*以上分析基于历史数据统计和公开数据整理，仅供参考，不构成投资建议。*
"""


def _build_overview_text_from_context(context: dict[str, Any]) -> str:
    headers = [str(item) for item in context.get("overview_headers") or []]
    row = [str(item) for item in context.get("overview_row") or []]
    lines = []
    for header, value in zip(headers, row):
        lines.append(f"{header} {value}".rstrip())
    return "\n".join([*lines, ""])


def build_report_overview_text(all_data: dict[str, Any]) -> str:
    return _build_overview_text_from_context(_prepare_report_context(all_data))


def _html_table(headers: list[str], rows: list[list[str]], bold_row_indices: set[int] | None = None) -> str:
    bold_row_indices = bold_row_indices or set()
    thead = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body_rows: list[str] = []
    for idx, row in enumerate(rows):
        cells: list[str] = []
        for cell in row:
            content = html.escape(cell)
            if idx in bold_row_indices:
                content = f"<strong>{content}</strong>"
            cells.append(f"<td>{content}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def _build_report_html(context: dict[str, Any]) -> str:
    comparable_table = _html_table(
        ["可比公司", "代码", "当前价", "PE(TTM)", "PB(LF)", "市值(亿)"],
        context["comparable_rows"] + context["comparable_summary_rows"],
        bold_row_indices={len(context["comparable_rows"]), len(context["comparable_rows"]) + 1, len(context["comparable_rows"]) + 2},
    )
    valuation_table = _html_table(
        ["", "目标价(元)", "预期涨幅"],
        context["valuation_rows"],
        bold_row_indices={2},
    )
    recent_table = _html_table(
        ["代码", "简称", "上市日", "发行价", "首日均价", "均价涨幅", "行业"],
        context["recent_rows"],
    )
    subscription_prediction_table = _html_table(
        ["字段", "数值"],
        context["subscription_prediction_display_rows"],
    )

    basic_rows_html = "".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(value)}</td></tr>" for label, value in context["basic_rows"]
    )
    method1_html = "".join(f"<li>{html.escape(line)}</li>" for line in context["method1_lines"])
    method2_html = "".join(f"<li>{html.escape(line)}</li>" for line in context["method2_lines"])
    composite_html = "".join(f"<li>{html.escape(line)}</li>" for line in context["composite_lines"])
    notes_html = "".join(f"<li>{html.escape(item)}</li>" for item in context["note_items"])

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{html.escape(context['title'])}</title>
  <style>
    @page {{
      size: A4;
      margin: 14mm 12mm;
    }}
    body {{
      font-family: "Microsoft YaHei", "SimSun", sans-serif;
      color: #1f2937;
      font-size: 11px;
      line-height: 1.55;
      margin: 0;
    }}
    h1 {{
      font-size: 21px;
      margin: 0 0 8px;
      color: #0f172a;
    }}
    h2 {{
      font-size: 15px;
      margin: 20px 0 8px;
      padding-bottom: 4px;
      border-bottom: 1px solid #cbd5e1;
      color: #0f172a;
    }}
    h3 {{
      font-size: 13px;
      margin: 14px 0 6px;
      color: #1d4ed8;
    }}
    p.meta {{
      margin: 0 0 14px;
      color: #475569;
    }}
    p.desc {{
      margin: 8px 0 14px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin: 8px 0 14px;
      table-layout: fixed;
      word-break: break-word;
    }}
    th, td {{
      border: 1px solid #cbd5e1;
      padding: 6px 8px;
      vertical-align: top;
    }}
    thead th {{
      background: #e2e8f0;
      color: #0f172a;
    }}
    ul {{
      margin: 6px 0 12px 18px;
      padding: 0;
    }}
    li {{
      margin: 4px 0;
    }}
    .footnote {{
      margin-top: 18px;
      color: #64748b;
      font-size: 10px;
    }}
  </style>
</head>
<body>
  <h1>{html.escape(context['title'])}</h1>
<p class="meta">分析日期：{html.escape(context['analysis_date'])} | 生成时间：{html.escape(context['generated_at_text'])} | 数据来源：{html.escape(context['ipo_source_text'])}，PDF（可选），{html.escape(context['wind_source_text'])}</p>

  <h2>一、新股基本信息</h2>
  <table>
    <tbody>
      {basic_rows_html}
    </tbody>
  </table>

  <h2>二、申购资金配售预测</h2>
  {subscription_prediction_table}

  <h2>三、公司概况</h2>
  <p class="desc">{html.escape(context['company_description'])}</p>

  <h3>可比上市公司估值对比</h3>
  {comparable_table}

  <h2>四、首日定价分析</h2>
  <h3>方法一：可比公司对比估值</h3>
  <ul>{method1_html}</ul>

  <h3>方法二：行业新股综合折溢价</h3>
  <ul>{method2_html}</ul>

  <h3>综合估值</h3>
  <ul>{composite_html}</ul>
  {valuation_table}

  <h2>五、关注提示</h2>
  <ul>{notes_html}</ul>

  <h2>近期北交所新股首日表现一览（近{html.escape(str(context['recent_days']))}天）</h2>
  {recent_table}

  <p class="footnote">以上分析基于历史数据统计和公开数据整理，仅供参考，不构成投资建议。</p>
</body>
</html>
"""


def _find_edge_executable() -> Path:
    for candidate in EDGE_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("未找到 Microsoft Edge，无法导出 PDF。")


def _decode_process_output(raw: bytes | None) -> str:
    if not raw:
        return ""
    for encoding in ("utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _pdf_file_looks_complete(pdf_path: Path) -> bool:
    if not pdf_path.exists():
        return False
    try:
        size = pdf_path.stat().st_size
        if size < PDF_READY_MIN_BYTES:
            return False
        with pdf_path.open("rb") as file_obj:
            file_obj.seek(max(0, size - 4096))
            tail = file_obj.read()
    except OSError:
        return False
    return b"%%EOF" in tail


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=3)
            except (subprocess.TimeoutExpired, OSError):
                pass
        except OSError:
            pass


def _read_edge_log(log_path: Path) -> str:
    if not log_path.exists():
        return ""
    try:
        return _decode_process_output(log_path.read_bytes()).strip()
    except OSError:
        return ""


def _render_pdf_from_html(html_text: str, pdf_path: Path) -> None:
    edge_path = _find_edge_executable()
    pdf_path = pdf_path.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    temp_dir = pdf_path.parent / f"_report_pdf_{uuid.uuid4().hex[:8]}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        temp_root = Path(temp_dir)
        html_path = temp_root / "report.html"
        user_data_dir = temp_root / "edge_profile"
        edge_log_path = temp_root / "edge.log"
        html_path.write_text(html_text, encoding="utf-8")

        command = [
            str(edge_path),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-mode",
            "--disable-background-networking",
            "--disable-crash-reporter",
            "--disable-extensions",
            "--disable-features=msEdgeStartupBoost,StartupBoost",
            "--allow-file-access-from-files",
            f"--user-data-dir={user_data_dir}",
            f"--print-to-pdf={pdf_path.resolve()}",
            html_path.resolve().as_uri(),
        ]

        edge_log = edge_log_path.open("wb")
        process = subprocess.Popen(command, stdout=edge_log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + PDF_RENDER_TIMEOUT_SECONDS
        stable_since: float | None = None
        last_size = -1

        try:
            while True:
                if _pdf_file_looks_complete(pdf_path):
                    current_size = pdf_path.stat().st_size
                    current_time = time.monotonic()
                    if current_size == last_size:
                        stable_since = stable_since or current_time
                        if current_time - stable_since >= PDF_READY_STABLE_SECONDS:
                            _stop_process(process)
                            edge_log.close()
                            return
                    else:
                        last_size = current_size
                        stable_since = current_time

                if process.poll() is not None:
                    edge_log.close()
                    if _pdf_file_looks_complete(pdf_path):
                        return
                    break

                if time.monotonic() >= deadline:
                    _stop_process(process)
                    edge_log.close()
                    if _pdf_file_looks_complete(pdf_path):
                        return
                    break

                time.sleep(0.5)
        finally:
            if not edge_log.closed:
                edge_log.close()

        if not _pdf_file_looks_complete(pdf_path):
            debug_html_path = pdf_path.with_suffix(".html")
            debug_html_path.write_text(html_text, encoding="utf-8")
            error_text = _read_edge_log(edge_log_path)
            raise RuntimeError(
                f"PDF 导出失败。已保留 HTML 调试文件：{debug_html_path}。"
                + (f" Edge 输出：{error_text}" if error_text else "")
            )
    finally:
        # Edge can briefly keep profile/cache files locked after exit.
        for attempt in range(3):
            try:
                shutil.rmtree(temp_root)
                break
            except FileNotFoundError:
                break
            except OSError:
                if attempt == 2:
                    shutil.rmtree(temp_root, ignore_errors=True)
                    break
                time.sleep(0.5)


def generate_report(all_data: dict[str, Any], output_dir: str) -> str:
    context = _prepare_report_context(all_data)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    pdf_path = output_path / f"{context['file_stem']}.pdf"
    overview_path = output_path / f"{context['file_stem']}_一览.txt"
    html_text = _build_report_html(context)
    _render_pdf_from_html(html_text, pdf_path)
    overview_path.write_text(_build_overview_text_from_context(context), encoding="utf-8-sig")
    return str(pdf_path.resolve())
