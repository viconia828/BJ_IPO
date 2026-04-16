from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import bse_ipo_valuation
import config_loader
import data_fetcher
import pdf_parser
import report_generator
import wind_helper


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
            "方法一当前直接使用东方财富可比快照",
            "方法二样本范围",
            "方法二实际样本代码",
            "综合估值公式",
            "区间宽度",
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
        "channel": "disabled",
        "requested_codes": list(codes),
        "returned_codes": list(codes),
        "fixed_cache_hits": [],
        "variable_cache_hits": [],
        "api_fetched_fixed": [],
        "api_fetched_variable": [],
        "stale_variable_used": [],
        "skipped_due_quota": [],
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
    return {"items": items, "summary": _build_fake_wind_summary(codes)}


def _patched_generate_report(all_data: dict[str, Any], output_dir: str) -> str:
    _ = output_dir
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ipo = all_data.get("ipo_info", {})
    file_path = OUTPUT_DIR / f"{ipo.get('SECURITY_CODE', 'unknown')}_{ipo.get('SECURITY_NAME_ABBR', '未知')}_估值_回归.md"
    file_path.write_text(report_generator.build_report_markdown(all_data), encoding="utf-8-sig")
    return str(file_path.resolve())


def _assert_contains(text: str, snippets: tuple[str, ...], failures: list[str], label: str) -> None:
    for snippet in snippets:
        if snippet not in text:
            failures.append(f"{label}: missing snippet {snippet}")


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


_ORIGINAL_FETCH_IPO_INFO = data_fetcher.fetch_ipo_info
_ORIGINAL_FETCH_RECENT_IPOS = data_fetcher.fetch_recent_ipos
_ORIGINAL_GET_COMPARABLE_VALUATIONS = wind_helper.get_comparable_valuations
_ORIGINAL_GENERATE_REPORT = report_generator.generate_report


if __name__ == "__main__":
    data_fetcher.fetch_ipo_info = _fake_fetch_ipo_info
    data_fetcher.fetch_recent_ipos = _fake_fetch_recent_ipos
    wind_helper.get_comparable_valuations = _fake_get_comparable_valuations
    report_generator.generate_report = _patched_generate_report
    try:
        raise SystemExit(main())
    finally:
        data_fetcher.fetch_ipo_info = _ORIGINAL_FETCH_IPO_INFO
        data_fetcher.fetch_recent_ipos = _ORIGINAL_FETCH_RECENT_IPOS
        wind_helper.get_comparable_valuations = _ORIGINAL_GET_COMPARABLE_VALUATIONS
        report_generator.generate_report = _ORIGINAL_GENERATE_REPORT
