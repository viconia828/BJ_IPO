from __future__ import annotations

import itertools
import json
import math
from collections import OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import bse_ipo_valuation
import data_fetcher
import pdf_parser
import tushare_helper
import valuation_engine
from industry_mapping import IndustryMapper
from local_file_db import LocalFileDB


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_PATH = REPO_ROOT / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_CANDIDATE_SET_DIR = REPO_ROOT / "data" / "offline_tuning" / "candidate_sets"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "输出" / "调参"
INTRADAY_DIR = REPO_ROOT / "首日分时走势"
PDF_DIR = REPO_ROOT / "公告文件"
DATASET_SCHEMA = "offline_tuning_replay_v1"
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


def _get_dataset_evaluation_scope(dataset: dict[str, Any]) -> str:
    scope = str(dataset.get("evaluation_scope") or "").strip().lower()
    if scope in SUPPORTED_EVALUATION_SCOPES:
        return scope
    return METHOD2_ONLY_SCOPE


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _rmse(errors: list[float]) -> float | None:
    if not errors:
        return None
    return math.sqrt(sum(error * error for error in errors) / len(errors))


def _fmt_metric(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


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


def discover_local_sample_codes() -> list[str]:
    codes = {
        path.stem[:6]
        for path in INTRADAY_DIR.glob("*.csv")
        if len(path.stem) >= 6 and path.stem[:6].isdigit()
    }
    return sorted(codes)


def list_stage_names() -> list[str]:
    return sorted(set(SEARCH_STAGE_GRIDS) | set(SEARCH_STAGE_CANDIDATES))


def _build_pdf_inputs(params: dict[str, Any], code: str) -> tuple[float, str, dict[str, Any] | None]:
    listing_pdf = bse_ipo_valuation._find_pdf(PDF_DIR, code, "上市公告书")
    prospectus_pdf = bse_ipo_valuation._pick_prospectus_pdf(PDF_DIR, code, "old_shares")
    return bse_ipo_valuation._resolve_old_shares(params, listing_pdf, prospectus_pdf)


def build_replay_dataset(
    params: dict[str, Any],
    months: int = 12,
    sample_codes: list[str] | None = None,
    page_size: int = 100,
) -> dict[str, Any]:
    mapper = IndustryMapper(params)
    requested_codes = sample_codes if sample_codes is not None else discover_local_sample_codes()
    requested_codes = sorted(_normalize_codes(requested_codes))
    raw_records = data_fetcher.fetch_recent_ipos(months=months, page_size=page_size)
    enriched_records = mapper.enrich_recent_ipos(list(raw_records))
    comparable_snapshot_cache: dict[tuple[str, str], dict[str, Any] | None] = {}

    record_by_code = {
        str(item.get("SECURITY_CODE", "")).strip(): item
        for item in enriched_records
        if str(item.get("SECURITY_CODE", "")).strip()
    }

    items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    method1_ready_count = 0
    for code in requested_codes:
        record = record_by_code.get(code)
        if record is None:
            skipped.append({"code": code, "reason": f"最近 {months} 个月历史池中未找到该样本"})
            continue

        old_shares, old_shares_desc, old_shares_meta = _build_pdf_inputs(params, code)
        total_issue_num = _safe_float(record.get("TOTAL_ISSUE_NUM"))
        if total_issue_num is None:
            total_issue_num = _safe_float(record.get("ISSUE_NUM"))

        industry = mapper.resolve_stock_industry(code, record)
        comparable_pdf = bse_ipo_valuation._pick_prospectus_pdf(PDF_DIR, code, "comparables")
        comparable_codes = pdf_parser.extract_comparable_companies(comparable_pdf) if comparable_pdf else []
        comparable_data, comparable_summary = _fetch_historical_comparable_data(
            comparable_codes,
            record.get("LISTING_DATE"),
            params,
            comparable_snapshot_cache,
        )
        method1_replay = valuation_engine.method1_comparable(
            issue_price=_safe_float(record.get("ISSUE_PRICE")),
            issue_pe=_safe_float(record.get("AFTER_ISSUE_PE")),
            comparable_data=comparable_data,
            params=params,
        )
        if method1_replay.get("available"):
            method1_ready_count += 1
        items.append(
            {
                "SECURITY_CODE": code,
                "SECURITY_NAME_ABBR": str(record.get("SECURITY_NAME_ABBR") or "").strip(),
                "APPLY_DATE": str(record.get("APPLY_DATE") or "").strip(),
                "LISTING_DATE": str(record.get("LISTING_DATE") or "").strip(),
                "ISSUE_PRICE": _safe_float(record.get("ISSUE_PRICE")),
                "AFTER_ISSUE_PE": _safe_float(record.get("AFTER_ISSUE_PE")),
                "INDUSTRY_PE_NEW": _safe_float(record.get("INDUSTRY_PE_NEW")),
                "TOTAL_ISSUE_NUM": total_issue_num,
                "CLOSE_PRICE": _safe_float(record.get("CLOSE_PRICE")),
                "LD_CLOSE_CHANGE": _safe_float(record.get("LD_CLOSE_CHANGE")),
                "TURNOVERRATE": _safe_float(record.get("TURNOVERRATE")),
                "SW_INDUSTRY": str(record.get("SW_INDUSTRY") or "").strip(),
                "INDUSTRY": str(record.get("INDUSTRY") or "").strip(),
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
            }
        )

    items.sort(key=lambda item: _parse_date_key(item.get("LISTING_DATE")))
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
        "sample_codes": [item["SECURITY_CODE"] for item in items],
        "requested_codes": requested_codes,
        "available_count": len(items),
        "method1_ready_count": method1_ready_count,
        "method1_ready_rate": (method1_ready_count / len(items)) if items else 0.0,
        "skipped": skipped,
        "caveats": caveats,
        "items": items,
    }


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
            "CLOSE_PRICE": item.get("CLOSE_PRICE"),
            "LD_CLOSE_CHANGE": item.get("LD_CLOSE_CHANGE"),
            "TURNOVERRATE": item.get("TURNOVERRATE"),
            "industry_primary": item.get("industry_primary"),
            "industry_secondary": item.get("industry_secondary"),
        }
        for item in items
    ]


