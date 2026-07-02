from __future__ import annotations

import os
import ssl
from pathlib import Path
import shutil
import sys
from datetime import datetime
from typing import Any


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
CODE_DIR = ROOT_DIR / "code"
TOOLS_DIR = ROOT_DIR / "tools"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import cache_listing_day_intraday
import tushare_helper


TEMP_ROOT = ROOT_DIR / "data" / "temp_validation" / "intraday_cache_validation"
TOKEN_ENV = "TUSHARE_TOKEN"

_ORIGINAL_CALL_TUSHARE_API = tushare_helper._call_tushare_api
_ORIGINAL_FETCH_RECENT_IPOS = cache_listing_day_intraday.data_fetcher.fetch_recent_ipos
_ORIGINAL_FETCH_INTRADAY_TRENDS = cache_listing_day_intraday.data_fetcher.fetch_intraday_trends
_ORIGINAL_URL_OPEN = cache_listing_day_intraday.data_fetcher.urllib.request.urlopen


def _today_at(hour: int, minute: int = 0) -> datetime:
    today = cache_listing_day_intraday.date.today()
    return datetime(today.year, today.month, today.day, hour, minute)


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _make_params(cache_root: Path) -> dict[str, Any]:
    return {
        "tushare_token_env": TOKEN_ENV,
        "tushare_cache_root": str(cache_root),
        "tushare_daily_request_quota": 50,
        "tushare_request_pause_seconds": 0.0,
        "tushare_static_ttl_days": 3650,
        "tushare_dynamic_ttl_hours": 24,
        "tushare_recent_trade_days": 12,
    }


def _build_fake_call(call_log: list[tuple[str, str]]) -> Any:
    today = cache_listing_day_intraday.date.today().isoformat()

    def _fake_call_tushare_api(
        api_name: str,
        params: dict[str, Any],
        fields: str,
        settings: dict[str, Any],
        db: Any,
    ) -> tuple[list[dict[str, Any]], str]:
        code = str(params.get("ts_code") or params.get("code") or "")
        call_log.append((api_name, code))

        if api_name == "rt_min_daily":
            return [], "Tushare 接口 rt_min_daily 调用失败: 抱歉，您没有接口访问权限。"

        if api_name != "stk_mins":
            return [], f"unexpected api_name: {api_name}"

        start_date = str(params.get("start_date") or "")
        if code == "920191.BJ" and start_date.startswith(today):
            return (
                [
                    {
                        "ts_code": "920191.BJ",
                        "trade_time": f"{today} 09:31:00",
                        "open": 10.2,
                        "close": 10.6,
                        "high": 10.8,
                        "low": 10.1,
                        "vol": 2200,
                        "amount": 23000,
                    },
                    {
                        "ts_code": "920191.BJ",
                        "trade_time": f"{today} 09:30:00",
                        "open": 10.0,
                        "close": 10.2,
                        "high": 10.3,
                        "low": 9.9,
                        "vol": 2000,
                        "amount": 21000,
                    },
                ],
                "",
            )

        if code == "920188.BJ" and start_date.startswith("2026-04-19"):
            return (
                [
                    {
                        "ts_code": "920188.BJ",
                        "trade_time": "2026-04-19 09:30:00",
                        "open": 9.5,
                        "close": 9.7,
                        "high": 9.8,
                        "low": 9.4,
                        "vol": 1500,
                        "amount": 18000,
                    }
                ],
                "",
            )

        if code == "920177.BJ" and start_date.startswith("2026-04-19"):
            return (
                [
                    {
                        "ts_code": "920177.BJ",
                        "trade_time": "2026-04-19 09:31:00",
                        "open": 21.2,
                        "close": 21.8,
                        "high": 22.0,
                        "low": 21.1,
                        "vol": 1800,
                        "amount": 39000,
                    },
                    {
                        "ts_code": "920177.BJ",
                        "trade_time": "2026-04-19 09:30:00",
                        "open": 21.0,
                        "close": 21.2,
                        "high": 21.3,
                        "low": 20.9,
                        "vol": 1600,
                        "amount": 34000,
                    },
                ],
                "",
            )

        return [], f"unexpected params for {api_name}: {params}"

    return _fake_call_tushare_api


