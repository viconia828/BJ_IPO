from __future__ import annotations

import os
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
import sys
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import data_fetcher
import tushare_ipo_helper


TEMP_ROOT = ROOT_DIR / "data" / "temp_validation" / "tushare_ipo_pipeline_validation"
TOKEN_ENV = "TUSHARE_TOKEN"

_ORIGINAL_CALL_TUSHARE = tushare_ipo_helper._call_tushare
_ORIGINAL_FETCH_IPO_INFO = data_fetcher.fetch_ipo_info
_ORIGINAL_FETCH_RECENT_IPOS = data_fetcher.fetch_recent_ipos
_ORIGINAL_FETCH_RECENT_IPOS_BY_DAYS = data_fetcher.fetch_recent_ipos_by_days
_ORIGINAL_TUSHARE_DATE = tushare_ipo_helper.date
FIXED_TODAY = date(2026, 4, 30)


class _FrozenDate(date):
    @classmethod
    def today(cls) -> "_FrozenDate":
        return cls(FIXED_TODAY.year, FIXED_TODAY.month, FIXED_TODAY.day)


def _reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _make_params(cache_root: Path, **overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "ipo_data_source": "tushare",
        "tushare_token_env": TOKEN_ENV,
        "tushare_cache_root": str(cache_root),
        "tushare_daily_request_quota": 200,
        "tushare_request_pause_seconds": 0.0,
        "tushare_static_ttl_days": 3650,
        "tushare_dynamic_ttl_hours": 24,
        "tushare_recent_trade_days": 12,
    }
    params.update(overrides)
    return params


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _assert_close(actual: Any, expected: float, message: str, failures: list[str], tolerance: float = 1e-6) -> None:
    value = float(actual)
    if abs(value - expected) > tolerance:
        failures.append(f"{message}: expected {expected}, got {value}")


def _contains_prefix(items: list[str], prefix: str) -> bool:
    return any(str(item).startswith(prefix) for item in items)


def _fake_fetch_ipo_info(code: str) -> dict[str, Any]:
    if code == "920177":
        return {
            "SECURITY_CODE": "920177",
            "SECURITY_NAME_ABBR": "恒道科技",
            "ISSUE_PRICE": 19.99,
            "AFTER_ISSUE_PE": 12.34,
            "TOTAL_ISSUE_NUM": 999.0,
            "INDUSTRY_PE_NEW": 28.7,
            "SW_INDUSTRY": "机械设备",
            "PRICE_WAY": "直接定价",
            "TOP_APPLY_MARKETCAP": 1500.0,
            "ONLINE_VA_NUM": 321000.0,
            "ONLINE_ISSUE_LWR": 0.0456,
            "MAIN_BUSINESS": "主要从事机械零部件研发制造。",
        }
    raise data_fetcher.DataFetcherError(f"missing fixture for {code}")


def _fake_fetch_recent_ipos(months: int = 3, page_size: int = 50) -> list[dict[str, Any]]:
    _ = (months, page_size)
    return [
        {
            "SECURITY_CODE": "920011",
            "SECURITY_NAME_ABBR": "晨光电机",
            "LISTING_DATE": "2026-04-08",
            "ISSUE_PRICE": 15.50,
            "CLOSE_PRICE": 28.80,
            "LD_CLOSE_CHANGE": 85.81,
            "TURNOVERRATE": 78.12,
        }
    ]


def _fake_fetch_recent_ipos_by_days(days: int = 90, page_size: int = 50) -> list[dict[str, Any]]:
    _ = days
    return _fake_fetch_recent_ipos(page_size=page_size)