def _calc_change_pct(issue_price: float | None, target_price: float | None) -> float | None:
    if not issue_price or not target_price:
        return None
    return (target_price / issue_price - 1) * 100


def _evaluate_replay_prediction(
    item: dict[str, Any],
    params: dict[str, Any],
    recent_pool: list[dict[str, Any]],
    evaluation_scope: str,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
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

    if evaluation_scope == METHOD2_ONLY_SCOPE:
        return method2, None, method2

    comparable_data = list(item.get("comparable_data") or [])
    method1 = valuation_engine.method1_comparable(
        issue_price=issue_price,
        issue_pe=_safe_float(item.get("AFTER_ISSUE_PE")),
        comparable_data=comparable_data,
        params=params,
    )
    final = valuation_engine.composite_valuation(method1, method2, params)
    return final, method1, method2


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
            "actual_change_pct": _safe_float(item.get("LD_CLOSE_CHANGE")),
        }
        valuation_result, method1, method2 = _evaluate_replay_prediction(item, params, recent_pool, evaluation_scope)
        if not valuation_result.get("available"):
            unavailable_results.append({**base_result, "reason": str(valuation_result.get("reason") or "")})
            continue

        predicted_price = _safe_float(valuation_result.get("target_price"))
        predicted_change = _calc_change_pct(_safe_float(item.get("ISSUE_PRICE")), predicted_price)
        actual_price = _safe_float(item.get("CLOSE_PRICE"))
        actual_change = _safe_float(item.get("LD_CLOSE_CHANGE"))

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
                "method2_available": bool(method2 and method2.get("available")),
                "method2_target_price": _safe_float((method2 or {}).get("target_price")),
                "method2_change_pct": _safe_float((method2 or {}).get("change_pct")),
                "sample_scope": (method2 or {}).get("sample_scope"),
                "sample_count": (method2 or {}).get("sample_count"),
                "sample_codes": list((method2 or {}).get("sample_codes") or []),
                "adj_factor": (method2 or {}).get("adj_factor"),
                "trend_factor": (method2 or {}).get("trend_factor"),
                "float_factor": (method2 or {}).get("float_factor"),
                "pe_factor": (method2 or {}).get("pe_factor"),
                "weight_comparable": _safe_float(valuation_result.get("weight_comparable")),
                "weight_industry_momentum": _safe_float(valuation_result.get("weight_industry_momentum")),
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
            "## 与当前参数对比",
            "",
            f"- 当前参数 MAE(涨幅)：`{_fmt_metric(baseline_metrics.get('mae_change_pct'))}`",
            f"- 推荐参数 MAE(涨幅)：`{_fmt_metric(best_metrics.get('mae_change_pct'))}`",
            f"- MAE 变化：`{_metric_delta_text(best_metrics.get('mae_change_pct'), baseline_metrics.get('mae_change_pct'))}`",
            f"- 当前参数 RMSE(涨幅)：`{_fmt_metric(baseline_metrics.get('rmse_change_pct'))}`",
            f"- 推荐参数 RMSE(涨幅)：`{_fmt_metric(best_metrics.get('rmse_change_pct'))}`",
            f"- RMSE 变化：`{_metric_delta_text(best_metrics.get('rmse_change_pct'), baseline_metrics.get('rmse_change_pct'))}`",
            "",
            "## 推荐参数表现",
            "",
            f"- 区间命中率：`{_fmt_metric(best_metrics.get('interval_hit_rate'))}`",
            f"- 方向命中率：`{_fmt_metric(best_metrics.get('direction_hit_rate'))}`",
            f"- 可用率：`{_fmt_metric(best_metrics.get('available_rate'))}`",
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
            "## 与 baseline 对比",
            "",
            f"- baseline 验证集 MAE(涨幅)：`{_fmt_metric(baseline_metrics.get('mae_change_pct'))}`",
            f"- 候选 验证集 MAE(涨幅)：`{_fmt_metric(best_candidate_metrics.get('mae_change_pct'))}`",
            f"- MAE 变化：`{_metric_delta_text(best_candidate_metrics.get('mae_change_pct'), baseline_metrics.get('mae_change_pct'))}`",
            f"- baseline 验证集 RMSE(涨幅)：`{_fmt_metric(baseline_metrics.get('rmse_change_pct'))}`",
            f"- 候选 验证集 RMSE(涨幅)：`{_fmt_metric(best_candidate_metrics.get('rmse_change_pct'))}`",
            f"- RMSE 变化：`{_metric_delta_text(best_candidate_metrics.get('rmse_change_pct'), baseline_metrics.get('rmse_change_pct'))}`",
            "",
            "## 候选集排序",
            "",
            *ranked_lines,
            "",
        ]
    )
    md_path.write_text(markdown, encoding="utf-8")
    return json_path, md_path
