from __future__ import annotations

from pathlib import Path
from typing import Any


SECTION_INDUSTRY_MAPPING = "industry_mapping"
ROOT_DIR = Path(__file__).resolve().parents[1]
SYSTEM_SOURCE_DEFAULTS: dict[str, Any] = {
    "comparable_data_source": "tushare",
    "ipo_data_source": "tushare",
    "wind_channel": "disabled",
    "wind_cache_root": "data/wind_db",
    "tushare_api_url": "https://api.tushare.pro",
    "tushare_token_env": "TUSHARE_TOKEN",
    "tushare_cache_root": "data/tushare_db",
}
TUNING_RUNTIME_DEFAULTS: dict[str, Any] = {
    "tuning_replay_months": 18,
    "tuning_page_size": 100,
    "tuning_train_ratio": 0.70,
    "tuning_min_train_samples": 8,
    "tuning_top_n": 10,
}
DEFAULT_RECENT_DAYS = 90


def _strip_inline_comment(raw_line: str) -> str:
    if "#" not in raw_line:
        return raw_line
    return raw_line.split("#", 1)[0]


def _parse_scalar(raw_value: str) -> Any:
    value = raw_value.strip()
    if value == "":
        return ""

    lowered = value.lower()
    if lowered in {
        "auto",
        "disabled",
        "api_only",
        "excel_only",
        "median",
        "mean",
        "ttm",
        "static",
        "time_decay",
        "wind",
        "tushare",
        "eastmoney",
    }:
        return lowered

    try:
        if "." not in value and "e" not in lowered:
            return int(value)
        return float(value)
    except ValueError:
        return value


def _parse_list(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    return [item.strip() for item in str(raw_value).split(",") if item.strip()]


def _parse_old_shares(raw_value: Any) -> str | float:
    if raw_value is None:
        return "auto"

    text = str(raw_value).strip()
    if not text:
        return "auto"
    if text.lower() == "auto":
        return "auto"

    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"old_shares_transfer 配置非法: {raw_value}") from exc


def resolve_recent_days(params: dict[str, Any]) -> int:
    raw_days = params.get("recent_days")
    if raw_days not in (None, ""):
        return max(int(float(raw_days)), 1)

    raw_months = params.get("recent_months")
    if raw_months not in (None, ""):
        return max(int(float(raw_months)) * 30, 1)

    return DEFAULT_RECENT_DAYS