def _run_today_scan_fallback_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "today_scan" / "cache_db"
    output_dir = TEMP_ROOT / "today_scan" / "csv"
    today = cache_listing_day_intraday.date.today().isoformat()
    seen_require_close_price: list[bool] = []
    call_log: list[tuple[str, str]] = []

    def _fake_fetch_recent_ipos(months: int = 3, page_size: int = 50, require_close_price: bool = True) -> list[dict[str, Any]]:
        seen_require_close_price.append(require_close_price)
        return [
            {
                "SECURITY_CODE": "920191",
                "SECURITY_NAME_ABBR": "瑞尔竞达",
                "LISTING_DATE": today,
                "CLOSE_PRICE": None,
            },
            {
                "SECURITY_CODE": "920188",
                "SECURITY_NAME_ABBR": "无关旧样本",
                "LISTING_DATE": "2026-04-19",
                "CLOSE_PRICE": 12.3,
            },
        ]

    os.environ[TOKEN_ENV] = "dummy"
    cache_listing_day_intraday.data_fetcher.fetch_recent_ipos = _fake_fetch_recent_ipos
    tushare_helper._call_tushare_api = _build_fake_call(call_log)

    summary = cache_listing_day_intraday.run_cache_job(
        target_date=today,
        months=1,
        output_dir=output_dir,
        params=_make_params(cache_root),
        current_datetime=_today_at(16, 0),
    )

    csv_path = output_dir / "920191.csv"
    content = csv_path.read_text(encoding="utf-8-sig").splitlines()

    _assert(seen_require_close_price == [False], "today_scan: expected require_close_price=False", failures)
    _assert(summary["matched_count"] == 1, "today_scan: expected one matched code", failures)
    _assert(summary["cached_count"] == 1, "today_scan: expected one cached csv", failures)
    _assert(summary["error_count"] == 0, "today_scan: expected zero errors", failures)
    _assert(csv_path.exists(), "today_scan: expected output csv", failures)
    _assert(summary["cached"][0]["source_api"] == "stk_mins", "today_scan: expected stk_mins fallback source", failures)
    _assert(summary["cached"][0]["attempted_apis"] == ["rt_min_daily", "stk_mins"], "today_scan: expected fallback api order", failures)
    _assert(call_log[:2] == [("rt_min_daily", "920191.BJ"), ("stk_mins", "920191.BJ")], "today_scan: expected rt->stk fallback order", failures)
    _assert(
        content[:3]
        == [
            "DateTime,open,high,low,close,volume,amount",
            f"{today.replace('-', '/')} 09:30,10.0,10.3,9.9,10.2,2000.0,21000.0",
            f"{today.replace('-', '/')} 09:31,10.2,10.8,10.1,10.6,2200.0,23000.0",
        ],
        "today_scan: csv content/order mismatch",
        failures,
    )
    print("OK today_scan: listed-today scan fell back from rt_min_daily to stk_mins and cached csv")


def _run_today_intraday_guard_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "today_intraday_guard" / "cache_db"
    output_dir = TEMP_ROOT / "today_intraday_guard" / "csv"
    today = cache_listing_day_intraday.date.today().isoformat()
    call_log: list[tuple[str, str]] = []

    def _fake_fetch_recent_ipos(months: int = 3, page_size: int = 50, require_close_price: bool = True) -> list[dict[str, Any]]:
        return [
            {
                "SECURITY_CODE": "920200",
                "SECURITY_NAME_ABBR": "振宏股份",
                "LISTING_DATE": today,
                "CLOSE_PRICE": None,
            },
            {
                "SECURITY_CODE": "920188",
                "SECURITY_NAME_ABBR": "无关旧样本",
                "LISTING_DATE": "2026-04-19",
                "CLOSE_PRICE": 12.3,
            },
        ]

    os.environ[TOKEN_ENV] = "dummy"
    cache_listing_day_intraday.data_fetcher.fetch_recent_ipos = _fake_fetch_recent_ipos
    tushare_helper._call_tushare_api = _build_fake_call(call_log)

    summary = cache_listing_day_intraday.run_cache_job(
        target_date=today,
        months=1,
        output_dir=output_dir,
        params=_make_params(cache_root),
        current_datetime=_today_at(10, 0),
    )

    _assert(summary["matched_count"] == 1, "today_intraday_guard: expected one matched code", failures)
    _assert(summary["cached_count"] == 0, "today_intraday_guard: should not cache during active listing day", failures)
    _assert(summary["deferred_count"] == 1, "today_intraday_guard: expected one deferred code", failures)
    _assert(summary["deferred"][0]["source_api"] == "intraday_guard", "today_intraday_guard: expected guard source", failures)
    _assert("15:30" in summary["deferred"][0]["reason"], "today_intraday_guard: reason should mention after-close retry time", failures)
    _assert(not (output_dir / "920200.csv").exists(), "today_intraday_guard: should not write today csv before ready time", failures)
    _assert(call_log == [], "today_intraday_guard: should not request minute APIs before ready time", failures)
    print("OK today_intraday_guard: listed-today IPO was deferred before after-close cache time")


