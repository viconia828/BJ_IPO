from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
import sys
from typing import Any


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import param_tuning


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


def _base_params(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
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
    issue_price = 10.0
    close_price = issue_price * (1 + change_pct / 100)
    return {
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


def _make_dataset() -> dict[str, Any]:
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
        "evaluation_scope": param_tuning.EVALUATION_SCOPE,
        "generated_at": "2026-04-18 18:00:00",
        "source_months": 12,
        "sample_codes": [item["SECURITY_CODE"] for item in items],
        "requested_codes": [item["SECURITY_CODE"] for item in items],
        "available_count": len(items),
        "skipped": [],
        "caveats": [
            "当前回放调参先聚焦方法二；方法一历史可比快照尚未按历史时点回放。",
        ],
        "items": items,
    }


def time_split_case(failures: list[str]) -> None:
    dataset = _make_dataset()
    train_codes, validation_codes = param_tuning.split_target_codes(dataset, train_ratio=0.5, min_train_samples=3)
    _assert(train_codes == ["000001", "000002", "000003"], "时间切分训练集顺序不正确", failures)
    _assert(validation_codes == ["000004", "000005", "000006"], "时间切分验证集顺序不正确", failures)


def replay_metrics_case(failures: list[str]) -> None:
    dataset = _make_dataset()
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
    dataset = _make_dataset()
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
    dataset = _make_dataset()
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
    dataset = _make_dataset()
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


def review_cli_case(failures: list[str]) -> None:
    _reset_dir(TEMP_ROOT)
    dataset = _make_dataset()
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


def wsi_turnover_stage_case(failures: list[str]) -> None:
    stage_names = param_tuning.list_stage_names()
    _assert("wsi_turnover_balance" in stage_names, "stage 列表应包含 wsi_turnover_balance", failures)

    candidates = param_tuning.build_stage_candidates("wsi_turnover_balance")
    _assert(len(candidates) >= 4, "wsi_turnover_balance 候选数过少", failures)

    for index, candidate in enumerate(candidates, start=1):
        total_weight = sum(float(candidate.get(key, 0.0)) for key in param_tuning.WSI_WEIGHT_KEYS)
        _assert_close(total_weight, 1.0, f"candidate #{index} 的 WSI 权重和应为 1", failures)
        _assert(
            float(candidate.get("wsi_weight_turnover", 0.0)) > 0.0,
            f"candidate #{index} 应包含正数换手权重",
            failures,
        )


def unsupported_composite_weight_case(failures: list[str]) -> None:
    dataset = _make_dataset()
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


def main() -> int:
    failures: list[str] = []
    time_split_case(failures)
    replay_metrics_case(failures)
    ranking_case(failures)
    review_case(failures)
    cli_case(failures)
    review_cli_case(failures)
    wsi_turnover_stage_case(failures)
    unsupported_composite_weight_case(failures)

    if failures:
        raise AssertionError("\n".join(failures))

    print("Param tuning validation passed: 8 cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
