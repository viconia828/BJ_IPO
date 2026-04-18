from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import data_fetcher
from local_file_db import LocalFileDB


DEFAULT_API_URL = "https://api.tushare.pro"
DEFAULT_CACHE_ROOT = Path("data") / "tushare_db"
SUPPORTED_TUSHARE_SUFFIXES = {"SH", "SZ", "BJ"}


def _now() -> datetime:
    return datetime.now()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _normalize_code(code: str) -> str:
    return LocalFileDB.normalize_code(code)


def _dedupe_codes(codes: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized_codes: list[str] = []
    for code in codes:
        normalized_code = _normalize_code(code)
        if not normalized_code or normalized_code in seen:
            continue
        seen.add(normalized_code)
        normalized_codes.append(normalized_code)
    return normalized_codes


def _supports_tushare_code(code: str) -> bool:
    normalized_code = _normalize_code(code)
    if not normalized_code:
        return False

    if "." in normalized_code:
        _, suffix = normalized_code.rsplit(".", 1)
        return suffix in SUPPORTED_TUSHARE_SUFFIXES

    if not normalized_code.isdigit():
        return False

    return normalized_code.startswith(
        (
            "000",
            "001",
            "002",
            "003",
            "300",
            "301",
            "600",
            "601",
            "603",
            "605",
            "688",
            "920",
        )
    )


def _parse_timestamp(raw_value: Any) -> datetime | None:
    if raw_value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(raw_value))
    except ValueError:
        return None


def _safe_float(raw_value: Any) -> float | None:
    if raw_value in (None, "", "--"):
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    if math.isnan(value):
        return None
    return value


def _positive_or_none(raw_value: Any) -> float | None:
    value = _safe_float(raw_value)
    if value is None or value <= 0:
        return None
    return value


def _snapshot_is_fresh(snapshot: dict[str, Any] | None, ttl: timedelta) -> bool:
    if not snapshot:
        return False
    updated_at = _parse_timestamp(snapshot.get("updated_at"))
    if updated_at is None:
        return False
    return _now() - updated_at <= ttl


def _append_unique(target: list[Any], value: Any) -> None:
    if value not in target:
        target.append(value)


def _calc_diff_pct(base_value: Any, compare_value: Any) -> float | None:
    base = _positive_or_none(base_value)
    compare = _positive_or_none(compare_value)
    if base is None or compare is None:
        return None
    return (compare / base - 1) * 100


def _build_settings(params: dict[str, Any] | None) -> dict[str, Any]:
    source = params or {}
    token_env = str(source.get("tushare_token_env", "TUSHARE_TOKEN")).strip() or "TUSHARE_TOKEN"
    return {
        "provider": "tushare",
        "channel": "tushare",
        "api_url": str(source.get("tushare_api_url", DEFAULT_API_URL)).strip() or DEFAULT_API_URL,
        "cache_root": Path(str(source.get("tushare_cache_root", DEFAULT_CACHE_ROOT))),
        "daily_quota": max(int(source.get("tushare_daily_request_quota", 200)), 0),
        "pause_seconds": max(float(source.get("tushare_request_pause_seconds", 0.12)), 0.0),
        "fixed_ttl": timedelta(days=max(float(source.get("tushare_static_ttl_days", 3650)), 1.0)),
        "variable_ttl": timedelta(hours=max(float(source.get("tushare_dynamic_ttl_hours", 24)), 1.0)),
        "recent_trade_days": max(int(source.get("tushare_recent_trade_days", 12)), 3),
        "token_env": token_env,
        "token": os.getenv(token_env, "").strip(),
        "eastmoney_backup_enabled": bool(int(source.get("eastmoney_backup_enabled", 1))),
        "eastmoney_validation_enabled": bool(int(source.get("eastmoney_validation_enabled", 1))),
    }


