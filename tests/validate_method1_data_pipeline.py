from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path
import sys
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import data_fetcher
from local_file_db import LocalFileDB
import wind_helper


TEMP_ROOT = ROOT_DIR / "data" / "temp_validation" / "method1_pipeline_validation"

_ORIGINAL_FETCH_EQUITY_SNAPSHOT = data_fetcher.fetch_equity_snapshot
_ORIGINAL_ENSURE_WIND_CLIENT = wind_helper._ensure_wind_client
_ORIGINAL_WSQ_BATCH = wind_helper._wsq_batch
_ORIGINAL_WSS_BATCH = wind_helper._wss_batch


class _FakeWindClient:
    def stop(self) -> None:
        return None

    def close(self) -> None:
        return None


def _reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _make_params(cache_root: Path, **overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "wind_cache_root": str(cache_root),
        "wind_daily_request_quota": 20,
        "wind_batch_size": 20,
        "wind_static_ttl_days": 3650,
        "wind_dynamic_ttl_hours": 24,
        "wind_request_pause_seconds": 0.0,
        "eastmoney_backup_enabled": 1,
        "eastmoney_validation_enabled": 1,
    }
    params.update(overrides)
    return params


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _seed_wind_snapshot(
    db: LocalFileDB,
    code: str,
    *,
    name: str,
    close: float,
    eps_ttm: float,
    total_shares: float,
    float_a_shares: float,
    updated_at: str,
    pe_ttm_direct: float | None = None,
    pb_lf_direct: float | None = None,
    mkt_cap_direct: float | None = None,
) -> None:
    db.save_fixed_record(code, {"name": name}, source="wind_api")
    db.save_variable_record(
        code,
        {
            "wind_snapshot": {
                "close": close,
                "eps_ttm": eps_ttm,
                "total_shares": total_shares,
                "float_a_shares": float_a_shares,
                "pe_ttm_direct": pe_ttm_direct,
                "pb_lf_direct": pb_lf_direct,
                "mkt_cap_direct": mkt_cap_direct,
                "trade_date": datetime.now().date().isoformat(),
                "updated_at": updated_at,
                "source": "wind_api",
                "price_basis": "rt_last",
            }
        },
        trade_date=datetime.now().date().isoformat(),
        source="wind_api",
    )


def _fake_fetch_equity_snapshot(code: str) -> dict[str, Any]:
    return {
        "code": code,
        "name": {
            "301581.SZ": "黄山谷捷",
            "688103.SH": "国力电子",
            "603344.SH": "星德胜",
            "002892.SZ": "科力尔",
        }.get(code, code),
        "close": {
            "301581.SZ": 45.49,
            "688103.SH": 56.28,
            "603344.SH": 26.35,
            "002892.SZ": 14.62,
        }.get(code, 20.0),
        "pe_ttm": {
            "301581.SZ": 50.8,
            "688103.SH": 75.97,
            "603344.SH": 31.2,
            "002892.SZ": 22.4,
        }.get(code, 25.0),
        "eps_ttm": {
            "301581.SZ": 0.895427015,
            "688103.SH": 0.7408,
            "603344.SH": 0.8445,
            "002892.SZ": 0.6527,
        }.get(code, 0.8),
        "pb_lf": {
            "301581.SZ": 3.62,
            "688103.SH": 4.75,
            "603344.SH": 3.10,
            "002892.SZ": 2.25,
        }.get(code, 2.5),
        "mkt_cap": {
            "301581.SZ": 36.392,
            "688103.SH": 53.64,
            "603344.SH": 61.3,
            "002892.SZ": 58.8,
        }.get(code, 50.0),
        "float_mkt_cap": {
            "301581.SZ": 22.2346022,
            "688103.SH": 36.12,
            "603344.SH": 40.7,
            "002892.SZ": 33.4,
        }.get(code, 30.0),
        "total_shares": {
            "301581.SZ": 80000000.0,
            "688103.SH": 95390000.0,
            "603344.SH": 232800000.0,
            "002892.SZ": 402000000.0,
        }.get(code, 100000000.0),
        "trade_date": datetime.now().date().isoformat(),
        "price_basis": "quote_last",
        "source": "eastmoney_api",
        "raw_fields": {},
    }


def _fake_ensure_wind_client(cache_root: Path) -> tuple[Any, str]:
    _ = cache_root
    return _FakeWindClient(), ""