def _validate_params(params: dict[str, Any]) -> None:
    comparable_data_source = str(params.get("comparable_data_source", SYSTEM_SOURCE_DEFAULTS["comparable_data_source"])).strip().lower() or SYSTEM_SOURCE_DEFAULTS["comparable_data_source"]
    if comparable_data_source not in {"wind", "tushare"}:
        raise ValueError("comparable_data_source 仅支持 wind / tushare")

    ipo_data_source = str(params.get("ipo_data_source", SYSTEM_SOURCE_DEFAULTS["ipo_data_source"])).strip().lower() or SYSTEM_SOURCE_DEFAULTS["ipo_data_source"]
    if ipo_data_source not in {"eastmoney", "tushare"}:
        raise ValueError("ipo_data_source 仅支持 eastmoney / tushare")

    wind_channel = str(params.get("wind_channel", SYSTEM_SOURCE_DEFAULTS["wind_channel"])).strip().lower() or SYSTEM_SOURCE_DEFAULTS["wind_channel"]
    if wind_channel not in {"disabled", "auto", "api_only", "excel_only"}:
        raise ValueError("wind_channel 仅支持 disabled / auto / api_only / excel_only")

    weight_comparable = float(params.get("weight_comparable", 0.5))
    weight_industry = float(params.get("weight_industry_momentum", 0.5))
    if abs((weight_comparable + weight_industry) - 1.0) > 1e-6:
        raise ValueError("weight_comparable 与 weight_industry_momentum 之和必须为 1")

    industry_trend_weight = float(params.get("industry_trend_weight", 0.6))
    market_sentiment_weight = float(params.get("market_sentiment_weight", 0.4))
    if abs((industry_trend_weight + market_sentiment_weight) - 1.0) > 1e-6:
        raise ValueError("industry_trend_weight 与 market_sentiment_weight 之和必须为 1")

    wsi_weight_keys = [
        "wsi_weight_close_vwap",
        "wsi_weight_price_retention",
        "wsi_weight_high_timing",
        "wsi_weight_closing_momentum",
        "wsi_weight_volume_rhythm",
        "wsi_weight_turnover",
    ]
    total_wsi_weight = sum(float(params.get(key, 0.0)) for key in wsi_weight_keys)
    if total_wsi_weight <= 0:
        raise ValueError("WSI 评分维度权重之和必须大于 0")
    if abs(total_wsi_weight - 1.0) > 1e-6:
        raise ValueError("WSI 评分维度权重之和必须为 1")

    sample_weight_mode = str(params.get("sample_weight_mode", "static")).strip().lower() or "static"
    if sample_weight_mode not in {"static", "time_decay"}:
        raise ValueError("sample_weight_mode 仅支持 static / time_decay")

    if float(params.get("sample_decay_half_life_days", 20)) <= 0:
        raise ValueError("sample_decay_half_life_days 必须大于 0")
    if float(params.get("sentiment_decay_half_life_days", params.get("sample_decay_half_life_days", 20))) <= 0:
        raise ValueError("sentiment_decay_half_life_days must be greater than 0")
    if float(params.get("sentiment_first_day_scale", params.get("market_sentiment_weight", 0.15))) < 0:
        raise ValueError("sentiment_first_day_scale cannot be negative")
    if float(params.get("sentiment_post_listing_scale", params.get("market_sentiment_weight", 0.15))) < 0:
        raise ValueError("sentiment_post_listing_scale cannot be negative")
    if float(params.get("sentiment_premium_floor_pct", -20.0)) > float(params.get("sentiment_premium_cap_pct", 35.0)):
        raise ValueError("sentiment_premium_floor_pct must be <= sentiment_premium_cap_pct")
    if float(params.get("robust_median_min_samples", 4)) <= 0:
        raise ValueError("robust_median_min_samples must be greater than 0")
    if float(params.get("robust_mad_multiplier", 3.0)) <= 0:
        raise ValueError("robust_mad_multiplier must be greater than 0")
    if float(
        params.get(
            "subscription_prediction_sample_decay_half_life_days",
            params.get("sample_decay_half_life_days", 20),
        )
    ) <= 0:
        raise ValueError("subscription_prediction_sample_decay_half_life_days 必须大于 0")
    for direction_key in (
        "subscription_prediction_cap_factor_direction",
        "subscription_prediction_issue_factor_direction",
    ):
        direction_value = str(params.get(direction_key, "target_over_median")).strip()
        if direction_value not in {"target_over_median", "median_over_target"}:
            raise ValueError(f"{direction_key} 仅支持 target_over_median / median_over_target")
    for non_negative_key in (
        "subscription_prediction_cap_factor_exponent",
        "subscription_prediction_issue_factor_exponent",
        "subscription_prediction_lock_factor_exponent",
        "subscription_prediction_guaranteed_buffer_min_wan",
        "subscription_prediction_guaranteed_buffer_max_wan",
        "subscription_prediction_similar_top_apply_frozen_weight",
        "subscription_prediction_similar_top_apply_frozen_max_relative_distance",
    ):
        if float(params.get(non_negative_key, 0)) < 0:
            raise ValueError(f"{non_negative_key} 不能小于 0")
    if float(params.get("subscription_prediction_multiple_scale", 1.0)) <= 0:
        raise ValueError("subscription_prediction_multiple_scale 必须大于 0")
    for positive_key in (
        "subscription_prediction_similar_top_apply_frozen_recent_samples",
        "subscription_prediction_similar_top_apply_frozen_min_samples",
        "subscription_prediction_similar_top_apply_frozen_half_life_samples",
        "subscription_prediction_similar_top_apply_frozen_bandwidth",
    ):
        if float(params.get(positive_key, 1)) <= 0:
            raise ValueError(f"{positive_key} 必须大于 0")
    if float(params.get("subscription_prediction_guaranteed_buffer_max_wan", 100)) < float(
        params.get("subscription_prediction_guaranteed_buffer_min_wan", 50)
    ):
        raise ValueError("subscription_prediction_guaranteed_buffer_max_wan 必须大于等于 min_wan")
    if int(params.get("subscription_prediction_lot_threshold_max_lots", 20)) <= 0:
        raise ValueError("subscription_prediction_lot_threshold_max_lots 必须大于 0")
    if int(params.get("recent_days", DEFAULT_RECENT_DAYS)) <= 0:
        raise ValueError("recent_days 必须大于 0")
    if int(params.get("wind_daily_request_quota", 20)) < 0:
        raise ValueError("wind_daily_request_quota 不能小于 0")
    if int(params.get("wind_batch_size", 20)) <= 0:
        raise ValueError("wind_batch_size 必须大于 0")
    if float(params.get("wind_static_ttl_days", 3650)) <= 0:
        raise ValueError("wind_static_ttl_days 必须大于 0")
    if float(params.get("wind_dynamic_ttl_hours", 24)) <= 0:
        raise ValueError("wind_dynamic_ttl_hours 必须大于 0")
    if float(params.get("wind_request_pause_seconds", 0.2)) < 0:
        raise ValueError("wind_request_pause_seconds 不能小于 0")
    if int(params.get("tushare_daily_request_quota", 200)) < 0:
        raise ValueError("tushare_daily_request_quota 不能小于 0")
    if int(params.get('tushare_intraday_request_quota', params.get('tushare_daily_request_quota', 200))) < 0:
        raise ValueError('tushare_intraday_request_quota 不能小于 0')
    if int(params.get('tushare_non_intraday_daily_request_quota', 50000)) < 0:
        raise ValueError('tushare_non_intraday_daily_request_quota 不能小于 0')
    if float(params.get("tushare_request_pause_seconds", 0.12)) < 0:
        raise ValueError("tushare_request_pause_seconds 不能小于 0")
    if float(params.get("tushare_static_ttl_days", 3650)) <= 0:
        raise ValueError("tushare_static_ttl_days 必须大于 0")
    if float(params.get("tushare_dynamic_ttl_hours", 24)) <= 0:
        raise ValueError("tushare_dynamic_ttl_hours 必须大于 0")
    if int(params.get("tushare_recent_trade_days", 12)) <= 0:
        raise ValueError("tushare_recent_trade_days 必须大于 0")
    if int(params.get("eastmoney_backup_enabled", 1)) not in {0, 1}:
        raise ValueError("eastmoney_backup_enabled 仅支持 0 / 1")
    if int(params.get("eastmoney_validation_enabled", 1)) not in {0, 1}:
        raise ValueError("eastmoney_validation_enabled 仅支持 0 / 1")

    if int(params.get("tuning_replay_months", TUNING_RUNTIME_DEFAULTS["tuning_replay_months"])) <= 0:
        raise ValueError("tuning_replay_months 必须大于 0")
    if int(params.get("tuning_page_size", TUNING_RUNTIME_DEFAULTS["tuning_page_size"])) <= 0:
        raise ValueError("tuning_page_size 必须大于 0")

    tuning_train_ratio = float(params.get("tuning_train_ratio", TUNING_RUNTIME_DEFAULTS["tuning_train_ratio"]))
    if not (0 < tuning_train_ratio < 1):
        raise ValueError("tuning_train_ratio 必须在 0 与 1 之间")

    if int(params.get("tuning_min_train_samples", TUNING_RUNTIME_DEFAULTS["tuning_min_train_samples"])) <= 0:
        raise ValueError("tuning_min_train_samples 必须大于 0")
    if int(params.get("tuning_top_n", TUNING_RUNTIME_DEFAULTS["tuning_top_n"])) <= 0:
        raise ValueError("tuning_top_n 必须大于 0")


