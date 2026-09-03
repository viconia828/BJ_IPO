from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import bse_ipo_valuation
import config_loader
from industry_mapping import IndustryMapper
import pdf_parser
import report_source_helper


PROSPECTUS_TEXT = """
本次发行概况
定价方式 发行人和主承销商采用直接定价的方式确定发行价格
每股发行价格 9.65 元/股
发行后市盈率（倍） 14.93
本次公开发行股票数量 2,231.7369 万股
预计发行日期 2026 年 6 月 1 日
发行后总股本 8,926.9476 万股
上市公司行业分类 C制造业 C30非金属矿物制品业
网上申购数量上限为 118.00 万股
"""

ISSUE_ANNOUNCEMENT_TEXT = """
发行公告
发行方式 发行人和主承销商采用直接定价方式发行
发行价格为 9.65 元/股
发行后市盈率 14.93 倍
网上投资者有效申购总量大于网上发行数量时，不足 100 股的部分按规则配售。
本次公开发行股份数量 2,231.7369 万股
网上申购日 2026 年 6 月 1 日
战略配售数量(万股) 223.1737 网上发行数量(万股) 2,008.5632
网上每笔申购数量上限(万股) 118.00
行业平均静态市盈率为 21.30 倍
"""

ISSUE_RESULT_TEXT = """
发行结果公告
公告日期：2026 年 6 月 4 日
本次网上发行数量为 1,000,000 股。
网上投资者有效申购户数为 12,345 户，有效申购数量为 30,000,000 股，冻结资金总额为 30,000.00 万元。
网上发行最终中签率为 3.3333%，有效申购倍数为 30.00 倍。
申购数量为 3,000 股的投资者获配100股，同等申购数量按申购时间优先排序。
申购数量（股） 户数
5000 100
4900 200
3000 300
网上获配户数为 1,000 户
"""

GREENSHOE_ISSUE_ANNOUNCEMENT_TEXT = """
发行公告
超额配售启用前，网上发行数量为732.90万股；
超额配售启用后，网上发行数量为889.95万股。
"""

REBALANCED_ISSUE_RESULT_TEXT = """
发行结果公告
回拨机制启动前，网上初始发行数量为532.80万股。
网上、网下回拨机制启动后，网上最终发行数量为666.00万股。
网上获配股数为6,660,000股。
"""


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _assert_close(actual: Any, expected: float, message: str, failures: list[str]) -> None:
    try:
        current = float(actual)
    except (TypeError, ValueError):
        failures.append(f"{message}: got {actual!r}")
        return
    if abs(current - expected) > 1e-6:
        failures.append(f"{message}: expected {expected}, got {current}")


def _run_parser_case(failures: list[str]) -> dict[str, Any]:
    result = pdf_parser._extract_prospectus_issue_info_from_text(PROSPECTUS_TEXT)
    fields = result.get("fields") or {}
    _assert(fields.get("PRICE_WAY") == "直接定价", "parser: PRICE_WAY mismatch", failures)
    _assert(fields.get("INDUSTRY") == "非金属矿物制品业", "parser: INDUSTRY mismatch", failures)
    _assert(fields.get("INDUSTRY_CODE") == "C30", "parser: INDUSTRY_CODE mismatch", failures)
    _assert(fields.get("APPLY_DATE") == "2026-06-01", "parser: APPLY_DATE mismatch", failures)
    _assert_close(fields.get("ISSUE_PRICE"), 9.65, "parser: ISSUE_PRICE mismatch", failures)
    _assert_close(fields.get("AFTER_ISSUE_PE"), 14.93, "parser: AFTER_ISSUE_PE mismatch", failures)
    _assert_close(fields.get("TOTAL_ISSUE_NUM"), 2231.7369, "parser: TOTAL_ISSUE_NUM mismatch", failures)
    _assert_close(
        fields.get("TOTAL_SHARE_CAPITAL_AFTER_ISSUE"),
        8926.9476,
        "parser: TOTAL_SHARE_CAPITAL_AFTER_ISSUE mismatch",
        failures,
    )
    _assert_close(
        fields.get("TOP_APPLY_MARKETCAP"),
        118.0 * 9.65,
        "parser: TOP_APPLY_MARKETCAP mismatch",
        failures,
    )
    return result


