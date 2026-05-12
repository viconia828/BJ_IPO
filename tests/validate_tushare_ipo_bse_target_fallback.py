from __future__ import annotations

from pathlib import Path
import os
import shutil
import sys
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import bse_official_helper
import data_fetcher
import tushare_ipo_helper


TEMP_ROOT = ROOT_DIR / "tests" / "_tmp" / "tushare_ipo_bse_target_fallback"
TOKEN_ENV = "TUSHARE_TEST_TOKEN_FOR_BSE_FALLBACK"

_ORIGINAL_FETCH_IPO_INFO = data_fetcher.fetch_ipo_info
_ORIGINAL_BSE_CLIENT = bse_official_helper.BSEOfficialClient
_ORIGINAL_GET_RECENT_NEW_SHARE_ROWS = tushare_ipo_helper._get_recent_new_share_rows
_ORIGINAL_GET_STOCK_PROFILE = tushare_ipo_helper._get_stock_profile
_ORIGINAL_GET_NEW_SHARE_ROW = tushare_ipo_helper._get_new_share_row
_ORIGINAL_GET_LISTING_DAY_SNAPSHOT = tushare_ipo_helper._get_listing_day_snapshot
_ORIGINAL_GET_INDUSTRY_PE_SNAPSHOT = tushare_ipo_helper._get_industry_pe_snapshot


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _make_params() -> dict[str, Any]:
    return {
        "tushare_token_env": TOKEN_ENV,
        "tushare_cache_root": str(TEMP_ROOT / "cache"),
        "tushare_daily_request_quota": 200,
        "tushare_request_pause_seconds": 0.0,
        "tushare_static_ttl_days": 3650,
        "tushare_dynamic_ttl_hours": 24,
        "tushare_recent_trade_days": 12,
    }


def _fake_fetch_ipo_info(code: str) -> dict[str, Any]:
    if code != "920220":
        raise AssertionError(f"unexpected Eastmoney target code: {code}")
    return {
        "SECURITY_CODE": "920220",
        "SECURITY_NAME_ABBR": "朗信电气",
        "ISSUE_PRICE": None,
        "AFTER_ISSUE_PE": None,
        "TOTAL_ISSUE_NUM": None,
        "APPLY_DATE": None,
        "MAIN_BUSINESS": "电机总成等热管理系统电驱动零部件产品的研发、生产与销售",
    }


def _fake_get_recent_new_share_rows(*args, **kwargs) -> list[dict[str, Any]]:
    _ = (args, kwargs)
    return [
        {
            "ts_code": "920177.BJ",
            "name": "恒道科技",
            "issue_date": "20260416",
            "ipo_date": "20260401",
            "amount": 1000,
            "price": 12.50,
            "pe": 12.00,
        }
    ]


def _fake_get_stock_profile(*args, **kwargs) -> dict[str, Any] | None:
    _ = (args, kwargs)
    return None


def _fake_get_new_share_row(*args, **kwargs) -> dict[str, Any] | None:
    _ = (args, kwargs)
    return None


def _fake_get_listing_day_snapshot(*args, **kwargs) -> dict[str, Any]:
    _ = (args, kwargs)
    return {
        "open": 18.00,
        "close": 20.00,
        "average_price": 19.00,
        "turnover_rate": 55.0,
    }


def _fake_get_industry_pe_snapshot(*args, **kwargs) -> None:
    _ = (args, kwargs)
    return None


class FakeBSEOfficialClient:
    def __init__(self, timeout: float = 20.0, status_callback=None) -> None:
        _ = (timeout, status_callback)

    def build_newshare_ipo_info_by_post_listing_code(self, code: str) -> dict[str, Any]:
        if code != "920220":
            raise AssertionError(f"unexpected BSE target code: {code}")
        return {
            "SECURITY_CODE": "920220",
            "SECURITY_NAME_ABBR": "朗信电气",
            "APPLY_DATE": "2026-05-13",
            "LISTING_DATE": "",
            "ISSUE_RESULT_DATE": "2026-05-18",
            "ISSUE_PRICE": 28.29,
            "AFTER_ISSUE_PE": 14.99,
            "TOTAL_ISSUE_NUM": 1324.1252,
            "PRE_LISTING_CODE": "874326",
            "source": "bse_newshare",
        }


