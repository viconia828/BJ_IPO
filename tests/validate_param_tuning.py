from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import date
from types import SimpleNamespace
from pathlib import Path
import sys
from typing import Any


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import param_tuning
import listing_average_price_helper
import subscription_ladder_labels
import valuation_engine

import importlib.util

TUNE_PARAMS_SPEC = importlib.util.spec_from_file_location("tune_params_cli", ROOT_DIR / "tools" / "tune_params.py")
assert TUNE_PARAMS_SPEC and TUNE_PARAMS_SPEC.loader
tune_params_cli = importlib.util.module_from_spec(TUNE_PARAMS_SPEC)
TUNE_PARAMS_SPEC.loader.exec_module(tune_params_cli)


TEMP_ROOT = ROOT_DIR / "data" / "temp_validation" / "param_tuning_validation"


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _assert_close(actual: Any, expected: float, message: str, failures: list[str], tolerance: float = 1e-6) -> None:
    value = float(actual)
    if abs(value - expected) > tolerance:
        failures.append(f"{message}: expected {expected}, got {value}")


def _reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _write_temp_params_file(path: Path, overrides: dict[str, Any]) -> None:
    content = (ROOT_DIR / "策略参数.txt").read_text(encoding="utf-8-sig")
    for key, value in overrides.items():
        rendered = str(value)
        pattern = rf"(?m)^({re.escape(key)}\s*=\s*).*$"
        if re.search(pattern, content):
            content = re.sub(pattern, rf"\g<1>{rendered}", content)
        else:
            content += f"\n{key} = {rendered}\n"
    path.write_text(content, encoding="utf-8-sig")


def _base_params(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "bse_discount_factor": 1.0,
        "min_industry_samples": 2,
        "float_size_threshold": 2000,
        "small_cap_premium": 0.0,
        "pe_low_threshold": 0.30,
        "pe_discount_boost": 0.10,
        "pe_high_threshold": 0.60,
        "pe_premium_drag": -0.10,
        "trend_strong_boost": 0.05,
        "trend_weak_discount": -0.05,
        "trend_score_stocks": 5,
        "trend_strong_threshold": 70,
        "trend_weak_threshold": 40,
        "industry_trend_weight": 0.60,
        "market_sentiment_weight": 0.40,
        "sample_weight_mode": "static",
        "sample_decay_half_life_days": 20,
        "recent_days": 365,
        "robust_median_min_samples": 4,
        "robust_mad_multiplier": 3.0,
        "sentiment_decay_half_life_days": 20,
        "sentiment_first_day_baseline_pct": 100.0,
        "sentiment_first_day_scale": 0.0,
        "sentiment_post_listing_scale": 0.0,
        "sentiment_premium_cap_pct": 35.0,
        "sentiment_premium_floor_pct": -20.0,
        "price_range_width": 0.10,
        "weight_comparable": 0.50,
        "weight_industry_momentum": 0.50,
        "method2_sample_confidence_enabled": False,
        "wsi_weight_close_vwap": 0.30,
        "wsi_weight_price_retention": 0.25,
        "wsi_weight_high_timing": 0.20,
        "wsi_weight_closing_momentum": 0.15,
        "wsi_weight_volume_rhythm": 0.10,
        "wsi_weight_turnover": 0.0,
    }
    params.update(overrides)
    return params


def _make_item(code: str, listing_date: str, change_pct: float, float_shares: float) -> dict[str, Any]:
    return _make_item_with_comparables(code, listing_date, change_pct, float_shares, comparable_pes=None)


def _make_item_with_comparables(
    code: str,
    listing_date: str,
    change_pct: float,
    float_shares: float,
    comparable_pes: list[float] | None,
    *,
    post_listing_profit_effect_pct: float | None = 0.0,
) -> dict[str, Any]:
    issue_price = 10.0
    close_price = issue_price * (1 + change_pct / 100)
    post_effect = post_listing_profit_effect_pct
    next_day_close = None
    third_day_close = None
    next_day_change = None
    third_day_change = None
    if post_effect is not None:
        next_day_close = close_price * (1 + post_effect / 100)
        third_day_close = close_price * (1 + post_effect / 100)
        next_day_change = (next_day_close / issue_price - 1) * 100
        third_day_change = (third_day_close / issue_price - 1) * 100
    item = {
        "SECURITY_CODE": code,
        "SECURITY_NAME_ABBR": f"样本{code}",
        "APPLY_DATE": listing_date,
        "LISTING_DATE": listing_date,
        "ISSUE_PRICE": issue_price,
        "AFTER_ISSUE_PE": 10.0,
        "INDUSTRY_PE_NEW": 20.0,
        "TOTAL_ISSUE_NUM": float_shares,
        "CLOSE_PRICE": round(close_price, 4),
        "AVERAGE_PRICE": round(close_price, 4),
        "LD_CLOSE_CHANGE": change_pct,
        "LD_AVERAGE_CHANGE": change_pct,
        "NEXT_DAY_CLOSE": round(next_day_close, 4) if next_day_close is not None else None,
        "THIRD_DAY_CLOSE": round(third_day_close, 4) if third_day_close is not None else None,
        "NEXT_DAY_CLOSE_CHANGE": next_day_change,
        "THIRD_DAY_CLOSE_CHANGE": third_day_change,
        "NEXT_DAY_FROM_LISTING_CLOSE_PCT": post_effect,
        "THIRD_DAY_FROM_LISTING_CLOSE_PCT": post_effect,
        "POST_LISTING_PROFIT_EFFECT_PCT": post_effect,
        "post_listing_performance_source": "fixture",
        "post_listing_performance_reason": "",
        "average_price_source": "fixture",
        "average_price_reason": "",
        "TURNOVERRATE": 75.0,
        "SW_INDUSTRY": "电子",
        "INDUSTRY": "半导体",
        "industry_primary": "信息技术",
        "industry_secondary": "电子",
        "industry_source": "fixture",
        "old_shares": 0.0,
        "old_shares_desc": "0 万股（fixture）",
        "old_shares_meta": {},
        "float_shares": float_shares,
        "has_intraday_file": False,
    }
    if comparable_pes:
        item["comparable_codes"] = [f"CMP{index:03d}.SZ" for index, _ in enumerate(comparable_pes, start=1)]
        item["comparable_data"] = [
            {
                "code": f"CMP{index:03d}.SZ",
                "name": f"可比{index}",
                "pe_ttm": pe_value,
                "trade_date": "20260131",
                "source": "fixture_historical",
            }
            for index, pe_value in enumerate(comparable_pes, start=1)
        ]
        item["comparable_summary"] = {
            "provider": "fixture_historical",
            "requested_codes": list(item["comparable_codes"]),
            "returned_codes": list(item["comparable_codes"]),
            "reference_trade_date": "20260131",
            "reason": "",
        }
        item["method1_replay_available"] = True
    else:
        item["comparable_codes"] = []
        item["comparable_data"] = []
        item["comparable_summary"] = {
            "provider": "fixture_historical",
            "requested_codes": [],
            "returned_codes": [],
            "reference_trade_date": "20260131",
            "reason": "fixture 未提供方法一历史快照。",
        }
        item["method1_replay_available"] = False
    return item


def _make_method2_dataset() -> dict[str, Any]:
    items = [
        _make_item("000001", "2026-01-01", 40.0, 3000.0),
        _make_item("000002", "2026-01-10", 50.0, 3000.0),
        _make_item("000003", "2026-01-20", 60.0, 3000.0),
        _make_item("000004", "2026-02-01", 55.0, 1000.0),
        _make_item("000005", "2026-02-10", 57.75, 1000.0),
        _make_item("000006", "2026-02-20", 60.5, 1000.0),
    ]
    return {
        "schema": param_tuning.DATASET_SCHEMA,
        "replay_item_cache_version": param_tuning.REPLAY_ITEM_CACHE_VERSION,
        "replay_refresh_contract": param_tuning._build_replay_refresh_contract(),
        "evaluation_scope": param_tuning.METHOD2_ONLY_SCOPE,
        "generated_at": "2026-04-18 18:00:00",
        "source_months": 12,
        "sample_codes": [item["SECURITY_CODE"] for item in items],
        "requested_codes": [item["SECURITY_CODE"] for item in items],
        "available_count": len(items),
        "method1_ready_count": 0,
        "method1_ready_rate": 0.0,
        "skipped": [],
        "caveats": [
            "当前回放调参先聚焦方法二；方法一历史可比快照尚未按历史时点回放。",
        ],
        "items": items,
    }


def _make_composite_dataset() -> dict[str, Any]:
    items = [
        _make_item_with_comparables("100001", "2026-01-01", 100.0, 3000.0, [40.0, 40.0, 40.0]),
        _make_item_with_comparables("100002", "2026-01-10", 100.0, 3000.0, [40.0, 40.0, 40.0]),
        _make_item_with_comparables("100003", "2026-01-20", 100.0, 3000.0, [40.0, 40.0, 40.0]),
        _make_item_with_comparables("100004", "2026-02-01", 260.0, 3000.0, [40.0, 40.0, 40.0]),
        _make_item_with_comparables("100005", "2026-02-01", 260.0, 3000.0, [40.0, 40.0, 40.0]),
        _make_item_with_comparables("100006", "2026-02-01", 260.0, 3000.0, [40.0, 40.0, 40.0]),
    ]
    return {
        "schema": param_tuning.DATASET_SCHEMA,
        "replay_item_cache_version": param_tuning.REPLAY_ITEM_CACHE_VERSION,
        "evaluation_scope": param_tuning.COMPOSITE_EVALUATION_SCOPE,
        "generated_at": "2026-04-20 10:00:00",
        "source_months": 12,
        "sample_codes": [item["SECURITY_CODE"] for item in items],
        "requested_codes": [item["SECURITY_CODE"] for item in items],
        "available_count": len(items),
        "method1_ready_count": len(items),
        "method1_ready_rate": 1.0,
        "skipped": [],
        "caveats": [
            "方法一历史回放快照使用 fixture，可用于校验综合权重调参。",
        ],
        "items": items,
    }


def _make_sentiment_dataset() -> dict[str, Any]:
    items = [
        _make_item("200001", "2026-01-01", 100.0, 3000.0),
        _make_item("200002", "2026-01-10", 100.0, 3000.0),
        _make_item("200003", "2026-01-20", 100.0, 3000.0),
        _make_item("200004", "2026-02-01", 115.0, 3000.0),
        _make_item("200005", "2026-02-10", 115.0, 3000.0),
        _make_item("200006", "2026-02-20", 115.0, 3000.0),
    ]
    for item in items:
        item["POST_LISTING_PROFIT_EFFECT_PCT"] = 100.0
        item["NEXT_DAY_FROM_LISTING_CLOSE_PCT"] = 100.0
        item["THIRD_DAY_FROM_LISTING_CLOSE_PCT"] = 100.0
    return {
        "schema": param_tuning.DATASET_SCHEMA,
        "replay_item_cache_version": param_tuning.REPLAY_ITEM_CACHE_VERSION,
        "evaluation_scope": param_tuning.COMPOSITE_EVALUATION_SCOPE,
        "generated_at": "2026-04-20 10:00:00",
        "source_months": 12,
        "sample_codes": [item["SECURITY_CODE"] for item in items],
        "requested_codes": [item["SECURITY_CODE"] for item in items],
        "available_count": len(items),
        "method1_ready_count": 0,
        "method1_ready_rate": 0.0,
        "skipped": [],
        "caveats": [
            "fixture 用于校验方法三情绪溢价调参。",
        ],
        "items": items,
    }