def _run_history_fetch_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "history_fetch" / "cache_db"
    call_log: list[tuple[str, str]] = []

    os.environ[TOKEN_ENV] = "dummy"
    tushare_helper._call_tushare_api = _build_fake_call(call_log)

    result = tushare_helper.fetch_intraday_bars("920188", trade_date="2026-04-19", params=_make_params(cache_root))
    rows = result.get("rows") or []
    summary = result.get("summary") or {}

    _assert(summary.get("source_api") == "stk_mins", "history_fetch: expected stk_mins api", failures)
    _assert(summary.get("attempted_apis") == ["stk_mins"], "history_fetch: expected only stk_mins attempt", failures)
    _assert(len(rows) == 1, "history_fetch: expected one normalized row", failures)
    _assert(rows[0]["DateTime"] == "2026/04/19 09:30", "history_fetch: normalized datetime mismatch", failures)
    print("OK history_fetch: historical trade date used stk_mins and normalized csv row schema")


def _run_latest_until_cached_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "latest_until_cached" / "cache_db"
    output_dir = TEMP_ROOT / "latest_until_cached" / "csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "920188.csv").write_text("DateTime,open,high,low,close,volume,amount\n", encoding="utf-8-sig")
    today = cache_listing_day_intraday.date.today().isoformat()
    call_log: list[tuple[str, str]] = []

    cache_listing_day_intraday.data_fetcher.fetch_recent_ipos = lambda months=3, page_size=50, require_close_price=True: [
        {"SECURITY_CODE": "920191", "SECURITY_NAME_ABBR": "瑞尔竞达", "LISTING_DATE": today},
        {"SECURITY_CODE": "920177", "SECURITY_NAME_ABBR": "志晟信息", "LISTING_DATE": "2026-04-19"},
        {"SECURITY_CODE": "920188", "SECURITY_NAME_ABBR": "已有缓存样本", "LISTING_DATE": "2026-04-18"},
        {"SECURITY_CODE": "920180", "SECURITY_NAME_ABBR": "不应继续扫描", "LISTING_DATE": "2026-04-17"},
    ]
    os.environ[TOKEN_ENV] = "dummy"
    tushare_helper._call_tushare_api = _build_fake_call(call_log)

    summary = cache_listing_day_intraday.run_latest_missing_cache_job(
        months=18,
        output_dir=output_dir,
        params=_make_params(cache_root),
        current_datetime=_today_at(16, 0),
    )

    _assert([item["code"] for item in summary["cached"]] == ["920191", "920177"], "latest_until_cached: expected two cached codes", failures)
    _assert(summary["stop_at_existing"]["code"] == "920188", "latest_until_cached: expected stop at existing 920188", failures)
    _assert(summary["checked_codes"] == ["920191", "920177", "920188"], "latest_until_cached: expected stop before 920180", failures)
    _assert((output_dir / "920191.csv").exists(), "latest_until_cached: expected 920191 csv", failures)
    _assert((output_dir / "920177.csv").exists(), "latest_until_cached: expected 920177 csv", failures)
    _assert(("stk_mins", "920180.BJ") not in call_log, "latest_until_cached: should not request older code after stop", failures)
    print("OK latest_until_cached: cached consecutive missing IPOs and stopped at first existing csv")


def _run_eastmoney_fallback_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "eastmoney_fallback" / "cache_db"
    output_dir = TEMP_ROOT / "eastmoney_fallback" / "csv"
    today = cache_listing_day_intraday.date.today().isoformat()

    def _fake_call_tushare_api(
        api_name: str,
        params: dict[str, Any],
        fields: str,
        settings: dict[str, Any],
        db: Any,
    ) -> tuple[list[dict[str, Any]], str]:
        return [], "Tushare 接口 stk_mins 调用失败: 抱歉，您每天最多访问该接口2次。"

    def _fake_fetch_recent_ipos(months: int = 3, page_size: int = 50, require_close_price: bool = True) -> list[dict[str, Any]]:
        return [{"SECURITY_CODE": "920191", "SECURITY_NAME_ABBR": "瑞尔竞达", "LISTING_DATE": today}]

    def _fake_fetch_intraday_trends(code: str, trade_date: str | Any = None) -> list[dict[str, Any]]:
        return [
            {
                "DateTime": f"{today.replace('-', '/')} 09:30",
                "open": 15.99,
                "high": 15.99,
                "low": 15.99,
                "close": 15.99,
                "volume": 14647.0,
                "amount": 23420680.0,
            }
        ]

    os.environ[TOKEN_ENV] = "dummy"
    cache_listing_day_intraday.data_fetcher.fetch_recent_ipos = _fake_fetch_recent_ipos
    cache_listing_day_intraday.data_fetcher.fetch_intraday_trends = _fake_fetch_intraday_trends
    tushare_helper._call_tushare_api = _fake_call_tushare_api

    summary = cache_listing_day_intraday.run_latest_missing_cache_job(
        months=18,
        output_dir=output_dir,
        params=_make_params(cache_root),
        current_datetime=_today_at(16, 0),
    )

    _assert(summary["cached_count"] == 1, "eastmoney_fallback: expected one cached csv", failures)
    _assert(summary["cached"][0]["source_api"] == "eastmoney_trends2", "eastmoney_fallback: expected eastmoney source", failures)
    _assert(summary["cached"][0]["attempted_apis"] == ["rt_min_daily", "stk_mins", "eastmoney_trends2"], "eastmoney_fallback: expected attempted api chain", failures)
    csv_path = output_dir / "920191.csv"
    _assert(csv_path.exists(), "eastmoney_fallback: expected output csv", failures)
    content = csv_path.read_text(encoding="utf-8-sig").splitlines() if csv_path.exists() else []
    _assert(summary["cached"][0]["unit_mode"] == "volume_hands_amount_yuan", "eastmoney_fallback: expected hand-volume unit mode", failures)
    _assert(summary["cached"][0]["unit_normalized"] is True, "eastmoney_fallback: expected unit normalization", failures)
    _assert(
        content[:2]
        == [
            "DateTime,open,high,low,close,volume,amount",
            f"{today.replace('-', '/')} 09:30,15.99,15.99,15.99,15.99,1464700.0,23420680.0",
        ],
        "eastmoney_fallback: expected volume converted from hands to shares",
        failures,
    )
    print("OK eastmoney_fallback: Eastmoney minute endpoint backfilled csv after Tushare failure")


