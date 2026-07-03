from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import bse_ipo_valuation
import comparable_data_helper
import config_loader
import data_fetcher
import ipo_data_helper
import note_builder
import pdf_parser
import report_generator


PDF_DIR = ROOT_DIR / "公告文件"
OUTPUT_DIR = ROOT_DIR / "输出" / "回归"

RECENT_IPO_FIXTURES = [
    {
        "SECURITY_CODE": "920086",
        "SECURITY_NAME_ABBR": "科马材料",
        "LISTING_DATE": "2026-01-16",
        "ISSUE_PRICE": 11.66,
        "CLOSE_PRICE": 54.95,
        "LD_CLOSE_CHANGE": 371.27,
        "TURNOVERRATE": 85.12,
    },
    {
        "SECURITY_CODE": "920050",
        "SECURITY_NAME_ABBR": "爱舍伦",
        "LISTING_DATE": "2026-01-21",
        "ISSUE_PRICE": 15.98,
        "CLOSE_PRICE": 44.04,
        "LD_CLOSE_CHANGE": 175.59,
        "TURNOVERRATE": 79.44,
    },
    {
        "SECURITY_CODE": "920076",
        "SECURITY_NAME_ABBR": "国亮新材",
        "LISTING_DATE": "2026-01-22",
        "ISSUE_PRICE": 10.76,
        "CLOSE_PRICE": 28.06,
        "LD_CLOSE_CHANGE": 160.78,
        "TURNOVERRATE": 74.31,
    },
    {
        "SECURITY_CODE": "920159",
        "SECURITY_NAME_ABBR": "农大科技",
        "LISTING_DATE": "2026-01-28",
        "ISSUE_PRICE": 25.00,
        "CLOSE_PRICE": 52.89,
        "LD_CLOSE_CHANGE": 111.56,
        "TURNOVERRATE": 66.28,
    },
    {
        "SECURITY_CODE": "920119",
        "SECURITY_NAME_ABBR": "美德乐",
        "LISTING_DATE": "2026-01-30",
        "ISSUE_PRICE": 41.88,
        "CLOSE_PRICE": 109.50,
        "LD_CLOSE_CHANGE": 161.46,
        "TURNOVERRATE": 82.67,
    },
    {
        "SECURITY_CODE": "920166",
        "SECURITY_NAME_ABBR": "海圣医疗",
        "LISTING_DATE": "2026-02-12",
        "ISSUE_PRICE": 12.64,
        "CLOSE_PRICE": 34.49,
        "LD_CLOSE_CHANGE": 172.86,
        "TURNOVERRATE": 88.15,
    },
]

IPO_FIXTURES = {
    "920180": {
        "SECURITY_CODE": "920180",
        "SECURITY_NAME_ABBR": "爱得科技",
        "APPLY_DATE": "2026-02-02",
        "LISTING_DATE": "2026-02-10",
        "PRICE_WAY": "直接定价",
        "TOTAL_ISSUE_NUM": 1750.0,
        "TOP_APPLY_MARKETCAP": 1180.0,
        "ONLINE_VA_NUM": 286000.0,
        "ONLINE_ISSUE_LWR": 0.0475,
        "ISSUE_PRICE": 7.67,
        "AFTER_ISSUE_PE": 15.2,
        "INDUSTRY_PE_NEW": 29.8,
        "SW_INDUSTRY": "医疗器械",
        "MAIN_BUSINESS": "公司主要从事骨科耗材相关医疗器械的研发、生产与销售。",
    },
    "920119": {
        "SECURITY_CODE": "920119",
        "SECURITY_NAME_ABBR": "美德乐",
        "APPLY_DATE": "2026-01-20",
        "LISTING_DATE": "2026-01-30",
        "PRICE_WAY": "直接定价",
        "TOTAL_ISSUE_NUM": 1325.0,
        "TOP_APPLY_MARKETCAP": 1896.0,
        "ONLINE_VA_NUM": 342000.0,
        "ONLINE_ISSUE_LWR": 0.0386,
        "ISSUE_PRICE": 41.88,
        "AFTER_ISSUE_PE": 16.4,
        "INDUSTRY_PE_NEW": 31.6,
        "SW_INDUSTRY": "仪器仪表",
        "MAIN_BUSINESS": "公司是一家专业从事智能制造装备研发、设计、制造和销售业务的国家级高新技术企业。",
    },
}

