from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT_DIR / "code"
TOOLS_DIR = ROOT_DIR / "tools"
for path in (CODE_DIR, TOOLS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import config_loader
import param_tuning
import revalidate_valuation_time_slices as time_slices


DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_PARAMS = ROOT_DIR / "策略参数.txt"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"


POLICIES: tuple[dict[str, Any], ...] = (
    {
        "name": "count_only",
        "overrides": {
            "method1_anchor_reliability_enabled": True,
            "method1_anchor_confidence_mode": "count_only",
            "method1_anchor_min_confidence": 0.40,
            "method1_anchor_full_confidence_samples": 4,
        },
    },
    {
        "name": "balanced_count_dispersion",
        "overrides": {
            "method1_anchor_reliability_enabled": True,
            "method1_anchor_confidence_mode": "count_and_dispersion",
            "method1_anchor_min_confidence": 0.35,
            "method1_anchor_full_confidence_samples": 4,
            "method1_anchor_dispersion_soft_ratio": 1.50,
            "method1_anchor_dispersion_hard_ratio": 3.00,
            "method1_anchor_dispersion_floor": 0.50,
        },
    },
    {
        "name": "balanced_with_method_gap",
        "overrides": {
            "method1_anchor_reliability_enabled": True,
            "method1_anchor_confidence_mode": "count_and_dispersion",
            "method1_anchor_min_confidence": 0.35,
            "method1_anchor_full_confidence_samples": 4,
            "method1_anchor_dispersion_soft_ratio": 1.50,
            "method1_anchor_dispersion_hard_ratio": 3.00,
            "method1_anchor_dispersion_floor": 0.50,
            "method1_anchor_disagreement_enabled": True,
            "method1_anchor_disagreement_soft_ratio": 1.50,
            "method1_anchor_disagreement_hard_ratio": 3.00,
            "method1_anchor_disagreement_floor": 0.50,
        },
    },
    {
        "name": "strict_count_dispersion_gap",
        "overrides": {
            "method1_anchor_reliability_enabled": True,
            "method1_anchor_confidence_mode": "count_and_dispersion",
            "method1_anchor_min_confidence": 0.25,
            "method1_anchor_full_confidence_samples": 5,
            "method1_anchor_dispersion_soft_ratio": 1.35,
            "method1_anchor_dispersion_hard_ratio": 2.50,
            "method1_anchor_dispersion_floor": 0.35,
            "method1_anchor_disagreement_enabled": True,
            "method1_anchor_disagreement_soft_ratio": 1.35,
            "method1_anchor_disagreement_hard_ratio": 2.50,
            "method1_anchor_disagreement_floor": 0.35,
        },
    },
)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _not_higher(candidate: Any, baseline: Any, tolerance: float = 1e-9) -> bool:
    candidate_value = _safe_float(candidate)
    baseline_value = _safe_float(baseline)
    return candidate_value is not None and baseline_value is not None and candidate_value <= baseline_value + tolerance


def _metric_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "target_count",
        "available_count",
        "available_rate",
        "price_eval_count",
        "hit_count",
        "interval_hit_rate",
        "change_eval_count",
        "mae_change_pct",
        "median_change_abs_error_pct",
        "p90_change_abs_error_pct",
        "worst_change_abs_error_pct",
        "mean_signed_error_pct",
        "overestimate_count",
        "underestimate_count",
    )
    payload = {key: metrics.get(key) for key in keys}
    if payload.get("hit_count") is None:
        payload["hit_count"] = round(float(metrics.get("interval_hit_rate") or 0.0) * int(metrics.get("price_eval_count") or 0))
    return payload


def _full_metrics_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    summary = _metric_summary(metrics)
    summary["median_change_abs_error_pct"] = None
    summary["mean_signed_error_pct"] = None
    return summary