def _run_industry_variant_cases(failures: list[str]) -> None:
    cases = [
        (
            "根据《国民经济行业分类》，公司所属行业为“电气机械和器材制造业(C38)”；",
            "电气机械和器材制造业",
            "C38",
        ),
        (
            "上市公司行业分类 C39计算机、通信和其他电子设备制造业 管理型行业分类 C398电子元件制造",
            "计算机、通信和其他电子设备制造业",
            "C39",
        ),
        (
            "上市公司行业分类 C制造业36汽车制造业367汽车零部件及配件制造",
            "汽车制造业",
            "C36",
        ),
    ]
    for text, expected_industry, expected_code in cases:
        result = pdf_parser._extract_prospectus_issue_info_from_text(text)
        fields = result.get("fields") or {}
        _assert(fields.get("INDUSTRY") == expected_industry, f"industry variant: {text}", failures)
        _assert(fields.get("INDUSTRY_CODE") == expected_code, f"industry code variant: {text}", failures)

    noisy = "执行事务合伙人珠海锐翔科技产业 E76珠海锐翔科技产业 投资者请关注。"
    fields = pdf_parser._extract_prospectus_issue_info_from_text(noisy).get("fields") or {}
    _assert("INDUSTRY" not in fields, "industry variant: noisy non-C code should be ignored", failures)


def _run_apply_case(result: dict[str, Any], failures: list[str]) -> None:
    ipo_info = {
        "SECURITY_CODE": "920083",
        "SECURITY_NAME_ABBR": "金戈新材",
        "ISSUE_PRICE": None,
        "TOTAL_ISSUE_NUM": None,
        "TOP_APPLY_MARKETCAP": None,
    }
    summary: dict[str, Any] = {"provider": "eastmoney"}
    applied = bse_ipo_valuation._apply_prospectus_issue_info(
        ipo_info,
        summary,
        result,
        Path("公告文件/920083_金戈新材_招股说明书.pdf"),
    )
    _assert("ISSUE_PRICE" in applied, "apply: ISSUE_PRICE not applied", failures)
    _assert("PRICE_WAY" in applied, "apply: PRICE_WAY not applied", failures)
    _assert(ipo_info.get("INDUSTRY_CODE") == "C30", "apply: industry code missing", failures)
    _assert(summary.get("prospectus_supplement_used") is True, "apply: summary flag missing", failures)

    source_text = report_source_helper.build_ipo_source_text(
        {
            "ipo_data_summary": summary,
            "params": {"ipo_data_source": "eastmoney"},
        }
    )
    _assert("招股说明书（字段补充：" in source_text, "source text: prospectus supplement missing", failures)


