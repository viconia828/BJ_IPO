from __future__ import annotations

import math
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import data_fetcher
from local_file_db import LocalFileDB


FIXED_FIELD_SPECS = (
    ("name", "sec_name"),
)
WSQ_QUOTE_FIELD_SPECS = (
    ("close", "rt_last"),
    ("pre_close", "rt_pre_close"),
    ("open", "rt_open"),
)
DIRECT_VARIABLE_FIELD_SPECS = (
    ("pe_ttm_direct", "pe_ttm"),
    ("pb_lf_direct", "pb_lf"),
    ("mkt_cap_direct", "mkt_cap_ard"),
)
RAW_VARIABLE_FIELD_SPECS = (
    ("eps_ttm", "eps_ttm"),
    ("total_shares", "total_shares"),
    ("float_a_shares", "float_a_shares"),
    ("free_float_shares", "free_float_shares"),
)
SUPPORTED_API_CHANNELS = {"auto", "api_only"}
SUPPORTED_CHANNELS = {"disabled", "auto", "api_only", "excel_only"}
DEFAULT_CACHE_ROOT = Path("data") / "wind_db"
DEFAULT_WIND_CLIENT_ROOT = Path(r"C:\Wind\Wind.NET.Client\WindNET\x64")


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


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


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


def _normalize_value(raw_value: Any) -> Any:
    if raw_value is None:
        return None
    if isinstance(raw_value, datetime):
        return raw_value.isoformat(sep=" ")
    if isinstance(raw_value, float) and math.isnan(raw_value):
        return None
    return raw_value


def _snapshot_is_fresh(snapshot: dict[str, Any] | None, ttl: timedelta) -> bool:
    if not snapshot:
        return False
    updated_at = _parse_timestamp(snapshot.get("updated_at"))
    if updated_at is None:
        return False
    return _now() - updated_at <= ttl


def _build_settings(params: dict[str, Any] | None, channel: str) -> dict[str, Any]:
    source = params or {}
    normalized_channel = str(channel or source.get("wind_channel", "disabled")).strip().lower() or "disabled"
    return {
        "channel": normalized_channel,
        "cache_root": Path(str(source.get("wind_cache_root", DEFAULT_CACHE_ROOT))),
        "daily_quota": max(int(source.get("wind_daily_request_quota", 20)), 0),
        "batch_size": max(int(source.get("wind_batch_size", 20)), 1),
        "fixed_ttl": timedelta(days=max(float(source.get("wind_static_ttl_days", 3650)), 1.0)),
        "variable_ttl": timedelta(hours=max(float(source.get("wind_dynamic_ttl_hours", 24)), 1.0)),
        "pause_seconds": max(float(source.get("wind_request_pause_seconds", 0.2)), 0.0),
        "eastmoney_backup_enabled": bool(int(source.get("eastmoney_backup_enabled", 1))),
        "eastmoney_validation_enabled": bool(int(source.get("eastmoney_validation_enabled", 1))),
    }