PDF_EXTRACTION_FIXTURES = {
    "920180": {
        "old_shares_result": pdf_parser.OldSharesExtractionResult(
            value_wan_shares=0.0,
            source_file_type="上市公告书",
            source_rule="listing_table",
            source_anchor="本次发行前后的股本结构变动情况",
            raw_snippet="无限售流通股小计 0",
            confidence=0.99,
            unit="股",
            pre_unrestricted_wan_shares=0.0,
        ),
        "comparable_codes": ["002901.SZ", "300326.SZ", "688161.SH", "688085.SH", "688236.SH"],
        "business_desc": "公司主要从事骨科耗材及创面修复产品相关医疗器械的研发、生产与销售。",
    },
    "920119": {
        "old_shares_result": pdf_parser.OldSharesExtractionResult(
            value_wan_shares=0.0,
            source_file_type="上市公告书",
            source_rule="listing_table",
            source_anchor="本次发行前后的股本结构变动情况",
            raw_snippet="无限售流通股小计 0",
            confidence=0.99,
            unit="股",
            pre_unrestricted_wan_shares=0.0,
        ),
        "comparable_codes": ["301029.SZ", "688097.SH", "300450.SZ", "301662.SZ", "300173.SZ"],
        "business_desc": "公司是一家专业从事智能制造装备研发、设计、制造和销售业务的国家级高新技术企业。",
    },
}

FAKE_COMPARABLE_ITEMS = {
    "301029.SZ": {"name": "怡合达", "close": 22.41, "pe_ttm": 31.5, "pb_lf": 3.8, "mkt_cap": 145.2},
    "688097.SH": {"name": "博众精工", "close": 29.18, "pe_ttm": 36.2, "pb_lf": 4.1, "mkt_cap": 208.4},
    "300450.SZ": {"name": "先导智能", "close": 21.74, "pe_ttm": 24.8, "pb_lf": 2.9, "mkt_cap": 340.7},
    "301662.SZ": {"name": "宏工科技", "close": 38.62, "pe_ttm": 42.3, "pb_lf": 5.6, "mkt_cap": 96.5},
    "300173.SZ": {"name": "福能东方", "close": 8.46, "pe_ttm": 29.7, "pb_lf": 2.1, "mkt_cap": 61.3},
    "002901.SZ": {"name": "大博医疗", "close": 34.25, "pe_ttm": 27.4, "pb_lf": 3.2, "mkt_cap": 142.8},
    "300326.SZ": {"name": "凯利泰", "close": 11.38, "pe_ttm": 22.1, "pb_lf": 1.9, "mkt_cap": 81.6},
    "688161.SH": {"name": "威高骨科", "close": 25.74, "pe_ttm": 33.8, "pb_lf": 2.6, "mkt_cap": 103.9},
    "688085.SH": {"name": "三友医疗", "close": 19.06, "pe_ttm": 30.5, "pb_lf": 2.3, "mkt_cap": 49.2},
    "688236.SH": {"name": "春立医疗", "close": 18.54, "pe_ttm": 26.7, "pb_lf": 2.0, "mkt_cap": 71.4},
    "920180.BJ": {"name": "爱得科技", "close": 21.23, "pe_ttm": 19.6, "pb_lf": 2.8, "mkt_cap": 36.1},
}


@dataclass(frozen=True)
class RegressionCase:
    code: str
    expected_old_shares: float | None = None
    expected_old_file_type: str | None = None
    expected_old_desc: str | None = None
    expected_comparable_codes: list[str] | None = None
    business_must_contain: tuple[str, ...] = ()
    report_must_contain: tuple[str, ...] = ()


REGRESSION_CASES = (
    RegressionCase(
        code="920180",
        expected_old_shares=0.0,
        expected_old_file_type="上市公告书",
        expected_old_desc="0.00 万股（PDF 提取：上市公告书）",
        business_must_contain=("骨科耗材", "医疗器械", "创面修复产品"),
        report_must_contain=(
            "爱得科技",
            "首日流通老股",
            "大博医疗",
            "方法二样本范围",
            "方法二实际样本代码",
            "综合估值公式",
            "区间宽度",
            "东方财富（仅展示补充：定价方式、有效申购户数、主营业务）",
        ),
    ),
    RegressionCase(
        code="920119",
        expected_comparable_codes=["301029.SZ", "688097.SH", "300450.SZ", "301662.SZ", "300173.SZ"],
        business_must_contain=("智能制造装备", "研发、设计、制造和销售业务"),
        report_must_contain=(
            "美德乐",
            "怡合达",
            "宏工科技",
            "综合估值",
            "方法二样本范围",
            "方法二实际样本代码",
            "综合估值公式",
            "区间宽度",
        ),
    ),
)


