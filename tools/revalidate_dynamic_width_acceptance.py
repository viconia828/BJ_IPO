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
import evaluate_local_proxy_strategy as proxy
import param_tuning
import revalidate_valuation_time_slices as time_slices


DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_PARAMS = ROOT_DIR / "策略参数.txt"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"


CANDIDATE_WIDTH_POLICIES = ("layered_v1", "conservative", "balanced")


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest_proxy_report(output_dir: Path) -> Path:
    candidates = sorted(output_dir.glob("local_proxy_strategy_all_actual_*.json"), key=lambda path: path.stat().st_mtime)
    if not candidates:
        raise FileNotFoundError("未找到 all_actual 本地 proxy 报告")
    return candidates[-1]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attach_walk_forward_proxy_tiers(rows: list[dict[str, Any]]) -> None:
    ordered = sorted(rows, key=lambda row: (str(row.get("listing_date") or ""), str(row.get("code") or "")))
    observed_scores: list[float] = []
    index = 0
    while index < len(ordered):
        listing_date = str(ordered[index].get("listing_date") or "")[:10]
        group: list[dict[str, Any]] = []
        while index < len(ordered) and str(ordered[index].get("listing_date") or "")[:10] == listing_date:
            group.append(ordered[index])
            index += 1
        group_scores = [float(row["proxy_score"]) for row in group if _safe_float(row.get("proxy_score")) is not None]
        reference = sorted([*observed_scores, *group_scores])
        for row in group:
            score = _safe_float(row.get("proxy_score"))
            if score is None or not reference:
                row["proxy_rank_pct"] = None
                row["proxy_tier"] = "unknown"
                continue
            if len(reference) < 3:
                row["proxy_rank_pct"] = 0.5
                row["proxy_tier"] = "mid"
                continue
            lower = sum(value < score for value in reference)
            equal = sum(value == score for value in reference)
            average_rank = lower + (equal + 1) / 2
            rank_pct = (average_rank - 1) / max(len(reference) - 1, 1)
            row["proxy_rank_pct"] = rank_pct
            row["proxy_tier"] = "low" if rank_pct <= 1 / 3 else ("mid" if rank_pct <= 2 / 3 else "high")
        observed_scores.extend(group_scores)


def _strategy(width_policy: str) -> dict[str, Any]:
    return {
        "name": f"current_params_model_{width_policy}_no_fallback",
        "model": "current_params",
        "center_policy": "model",
        "center_condition": "never",
        "center_alpha": 0.0,
        "width_policy": width_policy,
        "fallback_policy": "none",
        "research_only": width_policy != "fixed_10",
    }


def normalized_interval_loss(row: dict[str, Any], miss_penalty: float = 4.0) -> float | None:
    target = _safe_float(row.get("target_price"))
    actual = _safe_float(row.get("actual_price"))
    width = _safe_float(row.get("dynamic_width"))
    if target is None or target <= 0 or actual is None or width is None:
        return None
    relative_error = abs(actual / target - 1.0)
    outside_distance = max(relative_error - width, 0.0)
    return 2.0 * width + miss_penalty * outside_distance


def _metrics_from_rows(rows: list[dict[str, Any]], miss_penalty: float) -> dict[str, Any]:
    evaluated = [row for row in rows if row.get("interval_hit") is not None]
    widths = [float(row["dynamic_width"]) for row in evaluated if _safe_float(row.get("dynamic_width")) is not None]
    losses = [normalized_interval_loss(row, miss_penalty) for row in evaluated]
    losses = [float(value) for value in losses if value is not None]
    hit_count = sum(row.get("interval_hit") is True for row in evaluated)
    hit_rate = hit_count / len(evaluated) if evaluated else None
    avg_width = statistics.fmean(widths) if widths else None
    full_width = 2 * avg_width if avg_width is not None else None
    return {
        "evaluated_count": len(evaluated),
        "hit_count": hit_count,
        "hit_rate": hit_rate,
        "avg_half_width": avg_width,
        "median_half_width": statistics.median(widths) if widths else None,
        "average_normalized_interval_loss": statistics.fmean(losses) if losses else None,
        "p90_normalized_interval_loss": param_tuning._quantile(losses, 0.90),
        "coverage_per_full_width": hit_rate / full_width if hit_rate is not None and full_width and full_width > 0 else None,
    }


