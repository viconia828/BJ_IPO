from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))
if str(ROOT_DIR / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "tools"))

import config_loader
import local_learning_auto_rerank
import param_tuning


DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_PARAMS = ROOT_DIR / "策略参数.txt"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"
MODEL_SOURCE_FILES = (
    ROOT_DIR / "code" / "valuation_engine.py",
    ROOT_DIR / "code" / "param_tuning.py",
    ROOT_DIR / "tools" / "local_learning_auto_rerank.py",
)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metric_summary(metrics: dict[str, Any], auto_score: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "available_count": metrics.get("available_count"),
        "price_eval_count": metrics.get("price_eval_count"),
        "interval_hit_rate": metrics.get("interval_hit_rate"),
        "mae_change_pct": metrics.get("mae_change_pct"),
        "p90_change_abs_error_pct": metrics.get("p90_change_abs_error_pct"),
        "weighted_interval_hit_rate": (auto_score or {}).get("weighted_interval_hit_rate"),
        "weighted_mae_change_pct": (auto_score or {}).get("weighted_mae_change_pct"),
        "auto_score": (auto_score or {}).get("auto_score"),
    }


def _same_overrides(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True) == json.dumps(right, ensure_ascii=False, sort_keys=True)


def _local_core_comparison(result: dict[str, Any]) -> dict[str, Any]:
    local = result.get("local_learning_rerank") or {}
    core_overrides = dict(local.get("core_best_overrides") or {})
    selected = local.get("selected") or {}
    core_item = next(
        (
            item
            for item in local.get("ranking") or []
            if _same_overrides(dict(item.get("overrides") or {}), core_overrides)
        ),
        {},
    )
    selected_score = selected.get("learning_score")
    core_score = core_item.get("learning_score")
    gain = None
    if selected_score is not None and core_score is not None:
        gain = float(selected_score) - float(core_score)
    return {
        "applied": bool(local.get("applied")),
        "selection_changed": bool(local.get("selection_changed")),
        "author_inputs_used": bool(local.get("author_inputs_used")),
        "walk_forward_proxy": bool(local.get("walk_forward_proxy")),
        "pool_size": local.get("pool_size"),
        "core_best_overrides": core_overrides,
        "selected_overrides": dict(local.get("selected_overrides") or {}),
        "core_learning_score": core_score,
        "selected_learning_score": selected_score,
        "learning_score_gain_vs_core_best": gain,
        "selected_lines": {
            "conservative": selected.get("conservative") or {},
            "regime": selected.get("regime") or {},
            "rolling": selected.get("rolling") or {},
        },
    }