def time_split_case(failures: list[str]) -> None:
    dataset = _make_method2_dataset()
    train_codes, validation_codes = param_tuning.split_target_codes(dataset, train_ratio=0.5, min_train_samples=3)
    _assert(train_codes == ["000001", "000002", "000003"], "时间切分训练集顺序不正确", failures)
    _assert(validation_codes == ["000004", "000005", "000006"], "时间切分验证集顺序不正确", failures)


def replay_sample_source_includes_intraday_and_ladder_labels_case(failures: list[str]) -> None:
    temp_dir = TEMP_ROOT / "sample_sources"
    _reset_dir(temp_dir)
    intraday_dir = temp_dir / "intraday"
    intraday_dir.mkdir(parents=True, exist_ok=True)
    (intraday_dir / "920001.csv").write_text("time,price,volume,amount\n", encoding="utf-8")
    label_path = temp_dir / "subscription_ladder_labels.csv"
    subscription_ladder_labels.write_label_rows(
        [
            {
                "security_code": "920002",
                "security_name_abbr": "Label Source",
                "manual_ladder": "1+0=300",
            },
            {
                "security_code": "920003",
                "security_name_abbr": "Auto Context Only",
                "manual_ladder": "",
            },
        ],
        label_path,
    )
    codes = param_tuning.discover_replay_sample_codes(
        intraday_dir=intraday_dir,
        ladder_label_path=label_path,
    )
    _assert(codes == ["920001", "920002"], f"replay sample source should merge intraday and manual labels, got {codes}", failures)


def replay_dataset_sync_inspection_case(failures: list[str]) -> None:
    dataset = _make_method2_dataset()
    same_codes = ["000001", "000002", "000003", "000004", "000005", "000006"]
    synced_status = param_tuning.inspect_replay_dataset_sync(dataset, local_sample_codes=same_codes, months=12)
    _assert(not synced_status["needs_refresh"], "本地 CSV 与数据集一致时不应要求刷新", failures)

    expanded_status = param_tuning.inspect_replay_dataset_sync(
        dataset,
        local_sample_codes=[*same_codes, "000007"],
        months=12,
    )
    _assert(expanded_status["needs_refresh"], "本地 CSV 新增样本时应要求刷新", failures)
    _assert(expanded_status["missing_in_dataset"] == ["000007"], "新增 CSV 样本识别不正确", failures)

    removed_status = param_tuning.inspect_replay_dataset_sync(
        dataset,
        local_sample_codes=["000001", "000002", "000003", "000004", "000005"],
        months=12,
    )
    _assert(removed_status["extra_in_dataset"] == ["000006"], "本地删除 CSV 后应识别数据集多余样本", failures)

    months_status = param_tuning.inspect_replay_dataset_sync(dataset, local_sample_codes=same_codes, months=18)
    _assert(months_status["needs_refresh"], "回放月份参数变化时应要求刷新", failures)


    stale_contract_dataset = dict(dataset)
    stale_contract_dataset["replay_refresh_contract"] = {
        **param_tuning._build_replay_refresh_contract(),
        "record_signature_version": 1,
    }
    contract_status = param_tuning.inspect_replay_dataset_sync(
        stale_contract_dataset,
        local_sample_codes=same_codes,
        months=12,
    )
    _assert(
        contract_status["needs_refresh"],
        "replay refresh contract changes must trigger an incremental refresh",
        failures,
    )


def manual_dataset_auto_refresh_gate_case(failures: list[str]) -> None:
    default_dataset_path = Path(param_tuning.DEFAULT_DATASET_PATH)
    custom_dataset_path = TEMP_ROOT / "custom_replay_dataset.json"
    original_env_value = os.environ.pop("BSE_TUNING_NO_AUTO_REFRESH", None)

    try:
        args = SimpleNamespace(mode="offline", no_auto_refresh_dataset=False)
        _assert(
            tune_params_cli._should_auto_refresh_dataset(args, default_dataset_path, None),
            "手动模式默认数据集应启用自动同步",
            failures,
        )
        _assert(
            not tune_params_cli._should_auto_refresh_dataset(args, custom_dataset_path, None),
            "自定义数据集且未显式 sample-codes 时不应自动同步",
            failures,
        )
        _assert(
            tune_params_cli._should_auto_refresh_dataset(args, custom_dataset_path, ["000001"]),
            "显式 sample-codes 时应允许自定义数据集自动同步",
            failures,
        )

        search_args = SimpleNamespace(mode="search", no_auto_refresh_dataset=False)
        _assert(
            not tune_params_cli._should_auto_refresh_dataset(search_args, default_dataset_path, None),
            "search 模式不应默认启用手动同步逻辑",
            failures,
        )

        disabled_args = SimpleNamespace(mode="offline", no_auto_refresh_dataset=True)
        _assert(
            not tune_params_cli._should_auto_refresh_dataset(disabled_args, default_dataset_path, None),
            "--no-auto-refresh-dataset 应关闭自动同步",
            failures,
        )

        os.environ["BSE_TUNING_NO_AUTO_REFRESH"] = "1"
        _assert(
            not tune_params_cli._should_auto_refresh_dataset(args, default_dataset_path, None),
            "环境变量 BSE_TUNING_NO_AUTO_REFRESH 应关闭自动同步",
            failures,
        )
    finally:
        if original_env_value is None:
            os.environ.pop("BSE_TUNING_NO_AUTO_REFRESH", None)
        else:
            os.environ["BSE_TUNING_NO_AUTO_REFRESH"] = original_env_value


def manual_dataset_auto_refresh_failure_fallback_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    dataset = _make_method2_dataset()
    dataset_path = TEMP_ROOT / "stale_replay_dataset.json"
    param_tuning.save_replay_dataset(dataset, dataset_path)

    args = SimpleNamespace(
        rebuild_dataset=False,
        mode="offline",
        no_auto_refresh_dataset=False,
        months=12,
        page_size=100,
    )
    original_build = tune_params_cli.offline_tuning_sync.build_and_save_replay_dataset

    def _raise_build_error(*args: Any, **kwargs: Any) -> dict[str, Any]:
        _ = (args, kwargs)
        raise RuntimeError("fixture build failed")

    tune_params_cli.offline_tuning_sync.build_and_save_replay_dataset = _raise_build_error
    try:
        loaded = tune_params_cli._load_or_refresh_dataset(
            args,
            _base_params(),
            dataset_path,
            ["000001", "000002", "000003", "000004", "000005", "000006", "000007"],
        )
    finally:
        tune_params_cli.offline_tuning_sync.build_and_save_replay_dataset = original_build

    _assert(
        loaded.get("sample_codes") == dataset.get("sample_codes"),
        "自动刷新失败时应回退使用旧回放数据集",
        failures,
    )
    persisted = param_tuning.load_replay_dataset(dataset_path)
    _assert(
        persisted.get("sample_codes") == dataset.get("sample_codes"),
        "自动刷新失败时不应破坏旧回放数据集文件",
        failures,
    )


def replay_item_cache_incremental_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    cache_dir = TEMP_ROOT / "replay_items"
    existing_dataset = _make_method2_dataset()
    existing_dataset["items"] = [existing_dataset["items"][0]]
    for key in ("AVERAGE_PRICE", "LD_AVERAGE_CHANGE", "average_price_source", "average_price_reason"):
        existing_dataset["items"][0].pop(key, None)
    existing_dataset["sample_codes"] = ["000001"]
    existing_dataset["requested_codes"] = ["000001"]
    existing_dataset["available_count"] = 1

    def _record(code: str, listing_date: str, change_pct: float) -> dict[str, Any]:
        issue_price = 10.0
        return {
            "SECURITY_CODE": code,
            "SECURITY_NAME_ABBR": f"样本{code}",
            "APPLY_DATE": listing_date,
            "LISTING_DATE": listing_date,
            "ISSUE_PRICE": issue_price,
            "AFTER_ISSUE_PE": 10.0,
            "INDUSTRY_PE_NEW": 20.0,
            "TOTAL_ISSUE_NUM": 3000.0,
            "CLOSE_PRICE": issue_price * (1 + change_pct / 100),
            "LD_CLOSE_CHANGE": change_pct,
            "TURNOVERRATE": 75.0,
            "SW_INDUSTRY": "电子",
            "INDUSTRY": "半导体",
        }

    records = [
        _record("000001", "2026-01-01", 40.0),
        _record("000002", "2026-01-10", 50.0),
    ]
    build_calls: list[str] = []

    class FakeIndustryMapper:
        def __init__(self, params: dict[str, Any]) -> None:
            self.params = params

        def enrich_recent_ipos(self, raw_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                {
                    **record,
                    "industry_primary": "信息技术",
                    "industry_secondary": "电子",
                    "industry_source": "fixture",
                }
                for record in raw_records
            ]

        def resolve_stock_industry(self, code: str, record: dict[str, Any]) -> SimpleNamespace:
            _ = (code, record)
            return SimpleNamespace(primary="信息技术", secondary="电子", source="fixture")

    original_fetch = param_tuning.data_fetcher.fetch_recent_ipos
    original_mapper = param_tuning.IndustryMapper
    original_pdf_paths = param_tuning._resolve_replay_pdf_paths
    original_pdf_inputs = param_tuning._build_pdf_inputs_from_paths
    original_resolve_average = param_tuning._resolve_listing_average_price
    try:
        param_tuning.data_fetcher.fetch_recent_ipos = lambda months=12, page_size=100: list(records)
        param_tuning.IndustryMapper = FakeIndustryMapper
        param_tuning._resolve_replay_pdf_paths = lambda code: {"listing": None, "old_shares": None, "comparables": None}
        param_tuning._resolve_listing_average_price = lambda *args, **kwargs: {
            "average_price": 11.0,
            "source": "fixture_average",
            "reason": "",
        }
        invalid_cached_item = dict(existing_dataset["items"][0])
        invalid_record_signature = param_tuning._build_replay_record_signature(records[0])
        invalid_pdf_signature = param_tuning._build_replay_pdf_signature(
            {"listing": None, "old_shares": None, "comparables": None}
        )
        param_tuning.save_replay_item_cache(
            invalid_cached_item,
            invalid_record_signature,
            invalid_pdf_signature,
            cache_dir,
        )

        def _fake_pdf_inputs(params: dict[str, Any], pdf_paths: dict[str, Any]) -> tuple[float, str, dict[str, Any]]:
            _ = (params, pdf_paths)
            build_calls.append("built")
            return 0.0, "0 万股（fixture）", {}

        param_tuning._build_pdf_inputs_from_paths = _fake_pdf_inputs

        first_dataset = param_tuning.build_replay_dataset(
            _base_params(),
            months=12,
            sample_codes=["000001", "000002"],
            item_cache_dir=cache_dir,
            existing_dataset=existing_dataset,
        )
        first_cache = first_dataset.get("item_cache") or {}
        _assert(first_dataset.get("sample_codes") == ["000001", "000002"], "增量同步后样本代码不正确", failures)
        _assert(len(build_calls) == 1, "已有样本应轻量补齐均价后复用旧聚合数据集，只新建新增样本", failures)
        _assert(first_cache.get("existing_dataset_reused") == 1, "应记录复用旧数据集条目数", failures)
        _assert(first_cache.get("misses") == 1, "应只为新增样本产生一次缓存 miss", failures)
        _assert(first_cache.get("writes") == 2, "复用旧条目和新建条目都应写入单样本缓存", failures)
        _assert_close(
            (first_dataset.get("items") or [{}])[0].get("AVERAGE_PRICE"),
            11.0,
            "旧回放条目应只补齐首日成交均价",
            failures,
        )
        _assert(
            (first_dataset.get("items") or [{}])[0].get("average_price_source") == "fixture_average",
            "旧回放条目应记录补齐均价来源",
            failures,
        )

        build_calls.clear()
        second_dataset = param_tuning.build_replay_dataset(
            _base_params(),
            months=12,
            sample_codes=["000001", "000002"],
            item_cache_dir=cache_dir,
        )
        second_cache = second_dataset.get("item_cache") or {}
        _assert(len(build_calls) == 0, "单样本缓存齐全时不应重新构建回放条目", failures)
        _assert(second_cache.get("hits") == 2, "第二次构建应命中两个单样本缓存", failures)
    finally:
        param_tuning.data_fetcher.fetch_recent_ipos = original_fetch
        param_tuning.IndustryMapper = original_mapper
        param_tuning._resolve_replay_pdf_paths = original_pdf_paths
        param_tuning._build_pdf_inputs_from_paths = original_pdf_inputs
        param_tuning._resolve_listing_average_price = original_resolve_average