def _evaluate(width_policy: str, rows: list[dict[str, Any]], params: dict[str, Any], miss_penalty: float) -> dict[str, Any]:
    result = proxy._evaluate_strategy(_strategy(width_policy), rows, params)
    return {
        "strategy": result["strategy"],
        "metrics": _metrics_from_rows(list(result.get("rows") or []), miss_penalty),
        "rows": list(result.get("rows") or []),
    }


def _not_higher(candidate: Any, baseline: Any, tolerance: float = 1e-12) -> bool:
    candidate_value = _safe_float(candidate)
    baseline_value = _safe_float(baseline)
    return candidate_value is not None and baseline_value is not None and candidate_value <= baseline_value + tolerance


def build_acceptance(
    baseline_full: dict[str, Any],
    candidate_full: dict[str, Any],
    folds: list[dict[str, Any]],
    baseline_combined: dict[str, Any],
    candidate_combined: dict[str, Any],
    *,
    max_avg_half_width: float,
) -> dict[str, Any]:
    fold_loss_nonworse = sum(
        _not_higher(row["candidate"].get("average_normalized_interval_loss"), row["baseline"].get("average_normalized_interval_loss"))
        for row in folds
    )
    hit_loss_folds = [
        row["fold"]
        for row in folds
        if int(row["candidate"].get("hit_count") or 0) < int(row["baseline"].get("hit_count") or 0)
    ]
    checks = {
        "full_hit_count_higher": int(candidate_full.get("hit_count") or 0) > int(baseline_full.get("hit_count") or 0),
        "full_average_interval_loss_not_higher": _not_higher(candidate_full.get("average_normalized_interval_loss"), baseline_full.get("average_normalized_interval_loss")),
        "full_p90_interval_loss_not_higher": _not_higher(candidate_full.get("p90_normalized_interval_loss"), baseline_full.get("p90_normalized_interval_loss")),
        "full_average_half_width_within_cap": float(candidate_full.get("avg_half_width") or 1e9) <= max_avg_half_width + 1e-12,
        "full_coverage_efficiency_not_lower": float(candidate_full.get("coverage_per_full_width") or 0.0) >= float(baseline_full.get("coverage_per_full_width") or 0.0),
        "combined_hit_count_higher": int(candidate_combined.get("hit_count") or 0) > int(baseline_combined.get("hit_count") or 0),
        "combined_average_interval_loss_not_higher": _not_higher(candidate_combined.get("average_normalized_interval_loss"), baseline_combined.get("average_normalized_interval_loss")),
        "combined_p90_interval_loss_not_higher": _not_higher(candidate_combined.get("p90_normalized_interval_loss"), baseline_combined.get("p90_normalized_interval_loss")),
        "combined_average_half_width_within_cap": float(candidate_combined.get("avg_half_width") or 1e9) <= max_avg_half_width + 1e-12,
        "combined_coverage_efficiency_not_lower": float(candidate_combined.get("coverage_per_full_width") or 0.0) >= float(baseline_combined.get("coverage_per_full_width") or 0.0),
        "at_least_two_folds_interval_loss_not_higher": fold_loss_nonworse >= 2,
        "no_fold_loses_hits": not hit_loss_folds,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "fold_loss_nonworse_count": fold_loss_nonworse,
        "hit_loss_folds": hit_loss_folds,
    }


def _fmt(value: Any, digits: int = 3) -> str:
    number = _safe_float(value)
    return "-" if number is None else f"{number:.{digits}f}"


def _fmt_pct(value: Any) -> str:
    number = _safe_float(value)
    return "-" if number is None else f"{number:.1%}"


