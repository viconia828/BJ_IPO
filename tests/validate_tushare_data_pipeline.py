from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import data_fetcher
from local_file_db import LocalFileDB
import tushare_helper


TEMP_ROOT = ROOT_DIR / "data" / "temp_validation" / "tushare_pipeline_validation"
TOKEN_ENV = "TUSHARE_TOKEN"

_ORIGINAL_FETCH_EQUITY_SNAPSHOT = data_fetcher.fetch_equity_snapshot
_ORIGINAL_CALL_TUSHARE_API = tushare_helper._call_tushare_api


def _reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _make_params(cache_root: Path, **overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "tushare_token_env": TOKEN_ENV,
        "tushare_cache_root": str(cache_root),
        "tushare_daily_request_quota": 50,
        "tushare_request_pause_seconds": 0.0,
        "tushare_static_ttl_days": 3650,
        "tushare_dynamic_ttl_hours": 24,
        "tushare_recent_trade_days": 12,
        "eastmoney_backup_enabled": 1,
        "eastmoney_validation_enabled": 1,
    }
    params.update(overrides)
    return params


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _seed_tushare_snapshot(
    db: LocalFileDB,
    code: str,
    *,
    name: str,
    close: float,
    pe_ttm: float,
    pb_lf: float,
    mkt_cap: float,
    updated_at: str,
) -> None:
    db.save_fixed_record(code, {"name": name}, source="tushare_api")
    db.save_variable_record(
        code,
        {
            "tushare_snapshot": {
                "close": close,
                "pe_ttm": pe_ttm,
                "pb_lf": pb_lf,
                "mkt_cap": mkt_cap,
                "trade_date": datetime.now().date().strftime("%Y%m%d"),
                "updated_at": updated_at,
                "source": "tushare_api",
                "price_basis": "t-1_close",
            }
        },
        trade_date=datetime.now().date().strftime("%Y%m%d"),
        source="tushare_api",
    )


def _fake_fetch_equity_snapshot(code: str) -> dict[str, Any]:
    return {
        "code": code,
        "name": {"301029.SZ": "怡合达", "688097.SH": "博众精工"}.get(code, code),
        "close": {"301029.SZ": 22.41, "688097.SH": 29.18}.get(code, 18.0),
        "pe_ttm": {"301029.SZ": 31.5, "688097.SH": 36.2}.get(code, 20.0),
        "eps_ttm": {"301029.SZ": 0.7114, "688097.SH": 0.8061}.get(code, 0.9),
        "pb_lf": {"301029.SZ": 3.8, "688097.SH": 4.1}.get(code, 2.0),
        "mkt_cap": {"301029.SZ": 145.2, "688097.SH": 208.4}.get(code, 80.0),
        "float_mkt_cap": {"301029.SZ": 88.6, "688097.SH": 126.4}.get(code, 50.0),
        "total_shares": {"301029.SZ": 648000000.0, "688097.SH": 714000000.0}.get(code, 100000000.0),
        "trade_date": datetime.now().date().isoformat(),
        "price_basis": "quote_last",
        "source": "eastmoney_api",
        "raw_fields": {},
    }


def _fake_call_tushare_api(
    api_name: str,
    params: dict[str, Any],
    fields: str,
    settings: dict[str, Any],
    db: LocalFileDB,
) -> tuple[list[dict[str, Any]], str]:
    db.append_request_event(
        {
            "event_type": "api_call",
            "source": "tushare",
            "request_kind": api_name,
            "codes": [params.get("ts_code")] if params.get("ts_code") else [],
            "fields": fields.split(","),
            "error_code": 0,
        }
    )
    code = str(params.get("ts_code") or "")
    if api_name == "stock_basic":
        return [{"ts_code": code, "name": {"688097.SH": "博众精工"}.get(code, code)}], ""
    if api_name == "daily_basic":
        return [
            {
                "ts_code": code,
                "trade_date": datetime.now().date().strftime("%Y%m%d"),
                "close": 29.18,
                "pe_ttm": 36.2,
                "pb": 4.1,
                "total_share": 71400.0,
                "float_share": 43300.0,
                "free_share": 0.0,
                "total_mv": 2084000.0,
                "circ_mv": 1264000.0,
            }
        ], ""
    return [], ""


def _run_fresh_cache_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "fresh_cache"
    _reset_dir(cache_root)
    db = LocalFileDB(cache_root)
    _seed_tushare_snapshot(
        db,
        "301029.SZ",
        name="怡合达",
        close=22.41,
        pe_ttm=31.5,
        pb_lf=3.8,
        mkt_cap=145.2,
        updated_at=_now_iso(),
    )
    os.environ[TOKEN_ENV] = "dummy"
    result = tushare_helper.get_comparable_valuations(["301029.SZ"], params=_make_params(cache_root))
    summary = result["summary"]
    item = result["items"][0]
    _assert(item["pe_source"] == "tushare", "fresh_cache: expected tushare PE source", failures)
    _assert(summary["api_calls"] == 0, "fresh_cache: expected zero tushare api calls", failures)
    _assert(summary["eastmoney_api_calls"] == 0, "fresh_cache: expected zero eastmoney api calls", failures)
    _assert("301029.SZ" in summary["variable_cache_hits"], "fresh_cache: expected cache hit", failures)
    print("OK fresh_cache: reused local Tushare snapshot")


