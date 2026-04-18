from __future__ import annotations

from typing import Any

import data_fetcher
import tushare_ipo_helper


SUPPORTED_PROVIDERS = {"eastmoney", "tushare"}


def _build_eastmoney_summary(code: str, months: int, params: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = params or {}
    return {
        "provider": "eastmoney",
        "target_code": code,
        "recent_months": months,
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
        "reason": "",
        "token_env": str(settings.get("tushare_token_env", "TUSHARE_TOKEN")).strip() or "TUSHARE_TOKEN",
    }


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
        return tushare_ipo_helper.prepare_ipo_data(code, months, params=settings)

    ipo_info = data_fetcher.fetch_ipo_info(code)
    recent_ipos = data_fetcher.fetch_recent_ipos(months)
    summary = _build_eastmoney_summary(code, months, params=settings)
    summary["recent_returned_codes"] = [str(item.get("SECURITY_CODE", "")).strip() for item in recent_ipos]
    summary["recent_sample_count"] = len(recent_ipos)
    return {
        "ipo_info": ipo_info,
        "recent_ipos": recent_ipos,
        "summary": summary,
    }