def _run_existing_cache_normalization_case(failures: list[str]) -> None:
    output_dir = TEMP_ROOT / "existing_cache_normalization" / "csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    today = cache_listing_day_intraday.date.today().isoformat().replace("-", "/")
    csv_path = output_dir / "920191.csv"
    csv_path.write_text(
        "\n".join(
            [
                "DateTime,open,high,low,close,volume,amount",
                f"{today} 09:30:00,15.9,16.1,15.8,15.99,100,159900",
                f"{today} 09:31:00,16.0,16.2,15.9,16.1,120,193200",
            ]
        )
        + "\n",
        encoding="utf-8-sig",
    )

    summary = cache_listing_day_intraday.normalize_existing_intraday_cache(
        output_dir=output_dir,
        codes=["920191"],
    )
    content = csv_path.read_text(encoding="utf-8-sig").splitlines()

    _assert(summary["checked_count"] == 1, "existing_cache_normalization: expected one checked csv", failures)
    _assert(summary["normalized_count"] == 1, "existing_cache_normalization: expected one normalized csv", failures)
    _assert(summary["error_count"] == 0, "existing_cache_normalization: expected zero errors", failures)
    _assert(
        summary["normalized"][0]["unit_mode"] == "volume_hands_amount_yuan",
        "existing_cache_normalization: expected hand-volume unit mode",
        failures,
    )
    _assert(
        content[:3]
        == [
            "DateTime,open,high,low,close,volume,amount",
            f"{today} 09:30,15.9,16.1,15.8,15.99,10000.0,159900.0",
            f"{today} 09:31,16.0,16.2,15.9,16.1,12000.0,193200.0",
        ],
        "existing_cache_normalization: expected hand volume converted to shares",
        failures,
    )
    print("OK existing_cache_normalization: existing csv hand volume was converted to shares")