def _build_summary(codes: list[str], settings: dict[str, Any], db: LocalFileDB) -> dict[str, Any]:
    quota_used_today = db.get_today_api_call_count()
    quota_limit = int(settings["daily_quota"])
    return {
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


def _append_unique(target: list[Any], value: Any) -> None:
    if value not in target:
        target.append(value)


def _prepare_wind_runtime(cache_root: Path) -> tuple[Path | None, str]:
    if not DEFAULT_WIND_CLIENT_ROOT.exists():
        return None, f"未找到 Wind 客户端目录：{DEFAULT_WIND_CLIENT_ROOT}"

    runtime_site_packages = cache_root / "_wind_runtime" / "site-packages"
    runtime_site_packages.mkdir(parents=True, exist_ok=True)
    (runtime_site_packages / "WindPy.pth").write_text(str(DEFAULT_WIND_CLIENT_ROOT), encoding="utf-8")
    return runtime_site_packages, ""


def _ensure_wind_client(cache_root: Path) -> tuple[Any | None, str]:
    runtime_site_packages, error_message = _prepare_wind_runtime(cache_root)
    if runtime_site_packages is None:
        return None, error_message

    for path in (str(runtime_site_packages), str(DEFAULT_WIND_CLIENT_ROOT)):
        if path not in sys.path:
            sys.path.insert(0, path)

    try:
        sys.modules.pop("WindPy", None)
        from WindPy import w
    except Exception as exc:
        return None, f"导入 WindPy 失败：{exc}"

    result = w.start()
    error_code = getattr(result, "ErrorCode", 0)
    if error_code not in (0, None):
        return None, f"Wind 启动失败，错误码 {error_code}。"
    return w, ""


def _wss_batch(
    wind_client: Any,
    db: LocalFileDB,
    codes: list[str],
    field_specs: tuple[tuple[str, str], ...],
    request_kind: str,
) -> tuple[dict[str, dict[str, Any]], str]:
    fields = ",".join(field_name for _, field_name in field_specs)
    codes_text = ",".join(codes)
    result = wind_client.wss(codes_text, fields)
    error_code = getattr(result, "ErrorCode", 0)
    db.append_request_event(
        {
            "event_type": "api_call",
            "source": "wind",
            "request_kind": request_kind,
            "codes": list(codes),
            "fields": [field_name for _, field_name in field_specs],
            "error_code": error_code,
        }
    )

    if error_code not in (0, None):
        return {}, f"Wind 请求失败，错误码 {error_code}。"

    rows: dict[str, dict[str, Any]] = {code: {} for code in codes}
    for field_index, (field_key, _) in enumerate(field_specs):
        column = result.Data[field_index] if field_index < len(result.Data) else []
        for code_index, code in enumerate(codes):
            value = column[code_index] if code_index < len(column) else None
            rows[code][field_key] = _normalize_value(value)
    return rows, ""


def _wsq_batch(
    wind_client: Any,
    db: LocalFileDB,
    codes: list[str],
    field_specs: tuple[tuple[str, str], ...],
    request_kind: str,
) -> tuple[dict[str, dict[str, Any]], str]:
    fields = ",".join(field_name for _, field_name in field_specs)
    codes_text = ",".join(codes)
    result = wind_client.wsq(codes_text, fields)
    error_code = getattr(result, "ErrorCode", 0)
    db.append_request_event(
        {
            "event_type": "api_call",
            "source": "wind",
            "request_kind": request_kind,
            "codes": list(codes),
            "fields": [field_name for _, field_name in field_specs],
            "error_code": error_code,
        }
    )

    if error_code not in (0, None):
        return {}, f"Wind 实时请求失败，错误码 {error_code}。"

    rows: dict[str, dict[str, Any]] = {code: {} for code in codes}
    for field_index, (field_key, _) in enumerate(field_specs):
        column = result.Data[field_index] if field_index < len(result.Data) else []
        for code_index, code in enumerate(codes):
            value = column[code_index] if code_index < len(column) else None
            rows[code][field_key] = _normalize_value(value)
    return rows, ""


def _refresh_wss_group(
    wind_client: Any,
    db: LocalFileDB,
    codes: list[str],
    field_specs: tuple[tuple[str, str], ...],
    request_kind: str,
    summary_key: str,
    settings: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if not codes:
        return {}

    refreshed: dict[str, dict[str, Any]] = {}
    for batch in _chunked(codes, int(settings["batch_size"])):
        current_calls = db.get_today_api_call_count()
        if current_calls >= int(settings["daily_quota"]):
            summary["skipped_due_quota"].extend(batch)
            break

        rows, error_message = _wss_batch(wind_client, db, batch, field_specs, request_kind)
        summary["api_calls"] += 1
        if error_message:
            summary["reason"] = error_message
            break

        for code, fields in rows.items():
            refreshed.setdefault(code, {}).update(fields)
            _append_unique(summary[summary_key], code)

        if settings["pause_seconds"] > 0:
            time.sleep(float(settings["pause_seconds"]))

    return refreshed


def _refresh_wsq_group(
    wind_client: Any,
    db: LocalFileDB,
    codes: list[str],
    field_specs: tuple[tuple[str, str], ...],
    request_kind: str,
    summary_key: str,
    settings: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if not codes:
        return {}

    refreshed: dict[str, dict[str, Any]] = {}
    for batch in _chunked(codes, int(settings["batch_size"])):
        current_calls = db.get_today_api_call_count()
        if current_calls >= int(settings["daily_quota"]):
            summary["skipped_due_quota"].extend(batch)
            break

        rows, error_message = _wsq_batch(wind_client, db, batch, field_specs, request_kind)
        summary["api_calls"] += 1
        if error_message:
            summary["reason"] = error_message
            break

        for code, fields in rows.items():
            refreshed.setdefault(code, {}).update(fields)
            _append_unique(summary[summary_key], code)

        if settings["pause_seconds"] > 0:
            time.sleep(float(settings["pause_seconds"]))

    return refreshed


def _extract_fixed_name(record: dict[str, Any] | None) -> str | None:
    if not record:
        return None
    fields = record.get("fields") or {}
    name = fields.get("name")
    if name in (None, ""):
        return None
    return str(name).strip() or None


def _extract_wind_snapshot(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    fields = record.get("fields") or {}
    snapshot = fields.get("wind_snapshot")
    if isinstance(snapshot, dict):
        return dict(snapshot)

    legacy_keys = {"close", "pe_ttm", "pb_lf", "mkt_cap"}
    if not any(key in fields for key in legacy_keys):
        return None

    return {
        "close": fields.get("close"),
        "pe_ttm_direct": fields.get("pe_ttm"),
        "pb_lf_direct": fields.get("pb_lf"),
        "mkt_cap_direct": fields.get("mkt_cap"),
        "trade_date": record.get("trade_date"),
        "updated_at": record.get("updated_at"),
        "source": record.get("source", "wind_cache"),
    }


def _extract_eastmoney_snapshot(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    fields = record.get("fields") or {}
    snapshot = fields.get("eastmoney_snapshot")
    if isinstance(snapshot, dict):
        return dict(snapshot)
    return None


def _build_local_metrics(wind_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not wind_snapshot:
        return {}

    close = _positive_or_none(wind_snapshot.get("close"))
    eps_ttm = _positive_or_none(wind_snapshot.get("eps_ttm"))
    total_shares = _positive_or_none(wind_snapshot.get("total_shares"))
    float_shares = _positive_or_none(wind_snapshot.get("float_a_shares")) or _positive_or_none(wind_snapshot.get("free_float_shares"))

    derived: dict[str, Any] = {
        "updated_at": _now_iso(),
        "trade_date": wind_snapshot.get("trade_date"),
        "source": "wind_local_compute",
    }
    if close is not None and eps_ttm is not None:
        derived["pe_ttm"] = close / eps_ttm
    if close is not None and total_shares is not None:
        derived["mkt_cap"] = close * total_shares / 100000000
    if close is not None and float_shares is not None:
        derived["float_mkt_cap"] = close * float_shares / 100000000
    return derived


def _calc_diff_pct(base_value: Any, compare_value: Any) -> float | None:
    base = _positive_or_none(base_value)
    compare = _positive_or_none(compare_value)
    if base is None or compare is None:
        return None
    return (compare / base - 1) * 100


def _build_cross_validation(
    wind_snapshot: dict[str, Any] | None,
    local_metrics: dict[str, Any],
    eastmoney_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    if not wind_snapshot or not eastmoney_snapshot:
        return {}

    result: dict[str, Any] = {
        "updated_at": _now_iso(),
        "sources": ["wind", "eastmoney"],
        "compared_fields": [],
        "warnings": [],
    }

    close_diff = _calc_diff_pct(wind_snapshot.get("close"), eastmoney_snapshot.get("close"))
    if close_diff is not None:
        result["close_diff_pct"] = close_diff
        result["compared_fields"].append("close")

    wind_pe = local_metrics.get("pe_ttm") or wind_snapshot.get("pe_ttm_direct")
    pe_diff = _calc_diff_pct(wind_pe, eastmoney_snapshot.get("pe_ttm"))
    if pe_diff is not None:
        result["pe_diff_pct"] = pe_diff
        result["compared_fields"].append("pe_ttm")

    wind_cap = local_metrics.get("mkt_cap") or wind_snapshot.get("mkt_cap_direct")
    cap_diff = _calc_diff_pct(wind_cap, eastmoney_snapshot.get("mkt_cap"))
    if cap_diff is not None:
        result["mkt_cap_diff_pct"] = cap_diff
        result["compared_fields"].append("mkt_cap")

    eps_diff = _calc_diff_pct(wind_snapshot.get("eps_ttm"), eastmoney_snapshot.get("eps_ttm"))
    if eps_diff is not None:
        result["eps_ttm_diff_pct"] = eps_diff
        result["compared_fields"].append("eps_ttm")

    shares_diff = _calc_diff_pct(wind_snapshot.get("total_shares"), eastmoney_snapshot.get("total_shares"))
    if shares_diff is not None:
        result["total_shares_diff_pct"] = shares_diff
        result["compared_fields"].append("total_shares")

    for label, diff_value in (
        ("close", result.get("close_diff_pct")),
        ("pe_ttm", result.get("pe_diff_pct")),
        ("mkt_cap", result.get("mkt_cap_diff_pct")),
        ("eps_ttm", result.get("eps_ttm_diff_pct")),
        ("total_shares", result.get("total_shares_diff_pct")),
    ):
        if diff_value is not None and abs(diff_value) >= 5:
            result["warnings"].append(f"{label} 差异 {diff_value:.2f}%")

    return result


def _has_wind_primary_data(wind_snapshot: dict[str, Any] | None, local_metrics: dict[str, Any]) -> bool:
    return bool(
        _positive_or_none(local_metrics.get("pe_ttm"))
        or _positive_or_none(wind_snapshot.get("pe_ttm_direct") if wind_snapshot else None)
        or _positive_or_none(wind_snapshot.get("close") if wind_snapshot else None)
    )


def _has_wind_local_inputs(wind_snapshot: dict[str, Any] | None) -> bool:
    if not wind_snapshot:
        return False
    return bool(
        _positive_or_none(wind_snapshot.get("close"))
        and _positive_or_none(wind_snapshot.get("eps_ttm"))
        and _positive_or_none(wind_snapshot.get("total_shares"))
    )


def _build_item(
    code: str,
    fixed_record: dict[str, Any] | None,
    wind_snapshot: dict[str, Any] | None,
    eastmoney_snapshot: dict[str, Any] | None,
    local_metrics: dict[str, Any],
    cross_validation: dict[str, Any],
    summary: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any] | None:
    name = _extract_fixed_name(fixed_record)
    if not name and eastmoney_snapshot:
        name = str(eastmoney_snapshot.get("name", "")).strip() or None
    if not name and wind_snapshot:
        name = str(wind_snapshot.get("name", "")).strip() or None
    name = name or code

    close = _positive_or_none(wind_snapshot.get("close") if wind_snapshot else None)
    close_source = "wind"
    if close is None:
        close = _positive_or_none(eastmoney_snapshot.get("close") if eastmoney_snapshot else None)
        close_source = "eastmoney" if close is not None else ""

    pe_ttm = _positive_or_none(local_metrics.get("pe_ttm"))
    pe_source = "wind_local_compute"
    if pe_ttm is None:
        pe_ttm = _positive_or_none(wind_snapshot.get("pe_ttm_direct") if wind_snapshot else None)
        pe_source = "wind_direct" if pe_ttm is not None else ""
    if pe_ttm is None:
        pe_ttm = _positive_or_none(eastmoney_snapshot.get("pe_ttm") if eastmoney_snapshot else None)
        pe_source = "eastmoney" if pe_ttm is not None else ""

    pb_lf = _positive_or_none(wind_snapshot.get("pb_lf_direct") if wind_snapshot else None)
    pb_source = "wind_direct"
    if pb_lf is None:
        pb_lf = _positive_or_none(eastmoney_snapshot.get("pb_lf") if eastmoney_snapshot else None)
        pb_source = "eastmoney" if pb_lf is not None else ""

    mkt_cap = _positive_or_none(local_metrics.get("mkt_cap"))
    mkt_cap_source = "wind_local_compute"
    if mkt_cap is None:
        mkt_cap = _positive_or_none(wind_snapshot.get("mkt_cap_direct") if wind_snapshot else None)
        mkt_cap_source = "wind_direct" if mkt_cap is not None else ""
    if mkt_cap is None:
        mkt_cap = _positive_or_none(eastmoney_snapshot.get("mkt_cap") if eastmoney_snapshot else None)
        mkt_cap_source = "eastmoney" if mkt_cap is not None else ""

    if close is None and pe_ttm is None and pb_lf is None and mkt_cap is None:
        return None

    if pe_source == "wind_local_compute":
        _append_unique(summary["local_computed_codes"], code)
    if pe_source == "eastmoney" or pb_source == "eastmoney" or mkt_cap_source == "eastmoney" or close_source == "eastmoney":
        _append_unique(summary["eastmoney_fallback_used"], code)
    if cross_validation.get("compared_fields"):
        _append_unique(summary["cross_validated_codes"], code)
        for warning in cross_validation.get("warnings", []):
            _append_unique(summary["cross_validation_warnings"], f"{code}: {warning}")

    primary_snapshot = wind_snapshot if pe_source.startswith("wind") or close_source == "wind" else eastmoney_snapshot
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
        "data_sources": [item for item in ("wind" if wind_snapshot else "", "eastmoney" if eastmoney_snapshot else "") if item],
        "cross_validation": cross_validation,
        "is_stale": is_stale,
    }


def _fetch_eastmoney_snapshots(
    codes: list[str],
    summary: dict[str, Any],
) -> dict[str, dict[str, Any]]:
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


def get_comparable_valuations(
    codes: list[str],
    channel: str = "disabled",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = _build_settings(params, channel)
    normalized_codes = _dedupe_codes(codes)
    db = LocalFileDB(settings["cache_root"])
    summary = _build_summary(normalized_codes, settings, db)

    if not normalized_codes:
        summary["reason"] = "未提供可比公司代码。"
        return {"items": [], "summary": summary}

    if settings["channel"] not in SUPPORTED_CHANNELS:
        summary["reason"] = f"不支持的 Wind 通道：{settings['channel']}。"
        return {"items": [], "summary": summary}

    fixed_records: dict[str, dict[str, Any] | None] = {}
    variable_records: dict[str, dict[str, Any] | None] = {}
    wind_snapshots: dict[str, dict[str, Any]] = {}
    eastmoney_snapshots: dict[str, dict[str, Any]] = {}
    missing_fixed: list[str] = []
    need_wind_refresh: list[str] = []
    need_eastmoney_refresh: list[str] = []

    for code in normalized_codes:
        fixed_record = db.load_fixed_record(code)
        variable_record = db.load_variable_record(code)
        fixed_records[code] = fixed_record
        variable_records[code] = variable_record

        if fixed_record and _extract_fixed_name(fixed_record):
            summary["fixed_cache_hits"].append(code)
        else:
            missing_fixed.append(code)

        wind_snapshot = _extract_wind_snapshot(variable_record)
        if wind_snapshot:
            wind_snapshots[code] = wind_snapshot
            if _snapshot_is_fresh(wind_snapshot, settings["variable_ttl"]) and _has_wind_local_inputs(wind_snapshot):
                summary["variable_cache_hits"].append(code)
            else:
                need_wind_refresh.append(code)
        else:
            need_wind_refresh.append(code)

        eastmoney_snapshot = _extract_eastmoney_snapshot(variable_record)
        if eastmoney_snapshot:
            eastmoney_snapshots[code] = eastmoney_snapshot
            if _snapshot_is_fresh(eastmoney_snapshot, settings["variable_ttl"]):
                summary["eastmoney_cache_hits"].append(code)

    wind_client = None
    if settings["channel"] in SUPPORTED_API_CHANNELS and need_wind_refresh:
        wind_client, error_message = _ensure_wind_client(settings["cache_root"])
        if wind_client is None:
            summary["reason"] = error_message

    if wind_client is not None:
        quote_rows = _refresh_wsq_group(
            wind_client=wind_client,
            db=db,
            codes=need_wind_refresh,
            field_specs=WSQ_QUOTE_FIELD_SPECS,
            request_kind="comparable_wind_quote",
            summary_key="api_fetched_variable",
            settings=settings,
            summary=summary,
        )
        direct_rows = _refresh_wss_group(
            wind_client=wind_client,
            db=db,
            codes=need_wind_refresh,
            field_specs=DIRECT_VARIABLE_FIELD_SPECS,
            request_kind="comparable_wind_direct",
            summary_key="api_fetched_variable",
            settings=settings,
            summary=summary,
        )
        raw_rows = _refresh_wss_group(
            wind_client=wind_client,
            db=db,
            codes=need_wind_refresh,
            field_specs=RAW_VARIABLE_FIELD_SPECS,
            request_kind="comparable_wind_raw",
            summary_key="api_fetched_variable",
            settings=settings,
            summary=summary,
        )

        for code in normalized_codes:
            if code not in quote_rows and code not in direct_rows and code not in raw_rows:
                continue
            snapshot = dict(wind_snapshots.get(code) or {})
            snapshot.update(quote_rows.get(code, {}))
            snapshot.update(direct_rows.get(code, {}))
            snapshot.update(raw_rows.get(code, {}))
            snapshot["trade_date"] = datetime.now().date().isoformat()
            snapshot["updated_at"] = _now_iso()
            snapshot["source"] = "wind_api"
            snapshot["price_basis"] = "rt_last"
            wind_snapshots[code] = snapshot

        if missing_fixed:
            fixed_rows = _refresh_wss_group(
                wind_client=wind_client,
                db=db,
                codes=missing_fixed,
                field_specs=FIXED_FIELD_SPECS,
                request_kind="comparable_wind_fixed",
                summary_key="api_fetched_fixed",
                settings=settings,
                summary=summary,
            )
            for code, fields in fixed_rows.items():
                fixed_record = db.save_fixed_record(code, fields, source="wind_api")
                fixed_records[code] = fixed_record

        try:
            wind_client.stop()
        except Exception:
            try:
                wind_client.close()
            except Exception:
                pass
    elif settings["channel"] == "excel_only" and not summary["reason"]:
        summary["reason"] = "当前未实现 Excel 通道，已仅使用本地缓存。"
    elif settings["channel"] == "disabled" and not summary["reason"]:
        summary["reason"] = "Wind 当前处于禁用状态。"

    local_metrics_by_code = {code: _build_local_metrics(wind_snapshots.get(code)) for code in normalized_codes}
    for code in normalized_codes:
        eastmoney_snapshot = eastmoney_snapshots.get(code)
        eastmoney_fresh = _snapshot_is_fresh(eastmoney_snapshot, settings["variable_ttl"])
        has_wind_primary = _has_wind_primary_data(wind_snapshots.get(code), local_metrics_by_code.get(code, {}))

        needs_backup = settings["eastmoney_backup_enabled"] and (settings["channel"] == "disabled" or not has_wind_primary)
        needs_validation = settings["eastmoney_validation_enabled"] and code in summary["api_fetched_variable"]
        if (needs_backup or needs_validation) and not eastmoney_fresh:
            need_eastmoney_refresh.append(code)

    if need_eastmoney_refresh:
        refreshed_eastmoney = _fetch_eastmoney_snapshots(need_eastmoney_refresh, summary)
        eastmoney_snapshots.update(refreshed_eastmoney)

    items: list[dict[str, Any]] = []
    for code in normalized_codes:
        wind_snapshot = wind_snapshots.get(code)
        eastmoney_snapshot = eastmoney_snapshots.get(code)
        local_metrics = _build_local_metrics(wind_snapshot)
        cross_validation = _build_cross_validation(wind_snapshot, local_metrics, eastmoney_snapshot)

        fields: dict[str, Any] = {}
        if wind_snapshot:
            fields["wind_snapshot"] = wind_snapshot
        if eastmoney_snapshot:
            fields["eastmoney_snapshot"] = eastmoney_snapshot
        if local_metrics:
            fields["derived_snapshot"] = local_metrics
        if cross_validation:
            fields["cross_validation"] = cross_validation
        if fields:
            source = "wind_api" if wind_snapshot else "eastmoney_api"
            variable_records[code] = db.save_variable_record(
                code,
                fields,
                trade_date=(wind_snapshot or eastmoney_snapshot or {}).get("trade_date"),
                source=source,
            )

        if not fixed_records.get(code) and eastmoney_snapshot and eastmoney_snapshot.get("name"):
            fixed_records[code] = db.save_fixed_record(code, {"name": eastmoney_snapshot.get("name")}, source="eastmoney_api")

        item = _build_item(
            code=code,
            fixed_record=fixed_records.get(code),
            wind_snapshot=wind_snapshot,
            eastmoney_snapshot=eastmoney_snapshot,
            local_metrics=local_metrics,
            cross_validation=cross_validation,
            summary=summary,
            settings=settings,
        )
        if item is not None:
            items.append(item)

    if items and not any(_positive_or_none(item.get("pe_ttm")) is not None for item in items):
        summary["reason"] = (
            summary["reason"]
            or "Wind 当前可返回原料字段，但未形成有效 PE；若东方财富备选也未返回 PE，方法一会自动降级。"
        )
    elif settings["channel"] == "disabled" and items:
        if summary["reason"] in ("", "Wind 当前处于禁用状态。"):
            summary["reason"] = "Wind 当前处于禁用状态，已使用东方财富可比快照。"

    summary["returned_codes"] = [item["code"] for item in items]
    summary["quota_used_today"] = db.get_today_api_call_count()
    summary["quota_remaining"] = max(int(settings["daily_quota"]) - summary["quota_used_today"], 0)
    return {"items": items, "summary": summary}