def _run_issue_announcement_fallback_case(failures: list[str]) -> None:
    result = pdf_parser._extract_issue_announcement_info_from_text(ISSUE_ANNOUNCEMENT_TEXT)
    fields = result.get("fields") or {}
    sources = result.get("field_sources") or {}
    _assert(fields.get("PRICE_WAY") == "直接定价", "issue announcement parser: PRICE_WAY mismatch", failures)
    _assert(fields.get("APPLY_DATE") == "2026-06-01", "issue announcement parser: APPLY_DATE mismatch", failures)
    _assert_close(fields.get("ISSUE_PRICE"), 9.65, "issue announcement parser: ISSUE_PRICE mismatch", failures)
    _assert_close(fields.get("AFTER_ISSUE_PE"), 14.93, "issue announcement parser: AFTER_ISSUE_PE mismatch", failures)
    _assert_close(fields.get("TOTAL_ISSUE_NUM"), 2231.7369, "issue announcement parser: TOTAL_ISSUE_NUM mismatch", failures)
    _assert_close(
        fields.get("ONLINE_ISSUE_NUM"),
        20085632.0,
        "issue announcement parser: ONLINE_ISSUE_NUM mismatch",
        failures,
    )
    _assert_close(fields.get("INDUSTRY_PE_NEW"), 21.30, "issue announcement parser: INDUSTRY_PE_NEW mismatch", failures)
    _assert_close(
        fields.get("TOP_APPLY_MARKETCAP"),
        118.0 * 9.65,
        "issue announcement parser: TOP_APPLY_MARKETCAP mismatch",
        failures,
    )
    _assert(
        str(sources.get("ISSUE_PRICE") or "").startswith("issue_announcement:"),
        "issue announcement parser: source prefix mismatch",
        failures,
    )

    ipo_info = {
        "SECURITY_CODE": "920083",
        "SECURITY_NAME_ABBR": "金戈新材",
        "ISSUE_PRICE": None,
        "AFTER_ISSUE_PE": None,
        "TOP_APPLY_MARKETCAP": None,
        "INDUSTRY_PE_NEW": None,
    }
    summary: dict[str, Any] = {"provider": "eastmoney"}
    applied = bse_ipo_valuation._apply_issue_announcement_info(
        ipo_info,
        summary,
        result,
        Path("公告文件/920083_金戈新材_发行公告.pdf"),
    )
    _assert("ISSUE_PRICE" in applied, "issue announcement apply: ISSUE_PRICE not applied", failures)
    _assert("TOP_APPLY_MARKETCAP" in applied, "issue announcement apply: TOP_APPLY_MARKETCAP not applied", failures)
    _assert(summary.get("issue_announcement_supplement_used") is True, "issue announcement apply: summary flag missing", failures)

    source_text = report_source_helper.build_ipo_source_text(
        {
            "ipo_data_summary": summary,
            "params": {"ipo_data_source": "eastmoney"},
        }
    )
    _assert("发行公告（字段补充：" in source_text, "source text: issue announcement supplement missing", failures)


def _run_issue_result_parser_case(failures: list[str]) -> None:
    result = pdf_parser._extract_issue_result_info_from_text(ISSUE_RESULT_TEXT)
    fields = result.get("fields") or {}
    sources = result.get("field_sources") or {}
    _assert(fields.get("ISSUE_RESULT_DATE") == "2026-06-04", "issue result parser: result date mismatch", failures)
    _assert_close(fields.get("ONLINE_ISSUE_NUM"), 1000000.0, "issue result parser: online issue shares mismatch", failures)
    _assert_close(fields.get("ONLINE_VA_NUM"), 12345.0, "issue result parser: valid accounts mismatch", failures)
    _assert_close(fields.get("ONLINE_ALLOCATED_ACCOUNTS"), 1000.0, "issue result parser: allocated accounts mismatch", failures)
    _assert_close(fields.get("ONLINE_VA_SHARES"), 30000000.0, "issue result parser: valid shares mismatch", failures)
    _assert_close(fields.get("FROZEN_FUNDS_YI"), 3.0, "issue result parser: frozen funds mismatch", failures)
    _assert_close(fields.get("ONLINE_ISSUE_LWR"), 3.3333, "issue result parser: lwr mismatch", failures)
    _assert_close(fields.get("ONLINE_ES_MULTIPLE"), 30.0, "issue result parser: multiple mismatch", failures)
    _assert_close(fields.get("FRACTIONAL_THRESHOLD_SHARES"), 3000.0, "issue result parser: fractional threshold mismatch", failures)
    _assert(fields.get("FRACTIONAL_TIME_PRIORITY_REQUIRED") is True, "issue result parser: time priority missing", failures)
    distribution = fields.get("SUBSCRIPTION_AMOUNT_DISTRIBUTION") or []
    _assert(len(distribution) == 3, "issue result parser: distribution rows mismatch", failures)
    _assert(
        str(sources.get("ONLINE_VA_SHARES") or "").startswith("issue_result:"),
        "issue result parser: source prefix mismatch",
        failures,
    )