def _run_tushare_target_bse_fallback_case(failures: list[str]) -> None:
    if TEMP_ROOT.exists():
        shutil.rmtree(TEMP_ROOT)
    os.environ[TOKEN_ENV] = "fake-token"
    data_fetcher.fetch_ipo_info = _fake_fetch_ipo_info
    bse_official_helper.BSEOfficialClient = FakeBSEOfficialClient
    tushare_ipo_helper._get_recent_new_share_rows = _fake_get_recent_new_share_rows
    tushare_ipo_helper._get_stock_profile = _fake_get_stock_profile
    tushare_ipo_helper._get_new_share_row = _fake_get_new_share_row
    tushare_ipo_helper._get_listing_day_snapshot = _fake_get_listing_day_snapshot
    tushare_ipo_helper._get_industry_pe_snapshot = _fake_get_industry_pe_snapshot

    try:
        result = tushare_ipo_helper.prepare_ipo_data("920220", 3, params=_make_params())
    finally:
        data_fetcher.fetch_ipo_info = _ORIGINAL_FETCH_IPO_INFO
        bse_official_helper.BSEOfficialClient = _ORIGINAL_BSE_CLIENT
        tushare_ipo_helper._get_recent_new_share_rows = _ORIGINAL_GET_RECENT_NEW_SHARE_ROWS
        tushare_ipo_helper._get_stock_profile = _ORIGINAL_GET_STOCK_PROFILE
        tushare_ipo_helper._get_new_share_row = _ORIGINAL_GET_NEW_SHARE_ROW
        tushare_ipo_helper._get_listing_day_snapshot = _ORIGINAL_GET_LISTING_DAY_SNAPSHOT
        tushare_ipo_helper._get_industry_pe_snapshot = _ORIGINAL_GET_INDUSTRY_PE_SNAPSHOT
        os.environ.pop(TOKEN_ENV, None)

    ipo_info = result.get("ipo_info") or {}
    summary = result.get("summary") or {}
    _assert(ipo_info.get("SECURITY_NAME_ABBR") == "朗信电气", "tushare bse fallback: name mismatch", failures)
    _assert(ipo_info.get("MAIN_BUSINESS"), "tushare bse fallback: should keep Eastmoney business supplement", failures)
    _assert(ipo_info.get("ISSUE_PRICE") == 28.29, "tushare bse fallback: issue price mismatch", failures)
    _assert(ipo_info.get("AFTER_ISSUE_PE") == 14.99, "tushare bse fallback: issue PE mismatch", failures)
    _assert(abs(float(ipo_info.get("TOTAL_ISSUE_NUM")) - 1324.1252) < 0.0001, "tushare bse fallback: issue amount mismatch", failures)
    _assert(summary.get("target_source") == "eastmoney+bse_newshare", "tushare bse fallback: target source mismatch", failures)
    _assert(summary.get("target_fallback_used") is True, "tushare bse fallback: fallback flag missing", failures)
    _assert(len(result.get("recent_ipos") or []) == 1, "tushare bse fallback: recent sample missing", failures)
    print("OK tushare bse fallback: empty Eastmoney target fields are filled from BSE new-share data")


def main() -> int:
    failures: list[str] = []
    _run_tushare_target_bse_fallback_case(failures)

    if failures:
        print("\nTushare IPO BSE target fallback validation failed:")
        for item in failures:
            print(f"- {item}")
        return 1

    print("\nTushare IPO BSE target fallback validation passed: 1 case")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
