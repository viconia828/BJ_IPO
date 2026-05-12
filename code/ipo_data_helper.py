from __future__ import annotations

from typing import Any

import bse_official_helper
import data_fetcher
import listing_average_price_helper
import tushare_ipo_helper


SUPPORTED_PROVIDERS = {"eastmoney", "tushare"}


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