def _fake_wsq_batch(
    wind_client: Any,
    db: LocalFileDB,
    codes: list[str],
    field_specs: tuple[tuple[str, str], ...],
    request_kind: str,
) -> tuple[dict[str, dict[str, Any]], str]:
    _ = wind_client
    db.append_request_event(
        {
            "event_type": "api_call",
            "source": "wind",
            "request_kind": request_kind,
            "codes": list(codes),
            "fields": [field_name for _, field_name in field_specs],
            "error_code": 0,
        }
    )
    rows: dict[str, dict[str, Any]] = {}
    for code in codes:
        rows[code] = {
            "close": 56.2,
            "pre_close": 55.8,
            "open": 56.0,
        }
    return rows, ""


def _fake_wss_batch(
    wind_client: Any,
    db: LocalFileDB,
    codes: list[str],
    field_specs: tuple[tuple[str, str], ...],
    request_kind: str,
) -> tuple[dict[str, dict[str, Any]], str]:
    _ = wind_client
    db.append_request_event(
        {
            "event_type": "api_call",
            "source": "wind",
            "request_kind": request_kind,
            "codes": list(codes),
            "fields": [field_name for _, field_name in field_specs],
            "error_code": 0,
        }
    )
    rows: dict[str, dict[str, Any]] = {}
    for code in codes:
        if request_kind == "comparable_wind_direct":
            rows[code] = {
                "pe_ttm_direct": 76.4,
                "pb_lf_direct": 4.72,
                "mkt_cap_direct": 53.58,
            }
        elif request_kind == "comparable_wind_raw":
            rows[code] = {
                "eps_ttm": 0.74,
                "total_shares": 95390000.0,
                "float_a_shares": 64200000.0,
                "free_float_shares": 0.0,
            }
        elif request_kind == "comparable_wind_fixed":
            rows[code] = {
                "name": "国力电子",
            }
        else:
            rows[code] = {}
    return rows, ""


def _run_disabled_backup_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "disabled_backup"
    _reset_dir(cache_root)
    result = wind_helper.get_comparable_valuations(
        ["301581.SZ"],
        channel="disabled",
        params=_make_params(cache_root),
    )
    summary = result["summary"]
    item = result["items"][0]
    _assert(len(result["items"]) == 1, "disabled_backup: expected one returned item", failures)
    _assert(item["code"] == "301581.SZ", "disabled_backup: wrong returned code", failures)
    _assert(item["pe_source"] == "eastmoney", "disabled_backup: expected eastmoney PE source", failures)
    _assert(summary["eastmoney_api_calls"] == 1, "disabled_backup: expected one eastmoney fetch", failures)
    _assert(summary["returned_codes"] == ["301581.SZ"], "disabled_backup: wrong returned code summary", failures)
    _assert(
        summary["reason"] == "Wind 当前处于禁用状态，已使用东方财富可比快照。",
        "disabled_backup: unexpected summary reason",
        failures,
    )
    print("OK disabled_backup: eastmoney fallback returned 301581.SZ")


def _run_fresh_cache_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "fresh_cache"
    _reset_dir(cache_root)
    db = LocalFileDB(cache_root)
    _seed_wind_snapshot(
        db,
        "603344.SH",
        name="星德胜",
        close=26.35,
        eps_ttm=0.8445,
        total_shares=232800000.0,
        float_a_shares=154400000.0,
        updated_at=_now_iso(),
        pb_lf_direct=3.10,
    )
    result = wind_helper.get_comparable_valuations(
        ["603344.SH"],
        channel="auto",
        params=_make_params(cache_root),
    )
    summary = result["summary"]
    item = result["items"][0]
    expected_pe = 26.35 / 0.8445
    expected_mkt_cap = 26.35 * 232800000.0 / 100000000
    _assert(abs(item["pe_ttm"] - expected_pe) < 1e-6, "fresh_cache: local PE compute mismatch", failures)
    _assert(abs(item["mkt_cap"] - expected_mkt_cap) < 1e-6, "fresh_cache: local mkt cap mismatch", failures)
    _assert(item["pe_source"] == "wind_local_compute", "fresh_cache: expected wind_local_compute source", failures)
    _assert(summary["api_calls"] == 0, "fresh_cache: expected zero Wind API calls", failures)
    _assert(summary["eastmoney_api_calls"] == 0, "fresh_cache: expected zero eastmoney API calls", failures)
    _assert("603344.SH" in summary["variable_cache_hits"], "fresh_cache: missing variable cache hit", failures)
    _assert("603344.SH" in summary["local_computed_codes"], "fresh_cache: missing local computed code", failures)
    print("OK fresh_cache: local Wind snapshot reused and derived metrics computed")