def _run_eastmoney_imprecise_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "eastmoney_imprecise" / "cache_db"
    output_dir = TEMP_ROOT / "eastmoney_imprecise" / "csv"
    today = cache_listing_day_intraday.date.today().isoformat()

    def _fake_call_tushare_api(
        api_name: str,
        params: dict[str, Any],
        fields: str,
        settings: dict[str, Any],
        db: Any,
    ) -> tuple[list[dict[str, Any]], str]:
        return [], "Tushare 接口 stk_mins 调用失败: 抱歉，您每天最多访问该接口2次。"

    def _fake_fetch_recent_ipos(months: int = 3, page_size: int = 50, require_close_price: bool = True) -> list[dict[str, Any]]:
        return [
            {"SECURITY_CODE": "920177", "SECURITY_NAME_ABBR": "恒道科技", "LISTING_DATE": today},
            {"SECURITY_CODE": "920188", "SECURITY_NAME_ABBR": "已有缓存样本", "LISTING_DATE": "2026-04-19"},
        ]

    def _fake_fetch_intraday_trends(code: str, trade_date: str | Any = None) -> list[dict[str, Any]]:
        raise cache_listing_day_intraday.data_fetcher.DataFetcherError(
            f"东方财富返回的 {code} 上市首日分钟串存在 open=0，数据不精确，已取消缓存，留待下次执行补缓存程序再取"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "920188.csv").write_text("DateTime,open,high,low,close,volume,amount\n", encoding="utf-8-sig")

    os.environ[TOKEN_ENV] = "dummy"
    cache_listing_day_intraday.data_fetcher.fetch_recent_ipos = _fake_fetch_recent_ipos
    cache_listing_day_intraday.data_fetcher.fetch_intraday_trends = _fake_fetch_intraday_trends
    tushare_helper._call_tushare_api = _fake_call_tushare_api

    summary = cache_listing_day_intraday.run_latest_missing_cache_job(
        months=18,
        output_dir=output_dir,
        params=_make_params(cache_root),
        current_datetime=_today_at(16, 0),
    )

    _assert(summary["cached_count"] == 0, "eastmoney_imprecise: expected zero cached csv", failures)
    _assert(summary["deferred_count"] == 1, "eastmoney_imprecise: expected one deferred code", failures)
    _assert(summary["deferred"][0]["code"] == "920177", "eastmoney_imprecise: expected deferred 920177", failures)
    _assert(summary["stop_at_existing"]["code"] == "920188", "eastmoney_imprecise: expected stop at existing 920188", failures)
    _assert(not (output_dir / "920177.csv").exists(), "eastmoney_imprecise: should not write imprecise csv", failures)
    print("OK eastmoney_imprecise: imprecise Eastmoney minute data was deferred instead of being cached")


def _run_manual_file_fallback_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "manual_file_fallback" / "cache_db"
    output_dir = TEMP_ROOT / "manual_file_fallback" / "csv"
    manual_root = TEMP_ROOT / "manual_file_fallback" / "manual_files"
    today = cache_listing_day_intraday.date.today().isoformat()
    events: list[str] = []
    manual_root.mkdir(parents=True, exist_ok=True)
    (manual_root / "920191.csv").write_text(
        "\n".join(
            [
                "代码,名称,日期,开盘价(元),最高价(元),最低价(元),收盘价(元),涨跌幅,成交额(百万),成交量",
                f"920191.BJ,瑞尔竞达,{today} 09:31,16.2,16.8,16.0,16.4,0.02,2.46,150000",
                f"920191.BJ,瑞尔竞达,{today} 09:30,15.0,15.8,14.9,15.5,0.01,1.23,100000",
            ]
        ),
        encoding="utf-8-sig",
    )

    def _fake_call_tushare_api(
        api_name: str,
        params: dict[str, Any],
        fields: str,
        settings: dict[str, Any],
        db: Any,
    ) -> tuple[list[dict[str, Any]], str]:
        return [], f"Tushare 接口 {api_name} 调用失败: fixture failure"

    def _fake_fetch_recent_ipos(months: int = 3, page_size: int = 50, require_close_price: bool = True) -> list[dict[str, Any]]:
        return [{"SECURITY_CODE": "920191", "SECURITY_NAME_ABBR": "瑞尔竞达", "LISTING_DATE": today}]

    def _fake_fetch_intraday_trends(code: str, trade_date: str | Any = None) -> list[dict[str, Any]]:
        raise cache_listing_day_intraday.data_fetcher.DataFetcherError(
            f"东方财富返回的 {code} 上市首日分钟串存在 open=0，数据不精确，已取消缓存，留待下次执行补缓存程序再取"
        )

    params = _make_params(cache_root)
    params["manual_intraday_file_root"] = str(manual_root)
    os.environ[TOKEN_ENV] = "dummy"
    cache_listing_day_intraday.data_fetcher.fetch_recent_ipos = _fake_fetch_recent_ipos
    cache_listing_day_intraday.data_fetcher.fetch_intraday_trends = _fake_fetch_intraday_trends
    tushare_helper._call_tushare_api = _fake_call_tushare_api

    summary = cache_listing_day_intraday.run_latest_missing_cache_job(
        months=18,
        output_dir=output_dir,
        params=params,
        progress_callback=lambda event, payload: events.append(event),
        current_datetime=_today_at(16, 0),
    )

    csv_path = output_dir / "920191.csv"
    content = csv_path.read_text(encoding="utf-8-sig").splitlines()

    _assert(summary["cached_count"] == 1, "manual_file_fallback: expected one cached csv", failures)
    _assert(summary["cached"][0]["source_api"] == "manual_file", "manual_file_fallback: expected manual file source", failures)
    _assert(summary["cached"][0]["attempted_files"] == ["920191.csv"], "manual_file_fallback: expected attempted manual csv", failures)
    _assert(
        [item["source"] for item in summary["cached"][0]["source_failures"]] == ["Tushare", "东方财富"],
        "manual_file_fallback: expected both online source failures to be recorded",
        failures,
    )
    _assert(
        events == ["start", "scan_completed", "checking", "source_failed", "source_failed", "cached", "finished"],
        f"manual_file_fallback: unexpected event sequence {events}",
        failures,
    )
    _assert(
        content[:3]
        == [
            "DateTime,open,high,low,close,volume,amount",
            f"{today.replace('-', '/')} 09:30,15.0,15.8,14.9,15.5,100000.0,1230000.0",
            f"{today.replace('-', '/')} 09:31,16.2,16.8,16.0,16.4,150000.0,2460000.0",
        ],
        "manual_file_fallback: csv content/order/unit conversion mismatch",
        failures,
    )
    print("OK manual_file_fallback: local CSV was parsed after Tushare and Eastmoney failures")


