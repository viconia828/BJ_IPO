from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any


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


def _compress_text(text: str, limit: int = 180) -> str:
    clean = " ".join((text or "").split())
    if not clean:
        return "暂无公司简介，可在 `公告文件` 目录补充招股书摘要 PDF 后再生成增强版报告。"
    if len(clean) <= limit:
        return clean
    return f"{clean[:limit].rstrip()}..."


def _fmt_date(value: Any, fallback: str = "-") -> str:
    if value in (None, "", "--"):
        return fallback
    text = str(value).strip()
    if " " in text:
        return text.split(" ", 1)[0]
    return text


def _build_comparable_rows(comparable_data: list[dict[str, Any]]) -> tuple[str, str, str]:
    if not comparable_data:
        row = "| 暂无可比公司估值数据 | - | - | - | - | - |"
        return row, "-", "-"

    rows: list[str] = []
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
            "| {name} | {code} | {close} | {pe} | {pb} | {cap} |".format(
                name=item.get("name", "-"),
                code=item.get("code", "-"),
                close=_fmt_number(item.get("close")),
                pe=_fmt_number(item.get("pe_ttm")),
                pb=_fmt_number(item.get("pb_lf")),
                cap=_fmt_number(item.get("mkt_cap")),
            )
        )

    pe_median = _fmt_number(statistics.median(pe_values)) if pe_values else "-"
    pb_median = _fmt_number(statistics.median(pb_values)) if pb_values else "-"
    return "\n".join(rows), pe_median, pb_median


def _build_recent_rows(recent_ipos: list[dict[str, Any]]) -> str:
    if not recent_ipos:
        return "| 暂无近期样本 | - | - | - | - | - | - |"

    rows = []
    for item in recent_ipos:
        rows.append(
            "| {code} | {name} | {list_date} | {issue_price} | {close_price} | {chg} | {industry} |".format(
                code=item.get("SECURITY_CODE", "-"),
                name=item.get("SECURITY_NAME_ABBR", "-"),
                list_date=_fmt_date(item.get("LISTING_DATE")),
                issue_price=_fmt_number(item.get("ISSUE_PRICE")),
                close_price=_fmt_number(item.get("CLOSE_PRICE")),
                chg=_fmt_pct(item.get("LD_CLOSE_CHANGE"), 2),
                industry=item.get("industry_primary", "未分类"),
            )
        )
    return "\n".join(rows)