def _build_fake_wind_summary(codes: list[str]) -> dict[str, Any]:
    return {
        "provider": "wind",
        "channel": "disabled",
        "requested_codes": list(codes),
        "returned_codes": list(codes),
        "fixed_cache_hits": [],
        "variable_cache_hits": [],
        "api_fetched_fixed": [],
        "api_fetched_variable": [],
        "stale_variable_used": [],
        "skipped_due_quota": [],
        "skipped_unsupported": [],
        "api_calls": 0,
        "quota_limit": 20,
        "quota_used_today": 0,
        "quota_remaining": 20,
        "local_computed_codes": [],
        "eastmoney_api_calls": 0,
        "eastmoney_fetched": [],
        "eastmoney_cache_hits": list(codes),
        "eastmoney_fallback_used": [],
        "cross_validated_codes": [],
        "cross_validation_warnings": [],
        "reason": "Wind 当前处于禁用状态。",
    }


def _fake_fetch_ipo_info(code: str) -> dict[str, Any]:
    try:
        return dict(IPO_FIXTURES[code])
    except KeyError as exc:
        raise data_fetcher.DataFetcherError(f"未配置回归样本 {code} 的 IPO 信息") from exc


def _fake_fetch_recent_ipos(months: int = 3, page_size: int = 50) -> list[dict[str, Any]]:
    _ = (months, page_size)
    return [dict(item) for item in RECENT_IPO_FIXTURES]


