from __future__ import annotations

from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Any

import bse_official_helper
import data_fetcher
import listing_average_price_helper
import tushare_ipo_helper


SUPPORTED_PROVIDERS = {"eastmoney", "tushare"}


def supplement_local_recent_listings(
    records: list[dict[str, Any]],
    days: int,
    *,
    target_code: str = "",
    target_apply_date: Any = None,
    as_of_date: date | None = None,
    root: Path = listing_average_price_helper.ROOT_DIR,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Bridge delayed IPO calendars using completed local first-day evidence."""
    def day(value: Any) -> date | None:
        try:
            return date.fromisoformat(str(value or "")[:10])
        except ValueError:
            return None

    end = as_of_date or date.today()
    apply = day(target_apply_date)
    if apply:
        end = min(end, apply - timedelta(days=1))
    start = end - timedelta(days=max(int(days), 1))
    result = [dict(row) for row in records]
    positions = {str(row.get("SECURITY_CODE") or ""): i for i, row in enumerate(result)}
    candidates = {code: dict(result[i]) for code, i in positions.items()}
    summary: dict[str, Any] = {"as_of_date": end.isoformat(), "appended_codes": [], "updated_codes": [], "audit": []}
    path = root / "data/offline_tuning/replay_dataset.json"
    try:
        history = json.loads(path.read_text(encoding="utf-8")).get("items") or []
    except (OSError, ValueError) as exc:
        history = []
        summary["history_error"] = str(exc)
    # Replay contributes issuer metadata only; prices and dates need a complete tape.
    metadata = ("SECURITY_CODE", "SECURITY_NAME_ABBR", "ISSUE_PRICE", "APPLY_DATE",
                "AFTER_ISSUE_PE", "INDUSTRY_PE_NEW", "TOTAL_ISSUE_NUM", "INDUSTRY", "INDUSTRY_CODE",
                "float_shares", "old_shares", "ONLINE_ISSUE_NUM", "TOP_APPLY_MARKETCAP")
    for row in history:
        code = str(row.get("SECURITY_CODE") or "")
        candidate = candidates.setdefault(code, {})
        for key in metadata:
            if candidate.get(key) in (None, "", "--") and row.get(key) not in (None, "", "--"):
                candidate[key] = row[key]
    for code, candidate in candidates.items():
        if code == target_code:
            continue
        issue = listing_average_price_helper.safe_float(candidate.get("ISSUE_PRICE"))
        if issue is None or not math.isfinite(issue) or issue <= 0:
            continue
        tape = listing_average_price_helper.completed_local_listing(code, root)
        if not tape:
            continue
        listing = day(tape["LISTING_DATE"])
        if listing is None or not start <= listing <= end:
            continue
        source = str(candidate.get("LISTING_DATE_SOURCE") or candidate.get("listing_date_source") or "")
        previous_date = day(candidate.get("LISTING_DATE"))
        placeholder = source == "apply_date_fallback" or previous_date == day(candidate.get("APPLY_DATE"))
        if previous_date and not placeholder and previous_date != listing:
            summary["audit"].append({"code": code, "reason": "listing_date_conflict",
                                     "record_date": previous_date.isoformat(), "tape_date": listing.isoformat()})
            continue
        merged = dict(candidate)
        changed = []
        if not previous_date or placeholder:
            merged.update({"LISTING_DATE": tape["LISTING_DATE"],
                           "LISTING_DATE_SOURCE": "dated_listing_intraday_csv",
                           "listing_date_source": "dated_listing_intraday_csv"})
            changed.append("LISTING_DATE")
        for field, change_field in (("AVERAGE_PRICE", "LD_AVERAGE_CHANGE"), ("CLOSE_PRICE", "LD_CLOSE_CHANGE")):
            value = listing_average_price_helper.safe_float(merged.get(field))
            if placeholder or value is None or not math.isfinite(value) or value <= 0:
                merged[field] = tape[field]
                changed.append(field)
                if field == "AVERAGE_PRICE":
                    merged["average_price_source"] = "intraday_csv"
            merged[change_field] = listing_average_price_helper.calc_change_pct(issue, merged[field])
        if code in positions:
            result[positions[code]] = merged
            if changed:
                summary["updated_codes"].append(code)
        else:
            positions[code] = len(result)
            result.append(merged)
            summary["appended_codes"].append(code)
        if changed:
            summary["audit"].append({"code": code, "fields": changed,
                                     "source": tape["path"], "sha256": tape["sha256"]})
    return result, summary


def _resolve_recent_days(months: int, params: dict[str, Any] | None = None) -> int:
    settings = params or {}
    raw_days = settings.get("recent_days")
    if raw_days not in (None, ""):
        return max(int(float(raw_days)), 1)
    return max(int(months) * 30, 1)


def _build_eastmoney_summary(code: str, months: int, params: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = params or {}
    recent_days = _resolve_recent_days(months, settings)
    return {
        "provider": "eastmoney",
        "target_code": code,
        "recent_months": months,
        "recent_days": recent_days,
        "target_source": "eastmoney",
        "recent_source": "eastmoney",
        "api_calls": 0,
        "new_share_api_calls": 0,
        "stock_basic_api_calls": 0,
        "daily_api_calls": 0,
        "daily_basic_api_calls": 0,
        "recent_requested_codes": [],
        "recent_returned_codes": [],
        "recent_pending_codes": [],
        "recent_sample_count": 0,
        "eastmoney_supplement_used": False,
        "eastmoney_recent_fallback_used": False,
        "target_fallback_used": False,
        "supplemented_fields": [],
        "average_price_source_counts": {},
        "average_price_missing_codes": [],
        "average_price_fallback_used": False,
        "reason": "",
        "token_env": str(settings.get("tushare_token_env", "TUSHARE_TOKEN")).strip() or "TUSHARE_TOKEN",
    }


def _fetch_bse_newshare_ipo_info(code: str, status_callback=None) -> dict[str, Any]:
    client = bse_official_helper.BSEOfficialClient(status_callback=status_callback)
    return client.build_newshare_ipo_info_by_post_listing_code(code)


def _resolve_record_average_price(
    record: dict[str, Any],
    params: dict[str, Any],
    *,
    try_tushare: bool,
) -> dict[str, Any]:
    for key in ("AVERAGE_PRICE", "AVG_PRICE", "VWAP"):
        average_price = listing_average_price_helper.safe_float(record.get(key))
        if average_price is not None and average_price > 0:
            source = str(record.get("average_price_source") or key).strip().lower()
            return {"average_price": average_price, "source": source, "reason": ""}

    code = str(record.get("SECURITY_CODE") or "").strip()
    listing_date = str(record.get("LISTING_DATE") or "").strip()
    if try_tushare and code and listing_date:
        try:
            tushare_result = tushare_ipo_helper.get_listing_day_average_price(code, listing_date, params=params)
        except Exception as exc:
            tushare_result = {"average_price": None, "source": "", "reason": str(exc)}
        average_price = listing_average_price_helper.safe_float(tushare_result.get("average_price"))
        if average_price is not None and average_price > 0:
            return {"average_price": average_price, "source": "tushare_daily", "reason": ""}

    if code:
        csv_result = listing_average_price_helper.resolve_intraday_average_price(code)
        average_price = listing_average_price_helper.safe_float(csv_result.get("average_price"))
        if average_price is not None and average_price > 0:
            return {
                "average_price": average_price,
                "source": str(csv_result.get("source") or "intraday_csv").strip(),
                "reason": str(csv_result.get("reason") or "").strip(),
            }
        return {
            "average_price": None,
            "source": "",
            "reason": str(csv_result.get("reason") or "未取得首日成交均价"),
        }

    return {"average_price": None, "source": "", "reason": "样本缺少代码，未取得首日成交均价"}


def _supplement_average_prices(
    records: list[dict[str, Any]],
    params: dict[str, Any],
    summary: dict[str, Any],
    *,
    try_tushare: bool,
) -> list[dict[str, Any]]:
    source_counts = summary.setdefault("average_price_source_counts", {})
    missing_codes = summary.setdefault("average_price_missing_codes", [])
    supplemented: list[dict[str, Any]] = []

    for record in records:
        item = dict(record)
        result = _resolve_record_average_price(item, params, try_tushare=try_tushare)
        average_price = listing_average_price_helper.safe_float(result.get("average_price"))
        code = str(item.get("SECURITY_CODE") or "").strip()
        if average_price is not None and average_price > 0:
            source = str(result.get("source") or "unknown").strip() or "unknown"
            item["AVERAGE_PRICE"] = average_price
            item["LD_AVERAGE_CHANGE"] = listing_average_price_helper.calc_change_pct(item.get("ISSUE_PRICE"), average_price)
            item["average_price_source"] = source
            item["average_price_reason"] = str(result.get("reason") or "").strip()
            if isinstance(source_counts, dict):
                source_counts[source] = int(source_counts.get(source, 0)) + 1
            if source.startswith("intraday_csv"):
                summary["average_price_fallback_used"] = True
        else:
            item["average_price_reason"] = str(result.get("reason") or "").strip()
            if code and isinstance(missing_codes, list):
                missing_codes.append(code)
        supplemented.append(item)

    return supplemented


def prepare_ipo_data(
    code: str,
    months: int,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = params or {}
    provider = str(settings.get("ipo_data_source", "eastmoney")).strip().lower() or "eastmoney"
    if provider not in SUPPORTED_PROVIDERS:
        provider = "eastmoney"

    if provider == "tushare":
        bundle = tushare_ipo_helper.prepare_ipo_data(code, months, params=settings)
        summary = bundle.get("summary") or {}
        bundle["recent_ipos"] = _supplement_average_prices(
            bundle.get("recent_ipos") or [],
            settings,
            summary,
            try_tushare=False,
        )
        bundle["summary"] = summary
        return bundle

    summary = _build_eastmoney_summary(code, months, params=settings)
    try:
        ipo_info = data_fetcher.fetch_ipo_info(code)
    except data_fetcher.DataFetcherError as exc:
        try:
            ipo_info = _fetch_bse_newshare_ipo_info(code)
        except bse_official_helper.BSEOfficialError as fallback_exc:
            raise data_fetcher.DataFetcherError(
                f"东方财富未查询到目标新股，且北交所公开发行一览兜底失败：{fallback_exc}"
            ) from exc
        summary["target_source"] = "bse_newshare"
        summary["target_fallback_used"] = True
        summary["reason"] = f"东方财富目标新股数据不可用，已改用北交所公开发行一览：{exc}"

    recent_days = _resolve_recent_days(months, settings)
    recent_ipos = data_fetcher.fetch_recent_ipos_by_days(recent_days)
    recent_ipos = _supplement_average_prices(recent_ipos, settings, summary, try_tushare=True)
    summary["recent_returned_codes"] = [str(item.get("SECURITY_CODE", "")).strip() for item in recent_ipos]
    summary["recent_sample_count"] = len(recent_ipos)
    return {
        "ipo_info": ipo_info,
        "recent_ipos": recent_ipos,
        "summary": summary,
    }