def generate_report(all_data: dict[str, Any], output_dir: str) -> str:
    analysis_date = all_data["analysis_date"]
    ipo = all_data["ipo_info"]
    industry = all_data["industry"]
    method1 = all_data["method1"]
    method2 = all_data["method2"]
    final = all_data["final"]
    notes = all_data.get("notes") or []
    recent_ipos = all_data.get("recent_ipos") or []
    comparable_data = all_data.get("comparable_data") or []

    comparable_rows, pe_median, pb_median = _build_comparable_rows(comparable_data)
    recent_rows = _build_recent_rows(recent_ipos)
    note_lines = "\n".join(f"- {note}" for note in notes) if notes else "- 当前未触发额外风险提示。"

    issue_price = _safe_float(ipo.get("ISSUE_PRICE"))
    issue_pe = _safe_float(ipo.get("AFTER_ISSUE_PE"))
    industry_pe = _safe_float(ipo.get("INDUSTRY_PE_NEW"))
    pe_ratio = (issue_pe / industry_pe * 100) if issue_pe and industry_pe else None
    discount = ((1 - issue_pe / industry_pe) * 100) if issue_pe and industry_pe else None

    if method1.get("available"):
        method1_text = (
            f"- 新股 EPS = {_fmt_number(method1.get('eps'), 4)} 元\n"
            f"- 可比公司 PE 统计值 = {_fmt_number(method1.get('comp_pe'))} 倍\n"
            f"- 北交所折价系数 = {_fmt_number(all_data['params'].get('bse_discount_factor'))}\n"
            f"- **目标价 = {_fmt_number(method1.get('target_price'))} 元（涨幅 {_fmt_pct(method1.get('change_pct'))}）**"
        )
    else:
        method1_text = f"- {method1.get('reason', '当前未生成方法一结果。')}"

    if method2.get("available"):
        sample_label = industry["display_name"] if method2.get("sample_scope") != "全市场" else "全市场"
        base_stat_label = str(method2.get("base_stat_label", "中位数")).strip() or "中位数"
        method2_text = (
            f"- 近{all_data['params'].get('recent_months', 3)}月{sample_label}新股首日涨幅{base_stat_label} = {_fmt_pct(method2.get('base_chg'))}"
            f"（样本 {method2.get('sample_count', 0)} 只，{method2.get('sample_scope')}）\n"
            f"- 调节因子 = {_fmt_number(method2.get('adj_factor'), 4)}"
            f"（流通盘 {_fmt_number(method2.get('float_factor'), 2)} × PE {_fmt_number(method2.get('pe_factor'), 2)} × 走势 {_fmt_number(method2.get('trend_factor'), 2)}）\n"
            f"- **目标价 = {_fmt_number(method2.get('target_price'))} 元（涨幅 {_fmt_pct(method2.get('change_pct'))}）**"
        )
    else:
        method2_text = f"- {method2.get('reason', '当前未生成方法二结果。')}"

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    file_path = output_path / f"{ipo.get('SECURITY_CODE', 'unknown')}_{ipo.get('SECURITY_NAME_ABBR', '未知')}_估值_{analysis_date.replace('-', '')}.md"

    report = f"""# {ipo.get('SECURITY_NAME_ABBR', '未知')}（{ipo.get('SECURITY_CODE', '-')}）北交所新股上市首日估值分析

> 分析日期：{analysis_date} | 数据来源：东方财富，PDF（可选），Wind（当前禁用）

---

## 一、新股基本信息

| 项目 | 内容 |
|------|------|
| 股票代码 | {ipo.get('SECURITY_CODE', '-')} |
| 股票简称 | {ipo.get('SECURITY_NAME_ABBR', '-')} |
| 发行价格 | {_fmt_number(issue_price)} 元 |
| 发行市盈率 | {_fmt_number(issue_pe)} 倍 |
| 行业市盈率 | {_fmt_number(industry_pe)} 倍 |
| 发行PE / 行业PE | {_fmt_pct(pe_ratio)}（发行折价 {_fmt_pct(discount)}） |
| 申购日期 | {_fmt_date(ipo.get('APPLY_DATE'))} |
| 上市日期 | {_fmt_date(ipo.get('LISTING_DATE'))} |
| 定价方式 | {ipo.get('PRICE_WAY', '-')} |
| 发行总量 | {_fmt_number(ipo.get('TOTAL_ISSUE_NUM'))} 万股 |
| 顶格打新金额 | {_fmt_number(ipo.get('TOP_APPLY_MARKETCAP'))} 万元 |
| 首日流通盘 | {_fmt_number(all_data.get('float_shares'))} 万股 |
| 老股转让 | {all_data.get('old_shares_desc', '-')} |
| 有效申购户数 | {_fmt_number(((_safe_float(ipo.get('ONLINE_VA_NUM')) or 0) / 10000), 2)} 万户 |
| 中签率 | {_fmt_pct(ipo.get('ONLINE_ISSUE_LWR'), 4)} |
| 所属行业 | {industry['display_name']} |

## 二、公司概况

{_compress_text(all_data.get('company_description', ''))}

**可比上市公司估值对比**

| 可比公司 | 代码 | 当前价 | PE(TTM) | PB(LF) | 市值(亿) |
|----------|------|--------|---------|--------|----------|
{comparable_rows}
| **中位数** | - | - | {pe_median} | {pb_median} | - |
| **新股发行PE** | - | {_fmt_number(issue_price)} | {_fmt_number(issue_pe)} | - | - |
| **发行折价** | - | - | {_fmt_pct(discount)} | - | - |

## 三、首日定价分析

### 方法一：可比公司对比估值

{method1_text}

### 方法二：行业新股综合折溢价

{method2_text}

### 综合估值

| | 目标价(元) | 预期涨幅 |
|------|-----------|---------|
| 可比公司估值法 | {_fmt_number(method1.get('target_price'))} | {_fmt_pct(method1.get('change_pct'))} |
| 行业新股折溢价法 | {_fmt_number(method2.get('target_price'))} | {_fmt_pct(method2.get('change_pct'))} |
| **综合估值** | **{_fmt_number(final.get('target_price'))}** | **{_fmt_pct(all_data.get('final_change_pct'))}** |
| 估值区间 | {_fmt_number(final.get('range_low'))} - {_fmt_number(final.get('range_high'))} | {_fmt_pct(all_data.get('range_change_low'))} ~ {_fmt_pct(all_data.get('range_change_high'))} |

## 四、关注提示

{note_lines}

---

> 近期北交所新股首日表现一览（近{all_data['params'].get('recent_months', 3)}月）

| 代码 | 简称 | 上市日 | 发行价 | 首日收盘 | 首日涨幅 | 行业 |
|------|------|--------|--------|---------|---------|------|
{recent_rows}

*以上分析基于历史数据统计和公开数据整理，仅供参考，不构成投资建议。*
"""

    file_path.write_text(report, encoding="utf-8")
    return str(file_path.resolve())