def get_tuning_runtime_settings(params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: params.get(key, default_value)
        for key, default_value in TUNING_RUNTIME_DEFAULTS.items()
    }


def load_params(filepath: str | Path = ROOT_DIR / "策略参数.txt") -> dict[str, Any]:
    file_path = Path(filepath)
    if not file_path.exists():
        raise FileNotFoundError(f"参数文件不存在: {file_path}")

    params: dict[str, Any] = {"industry_mapping": {}, **SYSTEM_SOURCE_DEFAULTS}
    current_section: str | None = None

    with file_path.open("r", encoding="utf-8-sig") as handle:
        for raw_line in handle:
            line = _strip_inline_comment(raw_line).strip()
            if not line:
                continue

            if line.startswith("[") and line.endswith("]"):
                current_section = line[1:-1].strip().lower()
                continue

            if "=" not in line:
                continue

            key, raw_value = line.split("=", 1)
            key = key.strip()
            raw_value = raw_value.strip()

            if current_section == SECTION_INDUSTRY_MAPPING:
                params["industry_mapping"][key] = raw_value
            else:
                if key in SYSTEM_SOURCE_DEFAULTS:
                    continue
                params[key] = _parse_scalar(raw_value)

    params["comparable_companies"] = _parse_list(params.get("comparable_companies", ""))
    params["old_shares_transfer"] = _parse_old_shares(params.get("old_shares_transfer", "auto"))
    params["comparable_data_source"] = str(params.get("comparable_data_source", SYSTEM_SOURCE_DEFAULTS["comparable_data_source"])).strip().lower() or SYSTEM_SOURCE_DEFAULTS["comparable_data_source"]
    params["ipo_data_source"] = str(params.get("ipo_data_source", SYSTEM_SOURCE_DEFAULTS["ipo_data_source"])).strip().lower() or SYSTEM_SOURCE_DEFAULTS["ipo_data_source"]
    params["wind_channel"] = str(params.get("wind_channel", SYSTEM_SOURCE_DEFAULTS["wind_channel"])).strip().lower() or SYSTEM_SOURCE_DEFAULTS["wind_channel"]
    params["wind_cache_root"] = str(params.get("wind_cache_root", SYSTEM_SOURCE_DEFAULTS["wind_cache_root"])).strip() or SYSTEM_SOURCE_DEFAULTS["wind_cache_root"]
    params["tushare_api_url"] = str(params.get("tushare_api_url", SYSTEM_SOURCE_DEFAULTS["tushare_api_url"])).strip() or SYSTEM_SOURCE_DEFAULTS["tushare_api_url"]
    params["tushare_token_env"] = str(params.get("tushare_token_env", SYSTEM_SOURCE_DEFAULTS["tushare_token_env"])).strip() or SYSTEM_SOURCE_DEFAULTS["tushare_token_env"]
    params["tushare_cache_root"] = str(params.get("tushare_cache_root", SYSTEM_SOURCE_DEFAULTS["tushare_cache_root"])).strip() or SYSTEM_SOURCE_DEFAULTS["tushare_cache_root"]
    params["stock_industry"] = str(params.get("stock_industry", "auto")).strip() or "auto"
    params["recent_days"] = resolve_recent_days(params)
    if "recent_months" not in params:
        params["recent_months"] = max((int(params["recent_days"]) + 29) // 30, 1)
    for key, default_value in TUNING_RUNTIME_DEFAULTS.items():
        params[key] = params.get(key, default_value)

    _validate_params(params)
    return params