def _fake_call_tushare_happy(
    api_name: str,
    params: dict[str, Any],
    fields: str,
    settings: dict[str, Any],
    db: Any,
    summary: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    _ = (fields, settings, db, summary)
    ts_code = str(params.get("ts_code") or "")
    if api_name == "new_share":
        return [
            {
                "ts_code": "920177.BJ",
                "name": "恒道科技",
                "ipo_date": "20260407",
                "issue_date": "20260416",
                "amount": 1308.0,
                "market_amount": 1177.0,
                "price": 21.8,
                "pe": 14.99,
                "limit_amount": 58.86,
                "funds": 2.851,
                "ballot": 0.03,
            },
            {
                "ts_code": "920181.BJ",
                "name": "赛英电子",
                "ipo_date": "20260330",
                "issue_date": "20260410",
                "amount": 1080.0,
                "market_amount": 972.0,
                "price": 28.0,
                "pe": 13.79,
                "limit_amount": 48.6,
                "funds": 3.024,
                "ballot": 0.04,
            },
        ], ""
    if api_name == "stock_basic":
        mapping = {
            "920177.BJ": {"name": "恒道科技", "industry": "专用机械", "market": "北交所", "exchange": "BSE", "list_date": "20260416"},
            "920181.BJ": {"name": "赛英电子", "industry": "半导体", "market": "北交所", "exchange": "BSE", "list_date": "20260410"},
        }
        row = mapping.get(ts_code)
        return ([{"ts_code": ts_code, **row}] if row else []), ""
    if api_name == "daily":
        mapping = {
            "920177.BJ": [
                {"trade_date": "20260416", "open": 40.0, "high": 41.01, "low": 36.54, "close": 36.58, "pct_chg": 67.7982, "vol": 1000.0, "amount": 3658.0},
                {"trade_date": "20260417", "open": 37.0, "high": 39.2, "low": 36.8, "close": 38.0, "pct_chg": 3.8819, "vol": 900.0, "amount": 3420.0},
                {"trade_date": "20260420", "open": 38.2, "high": 38.8, "low": 36.9, "close": 37.5, "pct_chg": -1.3158, "vol": 850.0, "amount": 3187.5},
            ],
            "920181.BJ": [
                {"trade_date": "20260410", "open": 51.0, "high": 55.6, "low": 46.2, "close": 49.8, "pct_chg": 77.8571, "vol": 1000.0, "amount": 4980.0},
                {"trade_date": "20260413", "open": 50.0, "high": 52.4, "low": 49.1, "close": 52.0, "pct_chg": 4.4177, "vol": 930.0, "amount": 4836.0},
                {"trade_date": "20260414", "open": 52.2, "high": 53.0, "low": 50.1, "close": 50.5, "pct_chg": -2.8846, "vol": 880.0, "amount": 4444.0},
            ],
        }
        start_date = str(params.get("start_date") or "")
        end_date = str(params.get("end_date") or "")
        rows = []
        for row in mapping.get(ts_code, []):
            trade_date = str(row.get("trade_date") or "")
            if start_date and trade_date < start_date:
                continue
            if end_date and trade_date > end_date:
                continue
            rows.append({"ts_code": ts_code, **row})
        return rows, ""
    if api_name == "daily_basic":
        mapping = {
            "920177.BJ": {"trade_date": "20260416", "close": 36.58, "turnover_rate": 90.2507},
            "920181.BJ": {"trade_date": "20260410", "close": 49.8, "turnover_rate": 82.3145},
        }
        row = mapping.get(ts_code)
        return ([{"ts_code": ts_code, **row}] if row else []), ""
    if api_name == "index_classify":
        level = str(params.get("level") or "")
        if level == "L2":
            return [
                {"index_code": "801074.SI", "industry_name": "专用设备", "parent_code": "640000", "level": "L2", "industry_code": "640200", "is_pub": "1", "src": "SW2021"},
                {"index_code": "801081.SI", "industry_name": "半导体", "parent_code": "270000", "level": "L2", "industry_code": "270100", "is_pub": "1", "src": "SW2021"},
            ], ""
        if level == "L3":
            return [
                {"index_code": "850727.SI", "industry_name": "其他专用设备", "parent_code": "640200", "level": "L3", "industry_code": "640209", "is_pub": "1", "src": "SW2021"},
            ], ""
    if api_name == "sw_daily":
        mapping = {
            "850727.SI": [
                {"trade_date": "20260416", "name": "其他专用设备", "pe": 63.31, "pb": 4.92, "float_mv": 42076983.0, "total_mv": 83528326.0},
                {"trade_date": "20260415", "name": "其他专用设备", "pe": 62.88, "pb": 4.89, "float_mv": 41911230.0, "total_mv": 83200411.0},
                {"trade_date": "20260410", "name": "其他专用设备", "pe": 61.52, "pb": 4.76, "float_mv": 41011300.0, "total_mv": 82014500.0},
            ],
            "801081.SI": [
                {"trade_date": "20260410", "name": "半导体", "pe": 104.41, "pb": 7.14, "float_mv": 365760706.0, "total_mv": 717303095.0},
            ],
        }
        start_date = str(params.get("start_date") or "")
        end_date = str(params.get("end_date") or "")
        rows = []
        for row in mapping.get(ts_code, []):
            trade_date = str(row.get("trade_date") or "")
            if start_date and trade_date < start_date:
                continue
            if end_date and trade_date > end_date:
                continue
            rows.append({"ts_code": ts_code, **row})
        return rows, ""
    return [], ""


def _fake_call_tushare_happy_counted(
    api_name: str,
    params: dict[str, Any],
    fields: str,
    settings: dict[str, Any],
    db: Any,
    summary: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    rows, error_message = _fake_call_tushare_happy(api_name, params, fields, settings, db, summary)
    summary["api_calls"] += 1
    counter_key = f"{api_name}_api_calls"
    if counter_key in summary:
        summary[counter_key] += 1
    return rows, error_message


def _fake_call_tushare_with_future_recent(
    api_name: str,
    params: dict[str, Any],
    fields: str,
    settings: dict[str, Any],
    db: Any,
    summary: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    rows, error_message = _fake_call_tushare_happy(api_name, params, fields, settings, db, summary)
    if api_name != "new_share":
        return rows, error_message
    future_issue_date = (FIXED_TODAY + timedelta(days=2)).strftime("%Y%m%d")
    return rows + [
        {
            "ts_code": "920191.BJ",
            "name": "未上市样本",
            "ipo_date": FIXED_TODAY.strftime("%Y%m%d"),
            "issue_date": future_issue_date,
            "amount": 980.0,
            "market_amount": 882.0,
            "price": 18.6,
            "pe": 15.1,
            "limit_amount": 42.0,
            "funds": 1.823,
            "ballot": 0.05,
        }
    ], error_message


def _fake_call_tushare_with_future_recent_counted(
    api_name: str,
    params: dict[str, Any],
    fields: str,
    settings: dict[str, Any],
    db: Any,
    summary: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    rows, error_message = _fake_call_tushare_with_future_recent(api_name, params, fields, settings, db, summary)
    summary["api_calls"] += 1
    counter_key = f"{api_name}_api_calls"
    if counter_key in summary:
        summary[counter_key] += 1
    return rows, error_message


def _fake_call_tushare_recent_fallback(
    api_name: str,
    params: dict[str, Any],
    fields: str,
    settings: dict[str, Any],
    db: Any,
    summary: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    _ = (fields, settings, db, summary)
    ts_code = str(params.get("ts_code") or "")
    if api_name == "new_share":
        return [
            {
                "ts_code": "920177.BJ",
                "name": "恒道科技",
                "ipo_date": "20260407",
                "issue_date": "20260416",
                "amount": 1308.0,
                "market_amount": 1177.0,
                "price": 21.8,
                "pe": 14.99,
                "limit_amount": 58.86,
                "funds": 2.851,
                "ballot": 0.03,
            }
        ], ""
    if api_name == "stock_basic":
        return [
            {
                "ts_code": ts_code,
                "name": "恒道科技",
                "industry": "专用机械",
                "market": "北交所",
                "exchange": "BSE",
                "list_date": "20260416",
            }
        ], ""
    if api_name in {"daily", "daily_basic"}:
        return [], ""
    if api_name in {"index_classify", "sw_daily"}:
        return [], ""
    return [], ""


def _run_merge_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "merge_case"
    _reset_dir(cache_root)
    os.environ[TOKEN_ENV] = "dummy"
    tushare_ipo_helper._call_tushare = _fake_call_tushare_happy
    result = tushare_ipo_helper.prepare_ipo_data("920177", 3, params=_make_params(cache_root))
    ipo_info = result["ipo_info"]
    recent_ipos = result["recent_ipos"]
    summary = result["summary"]

    _assert(summary["provider"] == "tushare", "merge_case: expected tushare provider", failures)
    _assert(summary["recent_source"] == "tushare", "merge_case: expected tushare recent source", failures)
    _assert(summary["eastmoney_supplement_used"], "merge_case: expected eastmoney supplement", failures)
    _assert(summary["cache_root"] == str(cache_root), "merge_case: expected cache root in summary", failures)
    _assert(ipo_info["ISSUE_PRICE"] == 21.8, "merge_case: issue price should be overridden by tushare", failures)
    _assert(ipo_info["AFTER_ISSUE_PE"] == 14.99, "merge_case: issue pe should be overridden by tushare", failures)
    _assert(ipo_info["TOTAL_ISSUE_NUM"] == 1308.0, "merge_case: issue amount should be overridden by tushare", failures)
    _assert_close(ipo_info["TOP_APPLY_MARKETCAP"], 1283.148, "merge_case: expected tushare derived top apply marketcap", failures)
    _assert_close(ipo_info["ONLINE_ISSUE_NUM"], 1177.0 * 10000, "merge_case: expected tushare online issue shares", failures)
    _assert(ipo_info["INDUSTRY_PE_NEW"] == 63.31, "merge_case: expected tushare sw_daily industry pe", failures)
    _assert(ipo_info["INDUSTRY"] == "专用机械", "merge_case: expected tushare industry", failures)
    _assert(summary["industry_pe_source"] == "tushare_sw_daily", "merge_case: expected tushare industry pe source", failures)
    _assert(summary["industry_pe_index_code"] == "850727.SI", "merge_case: expected mapped sw index code", failures)
    _assert(summary["top_apply_marketcap_source"] == "tushare_new_share", "merge_case: expected tushare top apply marketcap source", failures)
    _assert(summary["online_issue_lwr_source"] == "tushare_new_share", "merge_case: expected tushare online issue lwr source", failures)
    _assert(ipo_info["ONLINE_ISSUE_LWR"] == 0.03, "merge_case: expected tushare ballot-based issue lwr", failures)
    _assert("INDUSTRY_PE_NEW" not in summary["supplemented_fields"], "merge_case: industry pe should not remain in eastmoney supplement list", failures)
    _assert("TOP_APPLY_MARKETCAP" not in summary["supplemented_fields"], "merge_case: top apply marketcap should not remain in eastmoney supplement list", failures)
    _assert("ONLINE_ISSUE_LWR" not in summary["supplemented_fields"], "merge_case: online issue lwr should not remain in eastmoney supplement list", failures)
    _assert("stock_basic:920177.BJ" in summary["api_fetched_fixed"], "merge_case: expected stock_basic api fetch marker", failures)
    _assert("index_classify:L2" in summary["api_fetched_fixed"], "merge_case: expected L2 classify api fetch marker", failures)
    _assert("index_classify:L3" in summary["api_fetched_fixed"], "merge_case: expected L3 classify api fetch marker", failures)
    _assert(_contains_prefix(summary["api_fetched_variable"], "new_share_range:"), "merge_case: expected new_share range api fetch marker", failures)
    _assert("listing_day:920177.BJ:2026-04-16" in summary["api_fetched_variable"], "merge_case: expected listing day api fetch marker", failures)
    _assert(_contains_prefix(summary["api_fetched_variable"], "sw_daily_range:850727.SI:"), "merge_case: expected sw_daily range api fetch marker", failures)
    _assert(len(recent_ipos) == 2, "merge_case: expected two recent sample rows", failures)
    _assert(recent_ipos[0]["TURNOVERRATE"] is not None, "merge_case: expected turnover rate", failures)
    _assert(recent_ipos[0]["LD_CLOSE_CHANGE"] is not None, "merge_case: expected ld close change", failures)
    recent_by_code = {item["SECURITY_CODE"]: item for item in recent_ipos}
    _assert_close(recent_by_code["920177"]["AVERAGE_PRICE"], 36.58, "merge_case: expected Tushare daily average price", failures)
    _assert_close(
        recent_by_code["920177"]["ONLINE_ISSUE_NUM"],
        1177.0 * 10000,
        "merge_case: expected Tushare online issue shares in recent sample",
        failures,
    )
    _assert_close(
        recent_by_code["920177"]["ONLINE_ISSUE_LWR"],
        0.03,
        "merge_case: expected Tushare ballot in recent sample",
        failures,
    )
    _assert_close(
        recent_by_code["920177"]["TOP_APPLY_MARKETCAP"],
        58.86 * 21.8,
        "merge_case: expected Tushare top apply amount in recent sample",
        failures,
    )
    _assert(
        recent_by_code["920177"]["LD_AVERAGE_CHANGE"] is not None,
        "merge_case: expected average-price first-day change",
        failures,
    )
    _assert_close(recent_by_code["920177"]["NEXT_DAY_CLOSE"], 38.0, "merge_case: expected next trading-day close", failures)
    _assert_close(recent_by_code["920177"]["THIRD_DAY_CLOSE"], 37.5, "merge_case: expected third trading-day close", failures)
    _assert(
        recent_by_code["920177"]["POST_LISTING_PROFIT_EFFECT_PCT"] is not None,
        "merge_case: expected post-listing profit effect",
        failures,
    )
    _assert("post_listing:920177.BJ:2026-04-16" in summary["api_fetched_variable"], "merge_case: expected post-listing api fetch marker", failures)
    print("OK merge_case: target IPO fields, industry PE, top apply marketcap and recent samples were built from Tushare with eastmoney display supplement")


def _run_missing_token_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "missing_token_case"
    _reset_dir(cache_root)
    os.environ.pop(TOKEN_ENV, None)
    tushare_ipo_helper._call_tushare = _fake_call_tushare_happy
    result = tushare_ipo_helper.prepare_ipo_data("920177", 3, params=_make_params(cache_root))
    summary = result["summary"]
    _assert(summary["target_source"] == "eastmoney", "missing_token_case: expected eastmoney target fallback", failures)
    _assert(summary["recent_source"] == "eastmoney", "missing_token_case: expected eastmoney recent fallback", failures)
    _assert(summary["target_fallback_used"], "missing_token_case: expected fallback flag", failures)
    _assert(result["ipo_info"]["SECURITY_CODE"] == "920177", "missing_token_case: expected eastmoney ipo info", failures)
    _assert(len(result["recent_ipos"]) == 1, "missing_token_case: expected eastmoney recent samples", failures)
    print("OK missing_token_case: missing token fell back to eastmoney")


def _run_recent_fallback_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "recent_fallback_case"
    _reset_dir(cache_root)
    os.environ[TOKEN_ENV] = "dummy"
    tushare_ipo_helper._call_tushare = _fake_call_tushare_recent_fallback
    result = tushare_ipo_helper.prepare_ipo_data("920177", 3, params=_make_params(cache_root))
    summary = result["summary"]
    _assert(summary["recent_source"] == "eastmoney", "recent_fallback_case: expected eastmoney recent fallback", failures)
    _assert(summary["eastmoney_recent_fallback_used"], "recent_fallback_case: expected recent fallback flag", failures)
    _assert(summary["industry_pe_source"] == "eastmoney", "recent_fallback_case: expected eastmoney industry pe fallback", failures)
    _assert(summary["top_apply_marketcap_source"] == "tushare_new_share", "recent_fallback_case: expected tushare top apply marketcap source", failures)
    _assert(summary["online_issue_lwr_source"] == "tushare_new_share", "recent_fallback_case: expected tushare online issue lwr source", failures)
    _assert(result["ipo_info"]["INDUSTRY_PE_NEW"] == 28.7, "recent_fallback_case: expected eastmoney industry pe", failures)
    _assert_close(result["ipo_info"]["TOP_APPLY_MARKETCAP"], 1283.148, "recent_fallback_case: expected tushare top apply marketcap", failures)
    _assert(result["ipo_info"]["ONLINE_ISSUE_LWR"] == 0.03, "recent_fallback_case: expected tushare ballot-based issue lwr", failures)
    _assert(len(result["recent_ipos"]) == 1, "recent_fallback_case: expected fallback recent sample", failures)
    print("OK recent_fallback_case: empty Tushare recent samples fell back to eastmoney")


def _run_cache_reuse_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "cache_reuse_case"
    _reset_dir(cache_root)
    os.environ[TOKEN_ENV] = "dummy"
    tushare_ipo_helper._call_tushare = _fake_call_tushare_happy_counted

    first = tushare_ipo_helper.prepare_ipo_data("920177", 3, params=_make_params(cache_root))
    second = tushare_ipo_helper.prepare_ipo_data("920177", 3, params=_make_params(cache_root))
    first_summary = first["summary"]
    second_summary = second["summary"]

    _assert(first_summary["api_calls"] > second_summary["api_calls"], "cache_reuse_case: expected fewer api calls on second run", failures)
    _assert(second_summary["api_calls"] == 0, "cache_reuse_case: expected zero api calls on second run", failures)
    _assert(second_summary["new_share_api_calls"] == 0, "cache_reuse_case: expected zero new_share api calls on second run", failures)
    _assert(second_summary["stock_basic_api_calls"] == 0, "cache_reuse_case: expected zero stock_basic api calls on second run", failures)
    _assert(second_summary["daily_api_calls"] == 0, "cache_reuse_case: expected zero daily api calls on second run", failures)
    _assert(second_summary["daily_basic_api_calls"] == 0, "cache_reuse_case: expected zero daily_basic api calls on second run", failures)
    _assert(_contains_prefix(second_summary["variable_cache_hits"], "new_share_range:"), "cache_reuse_case: expected new_share range cache hit", failures)
    _assert("stock_basic:920177.BJ" in second_summary["fixed_cache_hits"], "cache_reuse_case: expected stock_basic cache hit", failures)
    _assert("stock_basic:920181.BJ" in second_summary["fixed_cache_hits"], "cache_reuse_case: expected recent sample stock_basic cache hit", failures)
    _assert("index_classify:L2" in second_summary["fixed_cache_hits"], "cache_reuse_case: expected L2 classify cache hit", failures)
    _assert("index_classify:L3" in second_summary["fixed_cache_hits"], "cache_reuse_case: expected L3 classify cache hit", failures)
    _assert("listing_day:920177.BJ:2026-04-16" in second_summary["variable_cache_hits"], "cache_reuse_case: expected listing day cache hit", failures)
    _assert("listing_day:920181.BJ:2026-04-10" in second_summary["variable_cache_hits"], "cache_reuse_case: expected second listing day cache hit", failures)
    _assert("post_listing:920177.BJ:2026-04-16" in second_summary["variable_cache_hits"], "cache_reuse_case: expected post-listing cache hit", failures)
    _assert("post_listing:920181.BJ:2026-04-10" in second_summary["variable_cache_hits"], "cache_reuse_case: expected second post-listing cache hit", failures)
    _assert("sw_daily:850727.SI:20260416" in second_summary["variable_cache_hits"], "cache_reuse_case: expected sw_daily cache hit", failures)
    print("OK cache_reuse_case: second run fully reused new_share/stock_basic/listing_day/sw_daily caches")


def _run_new_share_incremental_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "new_share_incremental_case"
    _reset_dir(cache_root)
    os.environ[TOKEN_ENV] = "dummy"
    tushare_ipo_helper._call_tushare = _fake_call_tushare_happy_counted
    db = tushare_ipo_helper.LocalFileDB(cache_root)
    stale_snapshot = {
        "start_date": "20260103",
        "end_date": "20260410",
        "rows": [
            {
                "ts_code": "920177.BJ",
                "name": "恒道科技",
                "ipo_date": "20260407",
                "issue_date": "20260416",
                "amount": 1308.0,
                "market_amount": 1177.0,
                "price": 21.8,
                "pe": 14.99,
                "limit_amount": 58.86,
                "funds": 2.851,
                "ballot": 0.03,
            }
        ],
        "updated_at": (datetime.now() - timedelta(hours=48)).isoformat(timespec="seconds"),
        "source": "tushare_api",
    }
    db.save_variable_record(
        tushare_ipo_helper.NEW_SHARE_RANGE_CACHE_CODE,
        {tushare_ipo_helper.NEW_SHARE_RANGE_FIELD_KEY: stale_snapshot},
        trade_date=stale_snapshot["end_date"],
        source="tushare_api",
    )

    result = tushare_ipo_helper.prepare_ipo_data("920177", 3, params=_make_params(cache_root))
    summary = result["summary"]
    recent_codes = [item["SECURITY_CODE"] for item in result["recent_ipos"]]

    _assert(summary["new_share_api_calls"] == 1, "new_share_incremental_case: expected one incremental new_share refresh", failures)
    _assert(_contains_prefix(summary["variable_cache_hits"], "new_share_range_seed:"), "new_share_incremental_case: expected cached range seed reuse", failures)
    _assert(_contains_prefix(summary["api_fetched_variable"], "new_share_range:"), "new_share_incremental_case: expected new_share incremental fetch marker", failures)
    _assert("920181" in recent_codes, "new_share_incremental_case: expected incremental refresh to merge in 920181", failures)
    print("OK new_share_incremental_case: stale range cache reused prior rows and refreshed only the trailing window")


def _run_future_recent_skip_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "future_recent_skip_case"
    _reset_dir(cache_root)
    os.environ[TOKEN_ENV] = "dummy"
    tushare_ipo_helper._call_tushare = _fake_call_tushare_with_future_recent_counted
    result = tushare_ipo_helper.prepare_ipo_data("920177", 3, params=_make_params(cache_root))
    summary = result["summary"]
    recent_codes = [item["SECURITY_CODE"] for item in result["recent_ipos"]]

    _assert(summary["stock_basic_api_calls"] == 2, "future_recent_skip_case: future row should not trigger extra stock_basic fetch", failures)
    _assert(summary["daily_api_calls"] == 4, "future_recent_skip_case: future row should only trigger listing-day and post-listing daily fetches for listed rows", failures)
    _assert(summary["daily_basic_api_calls"] == 2, "future_recent_skip_case: future row should not trigger extra daily_basic fetch", failures)
    _assert("920191" not in summary["recent_requested_codes"], "future_recent_skip_case: future row should not enter requested samples", failures)
    _assert("920191" in summary["recent_pending_codes"], "future_recent_skip_case: expected future row to be tracked as pending", failures)
    _assert("920191" not in recent_codes, "future_recent_skip_case: future row should not appear in recent sample output", failures)
    print("OK future_recent_skip_case: future IPO rows were excluded from recent sample prewarm")


def _run_sw_daily_range_reuse_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "sw_daily_range_reuse_case"
    _reset_dir(cache_root)
    os.environ[TOKEN_ENV] = "dummy"
    tushare_ipo_helper._call_tushare = _fake_call_tushare_happy_counted
    settings = tushare_ipo_helper.tushare_helper._build_settings(_make_params(cache_root))
    db = tushare_ipo_helper.LocalFileDB(cache_root)

    first_summary = tushare_ipo_helper._build_summary("920177", 3, settings, db)
    first_snapshot = tushare_ipo_helper._get_sw_daily_snapshot("850727.SI", "20260416", settings, db, first_summary)
    second_summary = tushare_ipo_helper._build_summary("920177", 3, settings, db)
    second_snapshot = tushare_ipo_helper._get_sw_daily_snapshot("850727.SI", "20260415", settings, db, second_summary)

    _assert(first_snapshot is not None, "sw_daily_range_reuse_case: expected first snapshot", failures)
    _assert(second_snapshot is not None, "sw_daily_range_reuse_case: expected second snapshot", failures)
    _assert(first_snapshot.get("trade_date") == "20260416", "sw_daily_range_reuse_case: expected first trade date", failures)
    _assert(second_snapshot.get("trade_date") == "20260415", "sw_daily_range_reuse_case: expected second trade date from range cache", failures)
    _assert(first_summary["api_calls"] == 1, "sw_daily_range_reuse_case: expected one api call on first fetch", failures)
    _assert(second_summary["api_calls"] == 0, "sw_daily_range_reuse_case: expected zero api calls on second fetch", failures)
    _assert(_contains_prefix(first_summary["api_fetched_variable"], "sw_daily_range:850727.SI:"), "sw_daily_range_reuse_case: expected sw_daily range fetch marker", failures)
    _assert(_contains_prefix(second_summary["variable_cache_hits"], "sw_daily_range:850727.SI:"), "sw_daily_range_reuse_case: expected sw_daily range cache hit", failures)
    print("OK sw_daily_range_reuse_case: second anchor reused cached sw_daily window without extra api call")


def main() -> int:
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    data_fetcher.fetch_ipo_info = _fake_fetch_ipo_info
    data_fetcher.fetch_recent_ipos = _fake_fetch_recent_ipos
    data_fetcher.fetch_recent_ipos_by_days = _fake_fetch_recent_ipos_by_days
    tushare_ipo_helper.date = _FrozenDate
    try:
        _run_merge_case(failures)
        _run_missing_token_case(failures)
        _run_recent_fallback_case(failures)
        _run_cache_reuse_case(failures)
        _run_new_share_incremental_case(failures)
        _run_future_recent_skip_case(failures)
        _run_sw_daily_range_reuse_case(failures)
    finally:
        tushare_ipo_helper._call_tushare = _ORIGINAL_CALL_TUSHARE
        data_fetcher.fetch_ipo_info = _ORIGINAL_FETCH_IPO_INFO
        data_fetcher.fetch_recent_ipos = _ORIGINAL_FETCH_RECENT_IPOS
        data_fetcher.fetch_recent_ipos_by_days = _ORIGINAL_FETCH_RECENT_IPOS_BY_DAYS
        tushare_ipo_helper.date = _ORIGINAL_TUSHARE_DATE
        os.environ.pop(TOKEN_ENV, None)

    if failures:
        print("\nTushare IPO data pipeline validation failed:")
        for item in failures:
            print(f"- {item}")
        return 1

    print("\nTushare IPO data pipeline validation passed: 7 cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