def _build_summary(codes: list[str], settings: dict[str, Any], db: LocalFileDB) -> dict[str, Any]:
    quota_used_today = db.get_today_api_call_count(source="tushare")
    quota_limit = int(settings["daily_quota"])
    return {
        "provider": settings["provider"],
        "channel": settings["channel"],
        "cache_root": str(settings["cache_root"]),
        "requested_codes": list(codes),
        "returned_codes": [],
        "fixed_cache_hits": [],
        "variable_cache_hits": [],
        "api_fetched_fixed": [],
        "api_fetched_variable": [],
        "stale_variable_used": [],
        "skipped_due_quota": [],
        "skipped_unsupported": [],
        "api_calls": 0,
        "quota_limit": quota_limit,
        "quota_used_today": quota_used_today,
        "quota_remaining": max(quota_limit - quota_used_today, 0),
        "local_computed_codes": [],
        "eastmoney_api_calls": 0,
        "eastmoney_fetched": [],
        "eastmoney_cache_hits": [],
        "eastmoney_fallback_used": [],
        "cross_validated_codes": [],
        "cross_validation_warnings": [],
        "reason": "",
    }


def _call_tushare_api(
    api_name: str,
    params: dict[str, Any],
    fields: str,
    settings: dict[str, Any],
    db: LocalFileDB,
) -> tuple[list[dict[str, Any]], str]:
    quota_used_today = db.get_today_api_call_count(source="tushare")
    if quota_used_today >= int(settings["daily_quota"]):
        return [], "Tushare quota 已达上限，本次不再发起新请求。"

    payload = json.dumps(
        {
            "api_name": api_name,
            "token": settings["token"],
            "params": params,
            "fields": fields,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        str(settings["api_url"]),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        db.append_request_event(
            {
                "event_type": "api_call",
                "source": "tushare",
                "request_kind": api_name,
                "codes": [params.get("ts_code")] if params.get("ts_code") else [],
                "fields": fields.split(","),
                "error_code": "url_error",
            }
        )
        return [], f"Tushare 请求失败: {reason}"
    except json.JSONDecodeError:
        db.append_request_event(
            {
                "event_type": "api_call",
                "source": "tushare",
                "request_kind": api_name,
                "codes": [params.get("ts_code")] if params.get("ts_code") else [],
                "fields": fields.split(","),
                "error_code": "json_error",
            }
        )
        return [], "Tushare 返回内容无法解析为 JSON。"

    code = body.get("code", -1)
    db.append_request_event(
        {
            "event_type": "api_call",
            "source": "tushare",
            "request_kind": api_name,
            "codes": [params.get("ts_code")] if params.get("ts_code") else [],
            "fields": fields.split(","),
            "error_code": code,
        }
    )

    if code not in (0, None):
        return [], f"Tushare 接口 {api_name} 调用失败: {body.get('msg', 'unknown error')}"

    data = body.get("data") or {}
    header = data.get("fields") or []
    items = data.get("items") or []
    if not isinstance(header, list) or not isinstance(items, list):
        return [], f"Tushare 接口 {api_name} 返回结构异常。"

    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, list):
            continue
        rows.append({str(key): value for key, value in zip(header, item)})
    return rows, ""


def _extract_fixed_name(record: dict[str, Any] | None) -> str | None:
    if not record:
        return None
    fields = record.get("fields") or {}
    name = fields.get("name")
    if name in (None, ""):
        return None
    return str(name).strip() or None


def _extract_tushare_snapshot(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    fields = record.get("fields") or {}
    snapshot = fields.get("tushare_snapshot")
    if isinstance(snapshot, dict):
        return dict(snapshot)
    return None


def _extract_eastmoney_snapshot(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    fields = record.get("fields") or {}
    snapshot = fields.get("eastmoney_snapshot")
    if isinstance(snapshot, dict):
        return dict(snapshot)
    return None


def _build_tushare_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    total_mv_yi = None
    total_mv = _safe_float(row.get("total_mv"))
    if total_mv is not None:
        total_mv_yi = total_mv / 10000

    float_mkt_cap_yi = None
    circ_mv = _safe_float(row.get("circ_mv"))
    if circ_mv is not None:
        float_mkt_cap_yi = circ_mv / 10000

    return {
        "close": _safe_float(row.get("close")),
        "pe_ttm": _safe_float(row.get("pe_ttm")),
        "pb_lf": _safe_float(row.get("pb")),
        "mkt_cap": total_mv_yi,
        "float_mkt_cap": float_mkt_cap_yi,
        "total_shares": (_safe_float(row.get("total_share")) or 0.0) * 10000 if _safe_float(row.get("total_share")) is not None else None,
        "float_a_shares": (_safe_float(row.get("float_share")) or 0.0) * 10000 if _safe_float(row.get("float_share")) is not None else None,
        "free_float_shares": (_safe_float(row.get("free_share")) or 0.0) * 10000 if _safe_float(row.get("free_share")) is not None else None,
        "trade_date": str(row.get("trade_date") or ""),
        "updated_at": _now_iso(),
        "source": "tushare_api",
        "price_basis": "t-1_close",
    }


def _fetch_stock_name(
    code: str,
    settings: dict[str, Any],
    db: LocalFileDB,
) -> tuple[str | None, str]:
    rows, error_message = _call_tushare_api(
        "stock_basic",
        {"ts_code": code, "list_status": "L"},
        "ts_code,name",
        settings,
        db,
    )
    if error_message:
        return None, error_message
    if not rows:
        return None, ""
    name = str(rows[0].get("name") or "").strip()
    return name or None, ""


def _fetch_latest_tushare_snapshot(
    code: str,
    settings: dict[str, Any],
    db: LocalFileDB,
) -> tuple[dict[str, Any] | None, str]:
    end_date = date.today()
    lookback_days = max(int(settings["recent_trade_days"]) * 3, 15)
    start_date = end_date - timedelta(days=lookback_days)
    rows, error_message = _call_tushare_api(
        "daily_basic",
        {
            "ts_code": code,
            "start_date": start_date.strftime("%Y%m%d"),
            "end_date": end_date.strftime("%Y%m%d"),
        },
        "ts_code,trade_date,close,pe_ttm,pb,total_share,float_share,free_share,total_mv,circ_mv",
        settings,
        db,
    )
    if error_message:
        return None, error_message
    if not rows:
        return None, ""

    rows.sort(key=lambda item: str(item.get("trade_date") or ""), reverse=True)
    for row in rows:
        snapshot = _build_tushare_snapshot(row)
        if _positive_or_none(snapshot.get("close")) or _positive_or_none(snapshot.get("pe_ttm")):
            return snapshot, ""
    return None, ""


def _fetch_eastmoney_snapshots(codes: list[str], summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    for code in codes:
        try:
            snapshot = data_fetcher.fetch_equity_snapshot(code)
        except data_fetcher.DataFetcherError as exc:
            summary["reason"] = summary["reason"] or str(exc)
            continue
        snapshots[code] = {
            "name": snapshot.get("name"),
            "close": snapshot.get("close"),
            "pe_ttm": snapshot.get("pe_ttm"),
            "eps_ttm": snapshot.get("eps_ttm"),
            "pb_lf": snapshot.get("pb_lf"),
            "mkt_cap": snapshot.get("mkt_cap"),
            "float_mkt_cap": snapshot.get("float_mkt_cap"),
            "total_shares": snapshot.get("total_shares"),
            "trade_date": snapshot.get("trade_date"),
            "price_basis": snapshot.get("price_basis", "quote_last"),
            "raw_fields": snapshot.get("raw_fields") or {},
            "updated_at": _now_iso(),
            "source": "eastmoney_api",
        }
        summary["eastmoney_api_calls"] += 1
        _append_unique(summary["eastmoney_fetched"], code)
    return snapshots


def _build_cross_validation(
    tushare_snapshot: dict[str, Any] | None,
    eastmoney_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    if not tushare_snapshot or not eastmoney_snapshot:
        return {}

    result: dict[str, Any] = {
        "updated_at": _now_iso(),
        "sources": ["tushare", "eastmoney"],
        "compared_fields": [],
        "warnings": [],
    }

    for label, base_value, compare_value in (
        ("close", tushare_snapshot.get("close"), eastmoney_snapshot.get("close")),
        ("pe_ttm", tushare_snapshot.get("pe_ttm"), eastmoney_snapshot.get("pe_ttm")),
        ("mkt_cap", tushare_snapshot.get("mkt_cap"), eastmoney_snapshot.get("mkt_cap")),
        ("pb_lf", tushare_snapshot.get("pb_lf"), eastmoney_snapshot.get("pb_lf")),
    ):
        diff_value = _calc_diff_pct(base_value, compare_value)
        if diff_value is None:
            continue
        result[f"{label}_diff_pct"] = diff_value
        result["compared_fields"].append(label)
        if abs(diff_value) >= 5:
            result["warnings"].append(f"{label} 差异 {diff_value:.2f}%")
    return result


def _build_item(
    code: str,
    fixed_record: dict[str, Any] | None,
    tushare_snapshot: dict[str, Any] | None,
    eastmoney_snapshot: dict[str, Any] | None,
    cross_validation: dict[str, Any],
    summary: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any] | None:
    name = _extract_fixed_name(fixed_record)
    if not name and eastmoney_snapshot:
        name = str(eastmoney_snapshot.get("name", "")).strip() or None
    name = name or code

    close = _positive_or_none(tushare_snapshot.get("close") if tushare_snapshot else None)
    close_source = "tushare" if close is not None else ""
    if close is None:
        close = _positive_or_none(eastmoney_snapshot.get("close") if eastmoney_snapshot else None)
        close_source = "eastmoney" if close is not None else ""

    pe_ttm = _positive_or_none(tushare_snapshot.get("pe_ttm") if tushare_snapshot else None)
    pe_source = "tushare" if pe_ttm is not None else ""
    if pe_ttm is None:
        pe_ttm = _positive_or_none(eastmoney_snapshot.get("pe_ttm") if eastmoney_snapshot else None)
        pe_source = "eastmoney" if pe_ttm is not None else ""

    pb_lf = _positive_or_none(tushare_snapshot.get("pb_lf") if tushare_snapshot else None)
    pb_source = "tushare" if pb_lf is not None else ""
    if pb_lf is None:
        pb_lf = _positive_or_none(eastmoney_snapshot.get("pb_lf") if eastmoney_snapshot else None)
        pb_source = "eastmoney" if pb_lf is not None else ""

    mkt_cap = _positive_or_none(tushare_snapshot.get("mkt_cap") if tushare_snapshot else None)
    mkt_cap_source = "tushare" if mkt_cap is not None else ""
    if mkt_cap is None:
        mkt_cap = _positive_or_none(eastmoney_snapshot.get("mkt_cap") if eastmoney_snapshot else None)
        mkt_cap_source = "eastmoney" if mkt_cap is not None else ""

    if close is None and pe_ttm is None and pb_lf is None and mkt_cap is None:
        return None

    if (
        pe_source == "eastmoney"
        or pb_source == "eastmoney"
        or mkt_cap_source == "eastmoney"
        or close_source == "eastmoney"
    ):
        _append_unique(summary["eastmoney_fallback_used"], code)

    if cross_validation.get("compared_fields"):
        _append_unique(summary["cross_validated_codes"], code)
        for warning in cross_validation.get("warnings", []):
            _append_unique(summary["cross_validation_warnings"], f"{code}: {warning}")

    primary_snapshot = tushare_snapshot if pe_source == "tushare" or close_source == "tushare" else eastmoney_snapshot
    is_stale = not _snapshot_is_fresh(primary_snapshot, settings["variable_ttl"])
    if is_stale:
        _append_unique(summary["stale_variable_used"], code)

    return {
        "code": code,
        "name": name,
        "close": close,
        "pe_ttm": pe_ttm,
        "pb_lf": pb_lf,
        "mkt_cap": mkt_cap,
        "trade_date": (primary_snapshot or {}).get("trade_date"),
        "source": pe_source or close_source or "cache",
        "close_source": close_source,
        "pe_source": pe_source,
        "pb_source": pb_source,
        "mkt_cap_source": mkt_cap_source,
        "data_sources": [item for item in ("tushare" if tushare_snapshot else "", "eastmoney" if eastmoney_snapshot else "") if item],
        "cross_validation": cross_validation,
        "is_stale": is_stale,
    }


def get_comparable_valuations(
    codes: list[str],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = _build_settings(params)
    normalized_codes = _dedupe_codes(codes)
    supported_tushare_codes = [code for code in normalized_codes if _supports_tushare_code(code)]
    supported_tushare_code_set = set(supported_tushare_codes)
    db = LocalFileDB(settings["cache_root"])
    summary = _build_summary(normalized_codes, settings, db)
    summary["skipped_unsupported"] = [code for code in normalized_codes if code not in supported_tushare_code_set]

    if not normalized_codes:
        summary["reason"] = "未提供可比公司代码。"
        return {"items": [], "summary": summary}

    fixed_records: dict[str, dict[str, Any] | None] = {}
    variable_records: dict[str, dict[str, Any] | None] = {}
    tushare_snapshots: dict[str, dict[str, Any]] = {}
    eastmoney_snapshots: dict[str, dict[str, Any]] = {}
    missing_fixed: list[str] = []
    need_tushare_refresh: list[str] = []
    need_eastmoney_refresh: list[str] = []

    for code in normalized_codes:
        fixed_record = db.load_fixed_record(code)
        variable_record = db.load_variable_record(code)
        fixed_records[code] = fixed_record
        variable_records[code] = variable_record
        supports_tushare = code in supported_tushare_code_set

        if fixed_record and _extract_fixed_name(fixed_record):
            summary["fixed_cache_hits"].append(code)
        elif supports_tushare:
            missing_fixed.append(code)

        tushare_snapshot = _extract_tushare_snapshot(variable_record) if supports_tushare else None
        if tushare_snapshot:
            tushare_snapshots[code] = tushare_snapshot
            if _snapshot_is_fresh(tushare_snapshot, settings["variable_ttl"]):
                summary["variable_cache_hits"].append(code)
            elif supports_tushare:
                need_tushare_refresh.append(code)
        elif supports_tushare:
            need_tushare_refresh.append(code)

        eastmoney_snapshot = _extract_eastmoney_snapshot(variable_record)
        if eastmoney_snapshot:
            eastmoney_snapshots[code] = eastmoney_snapshot
            if _snapshot_is_fresh(eastmoney_snapshot, settings["variable_ttl"]):
                summary["eastmoney_cache_hits"].append(code)

    if not settings["token"]:
        summary["reason"] = f"Tushare token 未配置，请先设置环境变量 {settings['token_env']}。"
        need_tushare_refresh = []
        missing_fixed = []

    for code in missing_fixed:
        current_calls = db.get_today_api_call_count(source="tushare")
        if current_calls >= int(settings["daily_quota"]):
            _append_unique(summary["skipped_due_quota"], code)
            break
        name, error_message = _fetch_stock_name(code, settings, db)
        summary["api_calls"] += 1
        if error_message:
            summary["reason"] = summary["reason"] or error_message
            continue
        if name:
            fixed_records[code] = db.save_fixed_record(code, {"name": name}, source="tushare_api")
            _append_unique(summary["api_fetched_fixed"], code)
        if settings["pause_seconds"] > 0:
            time.sleep(float(settings["pause_seconds"]))

    for code in need_tushare_refresh:
        current_calls = db.get_today_api_call_count(source="tushare")
        if current_calls >= int(settings["daily_quota"]):
            _append_unique(summary["skipped_due_quota"], code)
            continue
        snapshot, error_message = _fetch_latest_tushare_snapshot(code, settings, db)
        summary["api_calls"] += 1
        if error_message:
            summary["reason"] = summary["reason"] or error_message
            continue
        if snapshot:
            tushare_snapshots[code] = snapshot
            _append_unique(summary["api_fetched_variable"], code)
        if settings["pause_seconds"] > 0:
            time.sleep(float(settings["pause_seconds"]))

    for code in normalized_codes:
        eastmoney_snapshot = eastmoney_snapshots.get(code)
        eastmoney_fresh = _snapshot_is_fresh(eastmoney_snapshot, settings["variable_ttl"])
        has_tushare_primary = bool(
            _positive_or_none((tushare_snapshots.get(code) or {}).get("pe_ttm"))
            or _positive_or_none((tushare_snapshots.get(code) or {}).get("close"))
        )
        needs_backup = settings["eastmoney_backup_enabled"] and not has_tushare_primary
        needs_validation = settings["eastmoney_validation_enabled"] and code in summary["api_fetched_variable"]
        if (needs_backup or needs_validation) and not eastmoney_fresh:
            need_eastmoney_refresh.append(code)

    if need_eastmoney_refresh:
        refreshed_eastmoney = _fetch_eastmoney_snapshots(need_eastmoney_refresh, summary)
        eastmoney_snapshots.update(refreshed_eastmoney)

    items: list[dict[str, Any]] = []
    for code in normalized_codes:
        tushare_snapshot = tushare_snapshots.get(code)
        eastmoney_snapshot = eastmoney_snapshots.get(code)
        cross_validation = _build_cross_validation(tushare_snapshot, eastmoney_snapshot)

        fields: dict[str, Any] = {}
        if tushare_snapshot:
            fields["tushare_snapshot"] = tushare_snapshot
        if eastmoney_snapshot:
            fields["eastmoney_snapshot"] = eastmoney_snapshot
        if cross_validation:
            fields["cross_validation"] = cross_validation
        if fields:
            source = "tushare_api" if tushare_snapshot else "eastmoney_api"
            variable_records[code] = db.save_variable_record(
                code,
                fields,
                trade_date=(tushare_snapshot or eastmoney_snapshot or {}).get("trade_date"),
                source=source,
            )

        if not fixed_records.get(code) and eastmoney_snapshot and eastmoney_snapshot.get("name"):
            fixed_records[code] = db.save_fixed_record(code, {"name": eastmoney_snapshot.get("name")}, source="eastmoney_api")

        item = _build_item(
            code=code,
            fixed_record=fixed_records.get(code),
            tushare_snapshot=tushare_snapshot,
            eastmoney_snapshot=eastmoney_snapshot,
            cross_validation=cross_validation,
            summary=summary,
            settings=settings,
        )
        if item is not None:
            items.append(item)

    if items and not any(_positive_or_none(item.get("pe_ttm")) is not None for item in items):
        summary["reason"] = (
            summary["reason"]
            or "Tushare 当前未形成有效 PE，且东方财富备选也未返回可用 PE，方法一会自动降级。"
        )
    elif not items and summary["reason"] == "":
        summary["reason"] = "Tushare 当前未取到可用的可比公司快照。"
    elif not settings["token"] and items:
        summary["reason"] = (
            f"Tushare token 未配置，当前已回退使用东方财富可比快照。"
        )

    summary["returned_codes"] = [item["code"] for item in items]
    summary["quota_used_today"] = db.get_today_api_call_count(source="tushare")
    summary["quota_remaining"] = max(int(settings["daily_quota"]) - summary["quota_used_today"], 0)
    return {"items": items, "summary": summary}
