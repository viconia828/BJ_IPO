from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import data_fetcher
from local_file_db import LocalFileDB
import tushare_helper


SUPPLEMENT_FIELD_NAMES = (
    "PRICE_WAY",
    "TOP_APPLY_MARKETCAP",
    "ONLINE_VA_NUM",
    "ONLINE_ISSUE_LWR",
    "INDUSTRY_PE_NEW",
    "SW_INDUSTRY",
    "MAIN_BUSINESS",
)

SW_CLASSIFY_FIELDS = "index_code,industry_name,parent_code,level,industry_code,is_pub,src"
SW_DAILY_FIELDS = "ts_code,trade_date,name,pe,pb,float_mv,total_mv"
SW_CLASSIFY_CACHE_PREFIX = "__TS_SW2021_CLASSIFY_"
SW_DAILY_CACHE_PREFIX = "__TS_SW_DAILY_"
NEW_SHARE_RANGE_CACHE_CODE = "__TS_NEW_SHARE_RANGE__BJ"
SW_CLASSIFY_FIELD_PREFIX = "tushare_sw2021_classify_"
SW_DAILY_FIELD_KEY = "tushare_sw_industry_daily"
SW_DAILY_RANGE_FIELD_KEY = "tushare_sw_industry_daily_range"
NEW_SHARE_RANGE_FIELD_KEY = "tushare_new_share_range"
INDUSTRY_PE_ALIAS_CODES: dict[str, tuple[str, ...]] = {
    "IT设备": ("852226.SI", "801103.SI"),
    "专用机械": ("850727.SI", "801074.SI"),
    "中成药": ("851521.SI", "801155.SI"),
    "乳制品": ("851243.SI", "801127.SI"),
    "仓储物流": ("851786.SI", "801178.SI"),
    "供气供热": ("851614.SI", "801163.SI"),
    "元器件": ("850823.SI", "801083.SI"),
    "农业综合": ("801016.SI",),
    "农用机械": ("801072.SI",),
    "农药化肥": ("801038.SI", "850333.SI"),
    "化工原料": ("850324.SI", "801033.SI"),
    "化纤": ("801032.SI",),
    "医疗保健": ("801153.SI", "851532.SI", "851533.SI", "801156.SI"),
    "家用电器": ("801116.SI", "801113.SI", "801111.SI"),
    "机械基件": ("801072.SI",),
    "汽车配件": ("850925.SI", "801093.SI"),
    "电器仪表": ("857323.SI", "850731.SI", "801738.SI"),
    "电气设备": ("801738.SI", "801733.SI"),
    "石油加工": ("801035.SI",),
    "石油开采": ("801023.SI",),
    "综合类": ("801231.SI",),
    "软件服务": ("851041.SI", "851042.SI", "801104.SI"),
    "生物制药": ("801152.SI",),
    "运输设备": ("858811.SI", "801076.SI"),
    "钢加工": ("850401.SI", "801045.SI"),
    "食品": ("801124.SI",),
}


def _normalize_code(code: str) -> str:
    return LocalFileDB.normalize_code(code)


def _base_code(code: str) -> str:
    normalized_code = _normalize_code(code)
    if "." in normalized_code:
        return normalized_code.split(".", 1)[0]
    return normalized_code


def _to_ts_code(code: str) -> str:
    normalized_code = _normalize_code(code)
    if "." in normalized_code:
        return normalized_code
    if normalized_code.startswith("920"):
        return f"{normalized_code}.BJ"
    return normalized_code


