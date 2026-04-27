from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
        "price_range_width": 0.10,
        "weight_comparable": 0.50,
        "weight_industry_momentum": 0.50,
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
) -> dict[str, Any]:
    issue_price = 10.0
    close_price = issue_price * (1 + change_pct / 100)
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
        "LD_CLOSE_CHANGE": change_pct,
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


def time_split_case(failures: list[str]) -> None:
    dataset = _make_method2_dataset()
    train_codes, validation_codes = param_tuning.split_target_codes(dataset, train_ratio=0.5, min_train_samples=3)
    _assert(train_codes == ["000001", "000002", "000003"], "时间切分训练集顺序不正确", failures)
    _assert(validation_codes == ["000004", "000005", "000006"], "时间切分验证集顺序不正确", failures)


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
    original_build = tune_params_cli._build_and_save_dataset

    def _raise_build_error(*args: Any, **kwargs: Any) -> dict[str, Any]:
        _ = (args, kwargs)
        raise RuntimeError("fixture build failed")

    tune_params_cli._build_and_save_dataset = _raise_build_error
    try:
        loaded = tune_params_cli._load_or_refresh_dataset(
            args,
            _base_params(),
            dataset_path,
            ["000001", "000002", "000003", "000004", "000005", "000006", "000007"],
        )
    finally:
        tune_params_cli._build_and_save_dataset = original_build

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


def replay_metrics_case(failures: list[str]) -> None:
    dataset = _make_method2_dataset()
    base_metrics = param_tuning.evaluate_replay_targets(
        dataset,
        _base_params(),
        target_codes=["000004", "000005", "000006"],
    )
    tuned_metrics = param_tuning.evaluate_replay_targets(
        dataset,
        _base_params(small_cap_premium=0.10),
        target_codes=["000004", "000005", "000006"],
    )
    _assert(base_metrics["available_count"] == 3, "baseline 回放可用样本数应为 3", failures)
    _assert(tuned_metrics["available_count"] == 3, "tuned 回放可用样本数应为 3", failures)
    _assert(
        float(tuned_metrics["mae_change_pct"]) < float(base_metrics["mae_change_pct"]),
        "调参后涨幅 MAE 应优于 baseline",
        failures,
    )
    _assert_close(tuned_metrics["mae_change_pct"], 0.0, "tuned MAE 应命中 fixture", failures)
    _assert_close(tuned_metrics["rmse_change_pct"], 0.0, "tuned RMSE 应命中 fixture", failures)
    _assert_close(tuned_metrics["direction_hit_rate"], 1.0, "tuned 方向命中率应为 1", failures)


def ranking_case(failures: list[str]) -> None:
    dataset = _make_method2_dataset()
    ranking = param_tuning.rank_param_candidates(
        dataset,
        _base_params(),
        candidates=[{"small_cap_premium": 0.10}],
        train_ratio=0.5,
        min_train_samples=3,
        top_n=2,
    )
    best = ranking.get("best") or {}
    _assert(best.get("label") != "baseline", "ranking 不应继续推荐 baseline", failures)
    _assert(
        dict(best.get("overrides") or {}).get("small_cap_premium") == 0.10,
        "ranking 推荐参数应命中 small_cap_premium=0.10",
        failures,
    )


def review_case(failures: list[str]) -> None:
    dataset = _make_method2_dataset()
    candidate_payload = {
        "name": "fixture_review",
        "description": "fixture candidate review",
        "candidates": [
            {
                "name": "keep_baseline_like",
                "description": "保留 baseline",
                "overrides": {"small_cap_premium": 0.0},
            },
            {
                "name": "lift_small_cap",
                "description": "提高小盘溢价",
                "overrides": {"small_cap_premium": 0.10},
            },
            {
                "name": "lift_small_cap_extra",
                "description": "同样命中但改动更多",
                "overrides": {"small_cap_premium": 0.10, "price_range_width": 0.10},
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
    _assert(best_candidate.get("name") == "lift_small_cap", "review 最佳候选应优先命中更精简的 lift_small_cap", failures)
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

    param_tuning.save_replay_dataset(dataset, dataset_path)

    command = [
        sys.executable,
        str(ROOT_DIR / "tools" / "tune_params.py"),
        "--mode",
        "observe",
        "--dataset-path",
        str(dataset_path),
        "--candidate",
        "small_cap_premium=0.10,price_range_width=0.12,float_size_threshold=1500,pe_low_threshold=0.20,pe_discount_boost=0.10,pe_high_threshold=0.60,pe_premium_drag=-0.10",
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


def manual_batch_entry_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    output_dir = TEMP_ROOT / "manual_batch_reports"
    input_text = "\n".join(
        [
            "1",
            "1",
            "",
            "small_cap_premium",
            "0.0,0.10",
            "",
        ]
    )
    env = os.environ.copy()
    env["CODEX_BATCH_NO_PAUSE"] = "1"
    env["BSE_TUNING_NO_AUTO_REFRESH"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    command = ["cmd", "/c", "手动调参.bat"]
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
    _assert("请选择执行模式" in completed.stdout, "manual batch 入口应显示模式提示词", failures)
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
        _base_params(weight_comparable=0.80, weight_industry_momentum=0.20),
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


def main() -> int:
    failures: list[str] = []
    time_split_case(failures)
    replay_dataset_sync_inspection_case(failures)
    manual_dataset_auto_refresh_gate_case(failures)
    manual_dataset_auto_refresh_failure_fallback_case(failures)
    replay_metrics_case(failures)
    ranking_case(failures)
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
    manual_batch_entry_case(failures)
    wsi_turnover_stage_case(failures)
    unsupported_composite_weight_case(failures)
    composite_replay_metrics_case(failures)
    composite_weight_ranking_case(failures)
    composite_cli_case(failures)

    if failures:
        raise AssertionError("\n".join(failures))

    print("Param tuning validation passed: 23 cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
