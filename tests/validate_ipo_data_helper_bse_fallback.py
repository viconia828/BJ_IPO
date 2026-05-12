from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import bse_official_helper
import data_fetcher
import ipo_data_helper


_ORIGINAL_FETCH_IPO_INFO = data_fetcher.fetch_ipo_info
_ORIGINAL_FETCH_RECENT_IPOS_BY_DAYS = data_fetcher.fetch_recent_ipos_by_days
_ORIGINAL_BSE_CLIENT = bse_official_helper.BSEOfficialClient


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _fake_fetch_ipo_info(code: str) -> dict[str, Any]:
    raise data_fetcher.DataFetcherError(f"未查询到新股代码 {code} 的信息")


def _fake_fetch_recent_ipos_by_days(days: int) -> list[dict[str, Any]]:
    _ = days
    return [
        {
            "SECURITY_CODE": "920177",
            "SECURITY_NAME_ABBR": "恒道科技",
            "LISTING_DATE": "2026-04-16",
            "ISSUE_PRICE": 12.50,
            "AVERAGE_PRICE": 20.00,
            "INDUSTRY": "机械设备",
        }
    ]


class FakeBSEOfficialClient:
    def __init__(self, timeout: float = 20.0, status_callback=None) -> None:
        _ = (timeout, status_callback)

    def resolve_newshare_issue_by_post_listing_code(self, code: str) -> bse_official_helper.NewShareIssue:
        _assert_code = "920220"
        if code != _assert_code:
            raise AssertionError(f"unexpected code: {code}")
        return bse_official_helper.NewShareIssue(
            issue_id=319,
            post_listing_code="920220",
            pre_listing_code="874326",
            stock_name="朗信电气",
            company_name="朗信电气",
            listing_date="",
            issue_result_date="2026-05-18",
        )

    def get_newshare_issue_detail(self, issue_id: int) -> dict[str, Any]:
        if issue_id != 319:
            raise AssertionError(f"unexpected issue id: {issue_id}")
        return {
            "newShare": {
                "fxCode": "920220",
                "stockCode": "874326",
                "stockName": "朗信电气",
                "initialIssueAmount": 13241252,
                "issuePrice": 28.29,
                "peRatio": 14.99,
                "purchaseDate": "2026-05-13",
                "enterPremiumDate": "",
            }
        }

    def build_newshare_ipo_info_by_post_listing_code(self, code: str) -> dict[str, Any]:
        issue = self.resolve_newshare_issue_by_post_listing_code(code)
        detail = self.get_newshare_issue_detail(issue.issue_id)
        new_share = detail["newShare"]
        return {
            "SECURITY_CODE": issue.post_listing_code,
            "SECURITY_NAME_ABBR": issue.stock_name,
            "APPLY_DATE": new_share["purchaseDate"],
            "LISTING_DATE": "",
            "ISSUE_RESULT_DATE": issue.issue_result_date,
            "ISSUE_PRICE": new_share["issuePrice"],
            "AFTER_ISSUE_PE": new_share["peRatio"],
            "TOTAL_ISSUE_NUM": new_share["initialIssueAmount"] / 10000,
            "PRE_LISTING_CODE": issue.pre_listing_code,
            "source": "bse_newshare",
        }


def _run_bse_newshare_target_fallback_case(failures: list[str]) -> None:
    data_fetcher.fetch_ipo_info = _fake_fetch_ipo_info
    data_fetcher.fetch_recent_ipos_by_days = _fake_fetch_recent_ipos_by_days
    bse_official_helper.BSEOfficialClient = FakeBSEOfficialClient

    try:
        result = ipo_data_helper.prepare_ipo_data("920220", 3, params={"ipo_data_source": "eastmoney"})
    finally:
        data_fetcher.fetch_ipo_info = _ORIGINAL_FETCH_IPO_INFO
        data_fetcher.fetch_recent_ipos_by_days = _ORIGINAL_FETCH_RECENT_IPOS_BY_DAYS
        bse_official_helper.BSEOfficialClient = _ORIGINAL_BSE_CLIENT

    ipo_info = result.get("ipo_info") or {}
    summary = result.get("summary") or {}
    _assert(ipo_info.get("SECURITY_NAME_ABBR") == "朗信电气", "bse fallback: target name mismatch", failures)
    _assert(ipo_info.get("PRE_LISTING_CODE") == "874326", "bse fallback: pre-listing code mismatch", failures)
    _assert(abs(float(ipo_info.get("TOTAL_ISSUE_NUM")) - 1324.1252) < 0.0001, "bse fallback: issue amount unit mismatch", failures)
    _assert(ipo_info.get("ISSUE_PRICE") == 28.29, "bse fallback: issue price mismatch", failures)
    _assert(ipo_info.get("AFTER_ISSUE_PE") == 14.99, "bse fallback: issue PE mismatch", failures)
    _assert(ipo_info.get("APPLY_DATE") == "2026-05-13", "bse fallback: apply date mismatch", failures)
    _assert(summary.get("target_source") == "bse_newshare", "bse fallback: target source mismatch", failures)
    _assert(summary.get("target_fallback_used") is True, "bse fallback: summary fallback flag missing", failures)
    _assert(len(result.get("recent_ipos") or []) == 1, "bse fallback: recent IPOs should still be loaded", failures)
    print("OK bse fallback: Eastmoney target miss falls back to BSE new-share issue data")


def main() -> int:
    failures: list[str] = []
    _run_bse_newshare_target_fallback_case(failures)

    if failures:
        print("\nIPO data helper BSE fallback validation failed:")
        for item in failures:
            print(f"- {item}")
        return 1

    print("\nIPO data helper BSE fallback validation passed: 1 case")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