def replay_pdf_signature_tracks_parser_versions_case(failures: list[str]) -> None:
    cache_dir = TEMP_ROOT / "parser_version_signature_replay_items"
    _reset_dir(cache_dir)
    signature = param_tuning._build_replay_pdf_signature(
        {"listing": None, "old_shares": None, "comparables": None}
    )
    parser_versions = signature.get("pdf_parser_versions") or {}
    _assert(
        parser_versions.get("comparable_companies")
        == param_tuning.pdf_parser.PARSE_CACHE_KIND_VERSIONS.get("comparable_companies"),
        "replay PDF 签名必须跟踪可比公司解析器版本",
        failures,
    )
    _assert(
        set((signature.get("files") or {}).keys()) == {"listing", "old_shares", "comparables"},
        "replay PDF 签名必须保留三类源文件签名",
        failures,
    )
    mapped_record_signature = param_tuning._build_replay_record_signature(
        {
            "SECURITY_CODE": "920220",
            "industry_primary": "高端装备",
            "industry_secondary": "汽车零部件",
            "industry_source": "builtin_code_mapping",
        }
    )
    _assert(
        mapped_record_signature.get("industry_secondary") == "汽车零部件",
        "replay record signature should track the derived industry mapping",
        failures,
    )
    record_signature = {"fixture": "parser-version"}
    item = {
        "SECURITY_CODE": "000008",
        "AVERAGE_PRICE": 10.0,
        "average_price_source": "fixture",
        "average_price_calc_version": param_tuning.REPLAY_AVERAGE_PRICE_CALC_VERSION,
    }
    param_tuning.save_replay_item_cache(
        item,
        record_signature,
        signature,
        cache_dir,
    )
    _assert(
        param_tuning.load_replay_item_cache(
            "000008",
            record_signature,
            signature,
            cache_dir,
        )
        is not None,
        "解析器版本未变化时 replay 单样本缓存应可复用",
        failures,
    )
    bumped_signature = json.loads(json.dumps(signature))
    bumped_signature["pdf_parser_versions"]["comparable_companies"] += 1
    _assert(
        param_tuning.load_replay_item_cache(
            "000008",
            record_signature,
            bumped_signature,
            cache_dir,
        )
        is None,
        "可比公司解析器版本变化时 replay 单样本缓存必须失效",
        failures,
    )