def _run_retry_deferred_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "retry_deferred" / "cache_db"
    output_dir = TEMP_ROOT / "retry_deferred" / "csv"
    today = cache_listing_day_intraday.date.today().isoformat()

    def _fake_call_tushare_api(
        api_name: str,
        params: dict[str, Any],
        fields: str,
        settings: dict[str, Any],
        db: Any,
    ) -> tuple[list[dict[str, Any]], str]:
        return [], "Tushare 接口 stk_mins 调用失败: 抱歉，您每天最多访问该接口2次。"

    def _fake_fetch_recent_ipos(months: int = 3, page_size: int = 50, require_close_price: bool = True) -> list[dict[str, Any]]:
        return [
            {"SECURITY_CODE": "920191", "SECURITY_NAME_ABBR": "瑞尔竞达", "LISTING_DATE": today},
            {"SECURITY_CODE": "920177", "SECURITY_NAME_ABBR": "恒道科技", "LISTING_DATE": "2026-04-16"},
            {"SECURITY_CODE": "920188", "SECURITY_NAME_ABBR": "已有缓存样本", "LISTING_DATE": "2026-04-13"},
        ]

    def _fake_fetch_intraday_trends(code: str, trade_date: str | Any = None) -> list[dict[str, Any]]:
        raise cache_listing_day_intraday.data_fetcher.DataFetcherError(
            f"东方财富返回的 {code} 上市首日分钟串存在 open=0，数据不精确，已取消缓存，留待下次执行补缓存程序再取"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "920191.csv").write_text("DateTime,open,high,low,close,volume,amount\n", encoding="utf-8-sig")
    (output_dir / "920188.csv").write_text("DateTime,open,high,low,close,volume,amount\n", encoding="utf-8-sig")
    cache_root.mkdir(parents=True, exist_ok=True)
    (cache_root / "deferred_intraday_codes.json").write_text('{"codes": ["920177"]}', encoding="utf-8")

    os.environ[TOKEN_ENV] = "dummy"
    cache_listing_day_intraday.data_fetcher.fetch_recent_ipos = _fake_fetch_recent_ipos
    cache_listing_day_intraday.data_fetcher.fetch_intraday_trends = _fake_fetch_intraday_trends
    tushare_helper._call_tushare_api = _fake_call_tushare_api

    summary = cache_listing_day_intraday.run_latest_missing_cache_job(
        months=18,
        output_dir=output_dir,
        params=_make_params(cache_root),
        current_datetime=_today_at(16, 0),
    )

    _assert(summary["checked_codes"] == ["920177", "920191"], "retry_deferred: expected deferred code retried before chronological scan", failures)
    _assert(summary["deferred_count"] == 1, "retry_deferred: expected one deferred code", failures)
    _assert(summary["pending_deferred_after"] == ["920177"], "retry_deferred: expected deferred code to remain pending", failures)
    _assert(summary["stop_at_existing"]["code"] == "920191", "retry_deferred: expected stop at first existing cache in chronological scan", failures)
    print("OK retry_deferred: pending deferred codes were retried first, then scan stopped at the first existing cache")