def _render_markdown(payload: dict[str, Any]) -> str:
    baseline = payload["baseline"]
    lines = [
        "# 动态宽度验收框架",
        "",
        f"> 生成时间：{payload['generated_at']}",
        f"> 结论：**{payload['verdict']}**；正式区间仍保持固定 ±10%。",
        "",
        "## 一、验收口径",
        "",
        f"- 归一化区间损失 = 全宽 + {payload['settings']['miss_penalty']:.1f} × 区间外距离；以目标价为分母，避免高价股主导。",
        f"- 平均半宽硬上限：{payload['settings']['max_avg_half_width']:.1%}。命中增加但宽度超限，仍拒绝正式化。",
        "- 三折沿用正式估值时间切片边界；proxy 分层改为逐上市日 walk-forward，同日样本不读取彼此上市结果。",
        "- 只比较同一正式中枢下的宽度变化，不混入滚动中枢或情绪兜底。",
        "",
        "## 二、全样本对照",
        "",
        "| 宽度策略 | 命中 | 平均半宽 | 平均区间损失 | P90区间损失 | 覆盖/全宽 |",
        "|---|---:|---:|---:|---:|---:|",
        f"| 固定 ±10% | {baseline['full']['hit_count']}/{baseline['full']['evaluated_count']} | {_fmt_pct(baseline['full']['avg_half_width'])} | {_fmt(baseline['full']['average_normalized_interval_loss'])} | {_fmt(baseline['full']['p90_normalized_interval_loss'])} | {_fmt(baseline['full']['coverage_per_full_width'])} |",
    ]
    for candidate in payload["candidates"]:
        metrics = candidate["full"]
        lines.append(
            f"| `{candidate['name']}` | {metrics['hit_count']}/{metrics['evaluated_count']} | {_fmt_pct(metrics['avg_half_width'])} | {_fmt(metrics['average_normalized_interval_loss'])} | {_fmt(metrics['p90_normalized_interval_loss'])} | {_fmt(metrics['coverage_per_full_width'])} |"
        )
    lines.extend(
        [
            "",
            "## 三、三折合并与验收",
            "",
            "| 宽度策略 | 三折命中 | 平均半宽 | 平均区间损失 | P90区间损失 | 通过 |",
            "|---|---:|---:|---:|---:|---|",
            f"| 固定 ±10% | {baseline['combined_folds']['hit_count']}/{baseline['combined_folds']['evaluated_count']} | {_fmt_pct(baseline['combined_folds']['avg_half_width'])} | {_fmt(baseline['combined_folds']['average_normalized_interval_loss'])} | {_fmt(baseline['combined_folds']['p90_normalized_interval_loss'])} | - |",
        ]
    )
    for candidate in payload["candidates"]:
        metrics = candidate["combined_folds"]
        lines.append(
            f"| `{candidate['name']}` | {metrics['hit_count']}/{metrics['evaluated_count']} | {_fmt_pct(metrics['avg_half_width'])} | {_fmt(metrics['average_normalized_interval_loss'])} | {_fmt(metrics['p90_normalized_interval_loss'])} | {'是' if candidate['acceptance']['passed'] else '否'} |"
        )
    lines.extend(["", "## 四、门槛明细", ""])
    for candidate in payload["candidates"]:
        lines.append(f"### `{candidate['name']}`")
        lines.append("")
        for name, passed in candidate["acceptance"]["checks"].items():
            lines.append(f"- {'通过' if passed else '失败'}：`{name}`")
        lines.append("")
    lines.extend(
        [
            "## 五、结论",
            "",
            f"- 具备正式化讨论资格的策略：`{', '.join(payload['accepted_policies']) if payload['accepted_policies'] else '无'}`。",
            "- 验收失败不影响影子观察；只有新增样本后重新通过全部门槛，才进入正式参数讨论。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.miss_penalty < 0:
        raise ValueError("miss_penalty 不能为负数")
    if not 0 < args.max_avg_half_width < 1:
        raise ValueError("max_avg_half_width 必须在 0 到 1 之间")
    if min(args.initial_train_size, args.fold_size, args.fold_count) <= 0:
        raise ValueError("时间切片参数必须为正整数")
    dataset = param_tuning.load_replay_dataset(args.dataset)
    params = config_loader.load_params(args.params)
    proxy_report = Path(args.proxy_report) if args.proxy_report else _latest_proxy_report(Path(args.output_dir))
    proxy_payload = _read_json(proxy_report)
    teacher_rows = [dict(row) for row in proxy_payload.get("teacher_rows") or []]
    if not teacher_rows:
        raise ValueError("proxy 报告没有 teacher_rows，无法执行动态宽度验收")
    _attach_walk_forward_proxy_tiers(teacher_rows)

    baseline_eval = _evaluate("fixed_10", teacher_rows, params, args.miss_penalty)
    folds = time_slices.build_anchored_folds(
        dataset,
        initial_train_size=args.initial_train_size,
        fold_size=args.fold_size,
        fold_count=args.fold_count,
    )
    rows_by_code = {str(row.get("code") or ""): row for row in teacher_rows}
    baseline_fold_results: list[dict[str, Any]] = []
    for fold in folds:
        validation_rows = [dict(rows_by_code[code]) for code in fold["validation_codes"] if code in rows_by_code]
        evaluated = _evaluate("fixed_10", validation_rows, params, args.miss_penalty)
        baseline_fold_results.append({"fold": fold["fold"], **evaluated})
    baseline_combined = _metrics_from_rows(
        [row for fold in baseline_fold_results for row in fold.get("rows") or []],
        args.miss_penalty,
    )

    candidates: list[dict[str, Any]] = []
    for width_policy in CANDIDATE_WIDTH_POLICIES:
        full_eval = _evaluate(width_policy, teacher_rows, params, args.miss_penalty)
        fold_results: list[dict[str, Any]] = []
        acceptance_folds: list[dict[str, Any]] = []
        for fold, baseline_fold in zip(folds, baseline_fold_results):
            validation_rows = [dict(rows_by_code[code]) for code in fold["validation_codes"] if code in rows_by_code]
            candidate_fold = _evaluate(width_policy, validation_rows, params, args.miss_penalty)
            fold_results.append({"fold": fold["fold"], **candidate_fold})
            acceptance_folds.append(
                {
                    "fold": fold["fold"],
                    "baseline": baseline_fold["metrics"],
                    "candidate": candidate_fold["metrics"],
                }
            )
        combined = _metrics_from_rows([row for fold in fold_results for row in fold.get("rows") or []], args.miss_penalty)
        acceptance = build_acceptance(
            baseline_eval["metrics"],
            full_eval["metrics"],
            acceptance_folds,
            baseline_combined,
            combined,
            max_avg_half_width=args.max_avg_half_width,
        )
        candidates.append(
            {
                "name": width_policy,
                "full": full_eval["metrics"],
                "folds": acceptance_folds,
                "combined_folds": combined,
                "acceptance": acceptance,
            }
        )

    accepted = [row["name"] for row in candidates if row["acceptance"]["passed"]]
    payload = {
        "schema": "dynamic_width_acceptance_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "verdict": "通过" if accepted else "未通过",
        "formal_params_unchanged": True,
        "inputs": {
            "dataset": str(Path(args.dataset)),
            "params": str(Path(args.params)),
            "proxy_report": str(proxy_report),
        },
        "settings": {
            "miss_penalty": args.miss_penalty,
            "max_avg_half_width": args.max_avg_half_width,
            "initial_train_size": args.initial_train_size,
            "fold_size": args.fold_size,
            "fold_count": args.fold_count,
        },
        "baseline": {
            "name": "fixed_10",
            "full": baseline_eval["metrics"],
            "folds": [{"fold": row["fold"], "metrics": row["metrics"]} for row in baseline_fold_results],
            "combined_folds": baseline_combined,
        },
        "candidates": candidates,
        "accepted_policies": accepted,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    json_path = output_dir / f"dynamic_width_acceptance_{timestamp}.json"
    md_path = output_dir / f"dynamic_width_acceptance_{timestamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "markdown": str(md_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate dynamic valuation widths with width penalties and anchored time slices.")
    parser.add_argument("--proxy-report", default="")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--params", default=str(DEFAULT_PARAMS))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--miss-penalty", type=float, default=4.0)
    parser.add_argument("--max-avg-half-width", type=float, default=0.15)
    parser.add_argument("--initial-train-size", type=int, default=20)
    parser.add_argument("--fold-size", type=int, default=7)
    parser.add_argument("--fold-count", type=int, default=3)
    return parser


def main() -> int:
    try:
        payload = run(build_parser().parse_args())
    except Exception as exc:
        print(f"动态宽度验收失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps({"outputs": payload["outputs"], "verdict": payload["verdict"], "accepted_policies": payload["accepted_policies"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