def replay_item_announcement_fallback_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    cache_dir = TEMP_ROOT / "announcement_fallback_replay_items"
    progress_statuses: list[str] = []

    class FakeIndustryMapper:
        def __init__(self, params: dict[str, Any]) -> None:
            self.params = params

        def enrich_recent_ipos(self, raw_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return list(raw_records)

        def resolve_stock_industry(self, code: str, record: dict[str, Any]) -> SimpleNamespace:
            _ = (code, record)
            return SimpleNamespace(primary="信息技术", secondary="电子", source="fixture")

    original_fetch = param_tuning.data_fetcher.fetch_recent_ipos
    original_mapper = param_tuning.IndustryMapper
    original_pdf_paths = param_tuning._resolve_replay_pdf_paths
    original_pdf_inputs = param_tuning._build_pdf_inputs_from_paths
    original_resolve_average = param_tuning._resolve_listing_average_price
    original_fallback_record = param_tuning._build_replay_record_from_announcements
    try:
        param_tuning.data_fetcher.fetch_recent_ipos = lambda months=12, page_size=100: []
        param_tuning.IndustryMapper = FakeIndustryMapper
        param_tuning._resolve_replay_pdf_paths = lambda code: {"listing": None, "old_shares": None, "comparables": None}
        param_tuning._build_pdf_inputs_from_paths = lambda params, pdf_paths: (0.0, "0 万股（fixture）", {})
        param_tuning._resolve_listing_average_price = lambda *args, **kwargs: {
            "average_price": None,
            "source": "fixture_missing_intraday",
            "reason": "fixture",
        }
        param_tuning._build_replay_record_from_announcements = lambda code: {
            "SECURITY_CODE": code,
            "SECURITY_NAME_ABBR": "公告样本",
            "APPLY_DATE": "2026-02-01",
            "LISTING_DATE": "2026-02-01",
            "LISTING_DATE_SOURCE": "apply_date_fallback",
            "ISSUE_PRICE": 8.14,
            "AFTER_ISSUE_PE": 12.3,
            "INDUSTRY_PE_NEW": 20.0,
            "TOTAL_ISSUE_NUM": 2000.0,
            "ISSUE_NUM": 2000.0,
            "ONLINE_ISSUE_NUM": 10000000.0,
            "TOP_APPLY_MARKETCAP": 500.0,
            "SUBSCRIPTION_LIMIT_WAN_SHARES": 60.0,
            "SW_INDUSTRY": "电子",
            "INDUSTRY": "半导体",
            "replay_record_source": "announcement_pdf_fallback",
            "announcement_fallback_field_sources": {"ISSUE_PRICE": "issue_announcement"},
            "announcement_fallback_parse_errors": [],
            "announcement_fallback_pdf_files": {"issue": "000009_样本_发行公告.pdf"},
        }

        dataset = param_tuning.build_replay_dataset(
            _base_params(),
            months=12,
            sample_codes=["000009"],
            item_cache_dir=cache_dir,
            progress_callback=lambda index, total, spec: progress_statuses.append(str(spec.get("status") or "")),
        )
    finally:
        param_tuning.data_fetcher.fetch_recent_ipos = original_fetch
        param_tuning.IndustryMapper = original_mapper
        param_tuning._resolve_replay_pdf_paths = original_pdf_paths
        param_tuning._build_pdf_inputs_from_paths = original_pdf_inputs
        param_tuning._resolve_listing_average_price = original_resolve_average
        param_tuning._build_replay_record_from_announcements = original_fallback_record

    items = dataset.get("items") or []
    item = items[0] if items else {}
    cache = dataset.get("item_cache") or {}
    _assert(dataset.get("sample_codes") == ["000009"], "公告兜底样本应进入 replay dataset", failures)
    _assert(dataset.get("skipped") == [], "公告兜底样本不应被 skipped", failures)
    _assert(item.get("replay_record_source") == "announcement_pdf_fallback", "公告兜底来源应写入 replay item", failures)
    _assert(item.get("ONLINE_ISSUE_NUM") == 10000000.0, "公告兜底样本应保留网上发行股数", failures)
    _assert(item.get("TOP_APPLY_MARKETCAP") == 500.0, "公告兜底样本应保留顶格申购金额", failures)
    _assert(item.get("listing_date_source") == "apply_date_fallback", "公告兜底样本应标记日期兜底来源", failures)
    _assert(item.get("AVERAGE_PRICE") is None, "申购样本无分时数据时不应伪造首日均价", failures)
    _assert(cache.get("misses") == 1 and cache.get("writes") == 1, "公告兜底样本应写入单样本缓存", failures)
    _assert(progress_statuses == ["announcement_fallback"], "公告兜底构建应暴露进度状态", failures)


def replay_metrics_case(failures: list[str]) -> None:
    dataset = _make_method2_dataset()
    base_metrics = param_tuning.evaluate_replay_targets(
        dataset,
        _base_params(method2_sample_confidence_enabled=False),
        target_codes=["000004", "000005", "000006"],
    )
    legacy_metrics = param_tuning.evaluate_replay_targets(
        dataset,
        _base_params(small_cap_premium=0.10),
        target_codes=["000004", "000005", "000006"],
    )
    _assert(base_metrics["available_count"] == 3, "baseline 回放可用样本数应为 3", failures)
    _assert(legacy_metrics["available_count"] == 3, "legacy 回放可用样本数应为 3", failures)
    _assert_close(base_metrics["mae_change_pct"], 5.25, "方法二 YTD 同二级行业 fixture MAE 应稳定", failures)
    _assert_close(
        legacy_metrics["mae_change_pct"],
        float(base_metrics["mae_change_pct"]),
        "方法二不应再受 small_cap_premium 影响",
        failures,
    )
    _assert_close(legacy_metrics["direction_hit_rate"], 1.0, "方法二方向命中率应为 1", failures)

def ranking_case(failures: list[str]) -> None:
    dataset = _make_sentiment_dataset()
    ranking = param_tuning.rank_param_candidates(
        dataset,
        _base_params(),
        candidates=[{"sentiment_post_listing_scale": 0.15}],
        train_ratio=0.5,
        min_train_samples=3,
        top_n=2,
    )
    best = ranking.get("best") or {}
    overrides = dict(best.get("overrides") or {})
    _assert(best.get("label") != "baseline", "ranking 应能推荐情绪溢价参数", failures)
    _assert_close(
        overrides.get("sentiment_post_listing_scale"),
        0.15,
        "ranking 推荐参数应命中 sentiment_post_listing_scale=0.15",
        failures,
    )
    validation_metrics = best.get("validation_metrics") or {}
    _assert_close(validation_metrics.get("mae_change_pct"), 0.0, "情绪溢价 ranking 验证集 MAE 应命中 fixture", failures)

def auto_score_case(failures: list[str]) -> None:
    reference_date = date(2026, 2, 20)
    same_hit_metrics = {
        "available_results": [
            {
                "listing_date": "2026-02-20",
                "actual_close_price": 10.0,
                "range_low": 9.0,
                "range_high": 11.0,
                "change_abs_error": 0.0,
            }
        ]
    }
    narrow_score = param_tuning._score_auto_metrics(
        same_hit_metrics,
        _base_params(price_range_width=0.10),
        reference_date,
    )
    wide_score = param_tuning._score_auto_metrics(
        same_hit_metrics,
        _base_params(price_range_width=0.50),
        reference_date,
    )
    _assert_close(narrow_score["auto_score"], wide_score["auto_score"], "固定手动区间宽度不应影响自动调参排序分", failures)
    _assert(
        float(wide_score["width_diagnostic_penalty"]) > float(narrow_score["width_diagnostic_penalty"]),
        "自动调参应单独展示更宽手动区间对应的诊断扣分",
        failures,
    )

    recency_metrics = {
        "available_results": [
            {
                "listing_date": "2025-11-01",
                "actual_close_price": 10.0,
                "range_low": 9.0,
                "range_high": 11.0,
                "change_abs_error": 0.0,
            },
            {
                "listing_date": "2026-02-20",
                "actual_close_price": 13.0,
                "range_low": 9.0,
                "range_high": 11.0,
                "change_abs_error": 30.0,
            },
        ]
    }
    recency_score = param_tuning._score_auto_metrics(
        recency_metrics,
        _base_params(price_range_width=0.10),
        reference_date,
    )
    _assert_close(
        recency_score.get("weighted_interval_hit_rate"),
        0.0,
        "3 个月前命中样本应衰减到忽略不计",
        failures,
    )

    recent_floor_metrics = {
        "available_results": [
            {
                "listing_date": "2026-03-31",
                "actual_close_price": 10.0,
                "range_low": 9.0,
                "range_high": 11.0,
                "change_abs_error": 0.0,
            },
            *[
                {
                    "listing_date": "2026-02-14",
                    "actual_close_price": 13.0,
                    "range_low": 9.0,
                    "range_high": 11.0,
                    "change_abs_error": 30.0,
                }
                for _ in range(6)
            ],
        ]
    }
    recent_floor_score = param_tuning._score_auto_metrics(
        recent_floor_metrics,
        _base_params(price_range_width=0.10),
        date(2026, 3, 31),
    )
    _assert_close(
        recent_floor_score.get("recent_weight_share"),
        0.5,
        "最近 30 天样本总权重不足时应抬到 50%",
        failures,
    )
    _assert(
        recent_floor_score.get("recent_floor_applied") is True,
        "最近 30 天样本权重下限应标记为已生效",
        failures,
    )
    _assert_close(
        recent_floor_score.get("weighted_interval_hit_rate"),
        0.5,
        "最近 30 天样本命中且被抬权后，加权命中率应体现 50% 权重",
        failures,
    )


def auto_tune_case(failures: list[str]) -> None:
    dataset = _make_sentiment_dataset()
    params = _base_params(price_range_width=0.10)
    result = param_tuning.auto_tune_params(
        dataset,
        params,
        top_n=3,
        max_passes=1,
        candidate_limit=120,
    )
    overrides = dict(result.get("changed_overrides") or {})
    baseline_score = (((result.get("baseline") or {}).get("auto_score") or {}).get("auto_score"))
    best_score = (((result.get("best") or {}).get("auto_score") or {}).get("auto_score"))
    _assert(overrides, "自动调参应给出优于 fixture baseline 的情绪参数修改", failures)
    _assert(float(best_score) > float(baseline_score), "自动调参组合分应优于 baseline", failures)
    _assert_close(
        overrides.get("sentiment_post_listing_scale"),
        0.15,
        "自动调参应能识别二三日赚钱效应溢价 scale",
        failures,
    )
    _assert("price_range_width" not in overrides, "自动调参不应建议修改 price_range_width", failures)
    contract = result.get("model_contract") or {}
    _assert(contract.get("version") == param_tuning.AUTO_TUNE_MODEL_CONTRACT_VERSION, "自动调参应记录模型契约版本", failures)
    _assert(contract.get("latest_model_compatible") is True, "自动调参应确认兼容最新估值模型", failures)
    _assert(not contract.get("missing_latest_model_keys"), "自动调参模型契约不应缺少最新参数", failures)


def auto_local_learning_rerank_case(failures: list[str]) -> None:
    dataset = _make_sentiment_dataset()
    params = _base_params(price_range_width=0.10)
    result = param_tuning.auto_tune_params(
        dataset,
        params,
        top_n=20,
        max_passes=1,
        candidate_limit=120,
    )
    reranked = tune_params_cli.local_learning_auto_rerank.rerank_auto_tune_result(
        dataset,
        params,
        result,
        pool_size=20,
    )
    local = reranked.get("local_learning_rerank") or {}
    selected = local.get("selected") or {}
    _assert(local.get("applied") is True, "自动调参应执行本地学习两级重排", failures)
    _assert(local.get("author_inputs_used") is False, "本地学习重排不得使用作者预测输入", failures)
    _assert(local.get("walk_forward_proxy") is True, "proxy 分层应按上市日 walk-forward", failures)
    _assert(int(local.get("target_code_count") or 0) > 0, "本地学习重排应包含近期目标样本", failures)
    _assert(bool(selected.get("conservative")), "本地学习重排应输出保守动态区间评分", failures)
    _assert(bool(selected.get("regime")), "本地学习重排应输出 regime-break 评分", failures)
    _assert(bool(selected.get("rolling")), "本地学习重排应输出滚动中枢评分", failures)
    _assert(
        dict(reranked.get("changed_overrides") or {}) == dict(local.get("selected_overrides") or {}),
        "自动调参最终 overrides 应来自本地学习重排胜者",
        failures,
    )
    walk_rows = [
        {"code": "A", "listing_date": "2026-01-01", "actual_change_pct": 10.0, "current_available": False},
        {"code": "B", "listing_date": "2026-01-02", "actual_change_pct": 20.0, "current_available": False},
        {"code": "C", "listing_date": "2026-01-02", "actual_change_pct": 30.0, "current_available": False},
        {"code": "D", "listing_date": "2026-01-03", "actual_change_pct": 40.0, "current_available": False},
    ]
    tune_params_cli.local_learning_auto_rerank._attach_walk_forward_proxy_features(walk_rows, params)
    by_code = {row["code"]: row for row in walk_rows}
    _assert(
        by_code["B"].get("rolling_proxy_history_count") == by_code["C"].get("rolling_proxy_history_count") == 1,
        "同日样本的滚动 proxy 不得读取彼此实际结果",
        failures,
    )
    _assert(by_code["D"].get("rolling_proxy_history_count") == 3, "下一交易日应读取此前已完成样本", failures)

def replay_recent_days_window_case(failures: list[str]) -> None:
    dataset = _make_method2_dataset()
    wide_metrics = param_tuning.evaluate_replay_targets(
        dataset,
        _base_params(recent_days=365),
        target_codes=["000006"],
    )
    narrow_metrics = param_tuning.evaluate_replay_targets(
        dataset,
        _base_params(recent_days=20),
        target_codes=["000006"],
    )
    wide_result = (wide_metrics.get("available_results") or [{}])[0]
    narrow_result = (narrow_metrics.get("available_results") or [{}])[0]
    _assert(
        wide_result.get("historical_sample_count") == 5,
        "方法二应纳入目标上市日前本年内全部历史样本",
        failures,
    )
    _assert(
        narrow_result.get("historical_sample_count") == 5,
        "方法二不应再受 recent_days 窄窗口影响",
        failures,
    )
    _assert(
        narrow_result.get("sample_codes") == ["000005", "000004", "000003", "000002", "000001"],
        "方法二样本代码应按目标上市日前本年同二级行业截取",
        failures,
    )
    _assert(wide_result.get("method3_sample_count") == 5, "方法三宽窗口应纳入全部近期情绪样本", failures)
    _assert(narrow_result.get("method3_sample_count") == 2, "方法三窄窗口应只纳入 recent_days 天内样本", failures)
    _assert(
        narrow_result.get("method3_sample_codes") == ["000005", "000004"],
        "方法三窄窗口样本代码应按 target 前 recent_days 截取",
        failures,
    )

def interval_hit_uses_average_price_case(failures: list[str]) -> None:
    item1 = _make_item("000001", "2026-01-01", 0.0, 3000.0)
    item2 = _make_item("000002", "2026-01-10", 30.0, 3000.0)
    item2["CLOSE_PRICE"] = 13.0
    item2["AVERAGE_PRICE"] = 10.0
    item2["LD_AVERAGE_CHANGE"] = 0.0
    item2["average_price_source"] = "fixture"
    dataset = {
        "schema": param_tuning.DATASET_SCHEMA,
        "evaluation_scope": param_tuning.METHOD2_ONLY_SCOPE,
        "generated_at": "2026-04-18 18:00:00",
        "source_months": 12,
        "sample_codes": ["000001", "000002"],
        "requested_codes": ["000001", "000002"],
        "available_count": 2,
        "method1_ready_count": 0,
        "method1_ready_rate": 0.0,
        "skipped": [],
        "caveats": [],
        "items": [item1, item2],
    }
    metrics = param_tuning.evaluate_replay_targets(
        dataset,
        _base_params(price_range_width=0.05, recent_days=365),
        target_codes=["000002"],
    )
    result = (metrics.get("available_results") or [{}])[0]
    _assert_close(
        metrics.get("interval_hit_rate"),
        1.0,
        "区间命中应按首日成交均价而不是收盘价判断",
        failures,
    )
    _assert_close(
        result.get("actual_interval_price"),
        10.0,
        "命中判定实际价格应使用首日成交均价",
        failures,
    )


def method2_uses_average_change_case(failures: list[str]) -> None:
    recent_ipos = [
        {
            "SECURITY_CODE": "000001",
            "SECURITY_NAME_ABBR": "样本000001",
            "LISTING_DATE": "2026-01-01",
            "ISSUE_PRICE": 10.0,
            "CLOSE_PRICE": 20.0,
            "AVERAGE_PRICE": 10.0,
            "LD_CLOSE_CHANGE": 100.0,
            "LD_AVERAGE_CHANGE": 0.0,
            "industry_primary": "电子",
            "industry_secondary": "半导体",
        }
    ]
    result = valuation_engine.method2_industry_momentum(
        issue_price=10.0,
        issue_pe=10.0,
        industry_pe=20.0,
        float_shares=3000.0,
        industry={"primary": "电子", "secondary": "半导体"},
        recent_ipos=recent_ipos,
        params=_base_params(min_industry_samples=1, recent_days=365),
        target_code="000002",
        target_listing_date="2026-02-01",
    )
    _assert(result.get("available"), "方法二应可基于样本生成结果", failures)
    _assert_close(
        result.get("target_price"),
        10.0,
        "正式估值方法二应使用首日成交均价涨幅而不是收盘涨幅",
        failures,
    )


def intraday_average_price_cache_case(failures: list[str]) -> None:
    temp_root = TEMP_ROOT / "listing_average_price_cache"
    _reset_dir(temp_root)
    intraday_dir = temp_root / "intraday"
    intraday_dir.mkdir(parents=True, exist_ok=True)
    cache_path = temp_root / "cache.json"
    (intraday_dir / "999998.csv").write_text(
        "DateTime,open,high,low,close,volume,amount\n"
        "2026-01-01 09:30,10,10,10,10,100,1000\n"
        "2026-01-01 09:31,12,12,12,12,200,2400\n",
        encoding="utf-8-sig",
    )
    (intraday_dir / "999997.csv").write_text(
        "DateTime,open,high,low,close,volume,amount,备注\n"
        "2026-01-01 09:30,10,10,10,10,100,1000,中文\n"
        "2026-01-01 09:31,12,12,12,12,200,2400,中文\n",
        encoding="gbk",
    )
    (intraday_dir / "999996.csv").write_text(
        "DateTime,open,high,low,close,volume,amount\n"
        "2026-01-01 09:30,10,10,10,10,1,1000\n"
        "2026-01-01 09:31,12,12,12,12,2,2400\n",
        encoding="utf-8-sig",
    )
    first = listing_average_price_helper.resolve_intraday_average_price(
        "999998",
        intraday_dir=intraday_dir,
        cache_path=cache_path,
    )
    second = listing_average_price_helper.resolve_intraday_average_price(
        "999998",
        intraday_dir=intraday_dir,
        cache_path=cache_path,
    )
    legacy = listing_average_price_helper.resolve_intraday_average_price(
        "999997",
        intraday_dir=intraday_dir,
        cache_path=cache_path,
    )
    _assert_close(first.get("average_price"), 11.3333333333, "CSV 首日成交均价计算不正确", failures)
    _assert_close(second.get("average_price"), 11.3333333333, "CSV 首日成交均价缓存读取不正确", failures)
    _assert_close(legacy.get("average_price"), 11.3333333333, "GBK CSV 首日成交均价计算不正确", failures)
    _assert(legacy.get("source") == "intraday_csv", "GBK CSV 应使用本地分时 CSV 来源", failures)
    _assert(cache_path.exists(), "CSV 首日成交均价应写入缓存", failures)


def intraday_average_price_hands_unit_case(failures: list[str]) -> None:
    temp_root = TEMP_ROOT / "listing_average_price_hands_unit"
    _reset_dir(temp_root)
    intraday_dir = temp_root / "intraday"
    intraday_dir.mkdir(parents=True, exist_ok=True)
    cache_path = temp_root / "cache.json"
    (intraday_dir / "999996.csv").write_text(
        "DateTime,open,high,low,close,volume,amount\n"
        "2026-01-01 09:30,10,10,10,10,1,1000\n"
        "2026-01-01 09:31,12,12,12,12,2,2400\n",
        encoding="utf-8-sig",
    )
    result = listing_average_price_helper.resolve_intraday_average_price(
        "999996",
        intraday_dir=intraday_dir,
        cache_path=cache_path,
    )
    _assert_close(result.get("average_price"), 11.3333333333, "成交量为手的 CSV 首日成交均价计算不正确", failures)
    _assert(result.get("unit_mode") == "volume_hands_amount_yuan", "成交量为手的 CSV 应识别成交量单位", failures)


def auto_candidate_groups_exclude_width_case(failures: list[str]) -> None:
    params = _base_params(price_range_width=0.10)
    groups = param_tuning.build_auto_tune_candidate_groups(params, _make_method2_dataset())
    group_names = [name for name, _ in groups]
    _assert("估值区间宽度" not in group_names, "自动调参不应包含估值区间宽度候选组", failures)
    width_candidates = [
        candidate
        for _, candidates in groups
        for candidate in candidates
        if "price_range_width" in candidate
    ]
    _assert(not width_candidates, "自动调参候选不应修改 price_range_width", failures)
    no_effect_valuation_keys = {
        "trend_strong_boost",
        "trend_weak_discount",
        "trend_strong_threshold",
        "trend_weak_threshold",
        "industry_trend_weight",
        "market_sentiment_weight",
        "wsi_weight_close_vwap",
        "wsi_weight_price_retention",
        "wsi_weight_high_timing",
        "wsi_weight_closing_momentum",
        "wsi_weight_volume_rhythm",
        "wsi_weight_turnover",
    }
    non_method2_keys = set(param_tuning.LATEST_METHOD1_AUTO_TUNABLE_KEYS) | set(
        param_tuning.LATEST_METHOD3_AUTO_TUNABLE_KEYS
    ) | set(param_tuning.LATEST_LOCAL_CENTER_AUTO_TUNABLE_KEYS) | {
        "sentiment_decay_half_life_days",
        "sentiment_first_day_baseline_pct",
        "sentiment_first_day_scale",
        "sentiment_post_listing_scale",
        "sentiment_premium_cap_pct",
        "sentiment_premium_floor_pct",
    }
    all_candidate_keys = {
        key
        for _, candidates in groups
        for candidate in candidates
        for key in candidate
    }
    _assert(
        set(param_tuning.LATEST_METHOD2_AUTO_TUNABLE_KEYS).issubset(all_candidate_keys),
        "method2_only 回放应完整覆盖方法二参数",
        failures,
    )
    _assert(
        not (no_effect_valuation_keys & all_candidate_keys),
        "估值回放不应搜索不影响估值结果的走势/WSI 参数",
        failures,
    )
    _assert(
        not (non_method2_keys & all_candidate_keys),
        "method2_only 回放不应浪费预算搜索其他方法参数",
        failures,
    )
    composite_params = _base_params(
        price_range_width=0.10,
        local_center_overlay_enabled=True,
        local_center_alpha=0.50,
        local_center_min_history=8,
        local_center_history_window=0,
        local_center_actual_cap_pct=80.0,
        local_center_slope_cap=1.50,
    )
    composite_groups = param_tuning.build_auto_tune_candidate_groups(
        composite_params,
        _make_composite_dataset(),
    )
    composite_group_names = [name for name, _ in composite_groups]
    composite_candidate_keys = {
        key
        for _, candidates in composite_groups
        for candidate in candidates
        for key in candidate
    }
    latest_required = (
        set(param_tuning.LATEST_METHOD1_AUTO_TUNABLE_KEYS)
        | set(param_tuning.LATEST_METHOD2_AUTO_TUNABLE_KEYS)
        | set(param_tuning.LATEST_METHOD2_CONFIDENCE_AUTO_TUNABLE_KEYS)
        | set(param_tuning.LATEST_METHOD3_AUTO_TUNABLE_KEYS)
        | set(param_tuning.LATEST_LOCAL_CENTER_AUTO_TUNABLE_KEYS)
    )
    _assert(
        latest_required.issubset(composite_candidate_keys),
        "composite 自动调参候选应完整覆盖最新三方法模型参数",
        failures,
    )
    for removed_group in ["走势权重", "强势走势加成", "弱势走势折价", "强势走势阈值", "弱势走势阈值", "WSI 权重组合"]:
        _assert(removed_group not in composite_group_names, f"估值调参应移除无效模块：{removed_group}", failures)
    for expected_group in [
        "方法二稳健过滤",
        "可比 PE 统计方式",
        "方法三情绪窗口",
        "sentiment_half_life",
        "sentiment_first_day_baseline",
        "sentiment_first_day_scale",
        "sentiment_post_listing_scale",
        "sentiment_premium_cap",
        "sentiment_premium_floor",
        "本地滚动中枢混合",
        "本地滚动中枢稳健性",
    ]:
        _assert(expected_group in composite_group_names, f"自动调参应包含最新模型模块：{expected_group}", failures)

def review_case(failures: list[str]) -> None:
    dataset = _make_sentiment_dataset()
    candidate_payload = {
        "name": "fixture_review",
        "description": "fixture candidate review",
        "candidates": [
            {
                "name": "keep_baseline_like",
                "description": "保留 baseline",
                "overrides": {"sentiment_post_listing_scale": 0.0},
            },
            {
                "name": "apply_sentiment",
                "description": "接入二三日赚钱效应溢价",
                "overrides": {"sentiment_post_listing_scale": 0.15},
            },
            {
                "name": "apply_sentiment_extra",
                "description": "同样命中但改动更多",
                "overrides": {"sentiment_post_listing_scale": 0.15, "price_range_width": 0.10},
            },
        ],
    }
    review = param_tuning.review_candidate_sets(
        dataset,
        _base_params(),
        candidate_payload,
        train_ratio=0.5,
        min_train_samples=3,
    )
    best_candidate = review.get("best_candidate") or {}
    _assert(best_candidate.get("name") == "apply_sentiment", "review 最佳候选应优先命中更精简的情绪溢价参数", failures)
    validation_metrics = best_candidate.get("validation_metrics") or {}
    _assert_close(validation_metrics.get("mae_change_pct"), 0.0, "review 验证集 MAE 应为 0", failures)
    full_metrics = best_candidate.get("full_metrics") or {}
    _assert(float(full_metrics.get("mae_change_pct")) < float((review.get("baseline") or {}).get("full_metrics", {}).get("mae_change_pct")), "review 全样本 MAE 应优于 baseline", failures)

def cli_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    dataset = _make_method2_dataset()
    dataset_path = TEMP_ROOT / "replay_dataset.json"
    output_dir = TEMP_ROOT / "reports"
    grid_path = TEMP_ROOT / "tiny_grid.json"

    param_tuning.save_replay_dataset(dataset, dataset_path)
    grid_path.write_text(
        json.dumps([{"small_cap_premium": 0.0}, {"small_cap_premium": 0.10}], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(ROOT_DIR / "tools" / "tune_params.py"),
        "--dataset-path",
        str(dataset_path),
        "--grid-file",
        str(grid_path),
        "--output-dir",
        str(output_dir),
        "--train-ratio",
        "0.5",
        "--min-train-samples",
        "3",
        "--top-n",
        "2",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    _assert(completed.returncode == 0, f"CLI 退出码异常: {completed.returncode}\n{completed.stdout}\n{completed.stderr}", failures)
    json_reports = sorted(output_dir.glob("tune_params_*.json"))
    md_reports = sorted(output_dir.glob("tune_params_*.md"))
    _assert(bool(json_reports), "CLI 未生成 JSON 报告", failures)
    _assert(bool(md_reports), "CLI 未生成 Markdown 报告", failures)
    if json_reports:
        payload = json.loads(json_reports[-1].read_text(encoding="utf-8"))
        report_comparisons = payload.get("report_comparisons") or {}
        _assert("selection_scope" in report_comparisons, "CLI JSON 报告应包含评分集对比", failures)
        _assert("full_scope" in report_comparisons, "CLI JSON 报告应包含全样本对比", failures)
    if md_reports:
        markdown = md_reports[-1].read_text(encoding="utf-8")
        _assert("## 前后汇总" in markdown, "CLI Markdown 报告应包含前后汇总", failures)
        _assert("逐样本对比（与真实结果）" in markdown, "CLI Markdown 报告应包含真实结果逐样本对比", failures)


def config_default_cli_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    dataset = _make_method2_dataset()
    dataset_path = TEMP_ROOT / "replay_dataset.json"
    output_dir = TEMP_ROOT / "config_default_reports"
    grid_path = TEMP_ROOT / "tiny_grid.json"
    params_path = TEMP_ROOT / "temp_params.txt"

    param_tuning.save_replay_dataset(dataset, dataset_path)
    grid_path.write_text(
        json.dumps([{"small_cap_premium": 0.0}, {"small_cap_premium": 0.10}], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_temp_params_file(
        params_path,
        {
            "tuning_train_ratio": 0.5,
            "tuning_min_train_samples": 3,
            "tuning_top_n": 2,
        },
    )

    command = [
        sys.executable,
        str(ROOT_DIR / "tools" / "tune_params.py"),
        "--params-file",
        str(params_path),
        "--dataset-path",
        str(dataset_path),
        "--grid-file",
        str(grid_path),
        "--output-dir",
        str(output_dir),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    _assert(
        completed.returncode == 0,
        f"config default CLI 退出码异常: {completed.returncode}\n{completed.stdout}\n{completed.stderr}",
        failures,
    )
    json_reports = sorted(output_dir.glob("tune_params_*.json"))
    _assert(bool(json_reports), "config default CLI 未生成 JSON 报告", failures)
    if json_reports:
        payload = json.loads(json_reports[-1].read_text(encoding="utf-8"))
        _assert(payload.get("train_codes") == ["000001", "000002", "000003"], "config default CLI 训练集切分未读取 tuning_train_ratio/min_train_samples", failures)
        _assert(payload.get("validation_codes") == ["000004", "000005", "000006"], "config default CLI 验证集切分未读取 tuning_train_ratio/min_train_samples", failures)
        top_candidates = payload.get("top_candidates") or []
        _assert(len(top_candidates) == 2, "config default CLI 未读取 tuning_top_n", failures)


def review_cli_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    dataset = _make_method2_dataset()
    dataset_path = TEMP_ROOT / "replay_dataset.json"
    output_dir = TEMP_ROOT / "review_reports"
    candidate_path = TEMP_ROOT / "candidate_set.json"

    param_tuning.save_replay_dataset(dataset, dataset_path)
    candidate_path.write_text(
        json.dumps(
            {
                "name": "fixture_review",
                "candidates": [
                    {"name": "baseline_like", "overrides": {"small_cap_premium": 0.0}},
                    {"name": "better_one", "overrides": {"small_cap_premium": 0.10}},
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(ROOT_DIR / "tools" / "review_candidate_params.py"),
        "--dataset-path",
        str(dataset_path),
        "--candidate-file",
        str(candidate_path),
        "--output-dir",
        str(output_dir),
        "--train-ratio",
        "0.5",
        "--min-train-samples",
        "3",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    _assert(completed.returncode == 0, f"review CLI 退出码异常: {completed.returncode}\n{completed.stdout}\n{completed.stderr}", failures)
    json_reports = sorted(output_dir.glob("review_candidate_*.json"))
    md_reports = sorted(output_dir.glob("review_candidate_*.md"))
    _assert(bool(json_reports), "review CLI 未生成 JSON 报告", failures)
    _assert(bool(md_reports), "review CLI 未生成 Markdown 报告", failures)
    if json_reports:
        payload = json.loads(json_reports[-1].read_text(encoding="utf-8"))
        report_comparisons = payload.get("report_comparisons") or {}
        _assert("selection_scope" in report_comparisons, "review CLI JSON 报告应包含评分集对比", failures)
        _assert("full_scope" in report_comparisons, "review CLI JSON 报告应包含全样本对比", failures)
    if md_reports:
        markdown = md_reports[-1].read_text(encoding="utf-8")
        _assert("## 前后汇总" in markdown, "review CLI Markdown 报告应包含前后汇总", failures)
        _assert("逐样本对比（与真实结果）" in markdown, "review CLI Markdown 报告应包含真实结果逐样本对比", failures)


def review_config_default_cli_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    dataset = _make_method2_dataset()
    dataset_path = TEMP_ROOT / "replay_dataset.json"
    output_dir = TEMP_ROOT / "review_config_reports"
    candidate_path = TEMP_ROOT / "candidate_set.json"
    params_path = TEMP_ROOT / "temp_params.txt"

    param_tuning.save_replay_dataset(dataset, dataset_path)
    candidate_path.write_text(
        json.dumps(
            {
                "name": "fixture_review",
                "candidates": [
                    {"name": "baseline_like", "overrides": {"small_cap_premium": 0.0}},
                    {"name": "better_one", "overrides": {"small_cap_premium": 0.10}},
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_temp_params_file(
        params_path,
        {
            "tuning_train_ratio": 0.5,
            "tuning_min_train_samples": 3,
        },
    )

    command = [
        sys.executable,
        str(ROOT_DIR / "tools" / "review_candidate_params.py"),
        "--params-file",
        str(params_path),
        "--dataset-path",
        str(dataset_path),
        "--candidate-file",
        str(candidate_path),
        "--output-dir",
        str(output_dir),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    _assert(
        completed.returncode == 0,
        f"review config default CLI 退出码异常: {completed.returncode}\n{completed.stdout}\n{completed.stderr}",
        failures,
    )
    json_reports = sorted(output_dir.glob("review_candidate_*.json"))
    _assert(bool(json_reports), "review config default CLI 未生成 JSON 报告", failures)
    if json_reports:
        payload = json.loads(json_reports[-1].read_text(encoding="utf-8"))
        _assert(payload.get("train_codes") == ["000001", "000002", "000003"], "review config default CLI 训练集切分未读取 tuning_train_ratio/min_train_samples", failures)
        _assert(payload.get("validation_codes") == ["000004", "000005", "000006"], "review config default CLI 验证集切分未读取 tuning_train_ratio/min_train_samples", failures)


def manual_pair_weight_payload_case(failures: list[str]) -> None:
    payload = param_tuning.build_manual_candidate_payload(
        name="manual_pair_weight",
        param_name="industry_trend_weight",
        values=[0.65],
        base_params=_base_params(),
    )
    candidates = payload.get("candidates") or []
    _assert(len(candidates) == 1, "manual pair weight payload should generate one candidate", failures)
    if not candidates:
        return
    candidate = candidates[0]
    overrides = dict(candidate.get("overrides") or {})
    _assert_close(overrides.get("industry_trend_weight"), 0.65, "manual pair weight should keep industry_trend_weight", failures)
    _assert_close(overrides.get("market_sentiment_weight"), 0.35, "manual pair weight should auto-fill market_sentiment_weight", failures)
    description = str(candidate.get("description") or "")
    _assert("自动补足到 1" in description, "manual pair weight description should mention auto fill", failures)


def manual_wsi_weight_payload_case(failures: list[str]) -> None:
    payload = param_tuning.build_manual_candidate_payload(
        name="manual_wsi_weight",
        param_name="wsi_weight_turnover",
        values=[0.20],
        base_params=_base_params(),
    )
    candidates = payload.get("candidates") or []
    _assert(len(candidates) == 1, "manual WSI weight payload should generate one candidate", failures)
    if not candidates:
        return
    candidate = candidates[0]
    overrides = dict(candidate.get("overrides") or {})
    expected_weights = {
        "wsi_weight_close_vwap": 0.24,
        "wsi_weight_price_retention": 0.20,
        "wsi_weight_high_timing": 0.16,
        "wsi_weight_closing_momentum": 0.12,
        "wsi_weight_volume_rhythm": 0.08,
        "wsi_weight_turnover": 0.20,
    }
    for key, expected_value in expected_weights.items():
        _assert_close(overrides.get(key), expected_value, f"manual WSI weight should auto-scale {key}", failures)
    total_weight = sum(float(overrides.get(key, 0.0)) for key in param_tuning.WSI_WEIGHT_KEYS)
    _assert_close(total_weight, 1.0, "manual WSI weight sum should remain 1", failures)
    description = str(candidate.get("description") or "")
    _assert("按当前策略参数比例自动缩放" in description, "manual WSI weight description should mention proportional scaling", failures)


def manual_offline_cli_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    dataset = _make_method2_dataset()
    dataset_path = TEMP_ROOT / "replay_dataset.json"
    output_dir = TEMP_ROOT / "manual_offline_reports"

    param_tuning.save_replay_dataset(dataset, dataset_path)

    command = [
        sys.executable,
        str(ROOT_DIR / "tools" / "tune_params.py"),
        "--mode",
        "offline",
        "--dataset-path",
        str(dataset_path),
        "--param-name",
        "small_cap_premium",
        "--candidate-values",
        "0.0,0.10",
        "--output-dir",
        str(output_dir),
        "--train-ratio",
        "0.5",
        "--min-train-samples",
        "3",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    _assert(
        completed.returncode == 0,
        f"manual offline CLI 退出码异常: {completed.returncode}\n{completed.stdout}\n{completed.stderr}",
        failures,
    )
    _assert("手动候选离线复核完成" in completed.stdout, "manual offline CLI 应打印完成提示", failures)
    json_reports = sorted(output_dir.glob("review_candidate_*.json"))
    md_reports = sorted(output_dir.glob("review_candidate_*.md"))
    _assert(bool(json_reports), "manual offline CLI 未生成 JSON 报告", failures)
    _assert(bool(md_reports), "manual offline CLI 未生成 Markdown 报告", failures)


def manual_auto_normalize_cli_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    dataset = _make_method2_dataset()
    dataset_path = TEMP_ROOT / "replay_dataset.json"
    output_dir = TEMP_ROOT / "manual_auto_normalize_reports"

    param_tuning.save_replay_dataset(dataset, dataset_path)

    command = [
        sys.executable,
        str(ROOT_DIR / "tools" / "tune_params.py"),
        "--mode",
        "offline",
        "--dataset-path",
        str(dataset_path),
        "--param-name",
        "industry_trend_weight",
        "--candidate-values",
        "0.70",
        "--output-dir",
        str(output_dir),
        "--train-ratio",
        "0.5",
        "--min-train-samples",
        "3",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    _assert(
        completed.returncode == 0,
        f"manual auto-normalize CLI 退出码异常: {completed.returncode}\n{completed.stdout}\n{completed.stderr}",
        failures,
    )
    json_reports = sorted(output_dir.glob("review_candidate_*.json"))
    _assert(bool(json_reports), "manual auto-normalize CLI 未生成 JSON 报告", failures)
    if not json_reports:
        return
    payload = json.loads(json_reports[-1].read_text(encoding="utf-8"))
    ranked_results = payload.get("ranked_results") or []
    candidate = next((item for item in ranked_results if item.get("name") == "industry_trend_weight_0p7"), None)
    _assert(candidate is not None, "manual auto-normalize CLI 应包含 industry_trend_weight_0p7", failures)
    if not candidate:
        return
    overrides = dict(candidate.get("overrides") or {})
    _assert_close(overrides.get("industry_trend_weight"), 0.70, "manual auto-normalize CLI should keep industry_trend_weight", failures)
    _assert_close(overrides.get("market_sentiment_weight"), 0.30, "manual auto-normalize CLI should auto-fill market_sentiment_weight", failures)
    description = str(candidate.get("description") or "")
    _assert("market_sentiment_weight" in description, "manual auto-normalize CLI description should show effective overrides", failures)


def manual_observe_cli_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    dataset = _make_method2_dataset()
    dataset_path = TEMP_ROOT / "replay_dataset.json"
    output_dir = TEMP_ROOT / "manual_observe_reports"

    param_tuning.save_replay_dataset(dataset, dataset_path)

    command = [
        sys.executable,
        str(ROOT_DIR / "tools" / "tune_params.py"),
        "--mode",
        "observe",
        "--dataset-path",
        str(dataset_path),
        "--candidate",
        "small_cap_premium=0.10,price_range_width=0.10",
        "--codes",
        "000004,000005,000006",
        "--output-dir",
        str(output_dir),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    _assert(
        completed.returncode == 0,
        f"manual observe CLI 退出码异常: {completed.returncode}\n{completed.stdout}\n{completed.stderr}",
        failures,
    )
    _assert("手动候选 replay 观察完成" in completed.stdout, "manual observe CLI 应打印完成提示", failures)
    json_reports = sorted(output_dir.glob("observe_manual_*.json"))
    md_reports = sorted(output_dir.glob("observe_manual_*.md"))
    _assert(bool(json_reports), "manual observe CLI 未生成 JSON 报告", failures)
    _assert(bool(md_reports), "manual observe CLI 未生成 Markdown 报告", failures)

    payload = json.loads(json_reports[-1].read_text(encoding="utf-8"))
    _assert(payload.get("target_codes") == ["000004", "000005", "000006"], "manual observe CLI 观察代码不正确", failures)
    report_display = payload.get("report_display") or {}
    _assert(report_display.get("display_mode") == "changed_only", "manual observe CLI 应标记 changed_only 展示模式", failures)
    ranked_results = payload.get("ranked_results") or []
    _assert(bool(ranked_results), "manual observe CLI 应包含候选排序结果", failures)
    markdown = md_reports[-1].read_text(encoding="utf-8")
    _assert("只展示调参前后发生变化的样本" in markdown, "manual observe CLI Markdown 应说明只展示变化样本", failures)
    _assert("本次样本数" in markdown, "manual observe CLI Markdown 应包含本次样本数统计", failures)
    _assert("误差缩小样本" in markdown, "manual observe CLI Markdown 应包含误差缩小样本统计", failures)
    _assert("误差增大样本" in markdown, "manual observe CLI Markdown 应包含误差增大样本统计", failures)
    _assert("无变化样本" in markdown, "manual observe CLI Markdown 应包含无变化样本统计", failures)


    _assert("不可比较但前后相同样本" in markdown, "manual observe CLI Markdown 应包含不可比较但前后相同样本统计", failures)


def manual_observe_no_change_cli_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    dataset = _make_method2_dataset()
    dataset_path = TEMP_ROOT / "replay_dataset.json"
    output_dir = TEMP_ROOT / "manual_observe_no_change_reports"
    params_path = TEMP_ROOT / "manual_observe_no_change_params.txt"

    param_tuning.save_replay_dataset(dataset, dataset_path)
    _write_temp_params_file(
        params_path,
        {
            "small_cap_premium": 0.10,
            "price_range_width": 0.10,
            "float_size_threshold": 1500,
            "pe_low_threshold": 0.20,
            "pe_discount_boost": 0.10,
            "pe_high_threshold": 0.60,
            "pe_premium_drag": -0.10,
        },
    )

    command = [
        sys.executable,
        str(ROOT_DIR / "tools" / "tune_params.py"),
        "--mode",
        "observe",
        "--params-file",
        str(params_path),
        "--dataset-path",
        str(dataset_path),
        "--candidate",
        "small_cap_premium=0.10,price_range_width=0.10,float_size_threshold=1500,pe_low_threshold=0.20,pe_discount_boost=0.10,pe_high_threshold=0.60,pe_premium_drag=-0.10",
        "--codes",
        "000004,000005,000006",
        "--output-dir",
        str(output_dir),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    _assert(
        completed.returncode == 0,
        f"manual observe no-change CLI 退出码异常: {completed.returncode}\n{completed.stdout}\n{completed.stderr}",
        failures,
    )
    md_reports = sorted(output_dir.glob("observe_manual_*.md"))
    _assert(bool(md_reports), "manual observe no-change CLI 未生成 Markdown 报告", failures)
    if md_reports:
        markdown = md_reports[-1].read_text(encoding="utf-8")
        _assert("本轮无变化样本" in markdown, "manual observe no-change CLI 应提示无变化样本", failures)


def auto_cli_accept_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    dataset = _make_sentiment_dataset()
    dataset_path = TEMP_ROOT / "auto_replay_dataset.json"
    params_path = TEMP_ROOT / "auto_params.txt"
    record_path = TEMP_ROOT / "auto_record.txt"

    param_tuning.save_replay_dataset(dataset, dataset_path)
    _write_temp_params_file(
        params_path,
        {
            "sentiment_first_day_scale": 0.0,
            "sentiment_post_listing_scale": 0.0,
            "price_range_width": 0.10,
            "recent_days": 365,
            "tuning_top_n": 3,
        },
    )

    command = [
        sys.executable,
        str(ROOT_DIR / "tools" / "tune_params.py"),
        "--mode",
        "auto",
        "--params-file",
        str(params_path),
        "--dataset-path",
        str(dataset_path),
        "--auto-record-path",
        str(record_path),
        "--auto-shadow-context-path",
        str(TEMP_ROOT / "auto_shadow_context_latest.json"),
        "--no-auto-shadow-refresh",
        "--no-auto-time-slice-gate",
        "--top-n",
        "3",
        "--auto-max-refine-stages",
        "1",
        "--auto-stage-candidate-limit",
        "120",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT_DIR,
        input="1\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    _assert(
        completed.returncode == 0,
        f"auto CLI 退出码异常: {completed.returncode}\n{completed.stdout}\n{completed.stderr}",
        failures,
    )
    _assert("自动调参阶段完成" in completed.stdout, "auto CLI 应打印阶段完成提示", failures)
    _assert("第 1 轮后请选择下一步" in completed.stdout, "auto CLI 应在每轮结束后提示下一步", failures)
    _assert("当前累计建议修改的参数" in completed.stdout, "auto CLI 应打印建议修改参数", failures)
    _assert("已写入参数文件" in completed.stdout, "auto CLI 接受后应写入参数文件", failures)
    _assert(record_path.exists(), "auto CLI 接受后应写入自动调参记录", failures)
    if record_path.exists():
        record_text = record_path.read_text(encoding="utf-8")
        _assert("自动调参（已接受）" in record_text, "auto 调参记录应包含接受标题", failures)
        _assert("修改参数：" in record_text and "->" in record_text, "auto 调参记录应包含修改参数", failures)
        _assert("方法三情绪样本窗口" in record_text, "auto 调参记录应说明 recent_days 情绪样本窗口", failures)
        _assert("评分权重衰减窗口" in record_text, "auto 调参记录应区分评分权重衰减窗口", failures)
        _assert("本地学习重排" in record_text, "auto 调参记录应保存本地学习重排摘要", failures)
        _assert("本地学习重排作者输入：未使用" in record_text, "auto 调参记录应声明未使用作者预测输入", failures)

def auto_cli_continue_then_accept_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    dataset = _make_sentiment_dataset()
    dataset_path = TEMP_ROOT / "auto_replay_dataset.json"
    params_path = TEMP_ROOT / "auto_params.txt"
    record_path = TEMP_ROOT / "auto_record.txt"

    param_tuning.save_replay_dataset(dataset, dataset_path)
    _write_temp_params_file(
        params_path,
        {
            "sentiment_first_day_scale": 0.0,
            "sentiment_post_listing_scale": 0.0,
            "price_range_width": 0.10,
            "recent_days": 365,
            "tuning_top_n": 3,
        },
    )

    command = [
        sys.executable,
        str(ROOT_DIR / "tools" / "tune_params.py"),
        "--mode",
        "auto",
        "--params-file",
        str(params_path),
        "--dataset-path",
        str(dataset_path),
        "--auto-record-path",
        str(record_path),
        "--auto-shadow-context-path",
        str(TEMP_ROOT / "auto_shadow_context_latest.json"),
        "--no-auto-shadow-refresh",
        "--no-auto-time-slice-gate",
        "--top-n",
        "3",
        "--auto-max-refine-stages",
        "2",
        "--auto-stage-candidate-limit",
        "120",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT_DIR,
        input="2\n1\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    _assert(
        completed.returncode == 0,
        f"auto CLI 二轮接受退出码异常: {completed.returncode}\n{completed.stdout}\n{completed.stderr}",
        failures,
    )
    _assert("第 2 轮完成" in completed.stdout, "auto CLI 选择继续后应执行第二轮", failures)
    _assert("已写入参数文件" in completed.stdout, "auto CLI 第二轮接受后应写入参数文件", failures)
    _assert(record_path.exists(), "auto CLI 第二轮接受后应写入自动调参记录", failures)

def manual_batch_entry_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    output_dir = TEMP_ROOT / "manual_batch_reports"
    input_text = "\n".join(
        [
            "2",
            "1",
            "1",
            "",
            "small_cap_premium",
            "0.0,0.10",
            "",
        ]
    )
    env = os.environ.copy()
    env["BSE_TUNING_NO_AUTO_REFRESH"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    command = ["cmd", "/c", "调参.bat", "--no-pause"]
    completed = subprocess.run(
        command,
        cwd=ROOT_DIR,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    _assert(
        completed.returncode == 0,
        f"manual batch 入口退出码异常: {completed.returncode}\n{completed.stdout}\n{completed.stderr}",
        failures,
    )
    _assert("请选择调参模式" in completed.stdout, "manual batch 入口应显示总模式提示词", failures)
    _assert("估值自动调参" in completed.stdout, "manual batch 入口应显示估值自动调参选项", failures)
    _assert("申购资金自动调参" in completed.stdout, "manual batch 入口应显示申购资金自动调参选项", failures)
    _assert("请选择手动调参执行模式" in completed.stdout, "manual batch 入口应显示手动模式提示词", failures)
    _assert("候选值列表，逗号分隔" in completed.stdout, "manual batch 入口应显示规范候选值提示词", failures)
    _assert("执行完成" in completed.stdout, "manual batch 入口应打印执行完成提示", failures)
    reports = sorted((ROOT_DIR / "输出" / "调参").glob("review_candidate_manual_small_cap_premium_*.json"))
    _assert(bool(reports), "manual batch 入口未生成预期报告", failures)
    _assert("若只输入权重组中的一个因子" in completed.stdout, "manual batch 入口应提示权重组自动联动规则", failures)
    if reports:
        latest_report = reports[-1]
        payload = json.loads(latest_report.read_text(encoding="utf-8"))
        _assert(payload.get("review_name") == "manual_small_cap_premium", "manual batch 报告名不正确", failures)


def wsi_turnover_stage_case(failures: list[str]) -> None:
    stage_names = param_tuning.list_stage_names()
    _assert("wsi_turnover_balance" in stage_names, "stage 列表应包含 wsi_turnover_balance", failures)
    _assert("composite_weights" in stage_names, "stage 列表应包含 composite_weights", failures)

    candidates = param_tuning.build_stage_candidates("wsi_turnover_balance")
    _assert(len(candidates) >= 16, "wsi_turnover_balance 候选数过少", failures)
    turnover_levels = {float(candidate.get("wsi_weight_turnover", 0.0)) for candidate in candidates}
    _assert(turnover_levels == {0.05, 0.10, 0.15, 0.20}, "wsi_turnover_balance 应覆盖 0.05/0.10/0.15/0.20 四档换手权重", failures)

    for index, candidate in enumerate(candidates, start=1):
        total_weight = sum(float(candidate.get(key, 0.0)) for key in param_tuning.WSI_WEIGHT_KEYS)
        _assert_close(total_weight, 1.0, f"candidate #{index} 的 WSI 权重和应为 1", failures)
        _assert(
            float(candidate.get("wsi_weight_turnover", 0.0)) > 0.0,
            f"candidate #{index} 应包含正数换手权重",
            failures,
        )

    _assert(
        any(
            float(candidate.get("wsi_weight_price_retention", 0.0)) < 0.25
            and float(candidate.get("wsi_weight_closing_momentum", 0.0)) == 0.15
            for candidate in candidates
        ),
        "wsi_turnover_balance 应包含 price_retention 单独让权的候选",
        failures,
    )
    _assert(
        any(
            float(candidate.get("wsi_weight_price_retention", 0.0)) == 0.25
            and float(candidate.get("wsi_weight_closing_momentum", 0.0)) < 0.15
            for candidate in candidates
        ),
        "wsi_turnover_balance 应包含 closing_momentum 单独让权的候选",
        failures,
    )
    _assert(
        any(
            float(candidate.get("wsi_weight_price_retention", 0.0)) < 0.25
            and float(candidate.get("wsi_weight_closing_momentum", 0.0)) < 0.15
            for candidate in candidates
        ),
        "wsi_turnover_balance 应包含 price_retention 与 closing_momentum 联动让权的候选",
        failures,
    )

    composite_candidates = param_tuning.build_stage_candidates("composite_weights")
    _assert(len(composite_candidates) >= 5, "composite_weights 候选数过少", failures)
    for candidate in composite_candidates:
        weight_sum = float(candidate.get("weight_comparable", 0.0)) + float(candidate.get("weight_industry_momentum", 0.0))
        _assert_close(weight_sum, 1.0, "composite_weights 候选权重和应为 1", failures)


def unsupported_composite_weight_case(failures: list[str]) -> None:
    dataset = _make_method2_dataset()
    try:
        param_tuning.rank_param_candidates(
            dataset,
            _base_params(),
            candidates=[{"weight_industry_momentum": 0.60}],
            train_ratio=0.5,
            min_train_samples=3,
        )
    except ValueError as exc:
        _assert("weight_industry_momentum" in str(exc), "组合权重拦截报错里应包含 weight_industry_momentum", failures)
    else:
        failures.append("method2_only 回放不应允许直接调 weight_industry_momentum")


def composite_replay_metrics_case(failures: list[str]) -> None:
    dataset = _make_composite_dataset()
    base_metrics = param_tuning.evaluate_replay_targets(
        dataset,
        _base_params(),
        target_codes=["100004", "100005", "100006"],
    )
    tuned_metrics = param_tuning.evaluate_replay_targets(
        dataset,
        _base_params(
            weight_comparable=0.80,
            weight_industry_momentum=0.20,
            method2_sample_confidence_enabled=False,
        ),
        target_codes=["100004", "100005", "100006"],
    )
    _assert(base_metrics["evaluation_scope"] == param_tuning.COMPOSITE_EVALUATION_SCOPE, "composite 回放应标记为 composite", failures)
    _assert(float(tuned_metrics["mae_change_pct"]) < float(base_metrics["mae_change_pct"]), "综合权重调参后 MAE 应优于 baseline", failures)
    _assert_close(tuned_metrics["mae_change_pct"], 0.0, "综合权重 tuned MAE 应命中 fixture", failures)
    available_results = tuned_metrics.get("available_results") or []
    _assert(bool(available_results), "composite 回放应生成可用结果", failures)
    first_result = available_results[0]
    _assert(first_result.get("method1_available") is True, "composite 回放结果应包含方法一可用标记", failures)
    _assert_close(first_result.get("weight_comparable"), 0.80, "composite 回放结果应记录 weight_comparable", failures)


def composite_weight_ranking_case(failures: list[str]) -> None:
    dataset = _make_composite_dataset()
    ranking = param_tuning.rank_param_candidates(
        dataset,
        _base_params(),
        candidates=[
            {"weight_comparable": 0.20, "weight_industry_momentum": 0.80},
            {"weight_comparable": 0.80, "weight_industry_momentum": 0.20},
        ],
        train_ratio=0.5,
        min_train_samples=3,
        top_n=3,
    )
    best = ranking.get("best") or {}
    overrides = dict(best.get("overrides") or {})
    _assert(best.get("label") != "baseline", "composite ranking 不应继续推荐 baseline", failures)
    _assert_close(overrides.get("weight_comparable"), 0.80, "composite ranking 应推荐 weight_comparable=0.80", failures)


def composite_cli_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    dataset = _make_composite_dataset()
    dataset_path = TEMP_ROOT / "composite_replay_dataset.json"
    output_dir = TEMP_ROOT / "composite_reports"
    grid_path = TEMP_ROOT / "composite_grid.json"

    param_tuning.save_replay_dataset(dataset, dataset_path)
    grid_path.write_text(
        json.dumps(
            [
                {"weight_comparable": 0.20, "weight_industry_momentum": 0.80},
                {"weight_comparable": 0.80, "weight_industry_momentum": 0.20},
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(ROOT_DIR / "tools" / "tune_params.py"),
        "--dataset-path",
        str(dataset_path),
        "--grid-file",
        str(grid_path),
        "--output-dir",
        str(output_dir),
        "--train-ratio",
        "0.5",
        "--min-train-samples",
        "3",
        "--top-n",
        "2",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    _assert(completed.returncode == 0, f"composite CLI 退出码异常: {completed.returncode}\n{completed.stdout}\n{completed.stderr}", failures)
    _assert("最佳候选" in completed.stdout, "composite CLI 应打印最佳候选", failures)


def formal_acceptance_guard_case(failures: list[str]) -> None:
    baseline = {
        "interval_hit_rate": 0.30,
        "mae_change_pct": 100.0,
        "p90_change_abs_error_pct": 180.0,
        "available_rate": 1.0,
    }
    safe_candidate = {
        "interval_hit_rate": 0.31,
        "mae_change_pct": 99.0,
        "p90_change_abs_error_pct": 179.0,
        "available_rate": 1.0,
    }
    unsafe_candidate = {
        "interval_hit_rate": 0.35,
        "mae_change_pct": 101.0,
        "p90_change_abs_error_pct": 170.0,
        "available_rate": 1.0,
    }
    _assert(
        param_tuning._formal_acceptance_guard(safe_candidate, baseline).get("passed") is True,
        "正式写回安全门槛应接受命中率提高且 MAE/P90 不退化的候选",
        failures,
    )
    unsafe_guard = param_tuning._formal_acceptance_guard(unsafe_candidate, baseline)
    _assert(
        unsafe_guard.get("passed") is False
        and (unsafe_guard.get("checks") or {}).get("full_mae_not_higher") is False,
        "正式写回安全门槛应拒绝全样本 MAE 退化的候选",
        failures,
    )
    safe_entry = {
        "name": "safe",
        "overrides": {"bse_discount_factor": 0.9},
        "metrics": safe_candidate,
        "formal_acceptance_guard": param_tuning._formal_acceptance_guard(safe_candidate, baseline),
    }
    unsafe_entry = {
        "name": "unsafe",
        "overrides": {"bse_discount_factor": 0.8},
        "metrics": unsafe_candidate,
        "formal_acceptance_guard": unsafe_guard,
    }
    rerank_pool = tune_params_cli.local_learning_auto_rerank._candidate_pool(
        {"baseline": safe_entry, "stage_start": unsafe_entry, "best": safe_entry},
        10,
    )
    _assert(
        all(entry.get("name") != "unsafe" for entry in rerank_pool),
        "雪球学习二次排序不应重新纳入未通过正式安全门槛的候选",
        failures,
    )


def auto_time_slice_write_gate_case(failures: list[str]) -> None:
    args = SimpleNamespace(
        no_auto_time_slice_gate=False,
        dataset_path="fixture_dataset.json",
        params_file="fixture_params.txt",
        auto_time_slice_initial_train_size=20,
        auto_time_slice_fold_size=7,
        auto_time_slice_fold_count=3,
        auto_stage_candidate_limit=650,
        auto_stage_time_limit_seconds=180.0,
        auto_local_rerank_top_n=20,
    )
    result = {
        "stage_level": 3,
        "changed_overrides": {"bse_discount_factor": 0.65},
        "local_learning_rerank": {"applied": True},
    }
    original_run = tune_params_cli.subprocess.run
    try:
        tune_params_cli.subprocess.run = lambda *unused_args, **unused_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "core_passed": True,
                    "two_level_passed": False,
                    "core_parameter_stability_warning": False,
                    "two_level_parameter_stability_warning": False,
                    "outputs": {"markdown": "fixture.md", "json": "fixture.json"},
                }
            ),
            stderr="",
        )
        gate = tune_params_cli._run_auto_time_slice_gate(args, result)
    finally:
        tune_params_cli.subprocess.run = original_run
    _assert(gate.get("required_path") == "two_level", "执行雪球二级排序后应校验 two_level 时间切片路径", failures)
    _assert(gate.get("passed") is False, "two_level 时间切片失败时必须拒绝正式写回", failures)

    args.no_auto_time_slice_gate = True
    bypassed = tune_params_cli._run_auto_time_slice_gate(args, result)
    _assert(
        bypassed.get("passed") is True and bypassed.get("bypassed") is True,
        "显式应急参数应留下时间切片门槛绕过记录",
        failures,
    )


def auto_shadow_context_v2_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    dataset_path = TEMP_ROOT / "shadow_dataset.json"
    params_path = TEMP_ROOT / "shadow_params.txt"
    context_path = TEMP_ROOT / "valuation_auto_shadow_context_latest.json"
    dataset = {
        "items": [
            {
                "SECURITY_CODE": "000001",
                "LISTING_DATE": "2026-07-01",
                "AVERAGE_PRICE": 11.0,
            }
        ]
    }
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
    params_path.write_text("bse_discount_factor = 0.625\n", encoding="utf-8")
    metrics = {
        "available_results": [
            {
                "code": "000001",
                "actual_interval_price": 11.0,
                "range_low": 10.0,
                "range_high": 12.0,
            }
        ]
    }
    result = {
        "generated_at": "2026-07-12 20:00:00",
        "reference_date": "2026-07-12",
        "stage_level": 3,
        "changed_overrides": {"bse_discount_factor": 0.65},
        "baseline": {"metrics": metrics, "auto_score": {}},
        "best": {"metrics": metrics, "auto_score": {}},
        "formal_acceptance_guard": {"passed": True},
        "time_slice_gate": {"passed": False, "status": "rejected"},
        "model_contract": {"version": 3},
    }
    args = SimpleNamespace(
        params_file=str(params_path),
        dataset_path=str(dataset_path),
        auto_shadow_context_path=str(context_path),
    )
    tune_params_cli._write_auto_shadow_context(args, dataset, result, "rejected_time_slice")
    context = json.loads(context_path.read_text(encoding="utf-8"))
    _assert(context.get("schema") == "valuation_auto_shadow_context_v2", "影子上下文应升级为 v2", failures)
    _assert(context.get("auto_tune_status") == "rejected_time_slice", "影子上下文应记录时间切片拒绝状态", failures)
    _assert(
        ((context.get("top_candidates") or [{}])[0]).get("overrides") == {},
        "时间切片拒绝后影子流水线必须回退正式参数",
        failures,
    )
    _assert(
        (context.get("candidate") or {}).get("overrides") == {"bse_discount_factor": 0.65},
        "影子上下文应保留被拒候选供审计",
        failures,
    )
    _assert(
        ((context.get("baseline") or {}).get("exact_score") or {}).get("evaluated_count") == 1,
        "影子上下文区间命中统计不应继续输出 0/0",
        failures,
    )
    _assert(bool((context.get("input_signatures") or {}).get("dataset_sha256")), "影子上下文应记录数据签名", failures)


def main() -> int:
    failures: list[str] = []
    time_split_case(failures)
    replay_sample_source_includes_intraday_and_ladder_labels_case(failures)
    replay_dataset_sync_inspection_case(failures)
    manual_dataset_auto_refresh_gate_case(failures)
    manual_dataset_auto_refresh_failure_fallback_case(failures)
    replay_item_cache_incremental_case(failures)
    replay_pdf_signature_tracks_parser_versions_case(failures)
    replay_item_announcement_fallback_case(failures)
    replay_metrics_case(failures)
    ranking_case(failures)
    auto_score_case(failures)
    auto_tune_case(failures)
    auto_local_learning_rerank_case(failures)
    replay_recent_days_window_case(failures)
    interval_hit_uses_average_price_case(failures)
    method2_uses_average_change_case(failures)
    intraday_average_price_cache_case(failures)
    intraday_average_price_hands_unit_case(failures)
    auto_candidate_groups_exclude_width_case(failures)
    review_case(failures)
    cli_case(failures)
    config_default_cli_case(failures)
    review_cli_case(failures)
    review_config_default_cli_case(failures)
    manual_pair_weight_payload_case(failures)
    manual_wsi_weight_payload_case(failures)
    manual_offline_cli_case(failures)
    manual_auto_normalize_cli_case(failures)
    manual_observe_cli_case(failures)
    manual_observe_no_change_cli_case(failures)
    auto_cli_accept_case(failures)
    auto_cli_continue_then_accept_case(failures)
    manual_batch_entry_case(failures)
    wsi_turnover_stage_case(failures)
    unsupported_composite_weight_case(failures)
    composite_replay_metrics_case(failures)
    composite_weight_ranking_case(failures)
    composite_cli_case(failures)
    formal_acceptance_guard_case(failures)
    auto_time_slice_write_gate_case(failures)
    auto_shadow_context_v2_case(failures)

    if failures:
        raise AssertionError("\n".join(failures))

    print("Param tuning validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