def _policy_params(base_params: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    params = dict(base_params)
    params.update(dict(policy.get("overrides") or {}))
    return params


def _acceptance(
    full_baseline: dict[str, Any],
    full_candidate: dict[str, Any],
    folds: list[dict[str, Any]],
    combined_baseline: dict[str, Any],
    combined_candidate: dict[str, Any],
) -> dict[str, Any]:
    fold_mae_nonworse = sum(
        _not_higher(row["candidate"].get("mae_change_pct"), row["baseline"].get("mae_change_pct"))
        for row in folds
    )
    catastrophic_folds: list[int] = []
    for row in folds:
        baseline_mae = _safe_float(row["baseline"].get("mae_change_pct"))
        candidate_mae = _safe_float(row["candidate"].get("mae_change_pct"))
        loses_two_hits = int(row["candidate"].get("hit_count") or 0) + 1 < int(row["baseline"].get("hit_count") or 0)
        mae_worsens_over_ten_pct = (
            baseline_mae is not None
            and candidate_mae is not None
            and candidate_mae > baseline_mae * 1.10 + 1e-9
        )
        if loses_two_hits or mae_worsens_over_ten_pct:
            catastrophic_folds.append(int(row["fold"]))
    checks = {
        "full_hit_count_not_lower": int(full_candidate.get("hit_count") or 0) >= int(full_baseline.get("hit_count") or 0),
        "full_mae_not_higher": _not_higher(full_candidate.get("mae_change_pct"), full_baseline.get("mae_change_pct")),
        "full_p90_not_higher": _not_higher(full_candidate.get("p90_change_abs_error_pct"), full_baseline.get("p90_change_abs_error_pct")),
        "combined_hit_count_not_lower": int(combined_candidate.get("hit_count") or 0) >= int(combined_baseline.get("hit_count") or 0),
        "combined_mae_not_higher": _not_higher(combined_candidate.get("mae_change_pct"), combined_baseline.get("mae_change_pct")),
        "combined_p90_not_higher": _not_higher(combined_candidate.get("p90_change_abs_error_pct"), combined_baseline.get("p90_change_abs_error_pct")),
        "combined_available_rate_not_lower": float(combined_candidate.get("available_rate") or 0.0) >= float(combined_baseline.get("available_rate") or 0.0),
        "at_least_two_folds_mae_not_higher": fold_mae_nonworse >= 2,
        "no_catastrophic_fold": not catastrophic_folds,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "fold_mae_nonworse_count": fold_mae_nonworse,
        "catastrophic_folds": catastrophic_folds,
    }


def _anchor_rows(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in metrics.get("available_results") or []:
        quality = dict(row.get("method1_anchor_quality") or {})
        method1_target = _safe_float(row.get("method1_target_price"))
        method2_target = _safe_float(row.get("method2_target_price"))
        gap_ratio = None
        if method1_target and method1_target > 0 and method2_target and method2_target > 0:
            gap_ratio = max(method1_target, method2_target) / min(method1_target, method2_target)
        rows.append(
            {
                "code": row.get("code"),
                "name": row.get("name"),
                "anchor_source": row.get("method1_anchor_source"),
                "sample_count": row.get("method1_sample_count"),
                "max_min_ratio": quality.get("max_min_ratio"),
                "iqr_ratio": quality.get("iqr_ratio"),
                "robust_dispersion_ratio": quality.get("robust_dispersion_ratio"),
                "method1_method2_gap_ratio": gap_ratio,
            }
        )
    return rows


def _anchor_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    direct = [row for row in rows if row.get("anchor_source") == "prospectus_comparables"]
    sample_counts = [int(row.get("sample_count") or 0) for row in direct]
    max_min_ratios = [float(row["max_min_ratio"]) for row in direct if _safe_float(row.get("max_min_ratio")) is not None]
    robust_ratios = [float(row["robust_dispersion_ratio"]) for row in direct if _safe_float(row.get("robust_dispersion_ratio")) is not None]
    return {
        "evaluated_rows": len(rows),
        "direct_comparable_rows": len(direct),
        "industry_fallback_rows": sum(row.get("anchor_source") == "industry_pe_fallback" for row in rows),
        "direct_with_two_or_fewer": sum(value <= 2 for value in sample_counts),
        "direct_max_min_ratio_ge_3": sum(value >= 3.0 for value in max_min_ratios),
        "median_direct_sample_count": statistics.median(sample_counts) if sample_counts else None,
        "median_max_min_ratio": statistics.median(max_min_ratios) if max_min_ratios else None,
        "median_robust_dispersion_ratio": statistics.median(robust_ratios) if robust_ratios else None,
    }


def _fmt(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    return "-" if number is None else f"{number:.{digits}f}"


def _render_markdown(payload: dict[str, Any]) -> str:
    distribution = payload["anchor_distribution"]
    lines = [
        "# 方法一锚点可靠性影子实验",
        "",
        f"> 生成时间：{payload['generated_at']}",
        "> 影子实验不修改正式参数；正式方法一仍保持原置信度口径。",
        "",
        "## 一、锚点诊断",
        "",
        f"- 共诊断 {distribution['evaluated_rows']} 只样本；直接可比锚 {distribution['direct_comparable_rows']} 只，行业 PE 兜底 {distribution['industry_fallback_rows']} 只。",
        f"- 直接可比中，至多两家可比 {distribution['direct_with_two_or_fewer']} 只；最高/最低 PE 达 3 倍及以上 {distribution['direct_max_min_ratio_ge_3']} 只。",
        f"- 可比数量中位数 {_fmt(distribution['median_direct_sample_count'], 1)}；最高/最低 PE 倍数中位数 {_fmt(distribution['median_max_min_ratio'])}；稳健离散倍数中位数 {_fmt(distribution['median_robust_dispersion_ratio'])}。",
        "",
        "## 二、全样本与三折验收",
        "",
        "| 策略 | 全样本命中 | 全样本 MAE | 全样本 P90 | 三折命中 | 三折 MAE | 三折 P90 | 通过 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    baseline = payload["baseline"]
    lines.append(
        f"| 正式基线 | {baseline['full']['hit_count']}/{baseline['full']['price_eval_count']} | {_fmt(baseline['full']['mae_change_pct'])} | {_fmt(baseline['full']['p90_change_abs_error_pct'])} | {baseline['combined_folds']['hit_count']}/{baseline['combined_folds']['price_eval_count']} | {_fmt(baseline['combined_folds']['mae_change_pct'])} | {_fmt(baseline['combined_folds']['p90_change_abs_error_pct'])} | - |"
    )
    for result in payload["policies"]:
        full = result["full"]
        combined = result["combined_folds"]
        lines.append(
            f"| `{result['name']}` | {full['hit_count']}/{full['price_eval_count']} | {_fmt(full['mae_change_pct'])} | {_fmt(full['p90_change_abs_error_pct'])} | {combined['hit_count']}/{combined['price_eval_count']} | {_fmt(combined['mae_change_pct'])} | {_fmt(combined['p90_change_abs_error_pct'])} | {'是' if result['acceptance']['passed'] else '否'} |"
        )
    lines.extend(
        [
            "",
            "## 三、结论",
            "",
            f"- 推荐影子策略：`{payload.get('recommended_policy') or '无'}`。",
            "- 只有同时通过全样本命中/MAE/P90、三折合并命中/MAE/P90、至少两折 MAE 不恶化和无灾难折，才具备后续正式化讨论资格。",
            "- 当前只使用可比数量、PE 稳健离散度、锚点来源及方法一/方法二分歧；业务相似度尚无统一结构化字段，不在本轮凭文本主观打分。",
            "",
            "## 四、逐样本锚点诊断",
            "",
            "| 代码 | 简称 | 来源 | 可比数 | PE最高/最低 | 稳健离散 | 方法一/二差异 |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in payload["anchor_rows"]:
        lines.append(
            f"| {row.get('code') or ''} | {row.get('name') or ''} | {row.get('anchor_source') or ''} | {row.get('sample_count') or 0} | {_fmt(row.get('max_min_ratio'))} | {_fmt(row.get('robust_dispersion_ratio'))} | {_fmt(row.get('method1_method2_gap_ratio'))} |"
        )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset = param_tuning.load_replay_dataset(args.dataset)
    params = config_loader.load_params(args.params)
    baseline_raw = param_tuning.evaluate_replay_targets(dataset, params)
    baseline_full = _full_metrics_summary(baseline_raw)
    folds = time_slices.build_anchored_folds(
        dataset,
        initial_train_size=args.initial_train_size,
        fold_size=args.fold_size,
        fold_count=args.fold_count,
    )

    baseline_fold_raw: list[dict[str, Any]] = []
    baseline_folds: list[dict[str, Any]] = []
    for fold in folds:
        metrics = param_tuning.evaluate_replay_targets(dataset, params, target_codes=fold["validation_codes"])
        baseline_fold_raw.append(metrics)
        baseline_folds.append({"fold": fold["fold"], **_metric_summary(time_slices.aggregate_fold_metrics([metrics]))})
    baseline_combined_raw = time_slices.aggregate_fold_metrics(baseline_fold_raw)
    baseline_combined = _metric_summary(baseline_combined_raw)

    policy_results: list[dict[str, Any]] = []
    for policy in POLICIES:
        candidate_params = _policy_params(params, policy)
        full_raw = param_tuning.evaluate_replay_targets(dataset, candidate_params)
        candidate_fold_raw: list[dict[str, Any]] = []
        fold_rows: list[dict[str, Any]] = []
        for fold, baseline_fold in zip(folds, baseline_folds):
            metrics = param_tuning.evaluate_replay_targets(dataset, candidate_params, target_codes=fold["validation_codes"])
            candidate_fold_raw.append(metrics)
            candidate_summary = _metric_summary(time_slices.aggregate_fold_metrics([metrics]))
            fold_rows.append({"fold": fold["fold"], "baseline": baseline_fold, "candidate": candidate_summary})
        combined_raw = time_slices.aggregate_fold_metrics(candidate_fold_raw)
        full_summary = _full_metrics_summary(full_raw)
        combined_summary = _metric_summary(combined_raw)
        policy_results.append(
            {
                "name": policy["name"],
                "overrides": policy["overrides"],
                "full": full_summary,
                "folds": fold_rows,
                "combined_folds": combined_summary,
                "acceptance": _acceptance(baseline_full, full_summary, fold_rows, baseline_combined, combined_summary),
            }
        )

    passed = [row for row in policy_results if row["acceptance"]["passed"]]
    passed.sort(
        key=lambda row: (
            row["combined_folds"].get("mae_change_pct") if row["combined_folds"].get("mae_change_pct") is not None else float("inf"),
            row["combined_folds"].get("p90_change_abs_error_pct") if row["combined_folds"].get("p90_change_abs_error_pct") is not None else float("inf"),
            -int(row["combined_folds"].get("hit_count") or 0),
        )
    )
    anchor_rows = _anchor_rows(baseline_raw)
    payload = {
        "schema": "method1_anchor_reliability_shadow_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {"dataset": str(Path(args.dataset)), "params": str(Path(args.params))},
        "formal_params_unchanged": True,
        "anchor_distribution": _anchor_distribution(anchor_rows),
        "anchor_rows": anchor_rows,
        "baseline": {"full": baseline_full, "folds": baseline_folds, "combined_folds": baseline_combined},
        "policies": policy_results,
        "recommended_policy": passed[0]["name"] if passed else None,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    json_path = output_dir / f"method1_anchor_reliability_shadow_{timestamp}.json"
    md_path = output_dir / f"method1_anchor_reliability_shadow_{timestamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "markdown": str(md_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate method-1 anchor reliability as a shadow-only confidence overlay.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--params", default=str(DEFAULT_PARAMS))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--initial-train-size", type=int, default=20)
    parser.add_argument("--fold-size", type=int, default=7)
    parser.add_argument("--fold-count", type=int, default=3)
    return parser


def main() -> int:
    try:
        payload = run(build_parser().parse_args())
    except Exception as exc:
        print(f"方法一锚点可靠性影子实验失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps({"outputs": payload["outputs"], "recommended_policy": payload["recommended_policy"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