def _run_online_issue_priority_cases(failures: list[str]) -> None:
    greenshoe = pdf_parser._extract_issue_announcement_info_from_text(GREENSHOE_ISSUE_ANNOUNCEMENT_TEXT)
    _assert_close(
        (greenshoe.get("fields") or {}).get("ONLINE_ISSUE_NUM"),
        8899500.0,
        "issue announcement parser: post-greenshoe online shares mismatch",
        failures,
    )

    rebalanced = pdf_parser._extract_issue_result_info_from_text(REBALANCED_ISSUE_RESULT_TEXT)
    _assert_close(
        (rebalanced.get("fields") or {}).get("ONLINE_ISSUE_NUM"),
        6660000.0,
        "issue result parser: final rebalanced online shares mismatch",
        failures,
    )


def _run_industry_mapping_case(failures: list[str]) -> None:
    mapper = IndustryMapper({"industry_mapping": {"矿物制品": "化工新材 / 非金属材料"}})
    industry = mapper.resolve_stock_industry("920083", {"INDUSTRY": "非金属矿物制品业"})
    _assert(industry.display_name == "化工新材 / 非金属材料", "industry mapping: substring alias mismatch", failures)
    auto_parts = mapper.resolve_stock_industry("920079", {"INDUSTRY": "汽车制造业", "INDUSTRY_CODE": "C36"})
    _assert(auto_parts.display_name == "高端装备 / 汽车零部件", "industry code mapping: C36 should map to auto parts", failures)
    _assert(auto_parts.source == "built_in_sample_map", "industry mapping: audited code mapping should take precedence", failures)
    known_auto_parts = mapper.resolve_stock_industry("920086", {"INDUSTRY": "汽车制造业", "INDUSTRY_CODE": "C36"})
    _assert(known_auto_parts.display_name == "高端装备 / 汽车零部件", "industry mapping: known C36 sample should stay auto parts", failures)
    langxin_auto_parts = mapper.resolve_stock_industry("920220", {})
    _assert(langxin_auto_parts.display_name == "高端装备 / 汽车零部件", "industry mapping: 朗信电气 should be auto parts", failures)
    yongli_auto_parts = mapper.resolve_stock_industry("920136", {})
    _assert(yongli_auto_parts.display_name == "高端装备 / 汽车零部件", "industry mapping: 永励精密 should be auto parts", failures)
    zhongkeyi_semiconductor = mapper.resolve_stock_industry("920186", {})
    _assert(zhongkeyi_semiconductor.display_name == "信息技术 / 半导体制造", "industry mapping: 中科仪 should be semiconductor", failures)
    oulun_appliance = mapper.resolve_stock_industry("920081", {})
    _assert(oulun_appliance.display_name == "消费服务 / 家用电器", "industry mapping: 欧伦电气 home appliances should stay separate from consumer electronics", failures)
    nongda_fertilizer = mapper.resolve_stock_industry("920159", {})
    _assert(nongda_fertilizer.display_name == "化工新材 / 化学制品", "industry mapping: 农大科技 fertilizer should be chemical products", failures)
    chuangda_packaging = mapper.resolve_stock_industry("920012", {})
    _assert(chuangda_packaging.display_name == "信息技术 / 半导体制造", "industry mapping: 创达新材 semiconductor packaging materials should remain semiconductor", failures)
    confirmed_industries = {
        "920072": "医药生物 / 医疗器械",
        "920076": "化工新材 / 非金属材料",
        "920083": "化工新材 / 非金属材料",
        "920096": "高端装备 / 电气设备",
        "920119": "高端装备 / 机械设备",
        "920125": "信息技术 / 半导体制造",
        "920126": "高端装备 / 机械设备",
        "920156": "高端装备 / 机械设备",
        "920161": "化工新材 / 橡胶和塑料制品业",
        "920178": "高端装备 / 机械设备",
        "920188": "化工新材 / 橡胶和塑料制品业",
        "920189": "信息技术 / 半导体制造",
        "920191": "化工新材 / 非金属材料",
        "920193": "化工新材 / 化学制品",
        "920200": "高端装备 / 机械设备",
        "920211": "高端装备 / 机械设备",
        "920218": "化工新材 / 橡胶和塑料制品业",
        "920222": "高端装备 / 电气设备",
        "920038": "化工新材 / 金属新材料",
        "920059": "高端装备 / 汽车零部件",
        "920065": "消费服务 / 商贸零售",
        "920071": "化工新材 / 金属新材料",
        "920079": "高端装备 / 汽车零部件",
        "920093": "高端装备 / 机械设备",
        "920107": "化工新材 / 化学制品",
        "920117": "高端装备 / 机械设备",
        "920138": "信息技术 / 半导体制造",
        "920165": "医药生物 / 生物制品",
        "920176": "医药生物 / 生物制品",
        "920201": "医药生物 / 医疗器械",
        "920238": "高端装备 / 金属制品业",
        "920258": "化工新材 / 化学制品",
        "920268": "医药生物 / 医疗器械",
        "920288": "消费服务 / 轻工制造",
        "920298": "高端装备 / 机械设备",
    }
    for code, expected in confirmed_industries.items():
        actual = mapper.resolve_stock_industry(code, {}).display_name
        _assert(actual == expected, f"industry mapping: {code} expected {expected}, got {actual}", failures)
    production_mapper = IndustryMapper(config_loader.load_params(ROOT_DIR / "策略参数.txt"))
    home_appliance = production_mapper.resolve_stock_industry("fixture", {"INDUSTRY": "家用电器"})
    _assert(
        home_appliance.display_name == "消费服务 / 家用电器",
        "industry mapping: the production taxonomy must keep home appliances separate from consumer electronics",
        failures,
    )
    known_override = mapper.resolve_stock_industry("920069", {"INDUSTRY": "专用设备制造业", "INDUSTRY_CODE": "C35"})
    _assert(
        known_override.display_name == "医药生物 / 医疗器械",
        "industry code mapping: built-in verified map should take precedence",
        failures,
    )
    baizhi = mapper.resolve_stock_industry("920201", {"INDUSTRY": "医药制造业", "INDUSTRY_CODE": "C27"})
    _assert(baizhi.display_name == "医药生物 / 医疗器械", "industry mapping: 920201 peer group mismatch", failures)
    _assert(baizhi.statutory_industry == "医药制造业", "industry mapping: legal industry should be retained", failures)
    _assert("植入耗材" in baizhi.business_tags, "industry mapping: 920201 business tags missing", failures)
    _assert(baizhi.confidence == 1.0, "industry mapping: audited mapping confidence mismatch", failures)
    broad_c27 = mapper.resolve_stock_industry("fixture", {"INDUSTRY": "医药制造业", "INDUSTRY_CODE": "C27"})
    _assert(broad_c27.display_name == "未分类", "industry mapping: broad C27 must not invent a peer group", failures)
    paper = production_mapper.resolve_stock_industry("fixture", {"INDUSTRY": "造纸和纸制品业", "INDUSTRY_CODE": "C22"})
    _assert(paper.display_name == "消费服务 / 轻工制造", "industry mapping: two-character 造纸 alias mismatch", failures)


def main() -> int:
    failures: list[str] = []
    result = _run_parser_case(failures)
    _run_industry_variant_cases(failures)
    _run_apply_case(result, failures)
    _run_issue_announcement_fallback_case(failures)
    _run_issue_result_parser_case(failures)
    _run_online_issue_priority_cases(failures)
    _run_industry_mapping_case(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("OK prospectus issue info validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