def _run_refresh_cross_validate_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "refresh_cross_validate"
    _reset_dir(cache_root)
    result = wind_helper.get_comparable_valuations(
        ["688103.SH"],
        channel="auto",
        params=_make_params(cache_root),
    )
    summary = result["summary"]
    item = result["items"][0]
    db = LocalFileDB(cache_root)
    _assert(summary["api_calls"] == 4, "refresh_cross_validate: expected four Wind API calls", failures)
    _assert("688103.SH" in summary["api_fetched_fixed"], "refresh_cross_validate: fixed fields not refreshed", failures)
    _assert("688103.SH" in summary["api_fetched_variable"], "refresh_cross_validate: variable fields not refreshed", failures)
    _assert("688103.SH" in summary["cross_validated_codes"], "refresh_cross_validate: cross validation missing", failures)
    _assert(summary["eastmoney_api_calls"] == 1, "refresh_cross_validate: expected one eastmoney validation fetch", failures)
    _assert("688103.SH" in summary["local_computed_codes"], "refresh_cross_validate: missing local computed code", failures)
    _assert(item["pe_source"] == "wind_local_compute", "refresh_cross_validate: expected local Wind PE source", failures)
    _assert(db.get_today_api_call_count() == 4, "refresh_cross_validate: request log count mismatch", failures)
    saved_variable = db.load_variable_record("688103.SH") or {}
    _assert(
        "cross_validation" in (saved_variable.get("fields") or {}),
        "refresh_cross_validate: missing persisted cross_validation",
        failures,
    )
    print("OK refresh_cross_validate: Wind refresh, local compute and cross validation all worked")


def _run_quota_stale_case(failures: list[str]) -> None:
    cache_root = TEMP_ROOT / "quota_stale"
    _reset_dir(cache_root)
    db = LocalFileDB(cache_root)
    stale_time = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    _seed_wind_snapshot(
        db,
        "002892.SZ",
        name="科力尔",
        close=14.62,
        eps_ttm=0.6527,
        total_shares=402000000.0,
        float_a_shares=228000000.0,
        updated_at=stale_time,
        pb_lf_direct=2.25,
    )
    for _ in range(20):
        db.append_request_event(
            {
                "event_type": "api_call",
                "source": "wind",
                "request_kind": "quota_seed",
                "codes": ["002892.SZ"],
                "fields": ["seed"],
                "error_code": 0,
            }
        )

    result = wind_helper.get_comparable_valuations(
        ["002892.SZ"],
        channel="auto",
        params=_make_params(cache_root),
    )
    summary = result["summary"]
    item = result["items"][0]
    _assert(summary["api_calls"] == 0, "quota_stale: expected zero API calls after quota exhaustion", failures)
    _assert("002892.SZ" in summary["skipped_due_quota"], "quota_stale: expected skipped_due_quota mark", failures)
    _assert("002892.SZ" in summary["stale_variable_used"], "quota_stale: expected stale variable mark", failures)
    _assert(summary["quota_used_today"] == 20, "quota_stale: quota used count mismatch", failures)
    _assert(item["is_stale"] is True, "quota_stale: expected stale item flag", failures)
    print("OK quota_stale: quota exhaustion preserved stale cache and flagged it")


def main() -> int:
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    data_fetcher.fetch_equity_snapshot = _fake_fetch_equity_snapshot
    wind_helper._ensure_wind_client = _fake_ensure_wind_client
    wind_helper._wsq_batch = _fake_wsq_batch
    wind_helper._wss_batch = _fake_wss_batch
    try:
        _run_disabled_backup_case(failures)
        _run_fresh_cache_case(failures)
        _run_refresh_cross_validate_case(failures)
        _run_quota_stale_case(failures)
    finally:
        data_fetcher.fetch_equity_snapshot = _ORIGINAL_FETCH_EQUITY_SNAPSHOT
        wind_helper._ensure_wind_client = _ORIGINAL_ENSURE_WIND_CLIENT
        wind_helper._wsq_batch = _ORIGINAL_WSQ_BATCH
        wind_helper._wss_batch = _ORIGINAL_WSS_BATCH

    if failures:
        print("\nMethod1 data pipeline validation failed:")
        for item in failures:
            print(f"- {item}")
        return 1

    print("\nMethod1 data pipeline validation passed: 4 cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
