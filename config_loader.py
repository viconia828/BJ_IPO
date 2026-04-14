from __future__ import annotations

from pathlib import Path
from typing import Any


SECTION_INDUSTRY_MAPPING = "industry_mapping"


def _strip_inline_comment(raw_line: str) -> str:
    if "#" not in raw_line:
        return raw_line
    return raw_line.split("#", 1)[0]


def _parse_scalar(raw_value: str) -> Any:
    value = raw_value.strip()
    if value == "":
        return ""

    lowered = value.lower()
    if lowered in {"auto", "disabled", "api_only", "excel_only", "median", "mean", "ttm", "static", "time_decay"}:
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


def _validate_params(params: dict[str, Any]) -> None:
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
        raise ValueError("sample_weight_mode 仅支持 static 或 time_decay")

    if float(params.get("sample_decay_half_life_days", 20)) <= 0:
        raise ValueError("sample_decay_half_life_days 必须大于 0")


def load_params(filepath: str | Path = "策略参数.txt") -> dict[str, Any]:
    file_path = Path(filepath)
    if not file_path.exists():
        raise FileNotFoundError(f"参数文件不存在: {file_path}")

    params: dict[str, Any] = {"industry_mapping": {}}
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
                params[key] = _parse_scalar(raw_value)

    params["comparable_companies"] = _parse_list(params.get("comparable_companies", ""))
    params["old_shares_transfer"] = _parse_old_shares(params.get("old_shares_transfer", "auto"))
    params["wind_channel"] = str(params.get("wind_channel", "disabled")).strip().lower() or "disabled"
    params["stock_industry"] = str(params.get("stock_industry", "auto")).strip() or "auto"

    _validate_params(params)
    return params