def _run_path(
    dataset: dict[str, Any],
    base_params: dict[str, Any],
    *,
    stages: int,
    candidate_limit: int,
    time_limit_seconds: float,
    pool_size: int,
    local_rerank: bool,
) -> dict[str, Any]:
    center = dict(base_params)
    stage_rows: list[dict[str, Any]] = []
    for stage_level in range(1, stages + 1):
        result = param_tuning.auto_tune_params(
            dataset,
            base_params,
            top_n=max(pool_size, 20),
            max_passes=1,
            stage_level=stage_level,
            center_params=center,
            candidate_limit=candidate_limit,
            time_limit_seconds=time_limit_seconds,
        )
        core_best = result.get("best") or {}
        local_comparison: dict[str, Any] | None = None
        if local_rerank:
            result = local_learning_auto_rerank.rerank_auto_tune_result(
                dataset,
                base_params,
                result,
                pool_size=pool_size,
            )
            local_comparison = _local_core_comparison(result)
        selected = result.get("best") or {}
        center = dict(base_params)
        center.update(dict(result.get("changed_overrides") or {}))
        stage_rows.append(
            {
                "stage_level": stage_level,
                "model_contract": result.get("model_contract") or {},
                "evaluated_step_count": result.get("evaluated_step_count"),
                "stop_reason": result.get("stop_reason"),
                "formal_acceptance_guard": result.get("formal_acceptance_guard") or {},
                "core_best_overrides": dict(core_best.get("overrides") or {}),
                "core_best": _metric_summary(core_best.get("metrics") or {}, core_best.get("auto_score") or {}),
                "selected_overrides": dict(selected.get("overrides") or {}),
                "selected": _metric_summary(selected.get("metrics") or {}, selected.get("auto_score") or {}),
                "local_rerank": local_comparison,
            }
        )
    final_metrics = param_tuning.evaluate_replay_targets(dataset, center)
    reference_date = param_tuning._auto_tune_reference_date(dataset)
    final_score = param_tuning._score_auto_metrics(final_metrics, center, reference_date)
    return {
        "path": "two_level_rerank" if local_rerank else "core_only",
        "final_overrides": param_tuning._diff_params(
            base_params,
            center,
            {key for row in stage_rows for key in row["selected_overrides"]},
        ),
        "final": _metric_summary(final_metrics, final_score),
        "stages": stage_rows,
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    baseline = payload["baseline"]
    core = payload["core_only"]["final"]
    rerank = payload["two_level_rerank"]["final"]
    lines = [
        "# 最新正式估值模型自动优化重验证",
        "",
        f"> 生成时间：{payload['generated_at']}",
        f"> 结论：**{payload['verdict']}**",
        f"> replay 样本：{payload['inputs']['sample_count']}；评估口径：`{payload['inputs']['evaluation_scope']}`",
        "",
        "## 总体对照",
        "",
        "| 路径 | 自动评分 | 加权命中率 | 加权涨幅 MAE | 原始命中率 | 原始涨幅 MAE | P90 绝对误差 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in (("正式参数 baseline", baseline), ("仅核心自动优化", core), ("两级学习重排", rerank)):
        lines.append(
            "| {name} | {score:.6f} | {weighted_hit:.2%} | {weighted_mae:.2f} | {hit:.2%} | {mae:.2f} | {p90:.2f} |".format(
                name=name,
                score=float(row.get("auto_score") or 0.0),
                weighted_hit=float(row.get("weighted_interval_hit_rate") or 0.0),
                weighted_mae=float(row.get("weighted_mae_change_pct") or 0.0),
                hit=float(row.get("interval_hit_rate") or 0.0),
                mae=float(row.get("mae_change_pct") or 0.0),
                p90=float(row.get("p90_change_abs_error_pct") or 0.0),
            )
        )
    lines.extend(
        [
            "",
            "## 最新模型接入检查",
            "",
            f"- 模型契约版本：`{payload['model_contract']['version']}`。",
            f"- 最新模型参数缺口：`{payload['model_contract']['missing_latest_model_keys']}`。",
            f"- 方法一 PE/流通盘开关：`{payload['structural_flags']['method1_pe_float_factors_enabled']}`。",
            f"- 方法一行业 PE 兜底开关：`{payload['structural_flags']['method1_industry_fallback_enabled']}`。",
            f"- replay 缓存版本：`{payload['inputs']['replay_item_cache_version']}`。",
            "",
            "## 两级重排逐轮结果",
            "",
        ]
    )
    for row in payload["two_level_rerank"]["stages"]:
        local = row.get("local_rerank") or {}
        lines.extend(
            [
                f"### 第 {row['stage_level']} 轮",
                "",
                f"- 是否改变核心最优：`{local.get('selection_changed')}`。",
                f"- 学习综合分相对核心最优增量：`{local.get('learning_score_gain_vs_core_best')}`。",
                f"- 作者输入：`{local.get('author_inputs_used')}`；walk-forward：`{local.get('walk_forward_proxy')}`。",
                f"- 核心 overrides：`{row.get('core_best_overrides')}`。",
                f"- 两级 overrides：`{row.get('selected_overrides')}`。",
                f"- 正式写回安全门槛：`{(row.get('formal_acceptance_guard') or {}).get('passed')}`。",
                "",
            ]
        )
    lines.extend(["## 判定依据", ""])
    for check, passed in payload["checks"].items():
        lines.append(f"- {'通过' if passed else '失败'}：{check}")
    lines.extend(
        [
            "",
            "## 效用边界",
            "",
            f"- 核心自动优化回放有效：`{payload['effectiveness']['core_optimization_effective_in_replay']}`。",
            f"- 二级学习重排改变选择的轮数：`{payload['effectiveness']['selection_changed_stage_count']}/{payload['effectiveness']['stage_count']}`。",
            f"- 是否观察到二级重排增量效用：`{payload['effectiveness']['local_rerank_incremental_effect_observed']}`。",
            f"- 解释：{payload['effectiveness']['interpretation']}",
        ]
    )
    lines.extend(
        [
            "",
            "本报告仅做离线回放验证，没有写入 `策略参数.txt`，也没有改变正式估值输出。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = Path(args.dataset)
    params_path = Path(args.params)
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    params = config_loader.load_params(params_path)
    reference_date = param_tuning._auto_tune_reference_date(dataset)
    baseline_metrics = param_tuning.evaluate_replay_targets(dataset, params)
    baseline_score = param_tuning._score_auto_metrics(baseline_metrics, params, reference_date)
    core_only = _run_path(
        dataset,
        params,
        stages=args.stages,
        candidate_limit=args.candidate_limit,
        time_limit_seconds=args.time_limit_seconds,
        pool_size=args.pool_size,
        local_rerank=False,
    )
    two_level = _run_path(
        dataset,
        params,
        stages=args.stages,
        candidate_limit=args.candidate_limit,
        time_limit_seconds=args.time_limit_seconds,
        pool_size=args.pool_size,
        local_rerank=True,
    )
    contract = (two_level["stages"][0].get("model_contract") or {}) if two_level["stages"] else {}
    local_rows = [row.get("local_rerank") or {} for row in two_level["stages"]]
    guard_rows = [row.get("formal_acceptance_guard") or {} for row in two_level["stages"]]
    structural_flags = {key: params.get(key) for key in param_tuning.LATEST_MODEL_STRUCTURAL_FLAGS}
    checks = {
        "自动优化候选完整覆盖最新三方法模型参数": bool(contract.get("latest_model_compatible")),
        "正式参数已启用方法一 PE/流通盘连续修正": _truthy(structural_flags["method1_pe_float_factors_enabled"]),
        "正式参数已启用行业 PE 低置信度兜底": _truthy(structural_flags["method1_industry_fallback_enabled"]),
        "replay 使用当前缓存版本": dataset.get("replay_item_cache_version") == param_tuning.REPLAY_ITEM_CACHE_VERSION,
        "核心自动优化优于正式参数 baseline": float(core_only["final"].get("auto_score") or 0.0) > float(baseline_score.get("auto_score") or 0.0),
        "每轮最终候选均通过全样本命中率/MAE/P90安全门槛": bool(guard_rows)
        and all(row.get("passed") is True for row in guard_rows),
        "每轮二级学习重排均成功执行": bool(local_rows) and all(row.get("applied") for row in local_rows),
        "二级重排未使用作者价格或文章输入": bool(local_rows) and all(not row.get("author_inputs_used") for row in local_rows),
        "二级重排保持 walk-forward 防泄漏": bool(local_rows) and all(row.get("walk_forward_proxy") for row in local_rows),
        "二级重排选择的学习综合分不低于同轮核心最优": bool(local_rows)
        and all(float(row.get("learning_score_gain_vs_core_best") or 0.0) >= -1e-12 for row in local_rows),
    }
    compatibility_passed = all(checks.values())
    incremental_effect_observed = any(row.get("selection_changed") for row in local_rows)
    if not compatibility_passed:
        verdict = "未通过：最新模型兼容性或自动优化执行存在待修复项"
    elif incremental_effect_observed:
        verdict = "兼容性通过：核心优化有效，且观察到二级学习重排改变候选选择"
    else:
        verdict = "兼容性通过：核心优化有效，但未观察到二级学习重排的增量效用"
    payload = {
        "schema": "latest_valuation_auto_optimization_revalidation_v2",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "verdict": verdict,
        "effectiveness": {
            "latest_model_compatible": compatibility_passed,
            "core_optimization_effective_in_replay": checks["核心自动优化优于正式参数 baseline"],
            "local_rerank_incremental_effect_observed": incremental_effect_observed,
            "selection_changed_stage_count": sum(bool(row.get("selection_changed")) for row in local_rows),
            "stage_count": len(local_rows),
            "interpretation": (
                "本轮二级重排与核心最优完全一致；只能证明兼容和执行正确，不能据此宣称雪球蒸馏规则带来额外收益。"
                if not incremental_effect_observed
                else "至少一轮二级重排改变了核心候选，需结合样本外结果判断收益是否稳健。"
            ),
        },
        "inputs": {
            "dataset": str(dataset_path.resolve()),
            "params": str(params_path.resolve()),
            "sample_count": len(dataset.get("items") or []),
            "evaluation_scope": dataset.get("evaluation_scope"),
            "replay_item_cache_version": dataset.get("replay_item_cache_version"),
            "stages": args.stages,
            "candidate_limit": args.candidate_limit,
            "pool_size": args.pool_size,
            "signatures": {
                "dataset_sha256": _sha256(dataset_path),
                "params_sha256": _sha256(params_path),
                "model_sources": {str(path.relative_to(ROOT_DIR)): _sha256(path) for path in MODEL_SOURCE_FILES},
            },
        },
        "model_contract": contract,
        "structural_flags": structural_flags,
        "baseline": _metric_summary(baseline_metrics, baseline_score),
        "core_only": core_only,
        "two_level_rerank": two_level,
        "checks": checks,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"latest_valuation_auto_optimization_revalidation_{timestamp}.json"
    markdown_path = output_dir / f"latest_valuation_auto_optimization_revalidation_{timestamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_render_markdown(payload), encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "markdown": str(markdown_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Revalidate core and local-learning auto optimization on the latest formal valuation model.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--params", default=str(DEFAULT_PARAMS))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--stages", type=int, default=3)
    parser.add_argument("--candidate-limit", type=int, default=param_tuning.AUTO_TUNE_STAGE_CANDIDATE_LIMIT)
    parser.add_argument("--time-limit-seconds", type=float, default=param_tuning.AUTO_TUNE_STAGE_TIME_LIMIT_SECONDS)
    parser.add_argument("--pool-size", type=int, default=20)
    return parser


def main() -> int:
    payload = run(build_parser().parse_args())
    print(json.dumps({"verdict": payload["verdict"], "checks": payload["checks"], "outputs": payload["outputs"]}, ensure_ascii=False, indent=2))
    return 0 if all(payload["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