def _run_error_retry_persistence_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "error_retry_persistence" / "cache_db"
    output_dir = TEMP_ROOT / "error_retry_persistence" / "csv"
    today = cache_listing_day_intraday.date.today().isoformat()

    scans = [
        [
            {"SECURITY_CODE": "920177", "SECURITY_NAME_ABBR": "恒道科技", "LISTING_DATE": "2026-04-19"},
            {"SECURITY_CODE": "920188", "SECURITY_NAME_ABBR": "已有缓存样本", "LISTING_DATE": "2026-04-18"},
        ],
        [
            {"SECURITY_CODE": "920191", "SECURITY_NAME_ABBR": "新近已有缓存", "LISTING_DATE": today},
            {"SECURITY_CODE": "920177", "SECURITY_NAME_ABBR": "恒道科技", "LISTING_DATE": "2026-04-19"},
            {"SECURITY_CODE": "920188", "SECURITY_NAME_ABBR": "已有缓存样本", "LISTING_DATE": "2026-04-18"},
        ],
    ]

    def _fake_call_tushare_api(
        api_name: str,
        params: dict[str, Any],
        fields: str,
        settings: dict[str, Any],
        db: Any,
    ) -> tuple[list[dict[str, Any]], str]:
        return [], "Tushare 接口 stk_mins 调用失败: 抱歉，您每天最多访问该接口2次。"

    def _fake_fetch_recent_ipos(months: int = 3, page_size: int = 50, require_close_price: bool = True) -> list[dict[str, Any]]:
        return scans.pop(0)

    def _fake_fetch_intraday_trends(code: str, trade_date: str | Any = None) -> list[dict[str, Any]]:
        raise cache_listing_day_intraday.data_fetcher.DataFetcherError(
            f"东方财富分钟线暂不可用：{code}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "920188.csv").write_text("DateTime,open,high,low,close,volume,amount\n", encoding="utf-8-sig")

    os.environ[TOKEN_ENV] = "dummy"
    cache_listing_day_intraday.data_fetcher.fetch_recent_ipos = _fake_fetch_recent_ipos
    cache_listing_day_intraday.data_fetcher.fetch_intraday_trends = _fake_fetch_intraday_trends
    tushare_helper._call_tushare_api = _fake_call_tushare_api

    first_summary = cache_listing_day_intraday.run_latest_missing_cache_job(
        months=18,
        output_dir=output_dir,
        params=_make_params(cache_root),
        current_datetime=_today_at(16, 0),
    )

    _assert(first_summary["error_count"] == 1, "error_retry_persistence: expected one failed code on first run", failures)
    _assert(first_summary["errors"][0]["code"] == "920177", "error_retry_persistence: expected 920177 error on first run", failures)
    _assert(first_summary["errors"][0].get("retry_pending") is True, "error_retry_persistence: expected retry_pending flag", failures)
    _assert(first_summary["pending_deferred_after"] == ["920177"], "error_retry_persistence: expected failed code to stay pending after first run", failures)
    _assert(first_summary["stop_at_existing"]["code"] == "920188", "error_retry_persistence: expected stop at existing 920188 on first run", failures)

    (output_dir / "920191.csv").write_text("DateTime,open,high,low,close,volume,amount\n", encoding="utf-8-sig")
    second_summary = cache_listing_day_intraday.run_latest_missing_cache_job(
        months=18,
        output_dir=output_dir,
        params=_make_params(cache_root),
        current_datetime=_today_at(16, 0),
    )

    _assert(second_summary["pending_deferred_before"] == ["920177"], "error_retry_persistence: expected pending marker on second run", failures)
    _assert(second_summary["checked_codes"] == ["920177", "920191"], "error_retry_persistence: expected deferred code retried before chronological scan", failures)
    _assert(second_summary["error_count"] == 1, "error_retry_persistence: expected one failed code on second run", failures)
    _assert(second_summary["errors"][0]["code"] == "920177", "error_retry_persistence: expected 920177 error on second run", failures)
    _assert(second_summary["pending_deferred_after"] == ["920177"], "error_retry_persistence: expected failed code to remain pending after second run", failures)
    _assert(second_summary["stop_at_existing"]["code"] == "920191", "error_retry_persistence: expected stop at first existing cache on second run", failures)
    print("OK error_retry_persistence: failed uncached codes stayed pending and were retried before the normal stop boundary")


def _run_progress_callback_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "progress_callback" / "cache_db"
    output_dir = TEMP_ROOT / "progress_callback" / "csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "920188.csv").write_text("DateTime,open,high,low,close,volume,amount\n", encoding="utf-8-sig")
    today = cache_listing_day_intraday.date.today().isoformat()
    call_log: list[tuple[str, str]] = []
    events: list[str] = []

    cache_listing_day_intraday.data_fetcher.fetch_recent_ipos = lambda months=3, page_size=50, require_close_price=True: [
        {"SECURITY_CODE": "920191", "SECURITY_NAME_ABBR": "瑞尔竞达", "LISTING_DATE": today},
        {"SECURITY_CODE": "920188", "SECURITY_NAME_ABBR": "已有缓存样本", "LISTING_DATE": "2026-04-13"},
    ]
    os.environ[TOKEN_ENV] = "dummy"
    tushare_helper._call_tushare_api = _build_fake_call(call_log)

    summary = cache_listing_day_intraday.run_latest_missing_cache_job(
        months=18,
        output_dir=output_dir,
        params=_make_params(cache_root),
        progress_callback=lambda event, payload: events.append(event),
        current_datetime=_today_at(16, 0),
    )

    _assert(summary["cached_count"] == 1, "progress_callback: expected one cached csv", failures)
    _assert(
        events == ["start", "scan_completed", "checking", "cached", "checking", "existing", "stop_at_existing", "finished"],
        f"progress_callback: unexpected event sequence {events}",
        failures,
    )
    print("OK progress_callback: latest cache job emitted real-time progress events in sequence")


def _run_scan_error_summary_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "scan_error_summary" / "cache_db"
    output_dir = TEMP_ROOT / "scan_error_summary" / "csv"
    events: list[tuple[str, dict[str, Any]]] = []

    def _raise_scan_error(*args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        raise cache_listing_day_intraday.data_fetcher.DataFetcherError("东方财富请求失败: fixture SSL EOF")

    cache_listing_day_intraday.data_fetcher.fetch_recent_ipos = _raise_scan_error
    summary = cache_listing_day_intraday.run_latest_missing_cache_job(
        months=18,
        output_dir=output_dir,
        params=_make_params(cache_root),
        progress_callback=lambda event, payload: events.append((event, dict(payload))),
        current_datetime=_today_at(16, 0),
    )

    _assert(summary["error_count"] == 1, "scan_error_summary: expected one global error", failures)
    _assert(summary["scan_error_count"] == 1, "scan_error_summary: expected one scan error", failures)
    _assert(summary["errors"] == [], "scan_error_summary: scan error should not become empty-code item", failures)
    _assert(
        summary["scan_errors"][0]["reason"] == "东方财富请求失败: fixture SSL EOF",
        "scan_error_summary: reason mismatch",
        failures,
    )
    _assert(
        [event for event, _ in events] == ["start", "scan_error"],
        f"scan_error_summary: unexpected events {events}",
        failures,
    )
    print("OK scan_error_summary: scan-level Eastmoney failures no longer render as blank stock errors")


def _run_eastmoney_ssl_error_wrapped_case(failures: list[str]) -> None:
    def _raise_ssl_error(*args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        raise ssl.SSLError("fixture bad record mac")

    previous_fetch_intraday_trends = cache_listing_day_intraday.data_fetcher.fetch_intraday_trends
    cache_listing_day_intraday.data_fetcher.fetch_intraday_trends = _ORIGINAL_FETCH_INTRADAY_TRENDS
    cache_listing_day_intraday.data_fetcher.urllib.request.urlopen = _raise_ssl_error
    try:
        try:
            cache_listing_day_intraday.data_fetcher.fetch_intraday_trends("920200", trade_date=cache_listing_day_intraday.date.today())
        except cache_listing_day_intraday.data_fetcher.DataFetcherError as exc:
            _assert("东方财富网络请求异常" in str(exc), "eastmoney_ssl: expected wrapped network error", failures)
        except ssl.SSLError:
            failures.append("eastmoney_ssl: raw ssl.SSLError should be wrapped as DataFetcherError")
        else:
            failures.append("eastmoney_ssl: expected DataFetcherError")
    finally:
        cache_listing_day_intraday.data_fetcher.fetch_intraday_trends = previous_fetch_intraday_trends
        cache_listing_day_intraday.data_fetcher.urllib.request.urlopen = _ORIGINAL_URL_OPEN
    print("OK eastmoney_ssl: raw SSL errors are wrapped as DataFetcherError")


def main() -> int:
    failures: list[str] = []
    if TEMP_ROOT.exists():
        shutil.rmtree(TEMP_ROOT)

    try:
        _run_today_intraday_guard_case(failures)
        _run_today_scan_fallback_case(failures)
        _run_history_fetch_case(failures)
        _run_latest_until_cached_case(failures)
        _run_eastmoney_fallback_case(failures)
        _run_existing_cache_normalization_case(failures)
        _run_eastmoney_imprecise_case(failures)
        _run_manual_file_fallback_case(failures)
        _run_retry_deferred_case(failures)
        _run_error_retry_persistence_case(failures)
        _run_progress_callback_case(failures)
        _run_scan_error_summary_case(failures)
        _run_eastmoney_ssl_error_wrapped_case(failures)
    finally:
        tushare_helper._call_tushare_api = _ORIGINAL_CALL_TUSHARE_API
        cache_listing_day_intraday.data_fetcher.fetch_recent_ipos = _ORIGINAL_FETCH_RECENT_IPOS
        cache_listing_day_intraday.data_fetcher.fetch_intraday_trends = _ORIGINAL_FETCH_INTRADAY_TRENDS
        cache_listing_day_intraday.data_fetcher.urllib.request.urlopen = _ORIGINAL_URL_OPEN
        os.environ.pop(TOKEN_ENV, None)

    if failures:
        print("\nIntraday cache validation failed:")
        for item in failures:
            print(f"- {item}")
        return 1

    print("\nIntraday cache validation passed: 11 cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
