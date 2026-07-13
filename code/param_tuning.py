from __future__ import annotations

import itertools
import json
import math
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import bse_ipo_valuation
import data_fetcher
import listing_average_price_helper
import pdf_parser
import subscription_ladder_labels
import tushare_helper
import tushare_ipo_helper
import valuation_engine
from industry_mapping import IndustryMapper
from local_file_db import LocalFileDB


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_PATH = REPO_ROOT / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_REPLAY_ITEM_CACHE_DIR = REPO_ROOT / "data" / "offline_tuning" / "replay_items"
DEFAULT_LADDER_LABEL_PATH = REPO_ROOT / "data" / "offline_tuning" / "subscription_ladder_labels.csv"
DEFAULT_LISTING_AVERAGE_PRICE_CACHE_PATH = listing_average_price_helper.DEFAULT_CACHE_PATH
DEFAULT_CANDIDATE_SET_DIR = REPO_ROOT / "data" / "offline_tuning" / "candidate_sets"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "输出" / "调参"
DEFAULT_OBSERVE_OUTPUT_DIR = REPO_ROOT / "输出" / "观察期"
DEFAULT_AUTO_TUNING_RECORD_PATH = REPO_ROOT / "自动调参记录.txt"
INTRADAY_DIR = listing_average_price_helper.DEFAULT_INTRADAY_DIR
PDF_DIR = REPO_ROOT / "公告文件"
DATASET_SCHEMA = "offline_tuning_replay_v1"
REPLAY_ITEM_SCHEMA = "offline_tuning_replay_item_v1"
REPLAY_ITEM_CACHE_VERSION = 8
REPLAY_RECORD_SIGNATURE_VERSION = 2
REPLAY_AVERAGE_PRICE_CALC_VERSION = listing_average_price_helper.AVERAGE_PRICE_CALC_VERSION
METHOD2_ONLY_SCOPE = "method2_only"
COMPOSITE_EVALUATION_SCOPE = "composite"
SUPPORTED_EVALUATION_SCOPES = {METHOD2_ONLY_SCOPE, COMPOSITE_EVALUATION_SCOPE}
EVALUATION_SCOPE = COMPOSITE_EVALUATION_SCOPE
WSI_WEIGHT_KEYS = [
    "wsi_weight_close_vwap",
    "wsi_weight_price_retention",
    "wsi_weight_high_timing",
    "wsi_weight_closing_momentum",
    "wsi_weight_volume_rhythm",
    "wsi_weight_turnover",
]
METHOD2_UNSUPPORTED_KEYS = {
    "weight_comparable",
    "weight_industry_momentum",
}
AUTO_NORMALIZE_WEIGHT_GROUPS: tuple[tuple[str, ...], ...] = (
    ("weight_comparable", "weight_industry_momentum"),
    ("industry_trend_weight", "market_sentiment_weight"),
    tuple(WSI_WEIGHT_KEYS),
)
AUTO_NORMALIZE_EPSILON = 1e-9
AUTO_TUNE_LOOKBACK_DAYS = 90
AUTO_TUNE_RECENT_FLOOR_DAYS = 30
AUTO_TUNE_RECENT_MIN_TOTAL_WEIGHT = 0.50
AUTO_TUNE_WIDTH_DIAGNOSTIC_FACTOR = 0.50
AUTO_TUNE_MAE_PENALTY = 0.002
AUTO_TUNE_STAGE_TIME_LIMIT_SECONDS = 180.0
AUTO_TUNE_STAGE_CANDIDATE_LIMIT = 650
AUTO_TUNE_MODEL_CONTRACT_VERSION = 3
LATEST_METHOD1_AUTO_TUNABLE_KEYS = {
    "bse_discount_factor",
    "weight_comparable",
    "weight_industry_momentum",
    "float_size_threshold",
    "small_cap_premium",
    "pe_low_threshold",
    "pe_discount_boost",
    "pe_high_threshold",
    "pe_premium_drag",
    "method1_industry_fallback_confidence",
    "comparable_pe_stat",
}
LATEST_METHOD2_AUTO_TUNABLE_KEYS = {
    "method2_weight_mode",
    "method2_decay_half_life_days",
    "robust_median_min_samples",
    "robust_mad_multiplier",
}
LATEST_METHOD2_CONFIDENCE_AUTO_TUNABLE_KEYS = {
    "method2_confidence_1_sample",
    "method2_confidence_2_samples",
    "method2_confidence_3_samples",
    "method2_confidence_4plus_samples",
}
LATEST_METHOD3_AUTO_TUNABLE_KEYS = {
    "recent_days",
    "sentiment_decay_half_life_days",
    "sentiment_first_day_baseline_pct",
    "sentiment_first_day_scale",
    "sentiment_post_listing_scale",
    "sentiment_premium_cap_pct",
    "sentiment_premium_floor_pct",
}
LATEST_LOCAL_CENTER_AUTO_TUNABLE_KEYS = {
    "local_center_alpha",
    "local_center_min_history",
    "local_center_history_window",
    "local_center_actual_cap_pct",
    "local_center_slope_cap",
}
LATEST_MODEL_STRUCTURAL_FLAGS = (
    "method1_pe_float_factors_enabled",
    "method1_industry_fallback_enabled",
    "method2_sample_confidence_enabled",
    "local_center_overlay_enabled",
)
REPLAY_RECORD_SIGNATURE_KEYS = (
    "SECURITY_CODE",
    "SECURITY_NAME_ABBR",
    "APPLY_DATE",
    "LISTING_DATE",
    "ISSUE_PRICE",
    "AFTER_ISSUE_PE",
    "INDUSTRY_PE_NEW",
    "TOTAL_ISSUE_NUM",
    "ISSUE_NUM",
    "ONLINE_ISSUE_NUM",
    "TOP_APPLY_MARKETCAP",
    "SUBSCRIPTION_LIMIT_WAN_SHARES",
    "CLOSE_PRICE",
    "LD_CLOSE_CHANGE",
    "NEXT_DAY_CLOSE",
    "THIRD_DAY_CLOSE",
    "NEXT_DAY_CLOSE_CHANGE",
    "THIRD_DAY_CLOSE_CHANGE",
    "NEXT_DAY_FROM_LISTING_CLOSE_PCT",
    "THIRD_DAY_FROM_LISTING_CLOSE_PCT",
    "POST_LISTING_PROFIT_EFFECT_PCT",
    "TURNOVERRATE",
    "SW_INDUSTRY",
    "INDUSTRY",
    "INDUSTRY_CODE",
    "industry_primary",
    "industry_secondary",
    "industry_source",
)
REPLAY_DERIVED_POST_LISTING_KEYS = {
    "NEXT_DAY_CLOSE",
    "THIRD_DAY_CLOSE",
    "NEXT_DAY_CLOSE_CHANGE",
    "THIRD_DAY_CLOSE_CHANGE",
    "NEXT_DAY_FROM_LISTING_CLOSE_PCT",
    "THIRD_DAY_FROM_LISTING_CLOSE_PCT",
    "POST_LISTING_PROFIT_EFFECT_PCT",
}
REPLAY_EXISTING_ITEM_SIGNATURE_KEYS = tuple(
    key
    for key in REPLAY_RECORD_SIGNATURE_KEYS
    if key != "ISSUE_NUM" and key not in REPLAY_DERIVED_POST_LISTING_KEYS
)


def _build_wsi_turnover_candidates() -> list[dict[str, float]]:
    candidates: list[dict[str, float]] = []

    def add_candidate(
        close_vwap: float,
        price_retention: float,
        high_timing: float,
        closing_momentum: float,
        volume_rhythm: float,
        turnover: float,
    ) -> None:
        candidates.append(
            {
                "wsi_weight_close_vwap": close_vwap,
                "wsi_weight_price_retention": price_retention,
                "wsi_weight_high_timing": high_timing,
                "wsi_weight_closing_momentum": closing_momentum,
                "wsi_weight_volume_rhythm": volume_rhythm,
                "wsi_weight_turnover": turnover,
            }
        )

    # Theme 1: let price retention give up weight to turnover.
    for turnover, price_retention in (
        (0.05, 0.20),
        (0.10, 0.15),
        (0.15, 0.10),
        (0.20, 0.05),
    ):
        add_candidate(0.30, price_retention, 0.20, 0.15, 0.10, turnover)

    # Theme 2: pair turnover with closing momentum first, then volume rhythm.
    for turnover, closing_momentum, volume_rhythm in (
        (0.05, 0.10, 0.10),
        (0.10, 0.05, 0.10),
        (0.15, 0.00, 0.10),
        (0.20, 0.00, 0.05),
    ):
        add_candidate(0.30, 0.25, 0.20, closing_momentum, volume_rhythm, turnover)

    # Theme 3: jointly pull weight from price retention and closing momentum.
    for turnover, price_retention, closing_momentum in (
        (0.05, 0.22, 0.13),
        (0.10, 0.19, 0.11),
        (0.15, 0.16, 0.09),
        (0.20, 0.13, 0.07),
    ):
        add_candidate(0.30, price_retention, 0.20, closing_momentum, 0.10, turnover)

    # Theme 4: pull jointly from close-vwap strength and price retention.
    for turnover, close_vwap, price_retention in (
        (0.05, 0.28, 0.22),
        (0.10, 0.26, 0.19),
        (0.15, 0.24, 0.16),
        (0.20, 0.22, 0.13),
    ):
        add_candidate(close_vwap, price_retention, 0.20, 0.15, 0.10, turnover)

    return candidates

SEARCH_STAGE_GRIDS: dict[str, dict[str, list[Any]]] = {
    "composite_weights": OrderedDict(
        [
            ("weight_comparable", [0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]),
        ]
    ),
    "quick_method2": OrderedDict(
        [
            ("price_range_width", [0.08, 0.10, 0.12]),
            ("small_cap_premium", [0.05, 0.10, 0.15]),
            ("float_size_threshold", [1500, 2000, 2500]),
            ("pe_low_threshold", [0.25, 0.30]),
            ("pe_discount_boost", [0.05, 0.10]),
            ("pe_high_threshold", [0.55, 0.60]),
            ("pe_premium_drag", [-0.05, -0.10]),
        ]
    ),
    "quick_method2_pe_focus": OrderedDict(
        [
            ("price_range_width", [0.12]),
            ("small_cap_premium", [0.10]),
            ("float_size_threshold", [1500]),
            ("pe_low_threshold", [0.20, 0.25, 0.30, 0.35]),
            ("pe_discount_boost", [0.05, 0.10, 0.15]),
            ("pe_high_threshold", [0.55, 0.60]),
            ("pe_premium_drag", [-0.05, -0.10]),
        ]
    ),
    "time_decay": OrderedDict(
        [
            ("sample_weight_mode", ["time_decay"]),
            ("sample_decay_half_life_days", [10, 20, 30, 45]),
            ("price_range_width", [0.08, 0.10, 0.12]),
        ]
    ),
}

