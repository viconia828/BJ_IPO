from __future__ import annotations

import os
from typing import Any

import tushare_helper
import wind_helper
from peer_fundamentals import attach_cached_fundamentals


SUPPORTED_PROVIDERS = {"wind", "tushare"}


def get_comparable_valuations(
    codes: list[str],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = params or {}
    provider = str(settings.get("comparable_data_source", "wind")).strip().lower() or "wind"
    if provider not in SUPPORTED_PROVIDERS:
        provider = "wind"

    tushare_token_env = str(settings.get("tushare_token_env", "TUSHARE_TOKEN")).strip() or "TUSHARE_TOKEN"
    if (
        provider == "wind"
        and os.getenv(tushare_token_env, "").strip()
        and str(settings.get("wind_channel", "disabled")).strip().lower() == "disabled"
    ):
        provider = "tushare"

    if provider == "tushare":
        result = tushare_helper.get_comparable_valuations(codes, params=settings)
    else:
        result = wind_helper.get_comparable_valuations(
            codes, str(settings.get("wind_channel", "disabled")), settings,
        )
    items, _ = attach_cached_fundamentals(
        result.get("items") or [], as_of=settings.get("fundamentals_as_of"))
    # Preserve business candidates even when they have no usable daily PE quote.
    candidates, fundamental_summary = attach_cached_fundamentals(
        [{"code": code} for code in dict.fromkeys(codes)], as_of=settings.get("fundamentals_as_of"))
    quoted_codes = {item.get("code") for item in items}
    fundamental_summary["missing_quote_codes"] = [c["code"] for c in candidates if c["code"] not in quoted_codes]
    return {**result, "items": items, "fundamental_candidates": candidates,
            "summary": {**(result.get("summary") or {}), "fundamentals": fundamental_summary}}