def _run_refresh_validation_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "refresh_validation"
    _reset_dir(cache_root)
    os.environ[TOKEN_ENV] = "dummy"
    result = tushare_helper.get_comparable_valuations(["688097.SH"], params=_make_params(cache_root))
    summary = result["summary"]
    item = result["items"][0]
    db = LocalFileDB(cache_root)
    _assert(summary["api_calls"] == 2, "refresh_validation: expected two tushare api calls", failures)
    _assert("688097.SH" in summary["api_fetched_fixed"], "refresh_validation: missing fixed refresh", failures)
    _assert("688097.SH" in summary["api_fetched_variable"], "refresh_validation: missing variable refresh", failures)
    _assert(summary["eastmoney_api_calls"] == 1, "refresh_validation: expected one eastmoney validation call", failures)
    _assert("688097.SH" in summary["cross_validated_codes"], "refresh_validation: expected cross validation", failures)
    _assert(item["pe_source"] == "tushare", "refresh_validation: expected tushare PE source", failures)
    _assert(db.get_today_api_call_count(source="tushare") == 2, "refresh_validation: request log count mismatch", failures)
    print("OK refresh_validation: Tushare refresh and eastmoney validation worked")


def _run_missing_token_backup_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "missing_token_backup"
    _reset_dir(cache_root)
    os.environ.pop(TOKEN_ENV, None)
    result = tushare_helper.get_comparable_valuations(["301029.SZ"], params=_make_params(cache_root))
    summary = result["summary"]
    item = result["items"][0]
    _assert(item["pe_source"] == "eastmoney", "missing_token_backup: expected eastmoney fallback", failures)
    _assert(summary["api_calls"] == 0, "missing_token_backup: expected zero tushare api calls", failures)
    _assert(summary["eastmoney_api_calls"] == 1, "missing_token_backup: expected one eastmoney fetch", failures)
    _assert("token" in str(summary["reason"]).lower(), "missing_token_backup: expected missing token reason", failures)
    print("OK missing_token_backup: missing token fell back to eastmoney")


def _run_unsupported_code_backup_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "unsupported_code_backup"
    _reset_dir(cache_root)
    os.environ[TOKEN_ENV] = "dummy"
    result = tushare_helper.get_comparable_valuations(["874616.NQ"], params=_make_params(cache_root))
    summary = result["summary"]
    item = result["items"][0]
    _assert(item["code"] == "874616.NQ", "unsupported_code_backup: expected returned NQ code", failures)
    _assert(item["pe_source"] == "eastmoney", "unsupported_code_backup: expected eastmoney fallback", failures)
    _assert(summary["api_calls"] == 0, "unsupported_code_backup: expected zero tushare api calls", failures)
    _assert(summary["eastmoney_api_calls"] == 1, "unsupported_code_backup: expected one eastmoney fetch", failures)
    _assert("874616.NQ" in summary["skipped_unsupported"], "unsupported_code_backup: expected unsupported marker", failures)
    print("OK unsupported_code_backup: unsupported market skipped Tushare and used eastmoney")



def _run_quota_bucket_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / 'quota_bucket'
    _reset_dir(cache_root)
    db = LocalFileDB(cache_root)
    for _ in range(50):
        db.append_request_event(
            {
                'event_type': 'api_call',
                'source': 'tushare',
                'request_kind': 'stk_mins',
                'codes': ['920191.BJ'],
                'fields': [],
                'error_code': 0,
            }
        )
    for _ in range(200):
        db.append_request_event(
            {
                'event_type': 'api_call',
                'source': 'tushare',
                'request_kind': 'daily_basic',
                'codes': ['688097.SH'],
                'fields': [],
                'error_code': 0,
            }
        )
    settings = tushare_helper._build_settings(
        _make_params(
            cache_root,
            tushare_intraday_request_quota=50,
            tushare_non_intraday_daily_request_quota=50000,
        )
    )
    intraday_used = tushare_helper._get_today_api_call_count_for_api(db, 'stk_mins')
    non_intraday_used = tushare_helper._get_today_api_call_count_for_api(db, 'daily')
    _assert(intraday_used == 50, 'quota_bucket: expected minute APIs to keep separate quota count', failures)
    _assert(non_intraday_used == 200, 'quota_bucket: expected non-intraday APIs to keep separate quota count', failures)
    _assert(
        tushare_helper._quota_exhausted(intraday_used, tushare_helper._quota_limit_for_api('stk_mins', settings)),
        'quota_bucket: expected intraday quota to remain conservative',
        failures,
    )
    _assert(
        not tushare_helper._quota_exhausted(non_intraday_used, tushare_helper._quota_limit_for_api('daily', settings)),
        'quota_bucket: expected daily close APIs not to be blocked by legacy 200 cap',
        failures,
    )
    print('OK quota_bucket: minute and non-intraday Tushare quotas are counted separately')

def main() -> int:
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    data_fetcher.fetch_equity_snapshot = _fake_fetch_equity_snapshot
    tushare_helper._call_tushare_api = _fake_call_tushare_api
    try:
        _run_fresh_cache_case(failures)
        _run_refresh_validation_case(failures)
        _run_quota_bucket_case(failures)
        _run_missing_token_backup_case(failures)
        _run_unsupported_code_backup_case(failures)
    finally:
        data_fetcher.fetch_equity_snapshot = _ORIGINAL_FETCH_EQUITY_SNAPSHOT
        tushare_helper._call_tushare_api = _ORIGINAL_CALL_TUSHARE_API
        os.environ.pop(TOKEN_ENV, None)

    if failures:
        print("\nTushare data pipeline validation failed:")
        for item in failures:
            print(f"- {item}")
        return 1

    print("\nTushare data pipeline validation passed: 5 cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