SEARCH_STAGE_CANDIDATES: dict[str, list[dict[str, Any]]] = {
    "trend_balance": [
        {
            "industry_trend_weight": industry_weight,
            "market_sentiment_weight": round(1 - industry_weight, 2),
            "sample_decay_half_life_days": half_life,
            "trend_strong_threshold": strong_threshold,
            "trend_weak_threshold": weak_threshold,
        }
        for industry_weight in (0.40, 0.50, 0.60, 0.70)
        for half_life in (10, 20, 30)
        for strong_threshold, weak_threshold in ((65, 35), (70, 40), (75, 45))
    ],
    "wsi_turnover_balance": _build_wsi_turnover_candidates(),
}


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_enabled(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off", "否", "关闭"}


def _parse_date_key(value: Any) -> tuple[int, str]:
    text = str(value or "").strip().split(" ", 1)[0]
    if not text:
        return (0, "")
    return (1, text.replace("/", "-"))


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip().split(" ", 1)[0].replace("/", "-")
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _previous_day_yyyymmdd(value: Any) -> str | None:
    parsed = _parse_date(value)
    if parsed is None:
        return None
    return (parsed - timedelta(days=1)).strftime("%Y%m%d")


def _replay_cache_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    text = str(value).strip()
    return text if text else None


def _build_replay_record_signature(record: dict[str, Any]) -> dict[str, Any]:
    return {key: _replay_cache_scalar(record.get(key)) for key in REPLAY_RECORD_SIGNATURE_KEYS}


def _build_replay_refresh_contract() -> dict[str, Any]:
    return {
        "record_signature_version": REPLAY_RECORD_SIGNATURE_VERSION,
        "record_signature_keys": list(REPLAY_RECORD_SIGNATURE_KEYS),
        "pdf_parser_versions": dict(pdf_parser.PARSE_CACHE_KIND_VERSIONS),
    }


def _is_existing_replay_item_compatible(item: dict[str, Any], record_signature: dict[str, Any]) -> bool:
    item_signature = {key: _replay_cache_scalar(item.get(key)) for key in REPLAY_EXISTING_ITEM_SIGNATURE_KEYS}
    record_subset = {key: record_signature.get(key) for key in REPLAY_EXISTING_ITEM_SIGNATURE_KEYS}
    if item_signature != record_subset:
        return False
    if "AVERAGE_PRICE" not in item or "average_price_source" not in item:
        return False
    try:
        item_calc_version = int(item.get("average_price_calc_version"))
    except (TypeError, ValueError):
        item_calc_version = None
    if item_calc_version != REPLAY_AVERAGE_PRICE_CALC_VERSION:
        return False
    return True


def _existing_replay_item_signature_matches(item: dict[str, Any], record_signature: dict[str, Any]) -> bool:
    item_signature = {key: _replay_cache_scalar(item.get(key)) for key in REPLAY_EXISTING_ITEM_SIGNATURE_KEYS}
    record_subset = {key: record_signature.get(key) for key in REPLAY_EXISTING_ITEM_SIGNATURE_KEYS}
    return item_signature == record_subset


def _cached_replay_pdf_signature_matches(
    code: str,
    pdf_signature: dict[str, Any],
    cache_dir: str | Path,
) -> bool | None:
    cache_path = _replay_item_cache_path(code, cache_dir)
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if payload.get("schema") != REPLAY_ITEM_SCHEMA:
        return False
    if payload.get("cache_version") != REPLAY_ITEM_CACHE_VERSION:
        return False
    if str(payload.get("code") or "").strip() != str(code or "").strip():
        return False
    return payload.get("pdf_signature") == pdf_signature


def _cached_replay_record_signature_matches(
    code: str,
    record_signature: dict[str, Any],
    cache_dir: str | Path,
) -> bool | None:
    cache_path = _replay_item_cache_path(code, cache_dir)
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if payload.get("schema") != REPLAY_ITEM_SCHEMA:
        return False
    if payload.get("cache_version") != REPLAY_ITEM_CACHE_VERSION:
        return False
    if str(payload.get("code") or "").strip() != str(code or "").strip():
        return False
    cached_signature = payload.get("record_signature") or {}
    cached_subset = {key: cached_signature.get(key) for key in REPLAY_EXISTING_ITEM_SIGNATURE_KEYS}
    current_subset = {key: record_signature.get(key) for key in REPLAY_EXISTING_ITEM_SIGNATURE_KEYS}
    return cached_subset == current_subset


def _file_signature(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return {"name": path.name, "missing": True}
    return {
        "name": path.name,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _resolve_listing_average_price(
    params: dict[str, Any],
    code: str,
    listing_date: Any,
    record: dict[str, Any],
    prefer_local_intraday: bool = False,
) -> dict[str, Any]:
    for key in ("AVERAGE_PRICE", "AVG_PRICE", "VWAP"):
        average_price = _safe_float(record.get(key))
        if average_price is not None and average_price > 0:
            source = str(record.get("average_price_source") or key).strip().lower()
            return {
                "average_price": average_price,
                "source": source,
                "reason": "",
                "calc_version": record.get("average_price_calc_version") or REPLAY_AVERAGE_PRICE_CALC_VERSION,
                "unit_mode": record.get("average_price_unit_mode") or record.get("unit_mode"),
                "price_reference": record.get("average_price_reference") or record.get("price_reference"),
            }

    local_intraday_result: dict[str, Any] | None = None
    if prefer_local_intraday:
        local_intraday_result = listing_average_price_helper.resolve_intraday_average_price(code)
        average_price = _safe_float(local_intraday_result.get("average_price"))
        if average_price is not None and average_price > 0:
            return {
                "average_price": average_price,
                "source": str(local_intraday_result.get("source") or "intraday_csv").strip(),
                "reason": str(local_intraday_result.get("reason") or "").strip(),
                "calc_version": local_intraday_result.get("calc_version") or REPLAY_AVERAGE_PRICE_CALC_VERSION,
                "unit_mode": local_intraday_result.get("unit_mode"),
                "price_reference": local_intraday_result.get("price_reference"),
            }

    listing_date_text = str(listing_date or "").strip()
    if listing_date_text:
        try:
            tushare_result = tushare_ipo_helper.get_listing_day_average_price(code, listing_date_text, params=params)
        except Exception as exc:
            tushare_result = {"average_price": None, "source": "", "reason": str(exc)}
        average_price = _safe_float(tushare_result.get("average_price"))
        if average_price is not None and average_price > 0:
            return {
                "average_price": average_price,
                "source": "tushare_daily",
                "reason": "",
                "calc_version": REPLAY_AVERAGE_PRICE_CALC_VERSION,
                "unit_mode": "tushare_daily_amount_thousand_yuan",
                "price_reference": None,
            }

    csv_result = local_intraday_result or listing_average_price_helper.resolve_intraday_average_price(code)
    average_price = _safe_float(csv_result.get("average_price"))
    if average_price is not None and average_price > 0:
        return {
            "average_price": average_price,
            "source": str(csv_result.get("source") or "intraday_csv").strip(),
            "reason": str(csv_result.get("reason") or "").strip(),
            "calc_version": csv_result.get("calc_version") or REPLAY_AVERAGE_PRICE_CALC_VERSION,
            "unit_mode": csv_result.get("unit_mode"),
            "price_reference": csv_result.get("price_reference"),
        }

    return {
        "average_price": None,
        "source": "",
        "reason": str(csv_result.get("reason") or "未取得首日成交均价"),
    }


def _resolve_post_listing_performance(
    params: dict[str, Any],
    code: str,
    listing_date: Any,
    issue_price: Any,
    record: dict[str, Any],
) -> dict[str, Any]:
    existing = {
        "next_close": _safe_float(record.get("NEXT_DAY_CLOSE")),
        "third_close": _safe_float(record.get("THIRD_DAY_CLOSE")),
        "next_day_change_pct": _safe_float(record.get("NEXT_DAY_CLOSE_CHANGE") or record.get("NEXT_DAY_CHANGE")),
        "third_day_change_pct": _safe_float(record.get("THIRD_DAY_CLOSE_CHANGE") or record.get("THIRD_DAY_CHANGE")),
        "next_day_from_listing_close_pct": _safe_float(record.get("NEXT_DAY_FROM_LISTING_CLOSE_PCT")),
        "third_day_from_listing_close_pct": _safe_float(record.get("THIRD_DAY_FROM_LISTING_CLOSE_PCT")),
        "post_listing_profit_effect_pct": _safe_float(record.get("POST_LISTING_PROFIT_EFFECT_PCT")),
        "source": str(record.get("post_listing_performance_source") or "").strip(),
        "reason": "",
    }
    if any(existing.get(key) is not None for key in ("next_close", "third_close", "post_listing_profit_effect_pct")):
        existing["source"] = existing["source"] or "record"
        return existing

    listing_date_text = str(listing_date or "").strip()
    if not listing_date_text:
        return {"source": "", "reason": "Missing listing date; post-listing performance cannot be calculated."}
    if _safe_float(issue_price) is None:
        return {"source": "", "reason": "Missing issue price; post-listing performance cannot be calculated."}

    try:
        tushare_result = tushare_ipo_helper.get_post_listing_performance(code, listing_date_text, issue_price, params=params)
    except Exception as exc:
        return {"source": "", "reason": str(exc)}

    performance = dict(tushare_result.get("performance") or {})
    if performance:
        performance["reason"] = ""
        return performance
    summary = tushare_result.get("summary") or {}
    return {"source": "", "reason": str(summary.get("reason") or "Post-listing performance unavailable.")}


def _resolve_replay_pdf_paths(code: str) -> dict[str, Path | None]:
    return {
        "listing": bse_ipo_valuation._find_pdf(PDF_DIR, code, "上市公告书"),
        "old_shares": bse_ipo_valuation._pick_prospectus_pdf(PDF_DIR, code, "old_shares"),
        "comparables": bse_ipo_valuation._pick_prospectus_pdf(PDF_DIR, code, "comparables"),
    }


def _build_replay_pdf_signature(pdf_paths: dict[str, Path | None]) -> dict[str, Any]:
    return {
        "files": {key: _file_signature(path) for key, path in pdf_paths.items()},
        "pdf_parser_versions": dict(pdf_parser.PARSE_CACHE_KIND_VERSIONS),
    }


def _replay_item_cache_path(code: str, cache_dir: str | Path = DEFAULT_REPLAY_ITEM_CACHE_DIR) -> Path:
    normalized_code = str(code or "").strip()
    if not normalized_code:
        raise ValueError("回放样本缓存代码不能为空")
    return Path(cache_dir) / f"{normalized_code}.json"


def load_replay_item_cache(
    code: str,
    record_signature: dict[str, Any],
    pdf_signature: dict[str, Any],
    cache_dir: str | Path = DEFAULT_REPLAY_ITEM_CACHE_DIR,
) -> dict[str, Any] | None:
    cache_path = _replay_item_cache_path(code, cache_dir)
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if payload.get("schema") != REPLAY_ITEM_SCHEMA:
        return None
    if payload.get("cache_version") != REPLAY_ITEM_CACHE_VERSION:
        return None
    if str(payload.get("code") or "").strip() != str(code or "").strip():
        return None
    if payload.get("record_signature") != record_signature:
        return None
    if payload.get("pdf_signature") != pdf_signature:
        return None

    item = payload.get("item")
    if not isinstance(item, dict):
        return None
    if "AVERAGE_PRICE" not in item or "average_price_source" not in item:
        return None
    try:
        item_calc_version = int(item.get("average_price_calc_version"))
    except (TypeError, ValueError):
        item_calc_version = None
    if item_calc_version != REPLAY_AVERAGE_PRICE_CALC_VERSION:
        return None
    cached_item = dict(item)
    cached_item["has_intraday_file"] = (INTRADAY_DIR / f"{code}.csv").exists()
    return cached_item


def save_replay_item_cache(
    item: dict[str, Any],
    record_signature: dict[str, Any],
    pdf_signature: dict[str, Any],
    cache_dir: str | Path = DEFAULT_REPLAY_ITEM_CACHE_DIR,
) -> Path:
    code = str(item.get("SECURITY_CODE") or "").strip()
    cache_path = _replay_item_cache_path(code, cache_dir)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": REPLAY_ITEM_SCHEMA,
        "cache_version": REPLAY_ITEM_CACHE_VERSION,
        "generated_at": _now_text(),
        "code": code,
        "listing_date": str(item.get("LISTING_DATE") or "").strip(),
        "record_signature": record_signature,
        "pdf_signature": pdf_signature,
        "item": item,
    }
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return cache_path


def _get_dataset_evaluation_scope(dataset: dict[str, Any]) -> str:
    scope = str(dataset.get("evaluation_scope") or "").strip().lower()
    if scope in SUPPORTED_EVALUATION_SCOPES:
        return scope
    return METHOD2_ONLY_SCOPE


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * min(max(q, 0.0), 1.0)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return ordered[low] * (1 - fraction) + ordered[high] * fraction


def _rmse(errors: list[float]) -> float | None:
    if not errors:
        return None
    return math.sqrt(sum(error * error for error in errors) / len(errors))


def _fmt_metric(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _fmt_flag(value: bool | None) -> str:
    if value is None:
        return "-"
    return "是" if value else "否"


def _normalize_codes(values: list[str] | None) -> list[str]:
    if values is None:
        return []
    normalized = []
    seen: set[str] = set()
    for raw in values:
        code = str(raw or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        normalized.append(code)
    return normalized


def discover_local_sample_codes(intraday_dir: str | Path = INTRADAY_DIR) -> list[str]:
    source_dir = Path(intraday_dir)
    codes = {
        path.stem[:6]
        for path in source_dir.glob("*.csv")
        if len(path.stem) >= 6 and path.stem[:6].isdigit()
    }
    return sorted(codes)


def discover_ladder_label_sample_codes(label_path: str | Path = DEFAULT_LADDER_LABEL_PATH) -> list[str]:
    codes = {
        str(row.get("security_code") or "").strip()
        for row in subscription_ladder_labels.load_label_rows(label_path)
        if str(row.get("security_code") or "").strip()
        and str(row.get("manual_ladder") or "").strip()
    }
    return sorted(codes)


def discover_replay_sample_codes(
    *,
    intraday_dir: str | Path = INTRADAY_DIR,
    ladder_label_path: str | Path = DEFAULT_LADDER_LABEL_PATH,
) -> list[str]:
    return sorted(
        set(discover_local_sample_codes(intraday_dir))
        | set(discover_ladder_label_sample_codes(ladder_label_path))
    )


def inspect_replay_dataset_sync(
    dataset: dict[str, Any],
    local_sample_codes: list[str] | None = None,
    months: int | None = None,
) -> dict[str, Any]:
    local_codes = _normalize_codes(local_sample_codes if local_sample_codes is not None else discover_replay_sample_codes())
    dataset_codes = _normalize_codes(dataset.get("requested_codes") or dataset.get("sample_codes") or [])
    local_code_set = set(local_codes)
    dataset_code_set = set(dataset_codes)
    missing_in_dataset = [code for code in local_codes if code not in dataset_code_set]
    extra_in_dataset = [code for code in dataset_codes if code not in local_code_set]

    reasons: list[str] = []
    if missing_in_dataset:
        reasons.append("本地样本源新增样本：" + ",".join(missing_in_dataset))
    if extra_in_dataset:
        reasons.append("回放数据集中存在本地样本源已无该样本：" + ",".join(extra_in_dataset))
    if months is not None:
        dataset_months = dataset.get("source_months")
        try:
            dataset_months_int = int(dataset_months)
        except (TypeError, ValueError):
            dataset_months_int = None
        if dataset_months_int != int(months):
            reasons.append(f"回放月份参数变化：数据集={dataset_months}，当前={months}")
    dataset_cache_version = dataset.get("replay_item_cache_version")
    try:
        dataset_cache_version_int = int(dataset_cache_version)
    except (TypeError, ValueError):
        dataset_cache_version_int = None
    if dataset_cache_version_int != REPLAY_ITEM_CACHE_VERSION:
        reasons.append(f"回放样本缓存版本变化：数据集={dataset_cache_version}，当前={REPLAY_ITEM_CACHE_VERSION}")
    dataset_refresh_contract = dataset.get("replay_refresh_contract")
    current_refresh_contract = _build_replay_refresh_contract()
    if dataset_refresh_contract != current_refresh_contract:
        reasons.append("replay 刷新契约发生变化：记录签名字段或 PDF 解析器版本已更新")

    return {
        "needs_refresh": bool(reasons),
        "reasons": reasons,
        "local_codes": local_codes,
        "dataset_codes": dataset_codes,
        "missing_in_dataset": missing_in_dataset,
        "extra_in_dataset": extra_in_dataset,
    }


def list_stage_names() -> list[str]:
    return sorted(set(SEARCH_STAGE_GRIDS) | set(SEARCH_STAGE_CANDIDATES))


def _build_pdf_inputs_from_paths(
    params: dict[str, Any],
    pdf_paths: dict[str, Path | None],
) -> tuple[float, str, dict[str, Any] | None]:
    return bse_ipo_valuation._resolve_old_shares(params, pdf_paths.get("listing"), pdf_paths.get("old_shares"))


def _build_pdf_inputs(params: dict[str, Any], code: str) -> tuple[float, str, dict[str, Any] | None]:
    return _build_pdf_inputs_from_paths(params, _resolve_replay_pdf_paths(code))


def _is_missing_replay_value(value: Any) -> bool:
    if value in (None, "", "--"):
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _merge_announcement_issue_fields(
    values: dict[str, Any],
    issue_info: dict[str, Any],
    sources: dict[str, str],
    *,
    source_label: str,
    override: bool = False,
) -> None:
    fields = dict(issue_info.get("fields") or {}) if isinstance(issue_info, dict) else {}
    if not fields:
        return
    field_sources = issue_info.get("field_sources") if isinstance(issue_info, dict) else {}
    field_sources = field_sources if isinstance(field_sources, dict) else {}
    for field_name in bse_ipo_valuation.ISSUE_DOCUMENT_SUPPLEMENT_FIELDS:
        value = fields.get(field_name)
        if _is_missing_replay_value(value):
            continue
        if not override and not _is_missing_replay_value(values.get(field_name)):
            continue
        values[field_name] = value
        sources[field_name] = str(field_sources.get(field_name) or source_label).strip()


def _fallback_replay_name_from_sources(code: str, pdfs: dict[str, Path | None]) -> str:
    for row in subscription_ladder_labels.load_label_rows(DEFAULT_LADDER_LABEL_PATH):
        if str(row.get("security_code") or "").strip() != code:
            continue
        name = str(row.get("security_name_abbr") or "").strip()
        if name:
            return name

    for path in pdfs.values():
        if path is None:
            continue
        stem = path.stem.strip()
        if stem.startswith(code):
            stem = stem[len(code) :].lstrip("_- ")
        for marker in ("招股说明书", "发行公告", "发行结果公告", "上市公告书"):
            if marker in stem:
                stem = stem.split(marker, 1)[0]
        stem = stem.strip("_- ")
        if stem:
            return stem
    return code


def _build_replay_record_from_announcements(code: str) -> dict[str, Any] | None:
    normalized_code = str(code or "").strip()
    if not normalized_code:
        return None

    pdfs = {
        "prospectus": bse_ipo_valuation._pick_prospectus_pdf(PDF_DIR, normalized_code, "old_shares"),
        "issue": bse_ipo_valuation._find_pdf(PDF_DIR, normalized_code, "发行公告"),
        "result": bse_ipo_valuation._find_pdf(PDF_DIR, normalized_code, "发行结果公告"),
        "listing": bse_ipo_valuation._find_pdf(PDF_DIR, normalized_code, "上市公告书"),
    }
    values: dict[str, Any] = {
        "SECURITY_CODE": normalized_code,
        "SECURITY_NAME_ABBR": _fallback_replay_name_from_sources(normalized_code, pdfs),
    }
    sources: dict[str, str] = {}
    parse_errors: list[str] = []

    if pdfs.get("prospectus") is not None:
        try:
            _merge_announcement_issue_fields(
                values,
                pdf_parser.extract_prospectus_issue_info(pdfs["prospectus"]),
                sources,
                source_label="prospectus",
            )
        except Exception as exc:
            parse_errors.append(f"prospectus:{exc}")
    if pdfs.get("issue") is not None:
        try:
            _merge_announcement_issue_fields(
                values,
                pdf_parser.extract_issue_announcement_info(pdfs["issue"]),
                sources,
                source_label="issue_announcement",
                override=True,
            )
        except Exception as exc:
            parse_errors.append(f"issue:{exc}")
    if pdfs.get("result") is not None:
        try:
            _merge_announcement_issue_fields(
                values,
                pdf_parser.extract_issue_result_info(pdfs["result"]),
                sources,
                source_label="issue_result",
            )
        except Exception as exc:
            parse_errors.append(f"result:{exc}")

    apply_date = str(values.get("APPLY_DATE") or "").strip()
    listing_date = str(values.get("LISTING_DATE") or "").strip()
    listing_date_source = str(sources.get("LISTING_DATE") or "").strip()
    if not listing_date and apply_date:
        listing_date = apply_date
        listing_date_source = "apply_date_fallback"

    total_issue_num = _safe_float(values.get("TOTAL_ISSUE_NUM")) or _safe_float(values.get("ISSUE_NUM"))
    issue_num = _safe_float(values.get("ISSUE_NUM")) or total_issue_num
    parsed_field_count = sum(
        1
        for field_name in (
            "APPLY_DATE",
            "ISSUE_PRICE",
            "AFTER_ISSUE_PE",
            "INDUSTRY_PE_NEW",
            "TOTAL_ISSUE_NUM",
            "ONLINE_ISSUE_NUM",
            "TOP_APPLY_MARKETCAP",
        )
        if not _is_missing_replay_value(values.get(field_name))
    )
    source = "announcement_pdf_fallback" if parsed_field_count else "sample_seed_pending_announcements"
    return {
        "SECURITY_CODE": normalized_code,
        "SECURITY_NAME_ABBR": str(values.get("SECURITY_NAME_ABBR") or normalized_code).strip(),
        "APPLY_DATE": apply_date,
        "LISTING_DATE": listing_date,
        "LISTING_DATE_SOURCE": listing_date_source,
        "ISSUE_PRICE": _safe_float(values.get("ISSUE_PRICE")),
        "AFTER_ISSUE_PE": _safe_float(values.get("AFTER_ISSUE_PE")),
        "INDUSTRY_PE_NEW": _safe_float(values.get("INDUSTRY_PE_NEW")),
        "TOTAL_ISSUE_NUM": total_issue_num,
        "ISSUE_NUM": issue_num,
        "ONLINE_ISSUE_NUM": _safe_float(values.get("ONLINE_ISSUE_NUM")),
        "TOP_APPLY_MARKETCAP": _safe_float(values.get("TOP_APPLY_MARKETCAP")),
        "SUBSCRIPTION_LIMIT_WAN_SHARES": _safe_float(values.get("SUBSCRIPTION_LIMIT_WAN_SHARES")),
        "SW_INDUSTRY": str(values.get("INDUSTRY") or "").strip(),
        "INDUSTRY": str(values.get("INDUSTRY") or "").strip(),
        "INDUSTRY_CODE": str(values.get("INDUSTRY_CODE") or "").strip(),
        "replay_record_source": source,
        "announcement_fallback_field_sources": sources,
        "announcement_fallback_parse_errors": parse_errors,
        "announcement_fallback_pdf_files": {
            label: path.name if path is not None else ""
            for label, path in pdfs.items()
        },
    }


def _upgrade_existing_replay_item_average_price(
    item: dict[str, Any],
    params: dict[str, Any],
    code: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    upgraded = dict(item)
    issue_price = _safe_float(upgraded.get("ISSUE_PRICE"))
    if issue_price is None:
        issue_price = _safe_float(record.get("ISSUE_PRICE"))
    average_price_result = _resolve_listing_average_price(
        params,
        code,
        record.get("LISTING_DATE") or upgraded.get("LISTING_DATE"),
        record,
        prefer_local_intraday=True,
    )
    average_price = _safe_float(average_price_result.get("average_price"))
    upgraded["AVERAGE_PRICE"] = average_price
    upgraded["LD_AVERAGE_CHANGE"] = _calc_change_pct(issue_price, average_price)
    upgraded["average_price_source"] = str(average_price_result.get("source") or "").strip()
    upgraded["average_price_reason"] = str(average_price_result.get("reason") or "").strip()
    upgraded["average_price_calc_version"] = average_price_result.get("calc_version") or REPLAY_AVERAGE_PRICE_CALC_VERSION
    upgraded["average_price_unit_mode"] = str(average_price_result.get("unit_mode") or "").strip()
    upgraded["average_price_reference"] = _safe_float(average_price_result.get("price_reference"))
    upgraded["has_intraday_file"] = (INTRADAY_DIR / f"{code}.csv").exists()
    return upgraded


def _build_replay_item(
    params: dict[str, Any],
    code: str,
    record: dict[str, Any],
    mapper: IndustryMapper,
    comparable_snapshot_cache: dict[tuple[str, str], dict[str, Any] | None],
    pdf_paths: dict[str, Path | None] | None = None,
) -> dict[str, Any]:
    if pdf_paths is None:
        pdf_paths = _resolve_replay_pdf_paths(code)

    old_shares, old_shares_desc, old_shares_meta = _build_pdf_inputs_from_paths(params, pdf_paths)
    total_issue_num = _safe_float(record.get("TOTAL_ISSUE_NUM"))
    if total_issue_num is None:
        total_issue_num = _safe_float(record.get("ISSUE_NUM"))
    issue_price = _safe_float(record.get("ISSUE_PRICE"))
    listing_date_for_average = record.get("LISTING_DATE")
    if str(record.get("LISTING_DATE_SOURCE") or "").strip() == "apply_date_fallback":
        listing_date_for_average = None
    average_price_result = _resolve_listing_average_price(
        params,
        code,
        listing_date_for_average,
        record,
        prefer_local_intraday=True,
    )
    average_price = _safe_float(average_price_result.get("average_price"))
    average_change = _calc_change_pct(issue_price, average_price)
    post_listing_result = _resolve_post_listing_performance(
        params,
        code,
        listing_date_for_average,
        issue_price,
        record,
    )

    industry = mapper.resolve_stock_industry(code, record)
    comparable_pdf = pdf_paths.get("comparables")
    comparable_codes = pdf_parser.extract_comparable_companies(comparable_pdf) if comparable_pdf else []
    comparable_data, comparable_summary = _fetch_historical_comparable_data(
        comparable_codes,
        listing_date_for_average,
        params,
        comparable_snapshot_cache,
    )
    method1_replay = valuation_engine.method1_comparable(
        issue_price=issue_price,
        issue_pe=_safe_float(record.get("AFTER_ISSUE_PE")),
        comparable_data=comparable_data,
        params=params,
        industry_pe=_safe_float(record.get("INDUSTRY_PE_NEW")),
        float_shares=(total_issue_num or 0.0) + old_shares,
    )
    return {
        "SECURITY_CODE": code,
        "SECURITY_NAME_ABBR": str(record.get("SECURITY_NAME_ABBR") or "").strip(),
        "APPLY_DATE": str(record.get("APPLY_DATE") or "").strip(),
        "LISTING_DATE": str(record.get("LISTING_DATE") or "").strip(),
        "ISSUE_PRICE": issue_price,
        "AFTER_ISSUE_PE": _safe_float(record.get("AFTER_ISSUE_PE")),
        "INDUSTRY_PE_NEW": _safe_float(record.get("INDUSTRY_PE_NEW")),
        "TOTAL_ISSUE_NUM": total_issue_num,
        "ONLINE_ISSUE_NUM": _safe_float(record.get("ONLINE_ISSUE_NUM")),
        "TOP_APPLY_MARKETCAP": _safe_float(record.get("TOP_APPLY_MARKETCAP")),
        "SUBSCRIPTION_LIMIT_WAN_SHARES": _safe_float(record.get("SUBSCRIPTION_LIMIT_WAN_SHARES")),
        "CLOSE_PRICE": _safe_float(record.get("CLOSE_PRICE")),
        "AVERAGE_PRICE": average_price,
        "LD_CLOSE_CHANGE": _safe_float(record.get("LD_CLOSE_CHANGE")),
        "LD_AVERAGE_CHANGE": average_change,
        "NEXT_DAY_CLOSE": _safe_float(post_listing_result.get("next_close")),
        "THIRD_DAY_CLOSE": _safe_float(post_listing_result.get("third_close")),
        "NEXT_DAY_CLOSE_CHANGE": _safe_float(post_listing_result.get("next_day_change_pct")),
        "THIRD_DAY_CLOSE_CHANGE": _safe_float(post_listing_result.get("third_day_change_pct")),
        "NEXT_DAY_FROM_LISTING_CLOSE_PCT": _safe_float(post_listing_result.get("next_day_from_listing_close_pct")),
        "THIRD_DAY_FROM_LISTING_CLOSE_PCT": _safe_float(post_listing_result.get("third_day_from_listing_close_pct")),
        "POST_LISTING_PROFIT_EFFECT_PCT": _safe_float(post_listing_result.get("post_listing_profit_effect_pct")),
        "post_listing_performance_source": str(post_listing_result.get("source") or "").strip(),
        "post_listing_performance_reason": str(post_listing_result.get("reason") or "").strip(),
        "TURNOVERRATE": _safe_float(record.get("TURNOVERRATE")),
        "SW_INDUSTRY": str(record.get("SW_INDUSTRY") or "").strip(),
        "INDUSTRY": str(record.get("INDUSTRY") or "").strip(),
        "INDUSTRY_CODE": str(record.get("INDUSTRY_CODE") or "").strip(),
        "industry_primary": industry.primary,
        "industry_secondary": industry.secondary,
        "industry_source": industry.source,
        "old_shares": old_shares,
        "old_shares_desc": old_shares_desc,
        "old_shares_meta": old_shares_meta or {},
        "float_shares": (total_issue_num or 0.0) + old_shares,
        "has_intraday_file": (INTRADAY_DIR / f"{code}.csv").exists(),
        "comparable_codes": comparable_codes,
        "comparable_data": comparable_data,
        "comparable_summary": comparable_summary,
        "method1_replay_available": bool(method1_replay.get("available")),
        "average_price_source": str(average_price_result.get("source") or "").strip(),
        "average_price_reason": str(average_price_result.get("reason") or "").strip(),
        "average_price_calc_version": average_price_result.get("calc_version") or REPLAY_AVERAGE_PRICE_CALC_VERSION,
        "average_price_unit_mode": str(average_price_result.get("unit_mode") or "").strip(),
        "average_price_reference": _safe_float(average_price_result.get("price_reference")),
        "replay_record_source": str(record.get("replay_record_source") or "").strip(),
        "listing_date_source": str(record.get("LISTING_DATE_SOURCE") or "").strip(),
    }


def _build_replay_dataset_payload(
    *,
    items: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    requested_codes: list[str],
    months: int,
    item_cache: dict[str, Any],
) -> dict[str, Any]:
    items.sort(key=lambda item: _parse_date_key(item.get("LISTING_DATE")))
    method1_ready_count = sum(1 for item in items if item.get("method1_replay_available"))
    evaluation_scope = COMPOSITE_EVALUATION_SCOPE if method1_ready_count > 0 else METHOD2_ONLY_SCOPE
    caveats = [
        "每只样本只使用上市当时可见的发行字段、历史新股样本和本地首日分时数据。",
        "历史流通盘使用本地 PDF 提取的 old_shares 结果，缺失时回退按 0 万股处理。",
    ]
    if evaluation_scope == COMPOSITE_EVALUATION_SCOPE:
        caveats.insert(
            0,
            "方法一历史回放快照基于 Tushare `daily_basic`，按标的上市日前一日向前回看交易窗口，未取到时该样本自动降级为仅方法二。",
        )
    else:
        caveats.insert(
            0,
            "当前未形成可用的方法一历史快照，离线调参仍按方法二口径评估。",
        )
    return {
        "schema": DATASET_SCHEMA,
        "evaluation_scope": evaluation_scope,
        "generated_at": _now_text(),
        "source_months": months,
        "replay_item_cache_version": REPLAY_ITEM_CACHE_VERSION,
        "replay_refresh_contract": _build_replay_refresh_contract(),
        "sample_codes": [item["SECURITY_CODE"] for item in items],
        "requested_codes": requested_codes,
        "available_count": len(items),
        "method1_ready_count": method1_ready_count,
        "method1_ready_rate": (method1_ready_count / len(items)) if items else 0.0,
        "skipped": skipped,
        "caveats": caveats,
        "item_cache": item_cache,
        "items": items,
    }


def build_replay_dataset(
    params: dict[str, Any],
    months: int = 12,
    sample_codes: list[str] | None = None,
    page_size: int = 100,
    item_cache_dir: str | Path | None = DEFAULT_REPLAY_ITEM_CACHE_DIR,
    use_item_cache: bool = True,
    existing_dataset: dict[str, Any] | None = None,
    progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    mapper = IndustryMapper(params)
    requested_codes = sample_codes if sample_codes is not None else discover_replay_sample_codes()
    requested_codes = sorted(_normalize_codes(requested_codes))
    raw_records = data_fetcher.fetch_recent_ipos(months=months, page_size=page_size)
    enriched_records = mapper.enrich_recent_ipos(list(raw_records))
    comparable_snapshot_cache: dict[tuple[str, str], dict[str, Any] | None] = {}

    record_by_code = {
        str(item.get("SECURITY_CODE", "")).strip(): item
        for item in enriched_records
        if str(item.get("SECURITY_CODE", "")).strip()
    }

    cache_enabled = bool(use_item_cache and item_cache_dir is not None)
    item_cache = {
        "enabled": cache_enabled,
        "dir": str(item_cache_dir) if item_cache_dir is not None else "",
        "hits": 0,
        "misses": 0,
        "writes": 0,
        "existing_dataset_reused": 0,
    }
    existing_items_by_code: dict[str, dict[str, Any]] = {}
    try:
        existing_months = int((existing_dataset or {}).get("source_months"))
    except (TypeError, ValueError):
        existing_months = None
    try:
        existing_cache_version = int((existing_dataset or {}).get("replay_item_cache_version"))
    except (TypeError, ValueError):
        existing_cache_version = None
    can_reuse_existing_dataset = (
        existing_months == int(months)
        and existing_cache_version == REPLAY_ITEM_CACHE_VERSION
    )
    if can_reuse_existing_dataset:
        existing_items_by_code = {
            str(item.get("SECURITY_CODE") or "").strip(): dict(item)
            for item in (existing_dataset or {}).get("items") or []
            if str(item.get("SECURITY_CODE") or "").strip()
        }

    items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    total_codes = len(requested_codes)
    for index, code in enumerate(requested_codes, start=1):
        record = record_by_code.get(code)
        fallback_record = False
        if record is None:
            record = _build_replay_record_from_announcements(code)
            fallback_record = record is not None
            if record is not None:
                record = mapper.enrich_recent_ipos([record])[0]
        if record is None:
            skipped.append({"code": code, "reason": f"最近 {months} 个月历史池中未找到该样本，且无法生成样本种子"})
            if progress_callback:
                progress_callback(index, total_codes, {"code": code, "status": "skipped"})
            continue

        record_signature = _build_replay_record_signature(record)
        pdf_paths = _resolve_replay_pdf_paths(code)
        pdf_signature = _build_replay_pdf_signature(pdf_paths)
        existing_item = existing_items_by_code.get(code)
        status = "announcement_fallback" if fallback_record else "built"
        item = None
        cache_path = _replay_item_cache_path(code, item_cache_dir) if cache_enabled else None
        cached_pdf_signature_matches = (
            _cached_replay_pdf_signature_matches(code, pdf_signature, item_cache_dir)
            if cache_enabled
            else None
        )

        if cache_enabled:
            item = load_replay_item_cache(code, record_signature, pdf_signature, item_cache_dir)
            if item is not None:
                item_cache["hits"] += 1
                status = "cache_hit"

        if (
            item is None
            and existing_item is not None
            and _existing_replay_item_signature_matches(existing_item, record_signature)
            and cached_pdf_signature_matches is not False
        ):
            if _is_existing_replay_item_compatible(existing_item, record_signature):
                item = dict(existing_item)
                item["has_intraday_file"] = (INTRADAY_DIR / f"{code}.csv").exists()
                status = "reused_dataset"
            else:
                item = _upgrade_existing_replay_item_average_price(existing_item, params, code, record)
                status = "upgraded_dataset"
            item_cache["existing_dataset_reused"] += 1
            if cache_enabled:
                save_replay_item_cache(item, record_signature, pdf_signature, item_cache_dir)
                item_cache["writes"] += 1

        if item is None:
            if cache_enabled:
                item_cache["misses"] += 1
            item = _build_replay_item(params, code, record, mapper, comparable_snapshot_cache, pdf_paths)
            if fallback_record:
                item["replay_record_source"] = str(record.get("replay_record_source") or "announcement_pdf_fallback")
                item["announcement_fallback_field_sources"] = dict(record.get("announcement_fallback_field_sources") or {})
                item["announcement_fallback_parse_errors"] = list(record.get("announcement_fallback_parse_errors") or [])
                item["announcement_fallback_pdf_files"] = dict(record.get("announcement_fallback_pdf_files") or {})
            if cache_enabled:
                save_replay_item_cache(item, record_signature, pdf_signature, item_cache_dir)
                item_cache["writes"] += 1

        items.append(item)
        if progress_callback:
            progress_callback(index, total_codes, {"code": code, "status": status})

    return _build_replay_dataset_payload(
        items=items,
        skipped=skipped,
        requested_codes=requested_codes,
        months=months,
        item_cache=item_cache,
    )


def save_replay_dataset(dataset: dict[str, Any], path: str | Path = DEFAULT_DATASET_PATH) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def load_replay_dataset(path: str | Path = DEFAULT_DATASET_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != DATASET_SCHEMA:
        raise ValueError(f"不支持的数据集 schema: {payload.get('schema')}")
    if str(payload.get("evaluation_scope") or "").strip().lower() not in SUPPORTED_EVALUATION_SCOPES:
        payload["evaluation_scope"] = METHOD2_ONLY_SCOPE
    return payload


def build_stage_candidates(stage_name: str) -> list[dict[str, Any]]:
    explicit_candidates = SEARCH_STAGE_CANDIDATES.get(stage_name)
    if explicit_candidates is not None:
        candidates = [dict(item) for item in explicit_candidates]
        if stage_name == "wsi_turnover_balance":
            for item in candidates:
                total_weight = sum(float(item.get(key, 0.0)) for key in WSI_WEIGHT_KEYS)
                if not math.isclose(total_weight, 1.0, rel_tol=0.0, abs_tol=1e-9):
                    raise ValueError(f"WSI 候选权重之和必须为 1.0，当前为 {total_weight:.6f}")
        return candidates

    grid = SEARCH_STAGE_GRIDS.get(stage_name)
    if grid is None:
        raise ValueError(f"未知调参阶段: {stage_name}")

    keys = list(grid.keys())
    values = [grid[key] for key in keys]
    candidates = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
    if stage_name == "composite_weights":
        for item in candidates:
            weight_comparable = float(item.get("weight_comparable", 0.5))
            item["weight_industry_momentum"] = round(1 - weight_comparable, 2)
    return candidates


def _build_historical_comparable_item(
    code: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    return {
        "code": code,
        "name": code,
        "close": snapshot.get("close"),
        "pe_ttm": snapshot.get("pe_ttm"),
        "pb_lf": snapshot.get("pb_lf"),
        "mkt_cap": snapshot.get("mkt_cap"),
        "trade_date": snapshot.get("trade_date"),
        "source": "tushare_historical",
        "close_source": "tushare_historical",
        "pe_source": "tushare_historical",
        "pb_source": "tushare_historical",
        "mkt_cap_source": "tushare_historical",
    }


def _fetch_historical_comparable_data(
    comparable_codes: list[str],
    listing_date: Any,
    params: dict[str, Any],
    snapshot_cache: dict[tuple[str, str], dict[str, Any] | None],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized_codes = _normalize_codes([LocalFileDB.normalize_code(code) for code in comparable_codes])
    reference_trade_date = _previous_day_yyyymmdd(listing_date)
    summary = {
        "provider": "tushare_historical",
        "requested_codes": list(normalized_codes),
        "returned_codes": [],
        "cache_hits": [],
        "api_fetched": [],
        "skipped_unsupported": [],
        "reference_trade_date": reference_trade_date,
        "reason": "",
    }

    if not normalized_codes:
        summary["reason"] = "未提取到可比公司代码。"
        return [], summary
    if reference_trade_date is None:
        summary["reason"] = "上市日期缺失，无法构建方法一历史快照。"
        return [], summary

    settings = tushare_helper._build_settings(params)
    if not settings.get("token"):
        summary["reason"] = f"Tushare token 未配置，无法构建历史可比快照（环境变量 {settings['token_env']}）。"
        return [], summary

    db = LocalFileDB(settings["cache_root"])
    lookback_days = max(int(settings["recent_trade_days"]) * 3, 15)
    end_date = datetime.strptime(reference_trade_date, "%Y%m%d").date()
    start_date = (end_date - timedelta(days=lookback_days)).strftime("%Y%m%d")

    items: list[dict[str, Any]] = []
    for code in normalized_codes:
        if not tushare_helper._supports_tushare_code(code):
            summary["skipped_unsupported"].append(code)
            continue

        cache_key = (code, reference_trade_date)
        snapshot = snapshot_cache.get(cache_key)
        if cache_key in snapshot_cache:
            if snapshot is not None:
                summary["cache_hits"].append(code)
        else:
            rows, error_message = tushare_helper._call_tushare_api(
                "daily_basic",
                {
                    "ts_code": code,
                    "start_date": start_date,
                    "end_date": reference_trade_date,
                },
                "ts_code,trade_date,close,pe_ttm,pb,total_share,float_share,free_share,total_mv,circ_mv",
                settings,
                db,
            )
            if error_message:
                summary["reason"] = summary["reason"] or error_message
                snapshot_cache[cache_key] = None
                continue

            rows.sort(key=lambda item: str(item.get("trade_date") or ""), reverse=True)
            snapshot = None
            for row in rows:
                candidate = tushare_helper._build_tushare_snapshot(row)
                if candidate.get("close") is not None or candidate.get("pe_ttm") is not None:
                    snapshot = candidate
                    break
            snapshot_cache[cache_key] = snapshot
            if snapshot is not None:
                summary["api_fetched"].append(code)

        if snapshot is None:
            continue

        items.append(_build_historical_comparable_item(code, snapshot))
        summary["returned_codes"].append(code)

    if not items and not summary["reason"]:
        summary["reason"] = "未取到可用的历史可比快照。"
    return items, summary


def load_search_candidates(grid_file: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(grid_file).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        candidates = [dict(item) for item in payload if isinstance(item, dict)]
        if not candidates:
            raise ValueError("grid 文件中未找到有效候选参数")
        return candidates

    if isinstance(payload, dict):
        keys = list(payload.keys())
        values: list[list[Any]] = []
        for key in keys:
            raw_values = payload.get(key)
            if not isinstance(raw_values, list) or not raw_values:
                raise ValueError(f"grid 文件字段 {key} 未配置候选列表")
            values.append(raw_values)
        return [dict(zip(keys, combo)) for combo in itertools.product(*values)]

    raise ValueError("grid 文件需为 list[dict] 或 dict[str, list]")


def load_named_candidate_sets(candidate_file: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(candidate_file).read_text(encoding="utf-8"))

    if isinstance(payload, list):
        candidates = payload
        metadata = {
            "name": Path(candidate_file).stem,
            "description": "",
        }
    elif isinstance(payload, dict):
        candidates = payload.get("candidates")
        metadata = {
            "name": str(payload.get("name") or Path(candidate_file).stem).strip(),
            "description": str(payload.get("description") or "").strip(),
            "source_report": str(payload.get("source_report") or "").strip(),
        }
    else:
        raise ValueError("candidate 文件需为 dict 或 list")

    if not isinstance(candidates, list) or not candidates:
        raise ValueError("candidate 文件中未找到有效 candidates")

    normalized_candidates: list[dict[str, Any]] = []
    for index, item in enumerate(candidates, start=1):
        if not isinstance(item, dict):
            raise ValueError("candidate 列表中的每一项都必须是 dict")
        overrides = item.get("overrides", item)
        if not isinstance(overrides, dict):
            raise ValueError(f"candidate #{index} overrides 必须是 dict")
        normalized_candidates.append(
            {
                "name": str(item.get("name") or f"candidate_{index}").strip(),
                "description": str(item.get("description") or "").strip(),
                "overrides": dict(overrides),
            }
        )

    return {
        **metadata,
        "candidates": normalized_candidates,
    }


def _manual_value_tag(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".").replace("-", "m").replace(".", "p")

    text = str(value or "").strip().lower()
    normalized = "".join(character if character.isalnum() else "_" for character in text)
    normalized = "_".join(part for part in normalized.split("_") if part)
    return normalized or "value"


def _rounded_weight(value: float) -> float:
    if abs(value) <= AUTO_NORMALIZE_EPSILON:
        return 0.0
    if abs(value - 1.0) <= AUTO_NORMALIZE_EPSILON:
        return 1.0
    return round(float(value), 10)


def _build_auto_normalize_note(group: tuple[str, ...], adjusted_keys: list[str]) -> str:
    if not adjusted_keys:
        return ""
    if len(group) == 2:
        return "同组另一权重已自动补足到 1"
    return "其余同组权重已按当前策略参数比例自动缩放"


def _auto_normalize_weight_overrides(
    base_params: dict[str, Any],
    overrides: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    normalized = dict(overrides)
    adjustment_notes: list[str] = []

    for group in AUTO_NORMALIZE_WEIGHT_GROUPS:
        explicit_keys = [key for key in group if key in overrides]
        if len(explicit_keys) != 1:
            continue

        target_key = explicit_keys[0]
        target_value = _safe_float(overrides.get(target_key))
        if target_value is None:
            raise ValueError(f"参数 {target_key} 需要是数值，才能自动归一化同组权重")
        if target_value < -AUTO_NORMALIZE_EPSILON or target_value > 1 + AUTO_NORMALIZE_EPSILON:
            raise ValueError(f"参数 {target_key} 超出 0 到 1，无法自动归一化同组权重")

        target_value = _rounded_weight(min(max(float(target_value), 0.0), 1.0))
        normalized[target_key] = target_value
        other_keys = [key for key in group if key != target_key]
        remaining_total = 1.0 - target_value
        if remaining_total < -AUTO_NORMALIZE_EPSILON:
            raise ValueError(f"参数 {target_key} 超出 0 到 1，无法自动归一化同组权重")
        if abs(remaining_total) <= AUTO_NORMALIZE_EPSILON:
            remaining_total = 0.0

        adjusted_keys: list[str] = []
        if len(other_keys) == 1:
            normalized[other_keys[0]] = _rounded_weight(remaining_total)
            adjusted_keys = list(other_keys)
        else:
            base_other_weights: list[float] = []
            for key in other_keys:
                base_value = _safe_float(base_params.get(key))
                if base_value is None:
                    raise ValueError(f"当前参数缺少 {key}，无法按现有比例自动缩放同组权重")
                base_other_weights.append(float(base_value))

            base_other_total = sum(base_other_weights)
            if base_other_total <= AUTO_NORMALIZE_EPSILON:
                if remaining_total > AUTO_NORMALIZE_EPSILON:
                    joined = ", ".join(other_keys)
                    raise ValueError(f"当前参数中的 {joined} 全为 0，无法按现有比例自动缩放同组权重")
                for key in other_keys:
                    normalized[key] = 0.0
                adjusted_keys = list(other_keys)
            else:
                assigned_total = target_value
                for index, key in enumerate(other_keys):
                    if index == len(other_keys) - 1:
                        scaled_value = 1.0 - assigned_total
                    else:
                        scaled_value = remaining_total * base_other_weights[index] / base_other_total
                        assigned_total += scaled_value
                    normalized[key] = _rounded_weight(scaled_value)
                total_weight = sum(_safe_float(normalized.get(key)) or 0.0 for key in group)
                drift = 1.0 - total_weight
                if abs(drift) > AUTO_NORMALIZE_EPSILON:
                    last_key = other_keys[-1]
                    last_value = _safe_float(normalized.get(last_key)) or 0.0
                    normalized[last_key] = _rounded_weight(last_value + drift)
                adjusted_keys = list(other_keys)

        note = _build_auto_normalize_note(group, adjusted_keys)
        if note:
            adjustment_notes.append(note)

    return normalized, adjustment_notes


def normalize_manual_candidate_payload(
    base_params: dict[str, Any],
    candidate_payload: dict[str, Any],
) -> dict[str, Any]:
    normalized_candidates: list[dict[str, Any]] = []
    for index, item in enumerate(candidate_payload.get("candidates") or [], start=1):
        name = str(item.get("name") or f"candidate_{index}").strip() or f"candidate_{index}"
        description = str(item.get("description") or "").strip()
        overrides = dict(item.get("overrides") or {})
        normalized_overrides, adjustment_notes = _auto_normalize_weight_overrides(base_params, overrides)

        rendered_overrides = "；".join(_build_param_lines(normalized_overrides))
        if adjustment_notes:
            adjustment_text = "；".join(adjustment_notes)
            if description:
                description = f"{description}；{adjustment_text}；实际执行为：{rendered_overrides}"
            else:
                description = f"{rendered_overrides}；{adjustment_text}"
        elif not description:
            description = rendered_overrides

        normalized_candidates.append(
            {
                "name": name,
                "description": description,
                "overrides": normalized_overrides,
            }
        )

    return {
        **candidate_payload,
        "candidates": normalized_candidates,
    }


def build_manual_candidate_payload(
    *,
    name: str | None = None,
    description: str = "",
    param_name: str | None = None,
    values: list[Any] | None = None,
    override_groups: list[dict[str, Any]] | None = None,
    base_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []

    if param_name is not None:
        param_key = str(param_name).strip()
        if not param_key:
            raise ValueError("手动候选的参数名不能为空")
        normalized_values = list(values or [])
        if not normalized_values:
            raise ValueError(f"参数 {param_key} 未提供候选值")
        for raw_value in normalized_values:
            overrides = {param_key: raw_value}
            candidates.append(
                {
                    "name": f"{param_key}_{_manual_value_tag(raw_value)}",
                    "description": "",
                    "overrides": overrides,
                }
            )

    for index, raw_overrides in enumerate(override_groups or [], start=1):
        overrides = dict(raw_overrides or {})
        if not overrides:
            continue
        if len(overrides) == 1:
            key, value = next(iter(overrides.items()))
            candidate_name = f"{key}_{_manual_value_tag(value)}"
        else:
            candidate_name = f"candidate_{index}"
        candidates.append(
            {
                "name": candidate_name,
                "description": "",
                "overrides": overrides,
            }
        )

    if not candidates:
        raise ValueError("未提供任何手动候选参数")

    payload_name = str(name or "").strip()
    if not payload_name:
        payload_name = f"manual_{param_name}" if param_name else "manual_candidates"

    payload = {
        "name": payload_name,
        "description": str(description or "").strip(),
        "candidates": candidates,
    }
    if base_params is not None:
        return normalize_manual_candidate_payload(base_params, payload)
    payload["candidates"] = [
        {
            **item,
            "description": str(item.get("description") or "").strip()
            or "；".join(_build_param_lines(dict(item.get("overrides") or {}))),
        }
        for item in candidates
    ]
    return payload


def load_manual_candidate_payload(
    candidate_file: str | Path,
    *,
    name: str | None = None,
    description: str = "",
    base_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = json.loads(Path(candidate_file).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("candidates"), list):
        named_payload = load_named_candidate_sets(candidate_file)
        if name:
            named_payload["name"] = str(name).strip()
        if description:
            named_payload["description"] = str(description).strip()
        if base_params is not None:
            return normalize_manual_candidate_payload(base_params, named_payload)
        return named_payload

    return build_manual_candidate_payload(
        name=name or Path(candidate_file).stem,
        description=description,
        override_groups=load_search_candidates(candidate_file),
        base_params=base_params,
    )


def split_target_codes(
    dataset: dict[str, Any],
    train_ratio: float = 0.7,
    min_train_samples: int = 8,
) -> tuple[list[str], list[str]]:
    items = list(dataset.get("items") or [])
    ordered_codes = [
        str(item.get("SECURITY_CODE") or "").strip()
        for item in items
        if str(item.get("SECURITY_CODE") or "").strip()
    ]
    if not ordered_codes:
        return [], []
    if len(ordered_codes) == 1:
        return ordered_codes, []

    split_index = max(int(len(ordered_codes) * train_ratio), min_train_samples)
    split_index = min(max(split_index, 1), len(ordered_codes) - 1)
    return ordered_codes[:split_index], ordered_codes[split_index:]


def _build_recent_pool(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "SECURITY_CODE": item.get("SECURITY_CODE"),
            "SECURITY_NAME_ABBR": item.get("SECURITY_NAME_ABBR"),
            "LISTING_DATE": item.get("LISTING_DATE"),
            "ISSUE_PRICE": item.get("ISSUE_PRICE"),
            "AFTER_ISSUE_PE": item.get("AFTER_ISSUE_PE"),
            "INDUSTRY_PE_NEW": item.get("INDUSTRY_PE_NEW"),
            "TOTAL_ISSUE_NUM": item.get("TOTAL_ISSUE_NUM"),
            "ONLINE_ISSUE_NUM": item.get("ONLINE_ISSUE_NUM"),
            "TOP_APPLY_MARKETCAP": item.get("TOP_APPLY_MARKETCAP"),
            "CLOSE_PRICE": item.get("CLOSE_PRICE"),
            "AVERAGE_PRICE": item.get("AVERAGE_PRICE"),
            "LD_CLOSE_CHANGE": item.get("LD_CLOSE_CHANGE"),
            "LD_AVERAGE_CHANGE": item.get("LD_AVERAGE_CHANGE"),
            "NEXT_DAY_CLOSE": item.get("NEXT_DAY_CLOSE"),
            "THIRD_DAY_CLOSE": item.get("THIRD_DAY_CLOSE"),
            "NEXT_DAY_CLOSE_CHANGE": item.get("NEXT_DAY_CLOSE_CHANGE"),
            "THIRD_DAY_CLOSE_CHANGE": item.get("THIRD_DAY_CLOSE_CHANGE"),
            "NEXT_DAY_FROM_LISTING_CLOSE_PCT": item.get("NEXT_DAY_FROM_LISTING_CLOSE_PCT"),
            "THIRD_DAY_FROM_LISTING_CLOSE_PCT": item.get("THIRD_DAY_FROM_LISTING_CLOSE_PCT"),
            "POST_LISTING_PROFIT_EFFECT_PCT": item.get("POST_LISTING_PROFIT_EFFECT_PCT"),
            "TURNOVERRATE": item.get("TURNOVERRATE"),
            "industry_primary": item.get("industry_primary"),
            "industry_secondary": item.get("industry_secondary"),
            "old_shares": item.get("old_shares"),
            "float_shares": item.get("float_shares"),
        }
        for item in items
    ]


def _calc_change_pct(issue_price: float | None, target_price: float | None) -> float | None:
    if not issue_price or not target_price:
        return None
    return (target_price / issue_price - 1) * 100


def _actual_interval_price(item: dict[str, Any]) -> float | None:
    average_price = _safe_float(item.get("AVERAGE_PRICE"))
    if average_price is not None and average_price > 0:
        return average_price
    return _safe_float(item.get("CLOSE_PRICE"))


def _actual_interval_price_source(item: dict[str, Any]) -> str:
    average_price = _safe_float(item.get("AVERAGE_PRICE"))
    if average_price is not None and average_price > 0:
        return str(item.get("average_price_source") or "首日成交均价").strip()
    return "close_price_fallback"


def _actual_interval_change_pct(item: dict[str, Any]) -> float | None:
    average_change = _safe_float(item.get("LD_AVERAGE_CHANGE"))
    if average_change is not None:
        return average_change
    return _calc_change_pct(_safe_float(item.get("ISSUE_PRICE")), _actual_interval_price(item))


def _evaluate_replay_prediction(
    item: dict[str, Any],
    params: dict[str, Any],
    recent_pool: list[dict[str, Any]],
    evaluation_scope: str,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    code = str(item.get("SECURITY_CODE") or "").strip()
    issue_price = _safe_float(item.get("ISSUE_PRICE"))
    method2 = valuation_engine.method2_industry_momentum(
        issue_price=issue_price,
        issue_pe=_safe_float(item.get("AFTER_ISSUE_PE")),
        industry_pe=_safe_float(item.get("INDUSTRY_PE_NEW")),
        float_shares=_safe_float(item.get("float_shares")),
        industry={
            "primary": str(item.get("industry_primary") or "未分类"),
            "secondary": str(item.get("industry_secondary") or "未分类"),
        },
        recent_ipos=recent_pool,
        params=params,
        target_code=code,
        target_listing_date=item.get("LISTING_DATE"),
    )

    method3 = valuation_engine.method3_recent_sentiment(
        issue_price=issue_price,
        recent_ipos=recent_pool,
        params=params,
        target_code=code,
        target_listing_date=item.get("LISTING_DATE"),
    )

    if evaluation_scope == METHOD2_ONLY_SCOPE:
        return method2, None, method2, method3

    comparable_data = list(item.get("comparable_data") or [])
    method1 = valuation_engine.method1_comparable(
        issue_price=issue_price,
        issue_pe=_safe_float(item.get("AFTER_ISSUE_PE")),
        comparable_data=comparable_data,
        params=params,
        industry_pe=_safe_float(item.get("INDUSTRY_PE_NEW")),
        float_shares=_safe_float(item.get("float_shares")),
    )
    final = valuation_engine.composite_valuation(method1, method2, params, method3=method3)
    final = valuation_engine.apply_local_center_overlay(
        final,
        issue_price=issue_price,
        issue_pe=_safe_float(item.get("AFTER_ISSUE_PE")),
        industry_pe=_safe_float(item.get("INDUSTRY_PE_NEW")),
        float_shares=_safe_float(item.get("float_shares")),
        old_shares=_safe_float(item.get("old_shares")),
        industry={
            "primary": str(item.get("industry_primary") or "未分类"),
            "secondary": str(item.get("industry_secondary") or "未分类"),
        },
        recent_ipos=recent_pool,
        params=params,
        target_code=code,
        target_listing_date=item.get("LISTING_DATE"),
        online_issue_num=_safe_float(item.get("ONLINE_ISSUE_NUM")),
        top_apply_marketcap=_safe_float(item.get("TOP_APPLY_MARKETCAP")),
    )
    return final, method1, method2, method3


def evaluate_replay_targets(
    dataset: dict[str, Any],
    params: dict[str, Any],
    target_codes: list[str] | None = None,
) -> dict[str, Any]:
    items = list(dataset.get("items") or [])
    target_set = set(_normalize_codes(target_codes))
    recent_pool = _build_recent_pool(items)
    evaluation_scope = _get_dataset_evaluation_scope(dataset)
    width = float(params.get("price_range_width", 0.10))

    available_results: list[dict[str, Any]] = []
    unavailable_results: list[dict[str, Any]] = []
    change_abs_errors: list[float] = []
    change_signed_errors: list[float] = []
    price_abs_errors: list[float] = []
    price_signed_errors: list[float] = []
    interval_hits = 0
    direction_hits = 0
    change_eval_count = 0
    price_eval_count = 0

    for item in items:
        code = str(item.get("SECURITY_CODE") or "").strip()
        if target_set and code not in target_set:
            continue

        base_result = {
            "code": code,
            "name": str(item.get("SECURITY_NAME_ABBR") or "").strip(),
            "listing_date": str(item.get("LISTING_DATE") or "").strip(),
            "actual_close_price": _safe_float(item.get("CLOSE_PRICE")),
            "actual_average_price": _safe_float(item.get("AVERAGE_PRICE")),
            "actual_interval_price": _actual_interval_price(item),
            "actual_interval_price_source": _actual_interval_price_source(item),
            "actual_change_pct": _safe_float(item.get("LD_CLOSE_CHANGE")),
            "actual_interval_change_pct": _actual_interval_change_pct(item),
        }
        valuation_result, method1, method2, method3 = _evaluate_replay_prediction(item, params, recent_pool, evaluation_scope)
        if not valuation_result.get("available"):
            unavailable_results.append({**base_result, "reason": str(valuation_result.get("reason") or "")})
            continue

        predicted_price = _safe_float(valuation_result.get("target_price"))
        predicted_change = _calc_change_pct(_safe_float(item.get("ISSUE_PRICE")), predicted_price)
        actual_price = _actual_interval_price(item)
        actual_change = _actual_interval_change_pct(item)

        if predicted_change is not None and actual_change is not None:
            change_error = predicted_change - actual_change
            change_eval_count += 1
            change_abs_errors.append(abs(change_error))
            change_signed_errors.append(change_error)
            direction_hits += int((predicted_change >= 0) == (actual_change >= 0))

        range_low = None
        range_high = None
        if predicted_price is not None and actual_price is not None:
            price_error = predicted_price - actual_price
            price_eval_count += 1
            price_abs_errors.append(abs(price_error))
            price_signed_errors.append(price_error)
            range_low = _safe_float(valuation_result.get("range_low"))
            range_high = _safe_float(valuation_result.get("range_high"))
            if range_low is None or range_high is None:
                range_low = predicted_price * (1 - width)
                range_high = predicted_price * (1 + width)
            interval_hits += int(range_low <= actual_price <= range_high)

        available_results.append(
            {
                **base_result,
                "predicted_target_price": predicted_price,
                "predicted_change_pct": predicted_change,
                "range_low": range_low,
                "range_high": range_high,
                "price_abs_error": abs(predicted_price - actual_price) if predicted_price is not None and actual_price is not None else None,
                "change_abs_error": abs(predicted_change - actual_change) if predicted_change is not None and actual_change is not None else None,
                "method1_available": bool(method1 and method1.get("available")),
                "method1_target_price": _safe_float((method1 or {}).get("target_price")),
                "method1_change_pct": _safe_float((method1 or {}).get("change_pct")),
                "method1_sample_count": (method1 or {}).get("sample_count"),
                "method1_anchor_source": (method1 or {}).get("anchor_source"),
                "method1_confidence_multiplier": _safe_float((method1 or {}).get("confidence_multiplier")),
                "method1_anchor_quality": dict((method1 or {}).get("anchor_quality") or {}),
                "method1_disagreement_ratio": _safe_float(valuation_result.get("method1_disagreement_ratio")),
                "method1_disagreement_factor": _safe_float(valuation_result.get("method1_disagreement_factor")),
                "method2_available": bool(method2 and method2.get("available")),
                "method2_target_price": _safe_float((method2 or {}).get("target_price")),
                "method2_change_pct": _safe_float((method2 or {}).get("change_pct")),
                "method3_available": bool(method3 and method3.get("available")),
                "method3_premium_price": _safe_float((method3 or {}).get("premium_price")),
                "method3_sentiment_premium_pct": _safe_float((method3 or {}).get("sentiment_premium_pct")),
                "method3_first_day_factor_pct": _safe_float((method3 or {}).get("first_day_factor_pct")),
                "method3_post_listing_factor_pct": _safe_float((method3 or {}).get("post_listing_factor_pct")),
                "method3_post_listing_sample_count": (method3 or {}).get("post_listing_sample_count"),
                "method3_change_pct": _safe_float((method3 or {}).get("change_pct")),
                "method3_sample_count": (method3 or {}).get("sample_count"),
                "method3_sample_codes": list((method3 or {}).get("sample_codes") or []),
                "sample_scope": (method2 or {}).get("sample_scope"),
                "sample_count": (method2 or {}).get("sample_count"),
                "historical_sample_count": (method2 or {}).get("historical_sample_count"),
                "recent_days": (method2 or {}).get("recent_days"),
                "sample_codes": list((method2 or {}).get("sample_codes") or []),
                "adj_factor": (method2 or {}).get("adj_factor"),
                "trend_factor": (method2 or {}).get("trend_factor"),
                "float_factor": (method2 or {}).get("float_factor"),
                "pe_factor": (method2 or {}).get("pe_factor"),
                "weight_comparable": _safe_float(valuation_result.get("weight_comparable")),
                "weight_industry_momentum": _safe_float(valuation_result.get("weight_industry_momentum")),
                "weight_recent_sentiment": _safe_float(valuation_result.get("weight_recent_sentiment")),
            }
        )

    total_targets = len(available_results) + len(unavailable_results)
    available_count = len(available_results)
    return {
        "evaluation_scope": evaluation_scope,
        "target_count": total_targets,
        "available_count": available_count,
        "available_rate": (available_count / total_targets) if total_targets else 0.0,
        "change_eval_count": change_eval_count,
        "price_eval_count": price_eval_count,
        "mae_change_pct": _mean(change_abs_errors),
        "p90_change_abs_error_pct": _quantile(change_abs_errors, 0.90),
        "worst_change_abs_error_pct": max(change_abs_errors) if change_abs_errors else None,
        "rmse_change_pct": _rmse(change_signed_errors),
        "mae_target_price": _mean(price_abs_errors),
        "rmse_target_price": _rmse(price_signed_errors),
        "interval_hit_rate": (interval_hits / price_eval_count) if price_eval_count else 0.0,
        "direction_hit_rate": (direction_hits / change_eval_count) if change_eval_count else 0.0,
        "available_results": available_results,
        "unavailable_results": unavailable_results,
    }


def _candidate_signature(overrides: dict[str, Any]) -> str:
    return json.dumps(overrides, ensure_ascii=False, sort_keys=True)


def _ranking_sort_key(metrics: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        metrics.get("mae_change_pct") if metrics.get("mae_change_pct") is not None else float("inf"),
        metrics.get("rmse_change_pct") if metrics.get("rmse_change_pct") is not None else float("inf"),
        -(metrics.get("interval_hit_rate") or 0.0),
        -(metrics.get("available_rate") or 0.0),
    )


def _candidate_review_sort_key(item: dict[str, Any], metrics_key: str) -> tuple[float, float, float, float, int, str]:
    return (
        *_ranking_sort_key(item.get(metrics_key) or {}),
        len(dict(item.get("overrides") or {})),
        str(item.get("name") or ""),
    )


def _validate_supported_overrides(
    dataset: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    unsupported_keys = sorted(
        {
            key
            for item in candidates
            for key in dict(item).keys()
            if key in METHOD2_UNSUPPORTED_KEYS
        }
    )
    if unsupported_keys and _get_dataset_evaluation_scope(dataset) != COMPOSITE_EVALUATION_SCOPE:
        joined = ", ".join(unsupported_keys)
        raise ValueError(
            f"当前离线调参评估范围仍为 {METHOD2_ONLY_SCOPE}，暂不支持直接调综合权重参数：{joined}。"
            "如需调这些参数，需要先补方法一历史回放，再切到综合口径评估。"
        )


def rank_param_candidates(
    dataset: dict[str, Any],
    base_params: dict[str, Any],
    candidates: list[dict[str, Any]],
    train_ratio: float = 0.7,
    min_train_samples: int = 8,
    top_n: int = 10,
    include_baseline: bool = True,
    progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    evaluation_scope = _get_dataset_evaluation_scope(dataset)
    _validate_supported_overrides(dataset, candidates)
    train_codes, validation_codes = split_target_codes(
        dataset,
        train_ratio=train_ratio,
        min_train_samples=min_train_samples,
    )
    selection_metrics_key = "validation_metrics" if validation_codes else "train_metrics"
    selection_metrics_scope = "validation" if validation_codes else "train"

    candidate_specs: list[dict[str, Any]] = []
    if include_baseline:
        candidate_specs.append({"label": "baseline", "overrides": {}})
    candidate_specs.extend({"label": "candidate", "overrides": dict(item)} for item in candidates)

    deduped_specs: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    for spec in candidate_specs:
        signature = _candidate_signature(spec["overrides"])
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        deduped_specs.append(spec)

    baseline_result: dict[str, Any] | None = None
    ranking: list[dict[str, Any]] = []
    total_specs = len(deduped_specs)
    for index, spec in enumerate(deduped_specs, start=1):
        if progress_callback is not None:
            progress_callback(index, total_specs, spec)
        candidate_params = dict(base_params)
        candidate_params.update(spec["overrides"])
        train_metrics = evaluate_replay_targets(dataset, candidate_params, train_codes)
        validation_metrics = evaluate_replay_targets(dataset, candidate_params, validation_codes)
        entry = {
            "rank_seed": index,
            "label": spec["label"],
            "overrides": dict(spec["overrides"]),
            "train_metrics": train_metrics,
            "validation_metrics": validation_metrics,
            "full_metrics": evaluate_replay_targets(dataset, candidate_params),
        }
        ranking.append(entry)
        if spec["label"] == "baseline":
            baseline_result = entry

    ranking.sort(key=lambda item: _ranking_sort_key(item[selection_metrics_key]))

    best = ranking[0] if ranking else None
    return {
        "generated_at": _now_text(),
        "evaluation_scope": evaluation_scope,
        "selection_metrics_scope": selection_metrics_scope,
        "train_codes": train_codes,
        "validation_codes": validation_codes,
        "searched_candidate_count": len(candidates),
        "candidate_count": len(deduped_specs),
        "baseline": baseline_result,
        "best": best,
        "best_is_baseline": bool(best and best.get("label") == "baseline"),
        "top_candidates": ranking[:top_n],
    }


def review_candidate_sets(
    dataset: dict[str, Any],
    base_params: dict[str, Any],
    candidate_payload: dict[str, Any],
    train_ratio: float = 0.7,
    min_train_samples: int = 8,
    progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    evaluation_scope = _get_dataset_evaluation_scope(dataset)
    _validate_supported_overrides(dataset, [dict(item.get("overrides") or {}) for item in candidate_payload.get("candidates") or []])
    train_codes, validation_codes = split_target_codes(
        dataset,
        train_ratio=train_ratio,
        min_train_samples=min_train_samples,
    )
    selection_metrics_key = "validation_metrics" if validation_codes else "train_metrics"
    selection_metrics_scope = "validation" if validation_codes else "train"

    specs: list[dict[str, Any]] = [
        {
            "name": "baseline",
            "description": "当前参数",
            "overrides": {},
        }
    ]
    for item in candidate_payload.get("candidates") or []:
        specs.append(
            {
                "name": str(item.get("name") or "").strip() or "candidate",
                "description": str(item.get("description") or "").strip(),
                "overrides": dict(item.get("overrides") or {}),
            }
        )

    deduped_specs: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    for spec in specs:
        signature = _candidate_signature(spec["overrides"])
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        deduped_specs.append(spec)

    results: list[dict[str, Any]] = []
    total_specs = len(deduped_specs)
    for index, spec in enumerate(deduped_specs, start=1):
        if progress_callback is not None:
            progress_callback(index, total_specs, spec)
        candidate_params = dict(base_params)
        candidate_params.update(spec["overrides"])
        results.append(
            {
                "name": spec["name"],
                "description": spec["description"],
                "overrides": dict(spec["overrides"]),
                "train_metrics": evaluate_replay_targets(dataset, candidate_params, train_codes),
                "validation_metrics": evaluate_replay_targets(dataset, candidate_params, validation_codes),
                "full_metrics": evaluate_replay_targets(dataset, candidate_params),
            }
        )

    ranking = sorted(results, key=lambda item: _candidate_review_sort_key(item, selection_metrics_key))
    baseline = next((item for item in results if item.get("name") == "baseline"), None)
    best_overall = ranking[0] if ranking else None
    best_candidate = next((item for item in ranking if item.get("name") != "baseline"), None)

    return {
        "generated_at": _now_text(),
        "evaluation_scope": evaluation_scope,
        "selection_metrics_scope": selection_metrics_scope,
        "review_name": str(candidate_payload.get("name") or "candidate_review").strip() or "candidate_review",
        "review_description": str(candidate_payload.get("description") or "").strip(),
        "source_report": str(candidate_payload.get("source_report") or "").strip(),
        "train_codes": train_codes,
        "validation_codes": validation_codes,
        "baseline": baseline,
        "best_overall": best_overall,
        "best_candidate": best_candidate,
        "ranked_results": ranking,
    }


def _resolve_target_codes(
    dataset: dict[str, Any],
    target_codes: list[str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    dataset_codes = [
        str(item.get("SECURITY_CODE") or "").strip()
        for item in dataset.get("items") or []
        if str(item.get("SECURITY_CODE") or "").strip()
    ]
    if target_codes is None:
        return dataset_codes, dataset_codes, []

    requested_codes = _normalize_codes(target_codes)
    dataset_code_set = set(dataset_codes)
    resolved_codes = [code for code in dataset_codes if code in set(requested_codes)]
    missing_codes = [code for code in requested_codes if code not in dataset_code_set]
    return requested_codes, resolved_codes, missing_codes


def _observe_candidate_sort_key(item: dict[str, Any]) -> tuple[float, float, float, float, int, str]:
    return (
        *_ranking_sort_key(item.get("observe_metrics") or {}),
        len(dict(item.get("overrides") or {})),
        str(item.get("name") or ""),
    )


def _index_metrics_results(metrics: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    available = {
        str(item.get("code") or "").strip(): item
        for item in metrics.get("available_results") or []
        if str(item.get("code") or "").strip()
    }
    unavailable = {
        str(item.get("code") or "").strip(): item
        for item in metrics.get("unavailable_results") or []
        if str(item.get("code") or "").strip()
    }
    return available, unavailable


def _is_interval_hit(
    actual_price: float | None,
    range_low: float | None,
    range_high: float | None,
) -> bool | None:
    if actual_price is None or range_low is None or range_high is None:
        return None
    return range_low <= actual_price <= range_high


def _build_observe_row_note(
    baseline_reason: str,
    candidate_reason: str,
) -> str:
    if baseline_reason and candidate_reason:
        if baseline_reason == candidate_reason:
            return f"baseline 与候选均不可用：{baseline_reason}"
        return f"baseline 不可用：{baseline_reason}；候选不可用：{candidate_reason}"
    if baseline_reason:
        return f"baseline 不可用：{baseline_reason}"
    if candidate_reason:
        return f"候选不可用：{candidate_reason}"
    return ""


def _build_candidate_observe_rows(
    dataset: dict[str, Any],
    target_codes: list[str],
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    dataset_items = {
        str(item.get("SECURITY_CODE") or "").strip(): item
        for item in dataset.get("items") or []
        if str(item.get("SECURITY_CODE") or "").strip()
    }
    baseline_available, baseline_unavailable = _index_metrics_results(baseline_metrics)
    candidate_available, candidate_unavailable = _index_metrics_results(candidate_metrics)

    rows: list[dict[str, Any]] = []
    for code in target_codes:
        dataset_item = dataset_items.get(code, {})
        baseline_result = baseline_available.get(code)
        candidate_result = candidate_available.get(code)
        baseline_reason = str((baseline_unavailable.get(code) or {}).get("reason") or "").strip()
        candidate_reason = str((candidate_unavailable.get(code) or {}).get("reason") or "").strip()
        actual_close_price = _safe_float(dataset_item.get("CLOSE_PRICE"))
        actual_average_price = _safe_float(dataset_item.get("AVERAGE_PRICE"))
        actual_interval_price = _actual_interval_price(dataset_item)
        baseline_range_low = _safe_float((baseline_result or {}).get("range_low"))
        baseline_range_high = _safe_float((baseline_result or {}).get("range_high"))
        candidate_range_low = _safe_float((candidate_result or {}).get("range_low"))
        candidate_range_high = _safe_float((candidate_result or {}).get("range_high"))

        rows.append(
            {
                "code": code,
                "name": str(dataset_item.get("SECURITY_NAME_ABBR") or "").strip(),
                "listing_date": str(dataset_item.get("LISTING_DATE") or "").strip(),
                "actual_close_price": actual_close_price,
                "actual_average_price": actual_average_price,
                "actual_interval_price": actual_interval_price,
                "actual_interval_price_source": _actual_interval_price_source(dataset_item),
                "actual_change_pct": _safe_float(dataset_item.get("LD_CLOSE_CHANGE")),
                "actual_interval_change_pct": _actual_interval_change_pct(dataset_item),
                "baseline_available": baseline_result is not None,
                "candidate_available": candidate_result is not None,
                "baseline_reason": baseline_reason,
                "candidate_reason": candidate_reason,
                "baseline_target_price": _safe_float((baseline_result or {}).get("predicted_target_price")),
                "candidate_target_price": _safe_float((candidate_result or {}).get("predicted_target_price")),
                "baseline_abs_price_error": _safe_float((baseline_result or {}).get("price_abs_error")),
                "candidate_abs_price_error": _safe_float((candidate_result or {}).get("price_abs_error")),
                "baseline_change_pct": _safe_float((baseline_result or {}).get("predicted_change_pct")),
                "candidate_change_pct": _safe_float((candidate_result or {}).get("predicted_change_pct")),
                "baseline_change_abs_error": _safe_float((baseline_result or {}).get("change_abs_error")),
                "candidate_change_abs_error": _safe_float((candidate_result or {}).get("change_abs_error")),
                "baseline_range_low": baseline_range_low,
                "baseline_range_high": baseline_range_high,
                "candidate_range_low": candidate_range_low,
                "candidate_range_high": candidate_range_high,
                "baseline_interval_hit": _is_interval_hit(actual_interval_price, baseline_range_low, baseline_range_high),
                "candidate_interval_hit": _is_interval_hit(actual_interval_price, candidate_range_low, candidate_range_high),
                "baseline_sample_scope": (baseline_result or {}).get("sample_scope"),
                "candidate_sample_scope": (candidate_result or {}).get("sample_scope"),
                "baseline_sample_count": (baseline_result or {}).get("sample_count"),
                "candidate_sample_count": (candidate_result or {}).get("sample_count"),
                "baseline_pe_factor": _safe_float((baseline_result or {}).get("pe_factor")),
                "candidate_pe_factor": _safe_float((candidate_result or {}).get("pe_factor")),
                "baseline_float_factor": _safe_float((baseline_result or {}).get("float_factor")),
                "candidate_float_factor": _safe_float((candidate_result or {}).get("float_factor")),
                "comparison_outcome": _compare_change_error(
                    _safe_float((baseline_result or {}).get("change_abs_error")),
                    _safe_float((candidate_result or {}).get("change_abs_error")),
                ),
                "note": _build_observe_row_note(baseline_reason, candidate_reason),
            }
        )
    return rows


def observe_candidate_sets(
    dataset: dict[str, Any],
    base_params: dict[str, Any],
    candidate_payload: dict[str, Any],
    target_codes: list[str] | None = None,
    progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    evaluation_scope = _get_dataset_evaluation_scope(dataset)
    _validate_supported_overrides(dataset, [dict(item.get("overrides") or {}) for item in candidate_payload.get("candidates") or []])

    requested_codes, resolved_codes, missing_codes = _resolve_target_codes(dataset, target_codes)
    if not resolved_codes:
        raise ValueError(
            "观察样本未命中回放数据集：{codes}".format(
                codes=",".join(requested_codes or missing_codes or ["<empty>"])
            )
        )

    specs: list[dict[str, Any]] = [
        {
            "name": "baseline",
            "description": "当前参数",
            "overrides": {},
        }
    ]
    for item in candidate_payload.get("candidates") or []:
        specs.append(
            {
                "name": str(item.get("name") or "").strip() or "candidate",
                "description": str(item.get("description") or "").strip(),
                "overrides": dict(item.get("overrides") or {}),
            }
        )

    deduped_specs: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    for spec in specs:
        signature = _candidate_signature(spec["overrides"])
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        deduped_specs.append(spec)

    raw_results: list[dict[str, Any]] = []
    total_specs = len(deduped_specs)
    for index, spec in enumerate(deduped_specs, start=1):
        if progress_callback is not None:
            progress_callback(index, total_specs, spec)
        candidate_params = dict(base_params)
        candidate_params.update(spec["overrides"])
        raw_results.append(
            {
                "name": spec["name"],
                "description": spec["description"],
                "overrides": dict(spec["overrides"]),
                "observe_metrics": evaluate_replay_targets(dataset, candidate_params, resolved_codes),
            }
        )

    baseline = next((item for item in raw_results if item.get("name") == "baseline"), None)
    if baseline is None:
        raise ValueError("观察结果缺少 baseline")

    ranking: list[dict[str, Any]] = []
    for item in raw_results:
        if item.get("name") == "baseline":
            continue
        ranking.append(
            {
                **item,
                "rows": _build_candidate_observe_rows(
                    dataset,
                    resolved_codes,
                    baseline.get("observe_metrics") or {},
                    item.get("observe_metrics") or {},
                ),
            }
        )
    ranking.sort(key=_observe_candidate_sort_key)

    combined_ranking = sorted(raw_results, key=_observe_candidate_sort_key)
    best_overall = combined_ranking[0] if combined_ranking else None
    best_candidate = ranking[0] if ranking else None

    return {
        "generated_at": _now_text(),
        "evaluation_scope": evaluation_scope,
        "observe_name": str(candidate_payload.get("name") or "manual_observe").strip() or "manual_observe",
        "observe_description": str(candidate_payload.get("description") or "").strip(),
        "requested_codes": requested_codes,
        "target_codes": resolved_codes,
        "missing_codes": missing_codes,
        "baseline": baseline,
        "best_overall": best_overall,
        "best_candidate": best_candidate,
        "ranked_results": ranking,
    }


def _value_options_with_current(
    base_params: dict[str, Any],
    key: str,
    values: list[Any],
) -> list[Any]:
    options = list(values)
    current_value = base_params.get(key)
    if current_value is not None:
        options.append(current_value)

    normalized: list[Any] = []
    seen: set[str] = set()
    for value in options:
        signature = _candidate_signature({"value": value})
        if signature in seen:
            continue
        seen.add(signature)
        normalized.append(value)

    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in normalized):
        return sorted(normalized, key=lambda item: float(item))
    return normalized


def _round_auto_value(value: float, decimals: int) -> float:
    rounded = round(float(value), decimals)
    if decimals <= 0:
        rounded = round(rounded)
    if abs(rounded) <= AUTO_NORMALIZE_EPSILON:
        return 0.0
    return float(rounded)


def _auto_spread_values(
    params: dict[str, Any],
    key: str,
    low: float,
    high: float,
    *,
    midpoint: float | None = None,
    decimals: int = 2,
) -> list[float]:
    current = _safe_float(params.get(key))
    if current is None:
        current = midpoint if midpoint is not None else (low + high) / 2
    midpoint_value = midpoint if midpoint is not None else (low + high) / 2
    values = [low, min(max(float(current), low), high), high, midpoint_value]
    normalized: list[float] = []
    seen: set[str] = set()
    for value in values:
        clipped = min(max(float(value), low), high)
        rendered = _round_auto_value(clipped, decimals)
        signature = _candidate_signature({"value": rendered})
        if signature in seen:
            continue
        seen.add(signature)
        normalized.append(rendered)
        if len(normalized) >= 3:
            break
    return sorted(normalized)


def _auto_local_values(
    params: dict[str, Any],
    key: str,
    low: float,
    high: float,
    step: float,
    *,
    decimals: int = 2,
) -> list[float]:
    current = _safe_float(params.get(key))
    if current is None:
        current = (low + high) / 2
    values = [float(current) - step, float(current), float(current) + step]
    normalized: list[float] = []
    seen: set[str] = set()
    for value in values:
        clipped = min(max(float(value), low), high)
        rendered = _round_auto_value(clipped, decimals)
        signature = _candidate_signature({"value": rendered})
        if signature in seen:
            continue
        seen.add(signature)
        normalized.append(rendered)
    return sorted(normalized)


def _auto_int_spread_values(
    params: dict[str, Any],
    key: str,
    low: int,
    high: int,
    *,
    midpoint: int | None = None,
) -> list[int]:
    return [int(round(value)) for value in _auto_spread_values(params, key, low, high, midpoint=midpoint, decimals=0)]


def _auto_int_local_values(
    params: dict[str, Any],
    key: str,
    low: int,
    high: int,
    step: int,
) -> list[int]:
    return [int(round(value)) for value in _auto_local_values(params, key, low, high, step, decimals=0)]


def _auto_stage_values(
    params: dict[str, Any],
    key: str,
    low: float,
    high: float,
    coarse_midpoint: float | None,
    fine_steps: tuple[float, float],
    stage_level: int,
    *,
    decimals: int = 2,
) -> list[float]:
    if stage_level <= 1:
        return _auto_spread_values(params, key, low, high, midpoint=coarse_midpoint, decimals=decimals)
    step = fine_steps[0] if stage_level == 2 else fine_steps[1]
    return _auto_local_values(params, key, low, high, step, decimals=decimals)


def _auto_stage_int_values(
    params: dict[str, Any],
    key: str,
    low: int,
    high: int,
    coarse_midpoint: int | None,
    fine_steps: tuple[int, int],
    stage_level: int,
) -> list[int]:
    if stage_level <= 1:
        return _auto_int_spread_values(params, key, low, high, midpoint=coarse_midpoint)
    step = fine_steps[0] if stage_level == 2 else fine_steps[1]
    return _auto_int_local_values(params, key, low, high, step)


def _auto_product_candidates(option_groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for combo in itertools.product(*option_groups):
        merged: dict[str, Any] = {}
        for overrides in combo:
            merged.update(overrides)
        candidates.append(merged)
    return _dedupe_override_list(candidates)


def build_auto_tune_candidate_groups(
    base_params: dict[str, Any],
    dataset: dict[str, Any],
    stage_level: int = 1,
    center_params: dict[str, Any] | None = None,
) -> list[tuple[str, list[dict[str, Any]]]]:
    evaluation_scope = _get_dataset_evaluation_scope(dataset)
    params = dict(center_params or base_params)
    groups: list[tuple[str, list[dict[str, Any]]]] = []

    if evaluation_scope == COMPOSITE_EVALUATION_SCOPE:
        weight_values = _auto_stage_values(params, "weight_comparable", 0.20, 0.80, 0.50, (0.05, 0.025), stage_level, decimals=3)
        discount_values = _auto_stage_values(params, "bse_discount_factor", 0.55, 0.85, 0.70, (0.05, 0.025), stage_level, decimals=3)
        groups.append(
            (
                "综合估值权重",
                [
                    {
                        "weight_comparable": value,
                        "weight_industry_momentum": round(1 - float(value), 10),
                    }
                    for value in weight_values
                ],
            )
        )
        groups.append(("北交所折扣", [{"bse_discount_factor": value} for value in discount_values]))

    if evaluation_scope == COMPOSITE_EVALUATION_SCOPE:
        float_threshold_values = _auto_stage_int_values(params, "float_size_threshold", 800, 2800, 1800, (300, 100), stage_level)
        small_cap_values = _auto_stage_values(params, "small_cap_premium", 0.00, 0.25, 0.10, (0.05, 0.025), stage_level, decimals=3)
        groups.append(("流通盘阈值", [{"float_size_threshold": threshold} for threshold in float_threshold_values]))
        groups.append(("小盘溢价", [{"small_cap_premium": premium} for premium in small_cap_values]))

        pe_low_values = _auto_stage_values(params, "pe_low_threshold", 0.20, 0.50, 0.35, (0.05, 0.025), stage_level, decimals=3)
        pe_boost_values = _auto_stage_values(params, "pe_discount_boost", 0.00, 0.15, 0.05, (0.025, 0.01), stage_level, decimals=3)
        pe_high_values = _auto_stage_values(params, "pe_high_threshold", 0.55, 0.85, 0.70, (0.05, 0.025), stage_level, decimals=3)
        pe_drag_values = _auto_stage_values(params, "pe_premium_drag", -0.15, 0.00, -0.05, (0.025, 0.01), stage_level, decimals=3)
        groups.append(
            (
                "PE 低估修正",
                _auto_product_candidates(
                    [
                        [{"pe_low_threshold": low} for low in pe_low_values],
                        [{"pe_discount_boost": boost} for boost in pe_boost_values],
                    ]
                ),
            )
        )
        groups.append(
            (
                "PE 高估修正",
                _auto_product_candidates(
                    [
                        [{"pe_high_threshold": high} for high in pe_high_values],
                        [{"pe_premium_drag": drag} for drag in pe_drag_values],
                    ]
                ),
            )
        )

    half_life_values = _auto_stage_int_values(params, "method2_decay_half_life_days", 30, 180, 90, (30, 15), stage_level)
    groups.append(("方法二样本权重模式", [{"method2_weight_mode": "static"}, {"method2_weight_mode": "time_decay"}]))
    groups.append(
        (
            "方法二样本半衰期",
            [
                {"method2_weight_mode": "time_decay", "method2_decay_half_life_days": value}
                for value in half_life_values
            ],
        )
    )
    groups.append(
        (
            "方法二稳健过滤",
            _auto_product_candidates(
                [
                    [
                        {"robust_median_min_samples": value}
                        for value in _auto_stage_int_values(
                            params,
                            "robust_median_min_samples",
                            3,
                            8,
                            4,
                            (1, 1),
                            stage_level,
                        )
                    ],
                    [
                        {"robust_mad_multiplier": value}
                        for value in _auto_stage_values(
                            params,
                            "robust_mad_multiplier",
                            2.0,
                            4.0,
                            3.0,
                            (0.5, 0.25),
                            stage_level,
                            decimals=2,
                        )
                    ],
                ]
            ),
        )
    )
    if evaluation_scope == COMPOSITE_EVALUATION_SCOPE:
        groups.append(
            (
                "方法二小样本置信度",
                [
                    {
                        "method2_confidence_1_sample": n1,
                        "method2_confidence_2_samples": n2,
                        "method2_confidence_3_samples": n3,
                        "method2_confidence_4plus_samples": n4,
                    }
                    for n1, n2, n3, n4 in (
                        (0.20, 0.20, 0.35, 0.40),
                        (0.05, 0.20, 0.45, 0.75),
                        (0.10, 0.25, 0.50, 0.80),
                        (0.15, 0.35, 0.60, 0.90),
                        (0.20, 0.40, 0.65, 0.90),
                        (0.25, 0.50, 0.75, 1.00),
                        (0.35, 0.60, 0.80, 1.00),
                    )
                ],
            )
        )
        groups.append(
            (
                "方法一行业 PE 兜底置信度",
                [
                    {"method1_industry_fallback_confidence": value}
                    for value in _auto_stage_values(
                        params,
                        "method1_industry_fallback_confidence",
                        0.10,
                        0.60,
                        0.30,
                        (0.10, 0.05),
                        stage_level,
                        decimals=3,
                    )
                ],
            )
        )
        groups.append(
            (
                "可比 PE 统计方式",
                [
                    {"comparable_pe_stat": "median"},
                    {"comparable_pe_stat": "mean"},
                ],
            )
        )

    groups.append(
        (
            "方法三情绪窗口",
            [
                {"recent_days": value}
                for value in _auto_stage_int_values(
                    params,
                    "recent_days",
                    30,
                    120,
                    60,
                    (15, 5),
                    stage_level,
                )
            ],
        )
    )
    sentiment_half_life_values = _auto_stage_int_values(params, "sentiment_decay_half_life_days", 2, 15, 5, (2, 1), stage_level)
    groups.append(("sentiment_half_life", [{"sentiment_decay_half_life_days": value} for value in sentiment_half_life_values]))
    groups.append(
        (
            "sentiment_first_day_baseline",
            [
                {"sentiment_first_day_baseline_pct": value}
                for value in _auto_stage_values(params, "sentiment_first_day_baseline_pct", 60.0, 160.0, 100.0, (20.0, 10.0), stage_level, decimals=1)
            ],
        )
    )
    groups.append(
        (
            "sentiment_first_day_scale",
            [
                {"sentiment_first_day_scale": value}
                for value in _auto_stage_values(params, "sentiment_first_day_scale", 0.05, 0.35, 0.15, (0.05, 0.025), stage_level, decimals=3)
            ],
        )
    )
    groups.append(
        (
            "sentiment_post_listing_scale",
            [
                {"sentiment_post_listing_scale": value}
                for value in _auto_stage_values(params, "sentiment_post_listing_scale", 0.00, 0.40, 0.15, (0.05, 0.025), stage_level, decimals=3)
            ],
        )
    )
    groups.append(
        (
            "sentiment_premium_cap",
            [
                {"sentiment_premium_cap_pct": value}
                for value in _auto_stage_values(params, "sentiment_premium_cap_pct", 15.0, 50.0, 35.0, (5.0, 2.5), stage_level, decimals=1)
            ],
        )
    )
    groups.append(
        (
            "sentiment_premium_floor",
            [
                {"sentiment_premium_floor_pct": value}
                for value in _auto_stage_values(params, "sentiment_premium_floor_pct", -35.0, 0.0, -20.0, (5.0, 2.5), stage_level, decimals=1)
            ],
        )
    )
    if evaluation_scope == COMPOSITE_EVALUATION_SCOPE and _is_enabled(params.get("local_center_overlay_enabled"), False):
        groups.append(
            (
                "本地滚动中枢混合",
                _auto_product_candidates(
                    [
                        [
                            {"local_center_alpha": value}
                            for value in _auto_stage_values(
                                params,
                                "local_center_alpha",
                                0.25,
                                0.75,
                                0.50,
                                (0.10, 0.05),
                                stage_level,
                                decimals=2,
                            )
                        ],
                        [
                            {"local_center_history_window": value}
                            for value in _auto_stage_int_values(
                                params,
                                "local_center_history_window",
                                10,
                                40,
                                20,
                                (5, 2),
                                stage_level,
                            )
                        ],
                    ]
                ),
            )
        )
        groups.append(
            (
                "本地滚动中枢稳健性",
                _auto_product_candidates(
                    [
                        [
                            {"local_center_min_history": value}
                            for value in _auto_stage_int_values(
                                params,
                                "local_center_min_history",
                                5,
                                12,
                                8,
                                (1, 1),
                                stage_level,
                            )
                        ],
                        [
                            {"local_center_actual_cap_pct": value}
                            for value in _auto_stage_values(
                                params,
                                "local_center_actual_cap_pct",
                                400.0,
                                900.0,
                                600.0,
                                (100.0, 50.0),
                                stage_level,
                                decimals=1,
                            )
                        ],
                        [
                            {"local_center_slope_cap": value}
                            for value in _auto_stage_values(
                                params,
                                "local_center_slope_cap",
                                10.0,
                                35.0,
                                25.0,
                                (5.0, 2.5),
                                stage_level,
                                decimals=1,
                            )
                        ],
                    ]
                ),
            )
        )
    if evaluation_scope == METHOD2_ONLY_SCOPE:
        allowed_keys = set(LATEST_METHOD2_AUTO_TUNABLE_KEYS)
        groups = [
            (
                name,
                [candidate for candidate in candidates if set(candidate).issubset(allowed_keys)],
            )
            for name, candidates in groups
        ]
    return [(name, _dedupe_override_list(candidates)) for name, candidates in groups if candidates]


def _dedupe_override_list(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        signature = _candidate_signature(candidate)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(dict(candidate))
    return deduped


def _auto_tune_reference_date(dataset: dict[str, Any]) -> date:
    dates = [_parse_date(item.get("LISTING_DATE")) for item in dataset.get("items") or []]
    dates = [item for item in dates if item is not None]
    if dates:
        return max(dates)
    return date.today()


def _auto_sample_window_days(params: dict[str, Any]) -> int:
    raw_days = params.get("recent_days")
    if raw_days not in (None, ""):
        return max(int(float(raw_days)), 1)
    raw_months = params.get("recent_months")
    if raw_months not in (None, ""):
        return max(int(float(raw_months)) * 30, 1)
    return AUTO_TUNE_LOOKBACK_DAYS


def _auto_sample_weight(sample_date: date | None, reference_date: date) -> float:
    if sample_date is None:
        return 0.0
    day_gap = max((reference_date - sample_date).days, 0)
    if day_gap >= AUTO_TUNE_LOOKBACK_DAYS:
        return 0.0
    return ((AUTO_TUNE_LOOKBACK_DAYS - day_gap) / AUTO_TUNE_LOOKBACK_DAYS) ** 2


def _is_recent_auto_sample(sample_date: date | None, reference_date: date) -> bool:
    if sample_date is None:
        return False
    day_gap = max((reference_date - sample_date).days, 0)
    return day_gap <= AUTO_TUNE_RECENT_FLOOR_DAYS


def _build_auto_weighted_results(
    results: list[dict[str, Any]],
    reference_date: date,
    *,
    use_recency_weight: bool,
) -> tuple[list[tuple[dict[str, Any], float]], dict[str, Any]]:
    raw_items: list[dict[str, Any]] = []
    for result in results:
        sample_date = _parse_date(result.get("listing_date"))
        weight = _auto_sample_weight(sample_date, reference_date) if use_recency_weight else 1.0
        if weight <= 0:
            continue
        raw_items.append(
            {
                "result": result,
                "weight": float(weight),
                "is_recent": _is_recent_auto_sample(sample_date, reference_date),
            }
        )

    total_weight = sum(float(item["weight"]) for item in raw_items)
    if total_weight <= AUTO_NORMALIZE_EPSILON:
        return [], {
            "recent_weight_share": 0.0,
            "recent_floor_applied": False,
            "recent_sample_count": 0,
        }

    for item in raw_items:
        item["weight"] = float(item["weight"]) / total_weight

    recent_items = [item for item in raw_items if item["is_recent"]]
    recent_weight_share = sum(float(item["weight"]) for item in recent_items)
    recent_floor_applied = False
    if (
        use_recency_weight
        and recent_items
        and recent_weight_share > AUTO_NORMALIZE_EPSILON
        and recent_weight_share < AUTO_TUNE_RECENT_MIN_TOTAL_WEIGHT
    ):
        older_weight_share = 1.0 - recent_weight_share
        if older_weight_share > AUTO_NORMALIZE_EPSILON:
            recent_scale = AUTO_TUNE_RECENT_MIN_TOTAL_WEIGHT / recent_weight_share
            older_scale = (1.0 - AUTO_TUNE_RECENT_MIN_TOTAL_WEIGHT) / older_weight_share
            for item in raw_items:
                if item["is_recent"]:
                    item["weight"] = float(item["weight"]) * recent_scale
                else:
                    item["weight"] = float(item["weight"]) * older_scale
            recent_weight_share = AUTO_TUNE_RECENT_MIN_TOTAL_WEIGHT
            recent_floor_applied = True

    return [(dict(item["result"]), float(item["weight"])) for item in raw_items], {
        "recent_weight_share": recent_weight_share,
        "recent_floor_applied": recent_floor_applied,
        "recent_sample_count": len(recent_items),
    }


def _score_auto_metrics(
    metrics: dict[str, Any],
    params: dict[str, Any],
    reference_date: date,
) -> dict[str, Any]:
    def _weighted_components(use_recency_weight: bool) -> tuple[float, float, float, float, dict[str, Any]]:
        total_weight = 0.0
        hit_weight = 0.0
        weighted_abs_change_error = 0.0
        error_weight = 0.0
        weighted_results, weight_summary = _build_auto_weighted_results(
            list(metrics.get("available_results") or []),
            reference_date,
            use_recency_weight=use_recency_weight,
        )
        for result, weight in weighted_results:
            actual_price = _safe_float(result.get("actual_interval_price"))
            if actual_price is None:
                actual_price = _safe_float(result.get("actual_close_price"))
            range_low = _safe_float(result.get("range_low"))
            range_high = _safe_float(result.get("range_high"))
            total_weight += weight
            if _is_interval_hit(actual_price, range_low, range_high):
                hit_weight += weight

            change_error = _safe_float(result.get("change_abs_error"))
            if change_error is not None:
                weighted_abs_change_error += weight * change_error
                error_weight += weight
        return total_weight, hit_weight, weighted_abs_change_error, error_weight, weight_summary

    total_weight, hit_weight, weighted_abs_change_error, error_weight, weight_summary = _weighted_components(use_recency_weight=True)
    recency_fallback_used = False
    if total_weight <= AUTO_NORMALIZE_EPSILON:
        total_weight, hit_weight, weighted_abs_change_error, error_weight, weight_summary = _weighted_components(use_recency_weight=False)
        recency_fallback_used = True

    weighted_hit_rate = (hit_weight / total_weight) if total_weight else 0.0
    weighted_mae_change_pct = (weighted_abs_change_error / error_weight) if error_weight else None
    width = max(float(params.get("price_range_width", 0.10)), 0.0)
    width_diagnostic_penalty = width * AUTO_TUNE_WIDTH_DIAGNOSTIC_FACTOR
    mae_penalty = (weighted_mae_change_pct or 0.0) * AUTO_TUNE_MAE_PENALTY
    auto_score = weighted_hit_rate - mae_penalty

    return {
        "auto_score": auto_score,
        "weighted_interval_hit_rate": weighted_hit_rate,
        "weighted_hit_score": hit_weight,
        "total_sample_weight": total_weight,
        "weighted_mae_change_pct": weighted_mae_change_pct,
        "price_range_width": width,
        "width_diagnostic_penalty": width_diagnostic_penalty,
        "mae_penalty": mae_penalty,
        "recency_fallback_used": recency_fallback_used,
        "lookback_days": AUTO_TUNE_LOOKBACK_DAYS,
        "recent_floor_days": AUTO_TUNE_RECENT_FLOOR_DAYS,
        "recent_min_total_weight": AUTO_TUNE_RECENT_MIN_TOTAL_WEIGHT,
        "recent_weight_share": weight_summary.get("recent_weight_share", 0.0),
        "recent_floor_applied": bool(weight_summary.get("recent_floor_applied")),
        "recent_sample_count": weight_summary.get("recent_sample_count", 0),
    }


def _auto_entry_sort_key(item: dict[str, Any]) -> tuple[float, float, float, float, int, str]:
    score = item.get("auto_score") or {}
    return (
        -(score.get("auto_score") if score.get("auto_score") is not None else float("-inf")),
        -(score.get("weighted_interval_hit_rate") or 0.0),
        score.get("weighted_mae_change_pct") if score.get("weighted_mae_change_pct") is not None else float("inf"),
        score.get("price_range_width") if score.get("price_range_width") is not None else float("inf"),
        len(dict(item.get("overrides") or {})),
        str(item.get("name") or ""),
    )


def _formal_acceptance_guard(
    candidate_metrics: dict[str, Any],
    baseline_metrics: dict[str, Any],
) -> dict[str, Any]:
    epsilon = 1e-12
    candidate_hit = float(candidate_metrics.get("interval_hit_rate") or 0.0)
    baseline_hit = float(baseline_metrics.get("interval_hit_rate") or 0.0)
    candidate_mae = _safe_float(candidate_metrics.get("mae_change_pct"))
    baseline_mae = _safe_float(baseline_metrics.get("mae_change_pct"))
    candidate_p90 = _safe_float(candidate_metrics.get("p90_change_abs_error_pct"))
    baseline_p90 = _safe_float(baseline_metrics.get("p90_change_abs_error_pct"))
    candidate_available = float(candidate_metrics.get("available_rate") or 0.0)
    baseline_available = float(baseline_metrics.get("available_rate") or 0.0)
    checks = {
        "full_hit_not_lower": candidate_hit + epsilon >= baseline_hit,
        "full_mae_not_higher": (
            candidate_mae is not None
            and baseline_mae is not None
            and candidate_mae <= baseline_mae + epsilon
        ),
        "full_p90_not_higher": (
            candidate_p90 is not None
            and baseline_p90 is not None
            and candidate_p90 <= baseline_p90 + epsilon
        ),
        "availability_not_lower": candidate_available + epsilon >= baseline_available,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "candidate": {
            "interval_hit_rate": candidate_hit,
            "mae_change_pct": candidate_mae,
            "p90_change_abs_error_pct": candidate_p90,
            "available_rate": candidate_available,
        },
        "baseline": {
            "interval_hit_rate": baseline_hit,
            "mae_change_pct": baseline_mae,
            "p90_change_abs_error_pct": baseline_p90,
            "available_rate": baseline_available,
        },
    }


def _diff_params(
    base_params: dict[str, Any],
    candidate_params: dict[str, Any],
    keys: set[str],
) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for key in sorted(keys):
        if _values_differ(base_params.get(key), candidate_params.get(key)):
            diff[key] = candidate_params.get(key)
    return diff


def _evaluate_auto_entry(
    dataset: dict[str, Any],
    candidate_params: dict[str, Any],
    overrides: dict[str, Any],
    reference_date: date,
    *,
    group_name: str,
    name: str,
) -> dict[str, Any]:
    metrics = evaluate_replay_targets(dataset, candidate_params)
    return {
        "group": group_name,
        "name": name,
        "overrides": dict(overrides),
        "metrics": metrics,
        "auto_score": _score_auto_metrics(metrics, candidate_params, reference_date),
    }


def auto_tune_params(
    dataset: dict[str, Any],
    base_params: dict[str, Any],
    top_n: int = 10,
    max_passes: int = 1,
    stage_level: int = 1,
    center_params: dict[str, Any] | None = None,
    candidate_limit: int | None = AUTO_TUNE_STAGE_CANDIDATE_LIMIT,
    time_limit_seconds: float | None = AUTO_TUNE_STAGE_TIME_LIMIT_SECONDS,
    progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    search_start_params = dict(center_params or base_params)
    groups = build_auto_tune_candidate_groups(base_params, dataset, stage_level=stage_level, center_params=search_start_params)
    evaluation_scope = _get_dataset_evaluation_scope(dataset)
    candidate_keys = sorted({key for _, candidates in groups for candidate in candidates for key in candidate})
    required_latest_keys = set(LATEST_METHOD2_AUTO_TUNABLE_KEYS)
    if evaluation_scope == COMPOSITE_EVALUATION_SCOPE:
        required_latest_keys.update(LATEST_METHOD1_AUTO_TUNABLE_KEYS)
        required_latest_keys.update(LATEST_METHOD2_CONFIDENCE_AUTO_TUNABLE_KEYS)
        required_latest_keys.update(LATEST_METHOD3_AUTO_TUNABLE_KEYS)
        if _is_enabled(base_params.get("local_center_overlay_enabled"), False):
            required_latest_keys.update(LATEST_LOCAL_CENTER_AUTO_TUNABLE_KEYS)
    missing_latest_keys = sorted(required_latest_keys - set(candidate_keys))
    model_contract = {
        "version": AUTO_TUNE_MODEL_CONTRACT_VERSION,
        "evaluation_scope": evaluation_scope,
        "candidate_keys": candidate_keys,
        "required_latest_model_keys": sorted(required_latest_keys),
        "missing_latest_model_keys": missing_latest_keys,
        "structural_flags": {key: base_params.get(key) for key in LATEST_MODEL_STRUCTURAL_FLAGS},
        "latest_model_compatible": not missing_latest_keys,
    }
    if missing_latest_keys:
        raise ValueError(f"自动调参候选缺少最新估值模型参数：{', '.join(missing_latest_keys)}")
    reference_date = _auto_tune_reference_date(dataset)
    tunable_keys = {key for _, candidates in groups for candidate in candidates for key in candidate.keys()}
    tunable_keys.update(key for key in search_start_params if _values_differ(base_params.get(key), search_start_params.get(key)))

    current_params = dict(search_start_params)
    current_overrides: dict[str, Any] = _diff_params(base_params, current_params, tunable_keys)
    baseline_entry = _evaluate_auto_entry(
        dataset,
        base_params,
        {},
        reference_date,
        group_name="baseline",
        name="baseline",
    )
    stage_start_entry = _evaluate_auto_entry(
        dataset,
        current_params,
        current_overrides,
        reference_date,
        group_name="stage_start",
        name=f"stage_{stage_level}_start",
    )
    current_entry = stage_start_entry
    evaluated_entries: list[dict[str, Any]] = [baseline_entry, stage_start_entry]
    pass_summaries: list[dict[str, Any]] = []
    planned_steps = sum(len(candidates) for _, candidates in groups) * max(max_passes, 1)
    if candidate_limit is not None:
        planned_steps = min(planned_steps, max(int(candidate_limit), 0))
    total_steps = max(planned_steps, 1)
    step_index = 0
    stop_reason = ""
    deadline = time.monotonic() + float(time_limit_seconds) if time_limit_seconds is not None else None

    for pass_index in range(1, max_passes + 1):
        pass_changed = False
        for group_name, candidates in groups:
            if stop_reason:
                break
            group_entries: list[dict[str, Any]] = [
                {
                    **current_entry,
                    "group": group_name,
                    "name": f"{group_name}_current",
                }
            ]
            for candidate_index, group_overrides in enumerate(candidates, start=1):
                if candidate_limit is not None and step_index >= int(candidate_limit):
                    stop_reason = f"候选数达到本轮上限 {candidate_limit}"
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    stop_reason = f"达到本轮时间上限 {int(float(time_limit_seconds))} 秒"
                    break
                step_index += 1
                candidate_params = dict(current_params)
                candidate_params.update(group_overrides)
                combined_overrides = _diff_params(base_params, candidate_params, tunable_keys)
                spec = {
                    "stage_level": stage_level,
                    "group": group_name,
                    "name": f"{group_name}_{candidate_index}",
                    "overrides": combined_overrides,
                }
                if progress_callback is not None:
                    progress_callback(step_index, total_steps, spec)
                entry = _evaluate_auto_entry(
                    dataset,
                    candidate_params,
                    combined_overrides,
                    reference_date,
                    group_name=group_name,
                    name=f"{group_name}_{candidate_index}",
                )
                group_entries.append(entry)
                evaluated_entries.append(entry)

            group_entries.sort(key=_auto_entry_sort_key)
            best_group_entry = group_entries[0]
            current_score = (current_entry.get("auto_score") or {}).get("auto_score")
            best_score = (best_group_entry.get("auto_score") or {}).get("auto_score")
            if (
                best_group_entry.get("name") != f"{group_name}_current"
                and best_score is not None
                and current_score is not None
                and best_score > current_score + 1e-12
            ):
                current_overrides = dict(best_group_entry.get("overrides") or {})
                current_params = dict(base_params)
                current_params.update(current_overrides)
                current_entry = best_group_entry
                pass_changed = True
                pass_summaries.append(
                    {
                        "stage_level": stage_level,
                        "pass": pass_index,
                        "group": group_name,
                        "selected_overrides": current_overrides,
                        "auto_score": best_group_entry.get("auto_score"),
                    }
                )
        if stop_reason:
            break
        if not pass_changed:
            break

    current_overrides = _diff_params(base_params, current_params, tunable_keys)
    best_entry = _evaluate_auto_entry(
        dataset,
        current_params,
        current_overrides,
        reference_date,
        group_name="final",
        name="best_auto",
    )
    evaluated_entries.append(best_entry)

    unique_entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in evaluated_entries:
        signature = _candidate_signature(dict(entry.get("overrides") or {}))
        if signature in seen:
            continue
        seen.add(signature)
        unique_entries.append(entry)
    unique_entries.sort(key=_auto_entry_sort_key)

    baseline_metrics = dict(baseline_entry.get("metrics") or {})
    for entry in unique_entries:
        entry["formal_acceptance_guard"] = _formal_acceptance_guard(
            dict(entry.get("metrics") or {}),
            baseline_metrics,
        )
    guarded_entries = [
        entry
        for entry in unique_entries
        if (entry.get("formal_acceptance_guard") or {}).get("passed")
    ]
    guarded_entries.sort(key=_auto_entry_sort_key)
    best_entry = guarded_entries[0] if guarded_entries else baseline_entry
    current_overrides = dict(best_entry.get("overrides") or {})
    final_guard = dict(
        best_entry.get("formal_acceptance_guard")
        or _formal_acceptance_guard(dict(best_entry.get("metrics") or {}), baseline_metrics)
    )

    return {
        "generated_at": _now_text(),
        "evaluation_scope": evaluation_scope,
        "model_contract": model_contract,
        "stage_level": stage_level,
        "reference_date": reference_date.isoformat(),
        "sample_window_days": _auto_sample_window_days(base_params),
        "lookback_days": AUTO_TUNE_LOOKBACK_DAYS,
        "recent_floor_days": AUTO_TUNE_RECENT_FLOOR_DAYS,
        "recent_min_total_weight": AUTO_TUNE_RECENT_MIN_TOTAL_WEIGHT,
        "candidate_group_count": len(groups),
        "evaluated_candidate_count": len(unique_entries),
        "evaluated_step_count": step_index,
        "planned_candidate_count": sum(len(candidates) for _, candidates in groups) * max(max_passes, 1),
        "stage_candidate_limit": candidate_limit,
        "stage_time_limit_seconds": time_limit_seconds,
        "stop_reason": stop_reason,
        "baseline": baseline_entry,
        "stage_start": stage_start_entry,
        "best": best_entry,
        "best_is_baseline": not bool(current_overrides),
        "changed_overrides": current_overrides,
        "formal_acceptance_guard": {
            **final_guard,
            "eligible_candidate_count": len(guarded_entries),
            "evaluated_candidate_count": len(unique_entries),
        },
        "pass_summaries": pass_summaries,
        "top_candidates": guarded_entries[:top_n],
    }


def build_auto_tune_change_lines(
    base_params: dict[str, Any],
    overrides: dict[str, Any],
) -> list[str]:
    lines: list[str] = []
    for key, new_value in overrides.items():
        old_value = base_params.get(key)
        if isinstance(old_value, float):
            old_text = f"{old_value:.4f}".rstrip("0").rstrip(".")
        else:
            old_text = str(old_value)
        if isinstance(new_value, float):
            new_text = f"{new_value:.4f}".rstrip("0").rstrip(".")
        else:
            new_text = str(new_value)
        lines.append(f"{key}: {old_text} -> {new_text}")
    return lines


def prepend_auto_tuning_record(
    record_path: str | Path,
    result: dict[str, Any],
    base_params: dict[str, Any],
    params_file: str | Path,
) -> Path:
    output_path = Path(record_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    overrides = dict(result.get("changed_overrides") or {})
    baseline_score = ((result.get("baseline") or {}).get("auto_score") or {})
    best_score = ((result.get("best") or {}).get("auto_score") or {})
    baseline_metrics = ((result.get("baseline") or {}).get("metrics") or {})
    best_metrics = ((result.get("best") or {}).get("metrics") or {})
    formal_guard = result.get("formal_acceptance_guard") or {}
    time_slice_gate = result.get("time_slice_gate") or {}
    local_rerank = result.get("local_learning_rerank") or {}
    local_selected = local_rerank.get("selected") or {}
    conservative = local_selected.get("conservative") or {}
    regime = local_selected.get("regime") or {}
    rolling = local_selected.get("rolling") or {}
    def _local_width_text(line: dict[str, Any]) -> str:
        width = _safe_float(line.get("weighted_avg_width"))
        return "" if width is None else f"{width * 100:.2f}%"

    change_lines = build_auto_tune_change_lines(base_params, overrides) or ["无参数变化"]
    record_lines = [
        f"## {_now_text()} 自动调参（已接受）",
        "",
        f"- 参数文件：{Path(params_file)}",
        f"- 评估范围：{result.get('evaluation_scope')}",
        f"- 最终搜索轮次：第 {result.get('stage_level')} 轮",
        f"- 方法三情绪样本窗口：{result.get('sample_window_days')} 天（recent_days）",
        f"- 近期权重基准日：{result.get('reference_date')}，评分权重衰减窗口：{result.get('lookback_days')} 天",
        f"- 最近 {result.get('recent_floor_days')} 天样本最低总权重：{_fmt_metric(result.get('recent_min_total_weight'))}",
        f"- baseline 排序分：{_fmt_metric(baseline_score.get('auto_score'))}",
        f"- 新参数排序分：{_fmt_metric(best_score.get('auto_score'))}",
        f"- baseline 近期加权命中率：{_fmt_metric(baseline_score.get('weighted_interval_hit_rate'))}",
        f"- 新参数近期加权命中率：{_fmt_metric(best_score.get('weighted_interval_hit_rate'))}",
        f"- 新参数最近样本权重占比：{_fmt_metric(best_score.get('recent_weight_share'))}",
        f"- 新参数加权 MAE(涨幅)：{_fmt_metric(best_score.get('weighted_mae_change_pct'))}",
        f"- 正式写回安全门槛：{'通过' if formal_guard.get('passed') else '未通过'}",
        f"- 三折时间切片门槛：{'通过' if time_slice_gate.get('passed') else '未通过'}；路径：{time_slice_gate.get('required_path') or '-'}；显式绕过：{'是' if time_slice_gate.get('bypassed') else '否'}",
        f"- 时间切片报告：{((time_slice_gate.get('outputs') or {}).get('markdown') or '-')}",
        f"- 全样本命中率 baseline / 新参数：{_fmt_metric(baseline_metrics.get('interval_hit_rate'))} / {_fmt_metric(best_metrics.get('interval_hit_rate'))}",
        f"- 全样本 MAE baseline / 新参数：{_fmt_metric(baseline_metrics.get('mae_change_pct'))} / {_fmt_metric(best_metrics.get('mae_change_pct'))}",
        f"- 全样本 P90 绝对误差 baseline / 新参数：{_fmt_metric(baseline_metrics.get('p90_change_abs_error_pct'))} / {_fmt_metric(best_metrics.get('p90_change_abs_error_pct'))}",
        f"- 当前手动区间宽度诊断扣分：{_fmt_metric(best_score.get('width_diagnostic_penalty'))}",
        f"- 本地学习重排：{'已执行' if local_rerank.get('applied') else '未执行'}；是否改变核心最优：{'是' if local_rerank.get('selection_changed') else '否'}",
        f"- 综合学习分：{_fmt_metric(local_selected.get('learning_score'))}",
        f"- 保守动态区间加权命中率 / MAE / 平均宽度：{_fmt_metric(conservative.get('weighted_interval_hit_rate'))} / {_fmt_metric(conservative.get('weighted_mae_change_pct'))} / {_local_width_text(conservative)}",
        f"- regime-break 加权命中率 / MAE / 平均宽度：{_fmt_metric(regime.get('weighted_interval_hit_rate'))} / {_fmt_metric(regime.get('weighted_mae_change_pct'))} / {_local_width_text(regime)}",
        f"- 滚动中枢加权命中率 / MAE / 平均宽度：{_fmt_metric(rolling.get('weighted_interval_hit_rate'))} / {_fmt_metric(rolling.get('weighted_mae_change_pct'))} / {_local_width_text(rolling)}",
        "- 本地学习重排作者输入：未使用",
        "",
        "修改参数：",
        *[f"- {line}" for line in change_lines],
        "",
    ]
    old_text = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
    output_path.write_text("\n".join(record_lines) + ("\n" + old_text if old_text else ""), encoding="utf-8")
    return output_path


def _build_param_lines(overrides: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key, value in overrides.items():
        if isinstance(value, float):
            rendered = f"{value:.4f}".rstrip("0").rstrip(".")
            lines.append(f"{key} = {rendered}")
        else:
            lines.append(f"{key} = {value}")
    return lines


def _build_candidate_text(item: dict[str, Any]) -> str:
    if item.get("label") == "baseline" and not item.get("overrides"):
        return "baseline（当前参数）"
    lines = _build_param_lines(dict(item.get("overrides") or {}))
    return "；".join(lines) if lines else "候选参数"


def _metric_delta_text(best_value: float | None, baseline_value: float | None) -> str:
    if best_value is None or baseline_value is None:
        return "-"
    delta = best_value - baseline_value
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.4f}"


def _build_review_name_text(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "").strip() or "candidate"
    description = str(item.get("description") or "").strip()
    if description:
        return f"{name}（{description}）"
    return name


def _scope_display_name(scope: str) -> str:
    mapping = {
        "train": "训练集",
        "validation": "验证集",
        "full": "全样本",
    }
    return mapping.get(scope, scope)


def _dataset_target_codes(dataset: dict[str, Any]) -> list[str]:
    sample_codes = _normalize_codes(list(dataset.get("sample_codes") or []))
    if sample_codes:
        return sample_codes
    return [
        str(item.get("SECURITY_CODE") or "").strip()
        for item in dataset.get("items") or []
        if str(item.get("SECURITY_CODE") or "").strip()
    ]


def _selection_scope_target_codes(result: dict[str, Any], metrics_scope: str) -> list[str]:
    if metrics_scope == "validation":
        return list(result.get("validation_codes") or [])
    return list(result.get("train_codes") or [])


def _compare_change_error(
    baseline_error: float | None,
    candidate_error: float | None,
) -> str:
    if baseline_error is None and candidate_error is None:
        return "-"
    if baseline_error is None:
        return "候选补出结果"
    if candidate_error is None:
        return "候选缺结果"
    if candidate_error < baseline_error:
        return "候选更优"
    if candidate_error > baseline_error:
        return "候选更差"
    return "持平"


def _summarize_compare_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    comparable_rows = [
        row
        for row in rows
        if row.get("baseline_change_abs_error") is not None and row.get("candidate_change_abs_error") is not None
    ]
    noncomparable_rows = [
        row
        for row in rows
        if row.get("baseline_change_abs_error") is None or row.get("candidate_change_abs_error") is None
    ]
    candidate_better_count = sum(
        1
        for row in comparable_rows
        if float(row["candidate_change_abs_error"]) < float(row["baseline_change_abs_error"])
    )
    candidate_worse_count = sum(
        1
        for row in comparable_rows
        if float(row["candidate_change_abs_error"]) > float(row["baseline_change_abs_error"])
    )
    tie_count = sum(
        1
        for row in comparable_rows
        if float(row["candidate_change_abs_error"]) == float(row["baseline_change_abs_error"])
    )
    changed_row_count = sum(1 for row in rows if _observe_row_has_effective_change(row))
    unchanged_row_count = max(len(rows) - changed_row_count, 0)
    noncomparable_unchanged_count = sum(1 for row in noncomparable_rows if not _observe_row_has_effective_change(row))
    noncomparable_changed_count = max(len(noncomparable_rows) - noncomparable_unchanged_count, 0)
    return {
        "row_count": len(rows),
        "comparable_count": len(comparable_rows),
        "noncomparable_count": len(noncomparable_rows),
        "candidate_better_count": candidate_better_count,
        "candidate_worse_count": candidate_worse_count,
        "tie_count": tie_count,
        "error_smaller_count": candidate_better_count,
        "error_larger_count": candidate_worse_count,
        "error_unchanged_count": tie_count,
        "changed_row_count": changed_row_count,
        "unchanged_row_count": unchanged_row_count,
        "noncomparable_unchanged_count": noncomparable_unchanged_count,
        "noncomparable_changed_count": noncomparable_changed_count,
        "interval_gain_count": sum(
            1
            for row in rows
            if row.get("baseline_interval_hit") is False and row.get("candidate_interval_hit") is True
        ),
        "interval_loss_count": sum(
            1
            for row in rows
            if row.get("baseline_interval_hit") is True and row.get("candidate_interval_hit") is False
        ),
        "baseline_unavailable_count": sum(1 for row in rows if not row.get("baseline_available")),
        "candidate_unavailable_count": sum(1 for row in rows if not row.get("candidate_available")),
    }


def _build_scope_comparison(
    dataset: dict[str, Any],
    target_codes: list[str],
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> dict[str, Any]:
    rows = _build_candidate_observe_rows(dataset, target_codes, baseline_metrics, candidate_metrics)
    return {
        "target_codes": list(target_codes),
        "summary": _summarize_compare_rows(rows),
        "rows": rows,
    }


def _build_metrics_overview_lines(
    scopes: list[tuple[str, dict[str, Any], dict[str, Any]]],
) -> list[str]:
    lines = [
        "| 范围 | baseline MAE | 候选 MAE | MAE 变化 | baseline RMSE | 候选 RMSE | RMSE 变化 | baseline 区间命中率 | 候选区间命中率 | baseline 可用率 | 候选可用率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    seen_scopes: set[str] = set()
    for scope, baseline_metrics, candidate_metrics in scopes:
        if scope in seen_scopes:
            continue
        seen_scopes.add(scope)
        lines.append(
            "| {scope} | {base_mae} | {cand_mae} | {mae_delta} | {base_rmse} | {cand_rmse} | {rmse_delta} | {base_hit} | {cand_hit} | {base_avail} | {cand_avail} |".format(
                scope=_scope_display_name(scope),
                base_mae=_fmt_metric(baseline_metrics.get("mae_change_pct")),
                cand_mae=_fmt_metric(candidate_metrics.get("mae_change_pct")),
                mae_delta=_metric_delta_text(candidate_metrics.get("mae_change_pct"), baseline_metrics.get("mae_change_pct")),
                base_rmse=_fmt_metric(baseline_metrics.get("rmse_change_pct")),
                cand_rmse=_fmt_metric(candidate_metrics.get("rmse_change_pct")),
                rmse_delta=_metric_delta_text(candidate_metrics.get("rmse_change_pct"), baseline_metrics.get("rmse_change_pct")),
                base_hit=_fmt_metric(baseline_metrics.get("interval_hit_rate")),
                cand_hit=_fmt_metric(candidate_metrics.get("interval_hit_rate")),
                base_avail=_fmt_metric(baseline_metrics.get("available_rate")),
                cand_avail=_fmt_metric(candidate_metrics.get("available_rate")),
            )
        )
    return lines


def _build_compare_summary_lines(summary: dict[str, Any]) -> list[str]:
    return [
        f"- 可比样本数：`{summary.get('comparable_count', 0)} / {summary.get('row_count', 0)}`",
        f"- 候选优于 baseline：`{summary.get('candidate_better_count', 0)}`",
        f"- 候选劣于 baseline：`{summary.get('candidate_worse_count', 0)}`",
        f"- 持平：`{summary.get('tie_count', 0)}`",
        f"- 候选新增区间命中：`{summary.get('interval_gain_count', 0)}`",
        f"- 候选丢失区间命中：`{summary.get('interval_loss_count', 0)}`",
        f"- baseline 不可用样本：`{summary.get('baseline_unavailable_count', 0)}`",
        f"- 候选不可用样本：`{summary.get('candidate_unavailable_count', 0)}`",
    ]


def _build_compare_table_lines(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 代码 | 名称 | 实际均价 | 实际涨幅 | baseline 目标价 | 候选目标价 | baseline 涨幅误差 | 候选涨幅误差 | baseline 区间命中 | 候选区间命中 | 结论 | 备注 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {code} | {name} | {actual_close} | {actual_change} | {baseline_target} | {candidate_target} | {baseline_change_error} | {candidate_change_error} | {baseline_hit} | {candidate_hit} | {outcome} | {note} |".format(
                code=row.get("code") or "-",
                name=row.get("name") or "-",
                actual_close=_fmt_metric(_safe_float(row.get("actual_interval_price"))),
                actual_change=_fmt_metric(_safe_float(row.get("actual_interval_change_pct"))),
                baseline_target=_fmt_metric(_safe_float(row.get("baseline_target_price"))),
                candidate_target=_fmt_metric(_safe_float(row.get("candidate_target_price"))),
                baseline_change_error=_fmt_metric(_safe_float(row.get("baseline_change_abs_error"))),
                candidate_change_error=_fmt_metric(_safe_float(row.get("candidate_change_abs_error"))),
                baseline_hit=_fmt_flag(row.get("baseline_interval_hit")),
                candidate_hit=_fmt_flag(row.get("candidate_interval_hit")),
                outcome=row.get("comparison_outcome") or "-",
                note=row.get("note") or "-",
            )
        )
    return lines


def _values_differ(left: Any, right: Any, tolerance: float = 1e-9) -> bool:
    if left is None or right is None:
        return left != right
    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
        return not math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance)
    return left != right


def _observe_row_has_effective_change(row: dict[str, Any]) -> bool:
    compare_pairs = [
        ("baseline_available", "candidate_available"),
        ("baseline_reason", "candidate_reason"),
        ("baseline_target_price", "candidate_target_price"),
        ("baseline_abs_price_error", "candidate_abs_price_error"),
        ("baseline_change_pct", "candidate_change_pct"),
        ("baseline_change_abs_error", "candidate_change_abs_error"),
        ("baseline_range_low", "candidate_range_low"),
        ("baseline_range_high", "candidate_range_high"),
        ("baseline_interval_hit", "candidate_interval_hit"),
        ("baseline_sample_scope", "candidate_sample_scope"),
        ("baseline_sample_count", "candidate_sample_count"),
        ("baseline_pe_factor", "candidate_pe_factor"),
        ("baseline_float_factor", "candidate_float_factor"),
    ]
    return any(_values_differ(row.get(left_key), row.get(right_key)) for left_key, right_key in compare_pairs)


def write_search_outputs(
    dataset: dict[str, Any],
    ranking_result: dict[str, Any],
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    stage_name: str = "custom",
) -> tuple[Path, Path]:
    best = ranking_result.get("best") or {}
    baseline = ranking_result.get("baseline") or {}
    metrics_scope = str(ranking_result.get("selection_metrics_scope") or "validation")
    metrics_key = f"{metrics_scope}_metrics"
    best_metrics = best.get(metrics_key) or {}
    baseline_metrics = baseline.get(metrics_key) or {}
    selection_target_codes = _selection_scope_target_codes(ranking_result, metrics_scope)
    selection_comparison = _build_scope_comparison(dataset, selection_target_codes, baseline_metrics, best_metrics)
    full_comparison = _build_scope_comparison(
        dataset,
        _dataset_target_codes(dataset),
        baseline.get("full_metrics") or {},
        best.get("full_metrics") or {},
    )
    metrics_overview_lines = _build_metrics_overview_lines(
        [
            ("train", baseline.get("train_metrics") or {}, best.get("train_metrics") or {}),
            (metrics_scope, baseline_metrics, best_metrics),
            ("full", baseline.get("full_metrics") or {}, best.get("full_metrics") or {}),
        ]
    )

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = target_dir / f"tune_params_{stage_name}_{timestamp}.json"
    md_path = target_dir / f"tune_params_{stage_name}_{timestamp}.md"

    payload = {
        "stage_name": stage_name,
        "dataset_summary": {
            "available_count": dataset.get("available_count"),
            "method1_ready_count": dataset.get("method1_ready_count"),
            "method1_ready_rate": dataset.get("method1_ready_rate"),
            "sample_codes": dataset.get("sample_codes"),
            "caveats": dataset.get("caveats"),
        },
        "report_comparisons": {
            "selection_scope": {
                "scope": metrics_scope,
                **selection_comparison,
            },
            "full_scope": {
                "scope": "full",
                **full_comparison,
            },
        },
        **ranking_result,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    top_lines = [
        "| 排名 | 候选 | MAE(涨幅) | RMSE(涨幅) | 区间命中率 | 可用率 |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for index, item in enumerate(ranking_result.get("top_candidates") or [], start=1):
        metrics = item.get(metrics_key) or {}
        top_lines.append(
            "| {rank} | {candidate} | {mae} | {rmse} | {hit} | {avail} |".format(
                rank=index,
                candidate=_build_candidate_text(item),
                mae=_fmt_metric(metrics.get("mae_change_pct")),
                rmse=_fmt_metric(metrics.get("rmse_change_pct")),
                hit=_fmt_metric(metrics.get("interval_hit_rate")),
                avail=_fmt_metric(metrics.get("available_rate")),
            )
        )

    best_param_lines = _build_param_lines(dict(best.get("overrides") or {}))
    if not best_param_lines and best.get("label") == "baseline":
        best_param_lines = ["沿用当前参数（baseline）"]

    caveat_lines = [f"- {item}" for item in dataset.get("caveats") or []]
    if not caveat_lines:
        caveat_lines = ["- 无"]
    markdown = "\n".join(
        [
            f"# 离线调参报告（{stage_name}）",
            "",
            f"- 生成时间：{ranking_result.get('generated_at', _now_text())}",
            f"- 评估范围：{ranking_result.get('evaluation_scope', EVALUATION_SCOPE)}",
            f"- 评分依据：{metrics_scope} 集指标",
            f"- 数据集样本数：{dataset.get('available_count', 0)}",
            f"- 方法一可回放样本数：{dataset.get('method1_ready_count', 0)}",
            f"- 训练集代码数：{len(ranking_result.get('train_codes') or [])}",
            f"- 验证集代码数：{len(ranking_result.get('validation_codes') or [])}",
            "",
            "## 说明",
            "",
            *caveat_lines,
            "",
            "## 推荐参数",
            "",
            *[f"- `{line}`" for line in best_param_lines],
            "",
            "## 前后汇总",
            "",
            *metrics_overview_lines,
            "",
            f"## {_scope_display_name(metrics_scope)}逐样本对比（与真实结果）",
            "",
            *(_build_compare_summary_lines(selection_comparison["summary"])),
            "",
            *(_build_compare_table_lines(selection_comparison["rows"])),
            "",
            "## 全样本真实结果对比摘要",
            "",
            *(_build_compare_summary_lines(full_comparison["summary"])),
            "",
            "## Top 候选",
            "",
            *top_lines,
            "",
        ]
    )
    md_path.write_text(markdown, encoding="utf-8")
    return json_path, md_path


def write_candidate_review_outputs(
    dataset: dict[str, Any],
    review_result: dict[str, Any],
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    review_name: str | None = None,
) -> tuple[Path, Path]:
    metrics_scope = str(review_result.get("selection_metrics_scope") or "validation")
    metrics_key = f"{metrics_scope}_metrics"
    baseline = review_result.get("baseline") or {}
    best_candidate = review_result.get("best_candidate") or {}
    baseline_metrics = baseline.get(metrics_key) or {}
    best_candidate_metrics = best_candidate.get(metrics_key) or {}
    selection_target_codes = _selection_scope_target_codes(review_result, metrics_scope)
    selection_comparison = _build_scope_comparison(dataset, selection_target_codes, baseline_metrics, best_candidate_metrics)
    full_comparison = _build_scope_comparison(
        dataset,
        _dataset_target_codes(dataset),
        baseline.get("full_metrics") or {},
        best_candidate.get("full_metrics") or {},
    )
    metrics_overview_lines = _build_metrics_overview_lines(
        [
            ("train", baseline.get("train_metrics") or {}, best_candidate.get("train_metrics") or {}),
            (metrics_scope, baseline_metrics, best_candidate_metrics),
            ("full", baseline.get("full_metrics") or {}, best_candidate.get("full_metrics") or {}),
        ]
    )

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = review_name or str(review_result.get("review_name") or "candidate_review")
    json_path = target_dir / f"review_candidate_{stem}_{timestamp}.json"
    md_path = target_dir / f"review_candidate_{stem}_{timestamp}.md"

    payload = {
        "dataset_summary": {
            "available_count": dataset.get("available_count"),
            "method1_ready_count": dataset.get("method1_ready_count"),
            "method1_ready_rate": dataset.get("method1_ready_rate"),
            "sample_codes": dataset.get("sample_codes"),
            "caveats": dataset.get("caveats"),
        },
        "report_comparisons": {
            "selection_scope": {
                "scope": metrics_scope,
                **selection_comparison,
            },
            "full_scope": {
                "scope": "full",
                **full_comparison,
            },
        },
        **review_result,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    ranked_lines = [
        "| 排名 | 方案 | 验证 MAE | 验证 RMSE | 全样本 MAE | 全样本 RMSE | 全样本区间命中率 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for index, item in enumerate(review_result.get("ranked_results") or [], start=1):
        validation_metrics = item.get("validation_metrics") or {}
        full_metrics = item.get("full_metrics") or {}
        ranked_lines.append(
            "| {rank} | {name} | {val_mae} | {val_rmse} | {full_mae} | {full_rmse} | {full_hit} |".format(
                rank=index,
                name=_build_review_name_text(item),
                val_mae=_fmt_metric(validation_metrics.get("mae_change_pct")),
                val_rmse=_fmt_metric(validation_metrics.get("rmse_change_pct")),
                full_mae=_fmt_metric(full_metrics.get("mae_change_pct")),
                full_rmse=_fmt_metric(full_metrics.get("rmse_change_pct")),
                full_hit=_fmt_metric(full_metrics.get("interval_hit_rate")),
            )
        )

    best_param_lines = _build_param_lines(dict(best_candidate.get("overrides") or {}))
    if not best_param_lines:
        best_param_lines = ["沿用当前参数（baseline）"]

    caveat_lines = [f"- {item}" for item in dataset.get("caveats") or []]
    if not caveat_lines:
        caveat_lines = ["- 无"]
    markdown = "\n".join(
        [
            f"# 候选参数集综合回放复核（{stem}）",
            "",
            f"- 生成时间：{review_result.get('generated_at', _now_text())}",
            f"- 评估范围：{review_result.get('evaluation_scope', EVALUATION_SCOPE)}",
            f"- 评分依据：{metrics_scope} 集指标",
            f"- 数据集样本数：{dataset.get('available_count', 0)}",
            f"- 方法一可回放样本数：{dataset.get('method1_ready_count', 0)}",
            f"- 训练集代码数：{len(review_result.get('train_codes') or [])}",
            f"- 验证集代码数：{len(review_result.get('validation_codes') or [])}",
            "",
            "## 说明",
            "",
            *caveat_lines,
            "",
            "## 当前推荐候选",
            "",
            f"- 方案：`{_build_review_name_text(best_candidate)}`",
            *[f"- `{line}`" for line in best_param_lines],
            "",
            "## 前后汇总",
            "",
            *metrics_overview_lines,
            "",
            f"## {_scope_display_name(metrics_scope)}逐样本对比（与真实结果）",
            "",
            *(_build_compare_summary_lines(selection_comparison["summary"])),
            "",
            *(_build_compare_table_lines(selection_comparison["rows"])),
            "",
            "## 全样本真实结果对比摘要",
            "",
            *(_build_compare_summary_lines(full_comparison["summary"])),
            "",
            "## 候选集排序",
            "",
            *ranked_lines,
            "",
        ]
    )
    md_path.write_text(markdown, encoding="utf-8")
    return json_path, md_path


def write_manual_observe_outputs(
    dataset: dict[str, Any],
    observe_result: dict[str, Any],
    output_dir: str | Path = DEFAULT_OBSERVE_OUTPUT_DIR,
    observe_name: str | None = None,
) -> tuple[Path, Path]:
    baseline = observe_result.get("baseline") or {}
    baseline_metrics = baseline.get("observe_metrics") or {}
    best_candidate = observe_result.get("best_candidate") or {}
    best_candidate_metrics = best_candidate.get("observe_metrics") or {}

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = observe_name or str(observe_result.get("observe_name") or "manual_observe")
    json_path = target_dir / f"observe_manual_{stem}_{timestamp}.json"
    md_path = target_dir / f"observe_manual_{stem}_{timestamp}.md"

    display_ranked_results: list[dict[str, Any]] = []
    for item in observe_result.get("ranked_results") or []:
        all_rows = list(item.get("rows") or [])
        changed_rows = [row for row in all_rows if _observe_row_has_effective_change(row)]
        compare_summary = _summarize_compare_rows(all_rows)
        display_ranked_results.append(
            {
                "name": item.get("name"),
                "description": item.get("description"),
                "changed_row_count": len(changed_rows),
                "hidden_unchanged_row_count": max(len(all_rows) - len(changed_rows), 0),
                "changed_rows": changed_rows,
                "compare_summary": compare_summary,
            }
        )

    payload = {
        "dataset_summary": {
            "available_count": dataset.get("available_count"),
            "method1_ready_count": dataset.get("method1_ready_count"),
            "method1_ready_rate": dataset.get("method1_ready_rate"),
            "sample_codes": dataset.get("sample_codes"),
            "caveats": dataset.get("caveats"),
        },
        "report_display": {
            "display_mode": "changed_only",
            "ranked_results": display_ranked_results,
        },
        **observe_result,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_lines = [
        "| 排名 | 方案 | 样本数 | 误差缩小 | 误差增大 | 无变化 | MAE(涨幅) | RMSE(涨幅) | 区间命中率 | 可用率 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for index, (item, display_item) in enumerate(zip(observe_result.get("ranked_results") or [], display_ranked_results), start=1):
        metrics = item.get("observe_metrics") or {}
        compare_summary = display_item.get("compare_summary") or {}
        summary_lines.append(
            "| {rank} | {name} | {sample_count} | {smaller} | {larger} | {unchanged} | {mae} | {rmse} | {hit} | {avail} |".format(
                rank=index,
                name=_build_review_name_text(item),
                sample_count=compare_summary.get("row_count", 0),
                smaller=compare_summary.get("error_smaller_count", 0),
                larger=compare_summary.get("error_larger_count", 0),
                unchanged=compare_summary.get("error_unchanged_count", 0),
                mae=_fmt_metric(metrics.get("mae_change_pct")),
                rmse=_fmt_metric(metrics.get("rmse_change_pct")),
                hit=_fmt_metric(metrics.get("interval_hit_rate")),
                avail=_fmt_metric(metrics.get("available_rate")),
            )
        )

    detail_sections: list[str] = []
    for item, display_item in zip(observe_result.get("ranked_results") or [], display_ranked_results):
        metrics = item.get("observe_metrics") or {}
        changed_rows = list(display_item.get("changed_rows") or [])
        hidden_unchanged_row_count = int(display_item.get("hidden_unchanged_row_count") or 0)
        compare_summary = display_item.get("compare_summary") or {}
        table_lines: list[str] = []
        if changed_rows:
            table_lines = [
                "| 代码 | 名称 | 实际均价 | baseline 目标价 | 候选目标价 | baseline 误差 | 候选误差 | baseline 区间命中 | 候选区间命中 | 备注 |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
            ]
            for row in changed_rows:
                table_lines.append(
                    "| {code} | {name} | {actual_close} | {baseline_target} | {candidate_target} | {baseline_error} | {candidate_error} | {baseline_hit} | {candidate_hit} | {note} |".format(
                        code=row.get("code") or "-",
                        name=row.get("name") or "-",
                        actual_close=_fmt_metric(_safe_float(row.get("actual_interval_price"))),
                        baseline_target=_fmt_metric(_safe_float(row.get("baseline_target_price"))),
                        candidate_target=_fmt_metric(_safe_float(row.get("candidate_target_price"))),
                        baseline_error=_fmt_metric(_safe_float(row.get("baseline_abs_price_error"))),
                        candidate_error=_fmt_metric(_safe_float(row.get("candidate_abs_price_error"))),
                        baseline_hit=_fmt_flag(row.get("baseline_interval_hit")),
                        candidate_hit=_fmt_flag(row.get("candidate_interval_hit")),
                        note=row.get("note") or "-",
                    )
                )

        candidate_param_lines = _build_param_lines(dict(item.get("overrides") or {}))
        if not candidate_param_lines:
            candidate_param_lines = ["沿用当前参数（baseline）"]

        section_lines = [
            f"## 样本对比：{_build_review_name_text(item)}",
            "",
            *[f"- `{line}`" for line in candidate_param_lines],
            f"- MAE(涨幅)：`{_fmt_metric(metrics.get('mae_change_pct'))}`",
            f"- RMSE(涨幅)：`{_fmt_metric(metrics.get('rmse_change_pct'))}`",
            f"- 区间命中率：`{_fmt_metric(metrics.get('interval_hit_rate'))}`",
            f"- 可用率：`{_fmt_metric(metrics.get('available_rate'))}`",
            f"- 本次样本数：`{compare_summary.get('row_count', 0)}`",
            f"- 与 baseline 相比涨幅误差缩小样本：`{compare_summary.get('error_smaller_count', 0)}`",
            f"- 与 baseline 相比涨幅误差增大样本：`{compare_summary.get('error_larger_count', 0)}`",
            f"- 与 baseline 相比涨幅误差无变化样本：`{compare_summary.get('error_unchanged_count', 0)}`",
            f"- 不可比较但前后相同样本：`{compare_summary.get('noncomparable_unchanged_count', 0)}`",
            f"- 展示变化样本数：`{len(changed_rows)}`",
            f"- 已省略未变化样本数：`{hidden_unchanged_row_count}`",
            "",
        ]
        if changed_rows:
            section_lines.extend([*table_lines, ""])
        else:
            section_lines.extend(
                [
                    "- 本轮无变化样本，已省略全量样本表。",
                    "",
                ]
            )
        detail_sections.extend(section_lines)

    caveat_lines = [f"- {item}" for item in dataset.get("caveats") or []]
    if not caveat_lines:
        caveat_lines = ["- 无"]
    missing_codes = observe_result.get("missing_codes") or []
    markdown = "\n".join(
        [
            f"# 手动参数 replay 观察（{stem}）",
            "",
            f"- 生成时间：{observe_result.get('generated_at', _now_text())}",
            f"- 评估范围：{observe_result.get('evaluation_scope', EVALUATION_SCOPE)}",
            f"- 数据集样本数：{dataset.get('available_count', 0)}",
            f"- 观察代码数：{len(observe_result.get('target_codes') or [])}",
            f"- 请求代码：`{', '.join(observe_result.get('requested_codes') or []) or '-'}`",
            f"- 未命中代码：`{', '.join(missing_codes) if missing_codes else '-'}`",
            "",
            "## 说明",
            "",
            *caveat_lines,
            "- 下方样本表默认只展示调参前后发生变化的样本；未变化样本不重复展开。",
            "- “误差缩小 / 增大 / 无变化”按与真实结果对比的“涨幅绝对误差”统计。",
            "- “不可比较但前后相同样本”表示 baseline 与候选都没有可比较的涨幅误差，但前后输出结果完全一致。",
            "",
            "## baseline",
            "",
            f"- MAE(涨幅)：`{_fmt_metric(baseline_metrics.get('mae_change_pct'))}`",
            f"- RMSE(涨幅)：`{_fmt_metric(baseline_metrics.get('rmse_change_pct'))}`",
            f"- 区间命中率：`{_fmt_metric(baseline_metrics.get('interval_hit_rate'))}`",
            f"- 可用率：`{_fmt_metric(baseline_metrics.get('available_rate'))}`",
            "",
            "## 候选汇总",
            "",
            *summary_lines,
            "",
            "## 当前最优候选",
            "",
            f"- 方案：`{_build_review_name_text(best_candidate) if best_candidate else '无'}`",
            f"- baseline MAE(涨幅)：`{_fmt_metric(baseline_metrics.get('mae_change_pct'))}`",
            f"- 候选 MAE(涨幅)：`{_fmt_metric(best_candidate_metrics.get('mae_change_pct'))}`",
            f"- MAE 变化：`{_metric_delta_text(best_candidate_metrics.get('mae_change_pct'), baseline_metrics.get('mae_change_pct'))}`",
            f"- baseline RMSE(涨幅)：`{_fmt_metric(baseline_metrics.get('rmse_change_pct'))}`",
            f"- 候选 RMSE(涨幅)：`{_fmt_metric(best_candidate_metrics.get('rmse_change_pct'))}`",
            f"- RMSE 变化：`{_metric_delta_text(best_candidate_metrics.get('rmse_change_pct'), baseline_metrics.get('rmse_change_pct'))}`",
            f"- 本次样本数：`{((display_ranked_results[0].get('compare_summary') or {}).get('row_count', 0)) if display_ranked_results else 0}`",
            f"- 误差缩小样本：`{((display_ranked_results[0].get('compare_summary') or {}).get('error_smaller_count', 0)) if display_ranked_results else 0}`",
            f"- 误差增大样本：`{((display_ranked_results[0].get('compare_summary') or {}).get('error_larger_count', 0)) if display_ranked_results else 0}`",
            f"- 无变化样本：`{((display_ranked_results[0].get('compare_summary') or {}).get('error_unchanged_count', 0)) if display_ranked_results else 0}`",
            f"- 不可比较但前后相同样本：`{((display_ranked_results[0].get('compare_summary') or {}).get('noncomparable_unchanged_count', 0)) if display_ranked_results else 0}`",
            "",
            *detail_sections,
        ]
    )
    md_path.write_text(markdown, encoding="utf-8")
    return json_path, md_path
