from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "code"
if str(CODE) not in sys.path:
    sys.path.insert(0, str(CODE))

import report_generator


def _payload() -> dict[str, object]:
    return {
        "analysis_date": "2026-07-12",
        "ipo_info": {
            "SECURITY_CODE": "920999",
            "SECURITY_NAME_ABBR": "双区间样本",
            "ISSUE_PRICE": 10.0,
            "AFTER_ISSUE_PE": 15.0,
            "INDUSTRY_PE_NEW": 30.0,
            "APPLY_DATE": "2026-07-01",
            "LISTING_DATE": "2026-07-15",
            "TOTAL_ISSUE_NUM": 1000.0,
        },
        "industry": {"display_name": "高端装备 / 机械设备"},
        "method1": {"available": True, "target_price": 18.0, "change_pct": 80.0},
        "method2": {"available": True, "target_price": 22.0, "change_pct": 120.0},
        "method3": {"available": False, "reason": "test"},
        "final": {
            "available": True,
            "target_price": 22.5,
            "range_low": 20.25,
            "range_high": 24.75,
            "pre_local_center_target_price": 20.0,
            "pre_local_center_change_pct": 100.0,
            "local_center_overlay_applied": True,
            "local_center_alpha": 0.50,
            "local_center_history_count": 20,
            "local_center_proxy_score": 8.0,
            "local_center_rolling_change_pct": 150.0,
            "weight_comparable": 0.2,
            "weight_industry_momentum": 0.8,
        },
        "params": {
            "price_range_width": 0.10,
            "recent_days": 60,
            "ipo_data_source": "tushare",
            "comparable_data_source": "tushare",
        },
        "notes": [],
        "recent_ipos": [],
        "comparable_data": [],
        "float_shares": 1000.0,
        "old_shares_desc": "无",
        "company_description": "测试",
        "final_change_pct": 125.0,
        "range_change_low": 102.5,
        "range_change_high": 147.5,
        "listing_pdf_found": True,
    }


def main() -> None:
    failures: list[str] = []
    payload = _payload()
    overview = report_generator.build_report_overview_text(payload)
    for expected in (
        "偏PE估值区间（元） 18.00 - 22.00",
        "偏情绪估值区间（元） 22.50 - 27.50",
    ):
        if expected not in overview:
            failures.append(f"overview missing: {expected}")

    markdown = report_generator.build_report_markdown(payload)
    for expected in (
        "| 偏PE估值区间 | 18.00 - 22.00 | 三方法原始中枢 |",
        "| 偏情绪估值区间 | 22.50 - 27.50 | 本地历史滚动中枢 |",
        "| 正式综合区间 | 20.25 - 24.75 | 102.50% ~ 147.50% |",
    ):
        if expected not in markdown:
            failures.append(f"report missing: {expected}")

    if failures:
        raise AssertionError("\n".join(failures))
    print("Dual valuation range report validation passed")


if __name__ == "__main__":
    main()