def _to_iso_date(raw_value: Any) -> str:
    text = str(raw_value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _to_ymd(raw_value: Any) -> str:
    return _to_iso_date(raw_value).replace("-", "")


def _parse_iso_date(raw_value: Any) -> date | None:
    text = _to_iso_date(raw_value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _resolve_recent_days(months: int, params: dict[str, Any] | None = None) -> int:
    settings = params or {}
    raw_days = settings.get("recent_days")
    if raw_days not in (None, ""):
        return max(int(float(raw_days)), 1)
    return max(int(months) * 30, 1)


def _is_within_recent_days(raw_date: Any, recent_days: int, reference_date: date) -> bool:
    parsed = _parse_iso_date(raw_date)
    if parsed is None:
        return False
    cutoff = reference_date - timedelta(days=max(int(recent_days), 1))
    return cutoff <= parsed <= reference_date


def _shift_ymd(ymd_text: str, days: int) -> str:
    base_date = datetime.strptime(ymd_text, "%Y%m%d").date()
    return (base_date + timedelta(days=days)).strftime("%Y%m%d")


def _append_unique(target: list[Any], value: Any) -> None:
    if value not in target:
        target.append(value)


def _discard_value(target: list[Any], value: Any) -> None:
    while value in target:
        target.remove(value)


def _record_summary_item(summary: dict[str, Any], key: str, value: Any) -> None:
    bucket = summary.setdefault(key, [])
    if isinstance(bucket, list) and value not in bucket:
        bucket.append(value)


def _refresh_quota_snapshot(summary: dict[str, Any], settings: dict[str, Any], db: LocalFileDB) -> None:
    quota_limit = int(settings["daily_quota"])
    quota_used_today = db.get_today_api_call_count(source="tushare")
    summary["quota_limit"] = quota_limit
    summary["quota_used_today"] = quota_used_today
    summary["quota_remaining"] = max(quota_limit - quota_used_today, 0)


def _normalize_industry_name(name: Any) -> str:
    return (
        str(name or "")
        .strip()
        .replace("Ⅰ", "")
        .replace("Ⅱ", "")
        .replace("Ⅲ", "")
        .replace("（申万）", "")
        .replace(" ", "")
    )


def _build_summary(
    code: str,
    months: int,
    settings: dict[str, Any],
    db: LocalFileDB,
    recent_days: int | None = None,
) -> dict[str, Any]:
    resolved_recent_days = int(recent_days) if recent_days is not None else _resolve_recent_days(months)
    summary = {
        "provider": "tushare",
        "target_code": _base_code(code),
        "recent_months": months,
        "recent_days": resolved_recent_days,
        "target_source": "tushare",
        "recent_source": "tushare",
        "cache_root": str(settings["cache_root"]),
        "api_calls": 0,
        "new_share_api_calls": 0,
        "stock_basic_api_calls": 0,
        "daily_api_calls": 0,
        "daily_basic_api_calls": 0,
        "fixed_cache_hits": [],
        "variable_cache_hits": [],
        "api_fetched_fixed": [],
        "api_fetched_variable": [],
        "recent_requested_codes": [],
        "recent_returned_codes": [],
        "recent_pending_codes": [],
        "recent_sample_count": 0,
        "eastmoney_supplement_used": False,
        "eastmoney_recent_fallback_used": False,
        "target_fallback_used": False,
        "supplemented_fields": [],
        "industry_pe_source": "",
        "industry_pe_index_code": "",
        "industry_pe_index_name": "",
        "industry_pe_trade_date": "",
        "top_apply_marketcap_source": "",
        "online_issue_lwr_source": "",
        "reason": "",
        "token_env": settings["token_env"],
    }
    _refresh_quota_snapshot(summary, settings, db)
    return summary


def _call_tushare(
    api_name: str,
    params: dict[str, Any],
    fields: str,
    settings: dict[str, Any],
    db: LocalFileDB,
    summary: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    rows, error_message = tushare_helper._call_tushare_api(api_name, params, fields, settings, db)
    summary["api_calls"] += 1
    counter_key = f"{api_name}_api_calls"
    if counter_key in summary:
        summary[counter_key] += 1
    _refresh_quota_snapshot(summary, settings, db)
    return rows, error_message


def _extract_profile(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    profile = (record.get("fields") or {}).get("tushare_stock_basic")
    if isinstance(profile, dict):
        return dict(profile)
    return None


def _extract_new_share(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    profile = (record.get("fields") or {}).get("tushare_new_share")
    if isinstance(profile, dict):
        return dict(profile)
    return None


def _extract_new_share_range_snapshot(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    snapshot = (record.get("fields") or {}).get(NEW_SHARE_RANGE_FIELD_KEY)
    if not isinstance(snapshot, dict):
        return None
    cleaned_rows: list[dict[str, Any]] = []
    for row in snapshot.get("rows") or []:
        if isinstance(row, dict):
            cleaned_rows.append(dict(row))
    return {
        "start_date": str(snapshot.get("start_date") or "").strip(),
        "end_date": str(snapshot.get("end_date") or "").strip(),
        "rows": cleaned_rows,
        "updated_at": str(snapshot.get("updated_at") or record.get("updated_at") or "").strip(),
        "source": str(snapshot.get("source") or record.get("source") or "").strip(),
    }


def _extract_listing_day(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    snapshot = (record.get("fields") or {}).get("tushare_ipo_listing_day")
    if isinstance(snapshot, dict):
        return dict(snapshot)
    return None


def _calc_daily_average_price(daily_row: dict[str, Any]) -> float | None:
    volume_hands = tushare_helper._safe_float(daily_row.get("vol"))
    amount_thousand_yuan = tushare_helper._safe_float(daily_row.get("amount"))
    if volume_hands is None or amount_thousand_yuan is None:
        return None
    if volume_hands <= 0 or amount_thousand_yuan <= 0:
        return None
    return amount_thousand_yuan * 10 / volume_hands


def _extract_sw_classify(record: dict[str, Any] | None, field_name: str) -> list[dict[str, Any]] | None:
    if not record:
        return None
    rows = (record.get("fields") or {}).get(field_name)
    if not isinstance(rows, list):
        return None
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            cleaned.append(dict(row))
    return cleaned


def _extract_sw_daily_snapshot(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    snapshot = (record.get("fields") or {}).get(SW_DAILY_FIELD_KEY)
    if isinstance(snapshot, dict):
        return dict(snapshot)
    return None


def _extract_sw_daily_range_snapshot(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    snapshot = (record.get("fields") or {}).get(SW_DAILY_RANGE_FIELD_KEY)
    if not isinstance(snapshot, dict):
        return None
    cleaned_rows: list[dict[str, Any]] = []
    for row in snapshot.get("rows") or []:
        if isinstance(row, dict):
            cleaned_rows.append(dict(row))
    return {
        "start_date": str(snapshot.get("start_date") or "").strip(),
        "end_date": str(snapshot.get("end_date") or "").strip(),
        "rows": cleaned_rows,
        "updated_at": str(snapshot.get("updated_at") or record.get("updated_at") or "").strip(),
        "source": str(snapshot.get("source") or record.get("source") or "").strip(),
    }


def _save_stock_profile(
    db: LocalFileDB,
    ts_code: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    return db.save_fixed_record(
        ts_code,
        {
            "name": profile.get("name"),
            "industry": profile.get("industry"),
            "market": profile.get("market"),
            "exchange": profile.get("exchange"),
            "list_date": profile.get("list_date"),
            "tushare_stock_basic": profile,
        },
        source="tushare_api",
    )


def _save_new_share(
    db: LocalFileDB,
    ts_code: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    return db.save_fixed_record(
        ts_code,
        {
            "name": row.get("name"),
            "tushare_new_share": row,
        },
        source="tushare_api",
    )


def _save_new_share_range_snapshot(
    db: LocalFileDB,
    start_date: str,
    end_date: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    snapshot = {
        "start_date": start_date,
        "end_date": end_date,
        "rows": rows,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "tushare_api",
    }
    return db.save_variable_record(
        NEW_SHARE_RANGE_CACHE_CODE,
        {NEW_SHARE_RANGE_FIELD_KEY: snapshot},
        trade_date=end_date,
        source="tushare_api",
    )


def _save_listing_day(
    db: LocalFileDB,
    ts_code: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    return db.save_variable_record(
        ts_code,
        {"tushare_ipo_listing_day": snapshot},
        trade_date=snapshot.get("trade_date"),
        source="tushare_api",
    )


def _save_sw_classify(
    db: LocalFileDB,
    level: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    cache_code = f"{SW_CLASSIFY_CACHE_PREFIX}{level}"
    field_name = f"{SW_CLASSIFY_FIELD_PREFIX}{level.lower()}"
    return db.save_fixed_record(
        cache_code,
        {field_name: rows},
        source="tushare_api",
    )


def _save_sw_daily_snapshot(
    db: LocalFileDB,
    index_code: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    return db.save_variable_record(
        f"{SW_DAILY_CACHE_PREFIX}{index_code}",
        {SW_DAILY_FIELD_KEY: snapshot},
        trade_date=snapshot.get("trade_date"),
        source="tushare_api",
    )


def _save_sw_daily_range_snapshot(
    db: LocalFileDB,
    index_code: str,
    start_date: str,
    end_date: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    snapshot = {
        "start_date": start_date,
        "end_date": end_date,
        "rows": rows,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "tushare_api",
    }
    return db.save_variable_record(
        f"{SW_DAILY_CACHE_PREFIX}{index_code}",
        {SW_DAILY_RANGE_FIELD_KEY: snapshot},
        trade_date=end_date,
        source="tushare_api",
    )


def _clean_new_share_rows(rows: list[dict[str, Any]], db: LocalFileDB) -> list[dict[str, Any]]:
    cleaned_rows: list[dict[str, Any]] = []
    for row in rows:
        ts_code = _normalize_code(row.get("ts_code"))
        if not ts_code.endswith(".BJ"):
            continue
        current = {
            "ts_code": ts_code,
            "name": str(row.get("name") or "").strip() or _base_code(ts_code),
            "ipo_date": str(row.get("ipo_date") or "").strip(),
            "issue_date": str(row.get("issue_date") or "").strip(),
            "amount": tushare_helper._safe_float(row.get("amount")),
            "market_amount": tushare_helper._safe_float(row.get("market_amount")),
            "price": tushare_helper._safe_float(row.get("price")),
            "pe": tushare_helper._safe_float(row.get("pe")),
            "limit_amount": tushare_helper._safe_float(row.get("limit_amount")),
            "funds": tushare_helper._safe_float(row.get("funds")),
            "ballot": tushare_helper._safe_float(row.get("ballot")),
        }
        _save_new_share(db, ts_code, current)
        cleaned_rows.append(current)
    return cleaned_rows


def _sort_new_share_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("issue_date") or ""),
            str(row.get("ipo_date") or ""),
            str(row.get("ts_code") or ""),
        ),
        reverse=True,
    )


def _row_in_new_share_window(row: dict[str, Any], start_date: str, end_date: str) -> bool:
    for field_name in ("ipo_date", "issue_date"):
        ymd_text = _to_ymd(row.get(field_name))
        if ymd_text and start_date <= ymd_text <= end_date:
            return True
    return False


def _filter_new_share_rows(rows: list[dict[str, Any]], start_date: str, end_date: str) -> list[dict[str, Any]]:
    return _sort_new_share_rows([dict(row) for row in rows if _row_in_new_share_window(row, start_date, end_date)])


def _merge_new_share_rows(existing_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in existing_rows:
        ts_code = _normalize_code(row.get("ts_code"))
        if ts_code:
            merged[ts_code] = dict(row)
    for row in incoming_rows:
        ts_code = _normalize_code(row.get("ts_code"))
        if ts_code:
            merged[ts_code] = dict(row)
    return _sort_new_share_rows(list(merged.values()))


def _clean_sw_daily_rows(rows: list[dict[str, Any]], index_code: str) -> list[dict[str, Any]]:
    cleaned_rows: list[dict[str, Any]] = []
    for row in rows:
        trade_date = str(row.get("trade_date") or "").strip()
        if not trade_date:
            continue
        cleaned_rows.append(
            {
                "index_code": index_code,
                "trade_date": trade_date,
                "name": str(row.get("name") or "").strip(),
                "pe": tushare_helper._safe_float(row.get("pe")),
                "pb": tushare_helper._safe_float(row.get("pb")),
                "float_mv": tushare_helper._safe_float(row.get("float_mv")),
                "total_mv": tushare_helper._safe_float(row.get("total_mv")),
            }
        )
    return cleaned_rows


def _sort_sw_daily_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: str(row.get("trade_date") or ""), reverse=True)


def _filter_sw_daily_rows(rows: list[dict[str, Any]], start_date: str, end_date: str) -> list[dict[str, Any]]:
    return _sort_sw_daily_rows(
        [
            dict(row)
            for row in rows
            if start_date <= _to_ymd(row.get("trade_date")) <= end_date
        ]
    )


def _merge_sw_daily_rows(existing_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in existing_rows:
        trade_date = _to_ymd(row.get("trade_date"))
        if trade_date:
            merged[trade_date] = dict(row)
    for row in incoming_rows:
        trade_date = _to_ymd(row.get("trade_date"))
        if trade_date:
            merged[trade_date] = dict(row)
    return _sort_sw_daily_rows(list(merged.values()))


def _build_sw_daily_snapshot(row: dict[str, Any], index_code: str, anchor_trade_date: str) -> dict[str, Any]:
    return {
        "index_code": index_code,
        "index_name": str(row.get("name") or "").strip(),
        "trade_date": str(row.get("trade_date") or "").strip(),
        "anchor_trade_date": anchor_trade_date,
        "pe": tushare_helper._safe_float(row.get("pe")),
        "pb": tushare_helper._safe_float(row.get("pb")),
        "float_mv": tushare_helper._safe_float(row.get("float_mv")),
        "total_mv": tushare_helper._safe_float(row.get("total_mv")),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "tushare_sw_daily",
    }


def _get_stock_profile(
    ts_code: str,
    settings: dict[str, Any],
    db: LocalFileDB,
    summary: dict[str, Any],
) -> dict[str, Any] | None:
    fixed_record = db.load_fixed_record(ts_code)
    cached = _extract_profile(fixed_record)
    if cached and tushare_helper._snapshot_is_fresh({"updated_at": fixed_record.get("updated_at")}, settings["fixed_ttl"]):
        _record_summary_item(summary, "fixed_cache_hits", f"stock_basic:{ts_code}")
        return cached

    rows, error_message = _call_tushare(
        "stock_basic",
        {"ts_code": ts_code, "list_status": "L"},
        "ts_code,name,industry,market,exchange,list_date",
        settings,
        db,
        summary,
    )
    if error_message:
        summary["reason"] = summary["reason"] or error_message
        return cached
    if not rows:
        return cached

    profile = {
        "ts_code": ts_code,
        "name": str(rows[0].get("name") or "").strip() or _base_code(ts_code),
        "industry": str(rows[0].get("industry") or "").strip(),
        "market": str(rows[0].get("market") or "").strip(),
        "exchange": str(rows[0].get("exchange") or "").strip(),
        "list_date": str(rows[0].get("list_date") or "").strip(),
    }
    _save_stock_profile(db, ts_code, profile)
    _record_summary_item(summary, "api_fetched_fixed", f"stock_basic:{ts_code}")
    return profile


def _get_sw_classify_rows(
    level: str,
    settings: dict[str, Any],
    db: LocalFileDB,
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    cache_code = f"{SW_CLASSIFY_CACHE_PREFIX}{level}"
    field_name = f"{SW_CLASSIFY_FIELD_PREFIX}{level.lower()}"
    fixed_record = db.load_fixed_record(cache_code)
    cached = _extract_sw_classify(fixed_record, field_name)
    if cached and tushare_helper._snapshot_is_fresh({"updated_at": fixed_record.get("updated_at")}, settings["fixed_ttl"]):
        _record_summary_item(summary, "fixed_cache_hits", f"index_classify:{level}")
        return cached

    rows, error_message = _call_tushare(
        "index_classify",
        {"level": level, "src": "SW2021"},
        SW_CLASSIFY_FIELDS,
        settings,
        db,
        summary,
    )
    if error_message:
        summary["reason"] = summary["reason"] or error_message
        return cached or []
    _record_summary_item(summary, "api_fetched_fixed", f"index_classify:{level}")
    if not rows:
        return cached or []

    cleaned_rows = [
        {
            "index_code": str(row.get("index_code") or "").strip(),
            "industry_name": str(row.get("industry_name") or "").strip(),
            "parent_code": str(row.get("parent_code") or "").strip(),
            "level": str(row.get("level") or level).strip() or level,
            "industry_code": str(row.get("industry_code") or "").strip(),
            "is_pub": str(row.get("is_pub") or "").strip(),
            "src": str(row.get("src") or "SW2021").strip() or "SW2021",
        }
        for row in rows
        if str(row.get("index_code") or "").strip()
    ]
    _save_sw_classify(db, level, cleaned_rows)
    return cleaned_rows


def _fetch_new_share_range_api(
    start_date: str,
    end_date: str,
    settings: dict[str, Any],
    db: LocalFileDB,
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    rows, error_message = _call_tushare(
        "new_share",
        {"start_date": start_date, "end_date": end_date},
        "ts_code,name,ipo_date,issue_date,amount,market_amount,price,pe,limit_amount,funds,ballot",
        settings,
        db,
        summary,
    )
    if error_message:
        summary["reason"] = summary["reason"] or error_message
        return []
    _record_summary_item(summary, "api_fetched_variable", f"new_share_range:{start_date}:{end_date}")
    return _clean_new_share_rows(rows, db)


def _get_recent_new_share_rows(
    start_date: str,
    end_date: str,
    settings: dict[str, Any],
    db: LocalFileDB,
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    variable_record = db.load_variable_record(NEW_SHARE_RANGE_CACHE_CODE)
    cached_snapshot = _extract_new_share_range_snapshot(variable_record)
    cached_rows = list((cached_snapshot or {}).get("rows") or [])
    cached_start = _to_ymd((cached_snapshot or {}).get("start_date"))
    cached_end = _to_ymd((cached_snapshot or {}).get("end_date"))
    snapshot_fresh = tushare_helper._snapshot_is_fresh(cached_snapshot, settings["variable_ttl"])

    if cached_rows and snapshot_fresh and cached_start and cached_end and cached_start <= start_date and cached_end >= end_date:
        _record_summary_item(summary, "variable_cache_hits", f"new_share_range:{start_date}:{end_date}")
        return _filter_new_share_rows(cached_rows, start_date, end_date)

    merged_rows = list(cached_rows)
    if cached_rows:
        _record_summary_item(
            summary,
            "variable_cache_hits",
            f"new_share_range_seed:{cached_start or start_date}:{cached_end or end_date}",
        )

    fetch_segments: list[tuple[str, str]] = []
    if not cached_rows:
        fetch_segments.append((start_date, end_date))
    else:
        if cached_start and start_date < cached_start:
            lower_end = _shift_ymd(cached_start, -1)
            if start_date <= lower_end:
                fetch_segments.append((start_date, lower_end))

        if not snapshot_fresh or not cached_end or end_date > cached_end:
            refresh_backfill_days = max(int(settings.get("recent_trade_days", 12)), 3)
            if cached_end:
                refresh_start = max(start_date, _shift_ymd(cached_end, -refresh_backfill_days))
            else:
                refresh_start = start_date
            if refresh_start <= end_date:
                fetch_segments.append((refresh_start, end_date))

    for segment_start, segment_end in fetch_segments:
        segment_rows = _fetch_new_share_range_api(segment_start, segment_end, settings, db, summary)
        if not segment_rows:
            continue
        merged_rows = _merge_new_share_rows(merged_rows, segment_rows)

    if merged_rows:
        next_start = cached_start or start_date
        next_end = cached_end or end_date
        if start_date < next_start:
            next_start = start_date
        if end_date > next_end:
            next_end = end_date
        _save_new_share_range_snapshot(db, next_start, next_end, merged_rows)

    return _filter_new_share_rows(merged_rows, start_date, end_date)


def _get_new_share_row(
    ts_code: str,
    candidate_rows: list[dict[str, Any]],
    settings: dict[str, Any],
    db: LocalFileDB,
    summary: dict[str, Any],
    list_date_hint: str = "",
) -> dict[str, Any] | None:
    for row in candidate_rows:
        if _normalize_code(row.get("ts_code")) == ts_code:
            return row

    cached = _extract_new_share(db.load_fixed_record(ts_code))
    if cached:
        _record_summary_item(summary, "fixed_cache_hits", f"new_share:{ts_code}")
        return cached

    if not list_date_hint:
        profile = _get_stock_profile(ts_code, settings, db, summary)
        list_date_hint = str((profile or {}).get("list_date") or "")

    if list_date_hint:
        list_date = datetime.strptime(_to_ymd(list_date_hint), "%Y%m%d").date()
        start_date = (list_date - timedelta(days=45)).strftime("%Y%m%d")
        end_date = (list_date + timedelta(days=10)).strftime("%Y%m%d")
    else:
        start_date = (date.today() - timedelta(days=365 * 3)).strftime("%Y%m%d")
        end_date = date.today().strftime("%Y%m%d")

    rows = _fetch_new_share_range_api(start_date, end_date, settings, db, summary)
    for row in rows:
        if _normalize_code(row.get("ts_code")) == ts_code:
            _record_summary_item(summary, "api_fetched_fixed", f"new_share:{ts_code}")
            return row
    return cached


def _get_listing_day_snapshot(
    ts_code: str,
    listing_date: str,
    settings: dict[str, Any],
    db: LocalFileDB,
    summary: dict[str, Any],
) -> dict[str, Any] | None:
    variable_record = db.load_variable_record(ts_code)
    cached = _extract_listing_day(variable_record)
    if cached and cached.get("listing_date") == _to_iso_date(listing_date):
        if tushare_helper._snapshot_is_fresh(cached, settings["fixed_ttl"]) and tushare_helper._safe_float(cached.get("average_price")) is not None:
            _record_summary_item(summary, "variable_cache_hits", f"listing_day:{ts_code}:{_to_iso_date(listing_date)}")
            return cached

    trade_date = _to_ymd(listing_date)
    if not trade_date:
        return cached
    if trade_date > date.today().strftime("%Y%m%d"):
        return cached

    daily_rows, daily_error = _call_tushare(
        "daily",
        {"ts_code": ts_code, "start_date": trade_date, "end_date": trade_date},
        "ts_code,trade_date,open,high,low,close,pct_chg,vol,amount",
        settings,
        db,
        summary,
    )
    if daily_error:
        summary["reason"] = summary["reason"] or daily_error
        return cached

    daily_basic_rows, daily_basic_error = _call_tushare(
        "daily_basic",
        {"ts_code": ts_code, "start_date": trade_date, "end_date": trade_date},
        "ts_code,trade_date,close,turnover_rate,pe_ttm,pb,total_mv,circ_mv",
        settings,
        db,
        summary,
    )
    if daily_basic_error:
        summary["reason"] = summary["reason"] or daily_basic_error
        return cached

    daily_row = daily_rows[0] if daily_rows else {}
    daily_basic_row = daily_basic_rows[0] if daily_basic_rows else {}
    if not daily_row and not daily_basic_row:
        return cached

    snapshot = {
        "listing_date": _to_iso_date(listing_date),
        "trade_date": str(daily_row.get("trade_date") or daily_basic_row.get("trade_date") or ""),
        "open": tushare_helper._safe_float(daily_row.get("open")),
        "close": tushare_helper._safe_float(daily_row.get("close") or daily_basic_row.get("close")),
        "high": tushare_helper._safe_float(daily_row.get("high")),
        "low": tushare_helper._safe_float(daily_row.get("low")),
        "volume": tushare_helper._safe_float(daily_row.get("vol")),
        "amount": tushare_helper._safe_float(daily_row.get("amount")),
        "average_price": _calc_daily_average_price(daily_row),
        "pct_chg": tushare_helper._safe_float(daily_row.get("pct_chg")),
        "turnover_rate": tushare_helper._safe_float(daily_basic_row.get("turnover_rate")),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "tushare_api",
    }
    _save_listing_day(db, ts_code, snapshot)
    _record_summary_item(summary, "api_fetched_variable", f"listing_day:{ts_code}:{_to_iso_date(listing_date)}")
    return snapshot


def get_listing_day_average_price(
    code: str,
    listing_date: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = tushare_helper._build_settings(params)
    if not settings["token"]:
        return {"average_price": None, "source": "", "reason": f"Tushare token 未配置：{settings['token_env']}"}

    ts_code = _to_ts_code(code)
    if not tushare_helper._supports_tushare_code(ts_code):
        return {"average_price": None, "source": "", "reason": f"{code} 不是当前支持的 Tushare 股票代码。"}

    db = LocalFileDB(settings["cache_root"])
    summary = _build_summary(code, 1, settings, db, recent_days=1)
    snapshot = _get_listing_day_snapshot(ts_code, listing_date, settings, db, summary)
    average_price = tushare_helper._safe_float((snapshot or {}).get("average_price"))
    return {
        "average_price": average_price,
        "source": str((snapshot or {}).get("source") or "tushare_api").strip() if average_price is not None else "",
        "reason": str(summary.get("reason") or "").strip(),
        "summary": summary,
    }


def _build_exact_sw_candidates(industry_name: str, sw_rows: list[dict[str, Any]]) -> list[str]:
    normalized_name = _normalize_industry_name(industry_name)
    if not normalized_name:
        return []

    matches = [
        row
        for row in sw_rows
        if _normalize_industry_name(row.get("industry_name")) == normalized_name
    ]
    matches.sort(
        key=lambda row: (
            0 if str(row.get("is_pub") or "").strip() == "1" else 1,
            0 if str(row.get("level") or "").strip() == "L3" else 1,
            str(row.get("industry_name") or ""),
        )
    )
    return [str(row.get("index_code") or "").strip() for row in matches if str(row.get("index_code") or "").strip()]


def _iter_industry_pe_candidate_codes(
    industry_name: str,
    sw_rows: list[dict[str, Any]],
) -> list[str]:
    ordered_codes: list[str] = []
    for code in INDUSTRY_PE_ALIAS_CODES.get(industry_name, ()):
        _append_unique(ordered_codes, code)
    for code in _build_exact_sw_candidates(industry_name, sw_rows):
        _append_unique(ordered_codes, code)
    return ordered_codes


def _get_sw_daily_snapshot(
    index_code: str,
    anchor_trade_date: str,
    settings: dict[str, Any],
    db: LocalFileDB,
    summary: dict[str, Any],
) -> dict[str, Any] | None:
    cache_code = f"{SW_DAILY_CACHE_PREFIX}{index_code}"
    variable_record = db.load_variable_record(cache_code)
    cached = _extract_sw_daily_snapshot(variable_record)
    cached_range_snapshot = _extract_sw_daily_range_snapshot(variable_record)
    if cached and cached.get("anchor_trade_date") == anchor_trade_date:
        if tushare_helper._snapshot_is_fresh(cached, settings["fixed_ttl"]):
            _record_summary_item(summary, "variable_cache_hits", f"sw_daily:{index_code}:{anchor_trade_date}")
            return cached

    if not anchor_trade_date:
        return cached

    anchor_date = datetime.strptime(anchor_trade_date, "%Y%m%d").date()
    start_date = (anchor_date - timedelta(days=20)).strftime("%Y%m%d")
    cached_range_rows = list((cached_range_snapshot or {}).get("rows") or [])
    if cached_range_rows and tushare_helper._snapshot_is_fresh(cached_range_snapshot, settings["variable_ttl"]):
        reusable_rows = _filter_sw_daily_rows(cached_range_rows, start_date, anchor_trade_date)
        if reusable_rows:
            _record_summary_item(summary, "variable_cache_hits", f"sw_daily_range:{index_code}:{start_date}:{anchor_trade_date}")
            snapshot = _build_sw_daily_snapshot(reusable_rows[0], index_code, anchor_trade_date)
            _save_sw_daily_snapshot(db, index_code, snapshot)
            return snapshot

    range_rows = _get_sw_daily_rows(index_code, start_date, anchor_trade_date, settings, db, summary)
    for row in range_rows:
        if _to_ymd(row.get("trade_date")) > anchor_trade_date:
            continue
        snapshot = _build_sw_daily_snapshot(row, index_code, anchor_trade_date)
        _save_sw_daily_snapshot(db, index_code, snapshot)
        return snapshot
    return cached


def _fetch_sw_daily_range_api(
    index_code: str,
    start_date: str,
    end_date: str,
    settings: dict[str, Any],
    db: LocalFileDB,
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    rows, error_message = _call_tushare(
        "sw_daily",
        {
            "ts_code": index_code,
            "start_date": start_date,
            "end_date": end_date,
        },
        SW_DAILY_FIELDS,
        settings,
        db,
        summary,
    )
    if error_message:
        summary["reason"] = summary["reason"] or error_message
        return []
    _record_summary_item(summary, "api_fetched_variable", f"sw_daily_range:{index_code}:{start_date}:{end_date}")
    return _clean_sw_daily_rows(rows, index_code)


def _get_sw_daily_rows(
    index_code: str,
    start_date: str,
    end_date: str,
    settings: dict[str, Any],
    db: LocalFileDB,
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    cache_code = f"{SW_DAILY_CACHE_PREFIX}{index_code}"
    variable_record = db.load_variable_record(cache_code)
    cached_snapshot = _extract_sw_daily_range_snapshot(variable_record)
    cached_rows = list((cached_snapshot or {}).get("rows") or [])
    cached_start = _to_ymd((cached_snapshot or {}).get("start_date"))
    cached_end = _to_ymd((cached_snapshot or {}).get("end_date"))
    snapshot_fresh = tushare_helper._snapshot_is_fresh(cached_snapshot, settings["variable_ttl"])

    if cached_rows and snapshot_fresh and cached_start and cached_end and cached_start <= start_date and cached_end >= end_date:
        _record_summary_item(summary, "variable_cache_hits", f"sw_daily_range:{index_code}:{start_date}:{end_date}")
        return _filter_sw_daily_rows(cached_rows, start_date, end_date)

    merged_rows = list(cached_rows)
    if cached_rows:
        _record_summary_item(
            summary,
            "variable_cache_hits",
            f"sw_daily_range_seed:{index_code}:{cached_start or start_date}:{cached_end or end_date}",
        )

    fetch_segments: list[tuple[str, str]] = []
    if not cached_rows:
        fetch_segments.append((start_date, end_date))
    else:
        if cached_start and start_date < cached_start:
            lower_end = _shift_ymd(cached_start, -1)
            if start_date <= lower_end:
                fetch_segments.append((start_date, lower_end))

        if not snapshot_fresh or not cached_end or end_date > cached_end:
            refresh_backfill_days = max(int(settings.get("recent_trade_days", 12)), 3)
            refresh_start = max(start_date, _shift_ymd(end_date, -refresh_backfill_days))
            if refresh_start <= end_date:
                fetch_segments.append((refresh_start, end_date))

    for segment_start, segment_end in fetch_segments:
        segment_rows = _fetch_sw_daily_range_api(index_code, segment_start, segment_end, settings, db, summary)
        if not segment_rows:
            continue
        merged_rows = _merge_sw_daily_rows(merged_rows, segment_rows)

    if merged_rows:
        next_start = cached_start or start_date
        next_end = cached_end or end_date
        if start_date < next_start:
            next_start = start_date
        if end_date > next_end:
            next_end = end_date
        _save_sw_daily_range_snapshot(db, index_code, next_start, next_end, merged_rows)

    return _filter_sw_daily_rows(merged_rows, start_date, end_date)


def _is_ready_recent_sample_row(row: dict[str, Any], asof_ymd: str) -> bool:
    issue_date = _to_ymd(row.get("issue_date"))
    if not issue_date:
        return False
    return issue_date <= asof_ymd


def _get_industry_pe_snapshot(
    stock_industry: str,
    listing_date: str,
    settings: dict[str, Any],
    db: LocalFileDB,
    summary: dict[str, Any],
) -> dict[str, Any] | None:
    industry_name = str(stock_industry or "").strip()
    if not industry_name:
        return None

    anchor_trade_date = _to_ymd(listing_date) or date.today().strftime("%Y%m%d")
    sw_rows: list[dict[str, Any]] = []
    sw_rows.extend(_get_sw_classify_rows("L2", settings, db, summary))
    sw_rows.extend(_get_sw_classify_rows("L3", settings, db, summary))

    for candidate_code in _iter_industry_pe_candidate_codes(industry_name, sw_rows):
        snapshot = _get_sw_daily_snapshot(candidate_code, anchor_trade_date, settings, db, summary)
        if snapshot and snapshot.get("pe") is not None:
            return snapshot
    return None


def _build_recent_ipo_record(
    new_share_row: dict[str, Any],
    profile: dict[str, Any] | None,
    listing_day: dict[str, Any] | None,
) -> dict[str, Any] | None:
    issue_price = tushare_helper._safe_float(new_share_row.get("price"))
    close_price = tushare_helper._safe_float((listing_day or {}).get("close"))
    average_price = tushare_helper._safe_float((listing_day or {}).get("average_price"))
    if not new_share_row.get("issue_date") or close_price is None:
        return None

    ld_close_change = None
    if issue_price and issue_price > 0 and close_price is not None:
        ld_close_change = (close_price / issue_price - 1) * 100
    ld_average_change = None
    if issue_price and issue_price > 0 and average_price is not None:
        ld_average_change = (average_price / issue_price - 1) * 100

    ts_code = _normalize_code(new_share_row.get("ts_code"))
    return {
        "SECURITY_CODE": _base_code(ts_code),
        "SECURITY_NAME_ABBR": str(new_share_row.get("name") or (profile or {}).get("name") or _base_code(ts_code)).strip(),
        "LISTING_DATE": _to_iso_date(new_share_row.get("issue_date")),
        "APPLY_DATE": _to_iso_date(new_share_row.get("ipo_date")),
        "ISSUE_PRICE": issue_price,
        "AFTER_ISSUE_PE": tushare_helper._safe_float(new_share_row.get("pe")),
        "TOTAL_ISSUE_NUM": tushare_helper._safe_float(new_share_row.get("amount")),
        "OPEN_PRICE": tushare_helper._safe_float((listing_day or {}).get("open")),
        "CLOSE_PRICE": close_price,
        "AVERAGE_PRICE": average_price,
        "LD_CLOSE_CHANGE": ld_close_change,
        "LD_AVERAGE_CHANGE": ld_average_change,
        "average_price_source": "tushare_daily" if average_price is not None else "",
        "average_price_reason": "",
        "TURNOVERRATE": tushare_helper._safe_float((listing_day or {}).get("turnover_rate")),
        "INDUSTRY": str((profile or {}).get("industry") or "").strip(),
        "source": "tushare",
    }


def _merge_target_ipo_info(
    code: str,
    target_row: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    supplement: dict[str, Any] | None,
    summary: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(supplement or {})
    target_code = _base_code(code)
    merged["SECURITY_CODE"] = target_code
    merged["SECURITY_NAME_ABBR"] = str(
        (target_row or {}).get("name") or (profile or {}).get("name") or merged.get("SECURITY_NAME_ABBR") or target_code
    ).strip()
    merged["APPLY_DATE"] = _to_iso_date((target_row or {}).get("ipo_date") or merged.get("APPLY_DATE"))
    merged["LISTING_DATE"] = _to_iso_date((target_row or {}).get("issue_date") or (profile or {}).get("list_date") or merged.get("LISTING_DATE"))
    merged["TOTAL_ISSUE_NUM"] = tushare_helper._safe_float((target_row or {}).get("amount")) or tushare_helper._safe_float(
        merged.get("TOTAL_ISSUE_NUM")
    )
    merged["ISSUE_PRICE"] = tushare_helper._safe_float((target_row or {}).get("price")) or tushare_helper._safe_float(
        merged.get("ISSUE_PRICE")
    )
    merged["AFTER_ISSUE_PE"] = tushare_helper._safe_float((target_row or {}).get("pe")) or tushare_helper._safe_float(
        merged.get("AFTER_ISSUE_PE")
    )
    merged["INDUSTRY"] = str((profile or {}).get("industry") or merged.get("INDUSTRY") or "").strip()
    issue_price = tushare_helper._safe_float(merged.get("ISSUE_PRICE"))
    limit_amount = tushare_helper._safe_float((target_row or {}).get("limit_amount"))
    if issue_price and issue_price > 0 and limit_amount and limit_amount > 0:
        merged["TOP_APPLY_MARKETCAP"] = limit_amount * issue_price
        summary["top_apply_marketcap_source"] = "tushare_new_share"
        _discard_value(summary["supplemented_fields"], "TOP_APPLY_MARKETCAP")
    elif tushare_helper._safe_float(merged.get("TOP_APPLY_MARKETCAP")) is not None:
        summary["top_apply_marketcap_source"] = "eastmoney"

    ballot = tushare_helper._safe_float((target_row or {}).get("ballot"))
    if ballot and ballot > 0:
        merged["ONLINE_ISSUE_LWR"] = ballot
        summary["online_issue_lwr_source"] = "tushare_new_share"
        _discard_value(summary["supplemented_fields"], "ONLINE_ISSUE_LWR")
    elif tushare_helper._safe_float(merged.get("ONLINE_ISSUE_LWR")) is not None:
        summary["online_issue_lwr_source"] = "eastmoney"

    if supplement:
        summary["eastmoney_supplement_used"] = True
        for field_name in SUPPLEMENT_FIELD_NAMES:
            if field_name == "TOP_APPLY_MARKETCAP" and summary.get("top_apply_marketcap_source") == "tushare_new_share":
                continue
            if field_name == "ONLINE_ISSUE_LWR" and summary.get("online_issue_lwr_source") == "tushare_new_share":
                continue
            if field_name in supplement and supplement.get(field_name) not in (None, "", "--"):
                _append_unique(summary["supplemented_fields"], field_name)
    return merged


def _apply_tushare_industry_pe(
    ipo_info: dict[str, Any],
    industry_pe_snapshot: dict[str, Any] | None,
    summary: dict[str, Any],
) -> None:
    if industry_pe_snapshot and industry_pe_snapshot.get("pe") is not None:
        ipo_info["INDUSTRY_PE_NEW"] = industry_pe_snapshot.get("pe")
        if not str(ipo_info.get("SW_INDUSTRY") or "").strip():
            ipo_info["SW_INDUSTRY"] = str(industry_pe_snapshot.get("index_name") or "").strip()
        summary["industry_pe_source"] = "tushare_sw_daily"
        summary["industry_pe_index_code"] = str(industry_pe_snapshot.get("index_code") or "").strip()
        summary["industry_pe_index_name"] = str(industry_pe_snapshot.get("index_name") or "").strip()
        summary["industry_pe_trade_date"] = str(industry_pe_snapshot.get("trade_date") or "").strip()
        _discard_value(summary["supplemented_fields"], "INDUSTRY_PE_NEW")
        return

    summary["industry_pe_index_code"] = ""
    summary["industry_pe_index_name"] = ""
    summary["industry_pe_trade_date"] = ""
    if tushare_helper._safe_float(ipo_info.get("INDUSTRY_PE_NEW")) is not None:
        summary["industry_pe_source"] = "eastmoney"


def _build_eastmoney_bundle(
    code: str,
    months: int,
    summary: dict[str, Any],
) -> dict[str, Any]:
    recent_days = int(summary.get("recent_days") or _resolve_recent_days(months))
    summary["provider"] = "eastmoney"
    summary["target_source"] = "eastmoney"
    summary["recent_source"] = "eastmoney"
    summary["target_fallback_used"] = True
    summary["eastmoney_recent_fallback_used"] = True
    ipo_info = data_fetcher.fetch_ipo_info(_base_code(code))
    recent_ipos = data_fetcher.fetch_recent_ipos_by_days(recent_days)
    summary["recent_returned_codes"] = [str(item.get("SECURITY_CODE", "")).strip() for item in recent_ipos]
    summary["recent_sample_count"] = len(recent_ipos)
    return {"ipo_info": ipo_info, "recent_ipos": recent_ipos, "summary": summary}


def prepare_ipo_data(
    code: str,
    months: int,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = tushare_helper._build_settings(params)
    db = LocalFileDB(settings["cache_root"])
    recent_days = _resolve_recent_days(months, params)
    summary = _build_summary(code, months, settings, db, recent_days)

    if not settings["token"]:
        summary["reason"] = f"Tushare token 未配置，请先设置环境变量 {settings['token_env']}。"
        return _build_eastmoney_bundle(code, months, summary)
    target_ts_code = _to_ts_code(code)
    today_ymd = date.today().strftime("%Y%m%d")
    today_date = date.today()
    cutoff_ymd = (today_date - timedelta(days=max(recent_days, 40))).strftime("%Y%m%d")

    recent_new_share_rows = _get_recent_new_share_rows(cutoff_ymd, today_ymd, settings, db, summary)

    target_profile = _get_stock_profile(target_ts_code, settings, db, summary)
    target_new_share = _get_new_share_row(
        target_ts_code,
        recent_new_share_rows,
        settings,
        db,
        summary,
        list_date_hint=str((target_profile or {}).get("list_date") or ""),
    )

    supplement: dict[str, Any] | None = None
    try:
        supplement = data_fetcher.fetch_ipo_info(_base_code(code))
    except data_fetcher.DataFetcherError as exc:
        summary["reason"] = summary["reason"] or str(exc)

    if not target_new_share and supplement is None:
        summary["reason"] = summary["reason"] or "Tushare 未取到目标 IPO 核心字段，且东方财富补充也不可用。"
        return _build_eastmoney_bundle(code, months, summary)
    if not target_new_share and supplement is not None:
        summary["target_source"] = "eastmoney"
        summary["target_fallback_used"] = True

    recent_ipos: list[dict[str, Any]] = []
    requested_codes: list[str] = []
    for row in recent_new_share_rows:
        ts_code = _normalize_code(row.get("ts_code"))
        if not _is_ready_recent_sample_row(row, today_ymd):
            if _to_ymd(row.get("issue_date")):
                _record_summary_item(summary, "recent_pending_codes", _base_code(ts_code))
            continue
        requested_codes.append(_base_code(ts_code))
        profile = _get_stock_profile(ts_code, settings, db, summary)
        listing_day = _get_listing_day_snapshot(ts_code, str(row.get("issue_date") or ""), settings, db, summary)
        sample = _build_recent_ipo_record(row, profile, listing_day)
        if sample is None:
            continue
        if not _is_within_recent_days(sample.get("LISTING_DATE"), recent_days, today_date):
            continue
        recent_ipos.append(sample)

    if not recent_ipos:
        try:
            fallback_recent_ipos = data_fetcher.fetch_recent_ipos_by_days(recent_days)
        except data_fetcher.DataFetcherError as exc:
            summary["reason"] = summary["reason"] or str(exc)
            fallback_recent_ipos = []
        if fallback_recent_ipos:
            summary["recent_source"] = "eastmoney"
            summary["eastmoney_recent_fallback_used"] = True
            recent_ipos = fallback_recent_ipos

    ipo_info = _merge_target_ipo_info(code, target_new_share, target_profile, supplement, summary)
    industry_pe_snapshot = _get_industry_pe_snapshot(
        str((target_profile or {}).get("industry") or ipo_info.get("INDUSTRY") or ""),
        str(ipo_info.get("LISTING_DATE") or ""),
        settings,
        db,
        summary,
    )
    _apply_tushare_industry_pe(ipo_info, industry_pe_snapshot, summary)
    if summary["target_fallback_used"] and supplement:
        ipo_info = dict(supplement)
        _apply_tushare_industry_pe(ipo_info, None, summary)

    summary["recent_requested_codes"] = requested_codes
    summary["recent_returned_codes"] = [str(item.get("SECURITY_CODE", "")).strip() for item in recent_ipos]
    summary["recent_sample_count"] = len(recent_ipos)
    if supplement and summary["target_source"] == "tushare":
        summary["target_source"] = "tushare+eastmoney"

    return {
        "ipo_info": ipo_info,
        "recent_ipos": recent_ipos,
        "summary": summary,
    }