def _fake_prepare_ipo_data(
    code: str,
    months: int,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ = (months, params)
    return {
        "ipo_info": _fake_fetch_ipo_info(code),
        "recent_ipos": _fake_fetch_recent_ipos(),
        "summary": {
            "provider": "tushare",
            "target_source": "tushare+eastmoney",
            "recent_source": "tushare",
            "api_calls": 0,
            "new_share_api_calls": 0,
            "stock_basic_api_calls": 0,
            "daily_api_calls": 0,
            "daily_basic_api_calls": 0,
            "recent_requested_codes": [item["SECURITY_CODE"] for item in RECENT_IPO_FIXTURES],
            "recent_returned_codes": [item["SECURITY_CODE"] for item in RECENT_IPO_FIXTURES],
            "recent_sample_count": len(RECENT_IPO_FIXTURES),
            "eastmoney_supplement_used": True,
            "eastmoney_recent_fallback_used": False,
            "target_fallback_used": False,
            "supplemented_fields": ["ONLINE_VA_NUM", "PRICE_WAY", "MAIN_BUSINESS"],
            "industry_pe_source": "tushare_sw_daily",
            "industry_pe_index_code": "801153.SI",
            "industry_pe_index_name": "医疗器械",
            "industry_pe_trade_date": "20260210",
            "top_apply_marketcap_source": "tushare_new_share",
            "online_issue_lwr_source": "tushare_new_share",
            "reason": "",
        },
    }


def _fake_get_comparable_valuations(
    codes: list[str],
    channel: str = "disabled",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ = (channel, params)
    items: list[dict[str, Any]] = []
    for index, code in enumerate(codes):
        seed = FAKE_COMPARABLE_ITEMS.get(code, {})
        items.append(
            {
                "code": code,
                "name": seed.get("name", code),
                "close": seed.get("close", 10.0 + index),
                "pe_ttm": seed.get("pe_ttm", 20.0 + index * 1.5),
                "pb_lf": seed.get("pb_lf", 2.0 + index * 0.2),
                "mkt_cap": seed.get("mkt_cap", 50.0 + index * 10.0),
                "trade_date": "2026-04-16",
                "source": "eastmoney",
                "close_source": "eastmoney",
                "pe_source": "eastmoney",
                "pb_source": "eastmoney",
                "mkt_cap_source": "eastmoney",
                "data_sources": ["eastmoney"],
                "cross_validation": {},
                "is_stale": False,
            }
        )
    summary = _build_fake_wind_summary(codes)
    summary["reason"] = ""
    return {"items": items, "summary": summary}


def _patched_generate_report(all_data: dict[str, Any], output_dir: str) -> str:
    _ = output_dir
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ipo = all_data.get("ipo_info", {})
    file_path = OUTPUT_DIR / f"{ipo.get('SECURITY_CODE', 'unknown')}_{ipo.get('SECURITY_NAME_ABBR', '未知')}_估值_回归.md"
    file_path.write_text(report_generator.build_report_markdown(all_data), encoding="utf-8-sig")
    return str(file_path.resolve())


def _extract_code_from_path(file_path: str | Path | None) -> str:
    if not file_path:
        return ""
    return str(Path(file_path).stem).strip()[:6]


def _fake_extract_old_shares_result(file_path: str | Path) -> pdf_parser.OldSharesExtractionResult | None:
    code = _extract_code_from_path(file_path)
    fixture = PDF_EXTRACTION_FIXTURES.get(code, {})
    return fixture.get("old_shares_result")


def _fake_extract_comparable_companies(file_path: str | Path) -> list[str]:
    code = _extract_code_from_path(file_path)
    fixture = PDF_EXTRACTION_FIXTURES.get(code, {})
    return list(fixture.get("comparable_codes") or [])


def _fake_extract_business_desc(file_path: str | Path) -> str:
    code = _extract_code_from_path(file_path)
    fixture = PDF_EXTRACTION_FIXTURES.get(code, {})
    return str(fixture.get("business_desc") or "")


def _assert_contains(text: str, snippets: tuple[str, ...], failures: list[str], label: str) -> None:
    for snippet in snippets:
        if snippet not in text:
            failures.append(f"{label}: missing snippet {snippet}")


def _assert_not_contains(text: str, snippets: tuple[str, ...], failures: list[str], label: str) -> None:
    for snippet in snippets:
        if snippet in text:
            failures.append(f"{label}: unexpected snippet {snippet}")


def _assert_missing_online_va_num_placeholder(failures: list[str]) -> None:
    markdown = report_generator.build_report_markdown(
        {
            "analysis_date": "2026-04-18",
            "ipo_info": {
                "SECURITY_CODE": "920000",
                "SECURITY_NAME_ABBR": "占位样本",
                "ISSUE_PRICE": 10.0,
                "AFTER_ISSUE_PE": 15.0,
                "INDUSTRY_PE_NEW": 20.0,
                "ONLINE_VA_NUM": None,
                "ONLINE_ISSUE_LWR": None,
                "APPLY_DATE": "2026-04-18",
                "LISTING_DATE": "2026-04-25",
                "PRICE_WAY": None,
                "TOTAL_ISSUE_NUM": 1000.0,
                "TOP_APPLY_MARKETCAP": None,
            },
            "industry": {"display_name": "通用设备"},
            "method1": {"available": False, "reason": "test"},
            "method2": {"available": False, "reason": "test"},
            "final": {
                "available": False,
                "reason": "test",
                "target_price": None,
                "range_low": None,
                "range_high": None,
            },
            "params": {
                "price_range_width": 0.15,
                "ipo_data_source": "eastmoney",
                "comparable_data_source": "wind",
            },
            "notes": [],
            "recent_ipos": [],
            "comparable_data": [],
            "float_shares": 500.0,
            "old_shares_desc": "无",
            "company_description": "",
            "final_change_pct": None,
            "range_change_low": None,
            "range_change_high": None,
        }
    )
    for snippet, label in (
        ("| 定价方式 | 未取到数据 |", "PRICE_WAY"),
        ("## 二、申购资金配售预测", "SUBSCRIPTION_SECTION"),
        ("| 顶格申购金额 | - |", "TOP_APPLY_MARKETCAP"),
        ("| 模型状态 | 历史有效申购样本不足 3 个 |", "SUBSCRIPTION_STATUS"),
        ("## 三、公司概况\n\n未取到数据", "MAIN_BUSINESS"),
    ):
        if snippet not in markdown:
            failures.append(f"report placeholder: missing {label} empty-state text")
    subscription_section = markdown.split("## 二、申购资金配售预测", 1)[1].split("## 三、公司概况", 1)[0]
    if "来源/口径" in subscription_section:
        failures.append("report placeholder: subscription prediction section should hide source column")


def _assert_recent_table_time_window(failures: list[str]) -> None:
    base_payload = {
        "analysis_date": "2026-04-27",
        "ipo_info": {
            "SECURITY_CODE": "920000",
            "SECURITY_NAME_ABBR": "占位样本",
            "ISSUE_PRICE": 10.0,
            "AFTER_ISSUE_PE": 15.0,
            "INDUSTRY_PE_NEW": 20.0,
            "APPLY_DATE": "2026-04-18",
            "LISTING_DATE": "2026-04-25",
            "PRICE_WAY": "直接定价",
            "TOTAL_ISSUE_NUM": 1000.0,
        },
        "industry": {"display_name": "通用设备"},
        "method1": {"available": False, "reason": "test"},
        "method2": {"available": False, "reason": "test"},
        "final": {
            "available": False,
            "reason": "test",
            "target_price": None,
            "range_low": None,
            "range_high": None,
        },
        "params": {
            "price_range_width": 0.15,
            "recent_days": 90,
            "ipo_data_source": "eastmoney",
            "comparable_data_source": "wind",
        },
        "notes": [],
        "recent_ipos": [
            {
                "SECURITY_CODE": "920001",
                "SECURITY_NAME_ABBR": "过旧样本",
                "LISTING_DATE": "2026-01-26",
                "ISSUE_PRICE": 10.0,
                "CLOSE_PRICE": 12.0,
                "LD_CLOSE_CHANGE": 20.0,
                "industry_primary": "高端装备",
            },
            {
                "SECURITY_CODE": "920002",
                "SECURITY_NAME_ABBR": "边界样本",
                "LISTING_DATE": "2026-01-27",
                "ISSUE_PRICE": 10.0,
                "CLOSE_PRICE": 13.0,
                "LD_CLOSE_CHANGE": 30.0,
                "industry_primary": "信息技术",
            },
            {
                "SECURITY_CODE": "920003",
                "SECURITY_NAME_ABBR": "窗口样本",
                "LISTING_DATE": "2026-04-20",
                "ISSUE_PRICE": 10.0,
                "CLOSE_PRICE": 14.0,
                "LD_CLOSE_CHANGE": 40.0,
                "industry_primary": "医药生物",
            },
            {
                "SECURITY_CODE": "920004",
                "SECURITY_NAME_ABBR": "未来样本",
                "LISTING_DATE": "2026-04-28",
                "ISSUE_PRICE": 10.0,
                "CLOSE_PRICE": 15.0,
                "LD_CLOSE_CHANGE": 50.0,
                "industry_primary": "消费服务",
            },
        ],
        "comparable_data": [],
        "float_shares": 500.0,
        "old_shares_desc": "无",
        "company_description": "",
        "final_change_pct": None,
        "range_change_low": None,
        "range_change_high": None,
    }
    markdown = report_generator.build_report_markdown(base_payload)
    _assert_contains(markdown, ("边界样本", "窗口样本"), failures, "recent table window")
    _assert_not_contains(markdown, ("过旧样本", "未来样本"), failures, "recent table window")


def _assert_report_overview_text(failures: list[str]) -> None:
    overview = report_generator.build_report_overview_text(
        {
            "analysis_date": "2026-04-18",
            "ipo_info": {
                "SECURITY_CODE": "920000",
                "SECURITY_NAME_ABBR": "占位样本",
                "ISSUE_PRICE": 10.0,
                "AFTER_ISSUE_PE": 15.0,
                "INDUSTRY_PE_NEW": 20.0,
                "APPLY_DATE": "2026-04-18",
                "LISTING_DATE": "2026-04-25",
                "TOP_APPLY_MARKETCAP": 1234.5,
                "ONLINE_ISSUE_NUM": 1000000.0,
                "ONLINE_VA_SHARES": 30000000.0,
            },
            "industry": {"display_name": "通用设备"},
            "method1": {"available": False, "reason": "test"},
            "method2": {"available": False, "reason": "test"},
            "final": {
                "available": True,
                "target_price": 13.4,
                "range_low": 12.3,
                "range_high": 14.5,
            },
            "params": {
                "price_range_width": 0.10,
                "recent_days": 90,
                "ipo_data_source": "eastmoney",
                "comparable_data_source": "wind",
                "subscription_prediction_lot_threshold_max_lots": 6,
            },
            "notes": [],
            "recent_ipos": [],
            "comparable_data": [],
            "float_shares": 500.0,
            "old_shares_desc": "无",
            "company_description": "",
            "final_change_pct": None,
            "range_change_low": None,
            "range_change_high": None,
            "listing_pdf_found": False,
        }
    )
    expected = (
        "代码 920000\n"
        "名称 占位样本\n"
        "申购日期 2026-04-18\n"
        "市盈率 15.00\n"
        "发行价 10.00\n"
        "最大申购上限（万元） 1234.50\n"
        "1+0门槛（万元） 3.00\n"
        "1+1门槛（万元） 6.00\n"
        "2+1门槛（万元） 6.00\n"
        "3+1门槛（万元） 9.00\n"
        "4+1门槛（万元） 12.00\n"
        "5+1门槛（万元） 15.00\n"
        "正股抢时间 否\n"
        "碎股抢时间 1+1以下可能\n"
        "上市日期\n"
        "估价区间（元） 12.30 - 14.50\n"
    )
    if overview != expected:
        failures.append(f"overview text mismatch: {overview!r}")

    protected_overview = report_generator.build_report_overview_text(
        {
            "analysis_date": "2026-04-18",
            "ipo_info": {
                "SECURITY_CODE": "920001",
                "SECURITY_NAME_ABBR": "保护样本",
                "ISSUE_PRICE": 10.0,
                "AFTER_ISSUE_PE": 15.0,
                "INDUSTRY_PE_NEW": 20.0,
                "APPLY_DATE": "2026-04-18",
                "LISTING_DATE": "2026-04-25",
                "TOP_APPLY_MARKETCAP": 80.0,
                "ONLINE_ISSUE_NUM": 1000000.0,
                "ONLINE_VA_SHARES": 30000000.0,
            },
            "industry": {"display_name": "通用设备"},
            "method1": {"available": False, "reason": "test"},
            "method2": {"available": False, "reason": "test"},
            "final": {"available": True, "target_price": 13.4, "range_low": 12.3, "range_high": 14.5},
            "params": {
                "price_range_width": 0.10,
                "recent_days": 90,
                "ipo_data_source": "eastmoney",
                "comparable_data_source": "wind",
                "subscription_prediction_lot_threshold_max_lots": 6,
            },
            "notes": [],
            "recent_ipos": [],
            "comparable_data": [],
            "float_shares": 500.0,
            "old_shares_desc": "无",
            "company_description": "",
            "final_change_pct": None,
            "range_change_low": None,
            "range_change_high": None,
            "listing_pdf_found": False,
        }
    )
    _assert_contains(
        protected_overview,
        ("1+0门槛（万元） 3.00", "1+1门槛（万元） 6.00", "5+1门槛（万元） 15.00"),
        failures,
        "protected overview",
    )
    _assert_not_contains(
        protected_overview,
        ("正股建议申购（万元）", "2+0门槛（万元） 6.00", "6+0门槛（万元） 18.00"),
        failures,
        "protected overview",
    )
    _assert_contains(protected_overview, ("正股抢时间 可能",), failures, "protected overview")

    estimated_overview = report_generator.build_report_overview_text(
        {
            "analysis_date": "2026-06-18",
            "ipo_info": {
                "SECURITY_CODE": "920222",
                "SECURITY_NAME_ABBR": "益坤电气",
                "ISSUE_PRICE": 10.09,
                "AFTER_ISSUE_PE": 14.98,
                "INDUSTRY_PE_NEW": 27.80,
                "APPLY_DATE": "2026-06-22",
                "TOP_APPLY_MARKETCAP": 583.3029,
                "ONLINE_ISSUE_NUM": 1156.2390 * 10000,
            },
            "industry": {"display_name": "电气机械和器材制造业"},
            "method1": {"available": False, "reason": "test"},
            "method2": {"available": False, "reason": "test"},
            "final": {"available": True, "target_price": 23.71, "range_low": 21.34, "range_high": 26.08},
            "params": {
                "price_range_width": 0.10,
                "recent_days": 90,
                "ipo_data_source": "tushare",
                "comparable_data_source": "wind",
            },
            "notes": [],
            "recent_ipos": [
                {
                    "SECURITY_CODE": "920069",
                    "ISSUE_PRICE": 18.38,
                    "ONLINE_ISSUE_NUM": 952.0 * 10000,
                    "ONLINE_ISSUE_LWR": 0.03,
                    "TOP_APPLY_MARKETCAP": 47.60 * 18.38,
                    "APPLY_DATE": "2026-04-20",
                    "ISSUE_RESULT_DATE": "2026-04-23",
                },
                {
                    "SECURITY_CODE": "920126",
                    "ISSUE_PRICE": 7.79,
                    "ONLINE_ISSUE_NUM": 4187.0 * 10000,
                    "ONLINE_ISSUE_LWR": 0.03,
                    "TOP_APPLY_MARKETCAP": 209.34 * 7.79,
                    "APPLY_DATE": "2026-06-03",
                    "ISSUE_RESULT_DATE": "2026-06-06",
                },
                {
                    "SECURITY_CODE": "920083",
                    "ISSUE_PRICE": 9.65,
                    "ONLINE_ISSUE_NUM": 2009.0 * 10000,
                    "ONLINE_ISSUE_LWR": 0.02,
                    "TOP_APPLY_MARKETCAP": 100.44 * 9.65,
                    "APPLY_DATE": "2026-06-01",
                    "ISSUE_RESULT_DATE": "2026-06-04",
                },
                {
                    "SECURITY_CODE": "920206",
                    "ISSUE_PRICE": 30.28,
                    "ONLINE_ISSUE_NUM": 718.0 * 10000,
                    "ONLINE_ISSUE_LWR": 0.02,
                    "TOP_APPLY_MARKETCAP": 35.88 * 30.28,
                    "APPLY_DATE": "2026-05-27",
                    "ISSUE_RESULT_DATE": "2026-05-30",
                },
            ],
            "comparable_data": [],
            "float_shares": 1284.71,
            "old_shares_desc": "无",
            "company_description": "",
            "final_change_pct": None,
            "range_change_low": None,
            "range_change_high": None,
            "listing_pdf_found": False,
        }
    )
    _assert_not_contains(
        estimated_overview,
        ("正股测算门槛（万元）\n", "正股建议申购（万元）\n"),
        failures,
        "estimated overview",
    )
    _assert_contains(
        estimated_overview,
        ("代码 920222", "1+0门槛（万元）", "正股抢时间 否", "碎股抢时间 1+1以下可能"),
        failures,
        "estimated overview",
    )


def _assert_note_builder_focus_scope(params: dict[str, Any], failures: list[str]) -> None:
    note_params = dict(params)
    note_params["float_size_threshold"] = 2000
    note_params["pe_low_threshold"] = 0.3
    note_params["pe_high_threshold"] = 0.6
    noisy_notes = note_builder.generate_notes(
        {
            "ipo_info": {
                "AFTER_ISSUE_PE": 20.0,
                "INDUSTRY_PE_NEW": 10.0,
            },
            "float_shares": 1000.0,
            "industry": {"primary": "未分类", "secondary": "未分类"},
            "method1": {"available": False},
            "method2": {
                "available": True,
                "sample_scope": "全市场",
                "base_stat_label": "时间衰减中位数（半衰期 20 天）",
                "trend_note": "这条说明不应再出现在关注提示里。",
            },
            "old_shares_desc": "待确认（当前按 0 万股计）",
            "old_shares_meta": {
                "pending_reason": "未找到上市公告书，且未找到可用招股文件",
            },
            "comparable_codes": ["300001.SZ"],
            "comparable_summary": {
                "provider": "tushare",
                "api_calls": 3,
                "eastmoney_fallback_used": ["300001.SZ"],
                "cross_validated_codes": ["300001.SZ"],
                "reason": "这条技术说明不应再出现在关注提示里。",
            },
            "ipo_data_summary": {
                "provider": "tushare",
                "recent_sample_count": 6,
                "industry_pe_source": "tushare_sw_daily",
                "supplemented_fields": ["PRICE_WAY", "ONLINE_VA_NUM", "MAIN_BUSINESS"],
                "reason": "这条 IPO 说明不应再出现在关注提示里。",
            },
        },
        note_params,
    )
    expected_notes = [
        "当前标的尚未完成行业映射，方法二已自动回退全市场样本。建议在 `策略参数.txt` 中补充 `stock_industry` 或行业映射。",
        "发行 PE 相对行业偏高，需要关注上市首日估值兑现压力。",
        "首日流通盘偏小，历史上这类新股更容易获得情绪溢价。",
        "首日流通老股数据待确认，原因：未找到上市公告书，且未找到可用招股文件。当前首日流通盘按仅新增发行量估算。",
    ]
    if noisy_notes != expected_notes:
        failures.append(f"note_builder focus scope mismatch: {noisy_notes}")

    low_pe_notes = note_builder.generate_notes(
        {
            "ipo_info": {
                "AFTER_ISSUE_PE": 1.9,
                "INDUSTRY_PE_NEW": 10.0,
            },
            "float_shares": 3000.0,
            "industry": {"primary": "高端装备", "secondary": "机械设备"},
            "old_shares_desc": "0.00 万股（PDF 提取：上市公告书）",
            "old_shares_meta": {},
        },
        note_params,
    )
    if low_pe_notes != ["发行 PE 显著低于行业 PE，定价具备一定折价优势。"]:
        failures.append(f"note_builder low PE mismatch: {low_pe_notes}")


def _resolve_case_inputs(code: str, params: dict[str, Any]) -> dict[str, Any]:
    listing_pdf = bse_ipo_valuation._find_pdf(PDF_DIR, code, "上市公告书")
    old_shares_pdf = bse_ipo_valuation._pick_prospectus_pdf(PDF_DIR, code, "old_shares")
    comparable_pdf = bse_ipo_valuation._pick_prospectus_pdf(PDF_DIR, code, "comparables")
    business_pdf = bse_ipo_valuation._pick_prospectus_pdf(PDF_DIR, code, "business")
    old_shares, old_desc, old_meta = bse_ipo_valuation._resolve_old_shares(params, listing_pdf, old_shares_pdf)
    comparable_codes = bse_ipo_valuation._load_comparable_codes(params, comparable_pdf)
    business_desc = pdf_parser.extract_business_desc(business_pdf) if business_pdf else ""
    return {
        "listing_pdf": listing_pdf,
        "old_shares_pdf": old_shares_pdf,
        "comparable_pdf": comparable_pdf,
        "business_pdf": business_pdf,
        "old_shares": old_shares,
        "old_desc": old_desc,
        "old_meta": old_meta,
        "comparable_codes": comparable_codes,
        "business_desc": business_desc,
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    params = config_loader.load_params(ROOT_DIR / "策略参数.txt")
    failures: list[str] = []
    _assert_missing_online_va_num_placeholder(failures)
    _assert_recent_table_time_window(failures)
    _assert_report_overview_text(failures)
    _assert_note_builder_focus_scope(params, failures)

    for case in REGRESSION_CASES:
        resolved = _resolve_case_inputs(case.code, params)

        if case.expected_old_shares is not None and abs(resolved["old_shares"] - case.expected_old_shares) > 1e-6:
            failures.append(
                f"{case.code}: expected old shares {case.expected_old_shares}, got {resolved['old_shares']}"
            )
        if case.expected_old_file_type and (resolved["old_meta"] or {}).get("source_file_type") != case.expected_old_file_type:
            failures.append(
                f"{case.code}: expected old share source {case.expected_old_file_type}, got {(resolved['old_meta'] or {}).get('source_file_type')}"
            )
        if case.expected_old_desc and resolved["old_desc"] != case.expected_old_desc:
            failures.append(
                f"{case.code}: expected old share desc {case.expected_old_desc}, got {resolved['old_desc']}"
            )
        if case.expected_comparable_codes is not None and resolved["comparable_codes"] != case.expected_comparable_codes:
            failures.append(
                f"{case.code}: expected comparable codes {case.expected_comparable_codes}, got {resolved['comparable_codes']}"
            )

        _assert_contains(resolved["business_desc"], case.business_must_contain, failures, f"{case.code} business_desc")

        report_path = Path(bse_ipo_valuation.run(case.code))
        report_text = report_path.read_text(encoding="utf-8-sig")
        _assert_contains(report_text, case.report_must_contain, failures, f"{case.code} report")
        _assert_not_contains(
            report_text,
            (
                "方法一当前直接使用东方财富可比快照",
                "当前方法二已实现“核心估值字段 Tushare 优先”；",
                "可比公司估值本次优先命中本地 Tushare 缓存，未新增 API 请求。",
                "走势模块说明：",
            ),
            failures,
            f"{case.code} report",
        )

        print(
            "OK {code}: old_shares={old_shares:.2f}, comparables={comparable_count}, report={report_path}".format(
                code=case.code,
                old_shares=resolved["old_shares"],
                comparable_count=len(resolved["comparable_codes"]),
                report_path=report_path,
            )
        )

    if failures:
        print("\nMain flow regression failed:")
        for item in failures:
            print(f"- {item}")
        return 1

    print(f"\nMain flow regression passed: {len(REGRESSION_CASES)} cases")
    return 0


_ORIGINAL_PREPARE_IPO_DATA = ipo_data_helper.prepare_ipo_data
_ORIGINAL_GET_COMPARABLE_VALUATIONS = comparable_data_helper.get_comparable_valuations
_ORIGINAL_GENERATE_REPORT = report_generator.generate_report
_ORIGINAL_EXTRACT_OLD_SHARES_RESULT = pdf_parser.extract_old_shares_result
_ORIGINAL_EXTRACT_COMPARABLE_COMPANIES = pdf_parser.extract_comparable_companies
_ORIGINAL_EXTRACT_BUSINESS_DESC = pdf_parser.extract_business_desc


if __name__ == "__main__":
    ipo_data_helper.prepare_ipo_data = _fake_prepare_ipo_data
    comparable_data_helper.get_comparable_valuations = _fake_get_comparable_valuations
    report_generator.generate_report = _patched_generate_report
    # Main-flow regression keeps PDF parsing out of the hot path; real PDF extraction is covered by golden tests.
    pdf_parser.extract_old_shares_result = _fake_extract_old_shares_result
    pdf_parser.extract_comparable_companies = _fake_extract_comparable_companies
    pdf_parser.extract_business_desc = _fake_extract_business_desc
    try:
        raise SystemExit(main())
    finally:
        ipo_data_helper.prepare_ipo_data = _ORIGINAL_PREPARE_IPO_DATA
        comparable_data_helper.get_comparable_valuations = _ORIGINAL_GET_COMPARABLE_VALUATIONS
        report_generator.generate_report = _ORIGINAL_GENERATE_REPORT
        pdf_parser.extract_old_shares_result = _ORIGINAL_EXTRACT_OLD_SHARES_RESULT
        pdf_parser.extract_comparable_companies = _ORIGINAL_EXTRACT_COMPARABLE_COMPANIES
        pdf_parser.extract_business_desc = _ORIGINAL_EXTRACT_BUSINESS_DESC
