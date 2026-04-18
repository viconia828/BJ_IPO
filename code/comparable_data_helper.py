from __future__ import annotations

import os
from typing import Any

import tushare_helper
import wind_helper


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
        return tushare_helper.get_comparable_valuations(codes, params=settings)

    return wind_helper.get_comparable_valuations(
        codes,
        str(settings.get("wind_channel", "disabled")),
        settings,
    )
