from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import config_loader
import param_tuning


DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_PARAMS = ROOT_DIR / "策略参数.txt"
DEFAULT_TRADEOFF = ROOT_DIR / "outputs" / "latest_valuation_tradeoff_analysis_20260712_182517.json"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rerank = _load_module("local_learning_auto_rerank_for_internalization", ROOT_DIR / "tools" / "local_learning_auto_rerank.py")
distill = rerank.distill
blend = rerank.blend


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def _linear_prediction(
    previous: list[dict[str, Any]],
    score: float,
    *,
    min_history: int,
    history_window: int,
    actual_cap_pct: float,
    slope_cap: float,
) -> float | None:
    history = previous[-history_window:] if history_window > 0 else previous
    pairs = []
    for row in history:
        x = _safe_float(row.get("proxy_score"))
        y = _safe_float(row.get("actual_change_pct"))
        if x is None or y is None:
            continue
        pairs.append((x, min(y, actual_cap_pct)))
    if len(pairs) < min_history:
        return None
    if len(pairs) < 5:
        similar = [y for x, y in pairs if abs(x - score) <= 2.5]
        return _median(similar if len(similar) >= 2 else [y for _, y in pairs])

    xs = [x for x, _ in pairs]
    ys = [y for _, y in pairs]
    mean_x = _mean(xs)
    mean_y = _mean(ys)
    if mean_x is None or mean_y is None:
        return None
    variance = sum((x - mean_x) ** 2 for x in xs)
    if variance <= 1e-9:
        return _median(ys)
    beta = sum((x - mean_x) * (y - mean_y) for x, y in pairs) / variance
    beta = max(-20.0, min(slope_cap, beta))
    alpha = mean_y - beta * mean_x
    return max(-50.0, min(actual_cap_pct, alpha + beta * score))


def _candidate_grid() -> list[dict[str, Any]]:
    candidates = []
    for base_model in ("formal", "optimized"):
        for center_alpha in (0.25, 0.35, 0.50, 0.65, 0.75):
            for min_history in (3, 5, 8):
                for history_window in (0, 10, 15, 20):
                    for actual_cap_pct in (400.0, 600.0, 900.0):
                        for slope_cap in (15.0, 25.0, 35.0):
                            candidates.append(
                                {
                                    "base_model": base_model,
                                    "local_center_alpha": center_alpha,
                                    "local_center_min_history": min_history,
                                    "local_center_history_window": history_window,
                                    "local_center_actual_cap_pct": actual_cap_pct,
                                    "local_center_slope_cap": slope_cap,
                                    "price_range_width": 0.10,
                                }
                            )
    return candidates


def _model_rows(
    dataset: dict[str, Any],
    params: dict[str, Any],
    optimized_params: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, dict[str, Any]]]]:
    formal_metrics = param_tuning.evaluate_replay_targets(dataset, params)
    optimized_metrics = param_tuning.evaluate_replay_targets(dataset, optimized_params)
    predictions = {
        "formal": blend._index_model_predictions(formal_metrics, "formal"),
        "optimized": blend._index_model_predictions(optimized_metrics, "optimized"),
    }
    by_code = distill._dataset_by_code(dataset)
    codes = [
        str(item.get("SECURITY_CODE") or "").strip()
        for item in dataset.get("items") or []
        if param_tuning._actual_interval_price(item) is not None
    ]
    teacher_rows = distill._build_teacher_rows(
        codes,
        list(dataset.get("items") or []),
        by_code,
        {
            "current_params": predictions["formal"],
            "scan_best": predictions["optimized"],
        },
        {},
        params,
    )
    rerank._attach_walk_forward_proxy_features(teacher_rows, params)
    # 正式模型不依赖历史样本当时的模型可用性或扫描候选；只保留可由发行资料和
    # 上市前历史稳定重建的 proxy 成分。
    for row in teacher_rows:
        row["proxy_score"] = (
            (_safe_float(row.get("proxy_score")) or 0.0)
            - (_safe_float(row.get("model_uncertainty_score")) or 0.0)
        )
    return teacher_rows, predictions


def _evaluate_candidate(
    candidate: dict[str, Any],
    teacher_rows: list[dict[str, Any]],
    scope_codes: set[str] | None = None,
) -> dict[str, Any]:
    ordered = sorted(teacher_rows, key=lambda row: (str(row.get("listing_date") or ""), str(row.get("code") or "")))
    previous: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    index = 0
    base_prefix = "current" if candidate["base_model"] == "formal" else "scan_best"
    while index < len(ordered):
        listing_date = str(ordered[index].get("listing_date") or "")[:10]
        group: list[dict[str, Any]] = []
        while index < len(ordered) and str(ordered[index].get("listing_date") or "")[:10] == listing_date:
            group.append(ordered[index])
            index += 1
        for row in group:
            code = str(row.get("code") or "")
            if scope_codes is not None and code not in scope_codes:
                continue
            actual = _safe_float(row.get("actual_change_pct"))
            base = _safe_float(row.get(f"{base_prefix}_predicted_change_pct"))
            available = bool(row.get(f"{base_prefix}_available")) and base is not None
            rolling = _linear_prediction(
                previous,
                _safe_float(row.get("proxy_score")) or 0.0,
                min_history=int(candidate["local_center_min_history"]),
                history_window=int(candidate["local_center_history_window"]),
                actual_cap_pct=float(candidate["local_center_actual_cap_pct"]),
                slope_cap=float(candidate["local_center_slope_cap"]),
            )
            predicted = base
            applied = False
            if available and rolling is not None:
                alpha = float(candidate["local_center_alpha"])
                predicted = base * (1 - alpha) + rolling * alpha
                applied = True
            width = float(candidate["price_range_width"])
            hit = None
            if predicted is not None and actual is not None:
                issue = _safe_float(row.get("issue_price"))
                if issue:
                    target = issue * (1 + predicted / 100)
                    actual_price = issue * (1 + actual / 100)
                    hit = target * (1 - width) <= actual_price <= target * (1 + width)
            rows.append(
                {
                    "code": code,
                    "listing_date": listing_date,
                    "actual_change_pct": actual,
                    "base_change_pct": base,
                    "rolling_change_pct": rolling,
                    "predicted_change_pct": predicted,
                    "overlay_applied": applied,
                    "interval_hit": hit,
                }
            )
        previous.extend(row for row in group if _safe_float(row.get("actual_change_pct")) is not None)

    evaluated = [row for row in rows if row.get("predicted_change_pct") is not None and row.get("actual_change_pct") is not None]
    errors = [float(row["predicted_change_pct"]) - float(row["actual_change_pct"]) for row in evaluated]
    hits = [row for row in evaluated if row.get("interval_hit") is True]
    return {
        "candidate": dict(candidate),
        "count": len(evaluated),
        "hit_count": len(hits),
        "hit_rate": len(hits) / len(evaluated) if evaluated else 0.0,
        "mae_change_pct": _mean([abs(value) for value in errors]),
        "mean_signed_change_error_pct": _mean(errors),
        "p90_abs_error_pct": _quantile([abs(value) for value in errors], 0.90),
        "worst_abs_error_pct": max([abs(value) for value in errors], default=None),
        "overlay_count": sum(1 for row in rows if row.get("overlay_applied")),
        "rows": rows,
    }


def _baseline_candidate(base_model: str) -> dict[str, Any]:
    return {
        "base_model": base_model,
        "local_center_alpha": 0.0,
        "local_center_min_history": 999,
        "local_center_history_window": 0,
        "local_center_actual_cap_pct": 900.0,
        "local_center_slope_cap": 35.0,
        "price_range_width": 0.10,
    }


def _selection_score(result: dict[str, Any]) -> float:
    return float(result.get("hit_rate") or 0.0) - 0.001 * float(result.get("mae_change_pct") or 1e9) - 0.0002 * abs(float(result.get("mean_signed_change_error_pct") or 0.0))


def _deployable_candidate(candidate: dict[str, Any]) -> bool:
    return bool(
        candidate.get("base_model") == "formal"
        and float(candidate.get("local_center_alpha") or 0.0) <= 0.50
        and int(candidate.get("local_center_min_history") or 0) >= 8
        and int(candidate.get("local_center_history_window") or 0) == 20
        and float(candidate.get("local_center_actual_cap_pct") or 0.0) == 900.0
        and float(candidate.get("local_center_slope_cap") or 0.0) <= 25.0
        and float(candidate.get("price_range_width") or 0.0) == 0.10
    )


def _compact(result: dict[str, Any], include_rows: bool = False) -> dict[str, Any]:
    keys = (
        "candidate",
        "count",
        "hit_count",
        "hit_rate",
        "mae_change_pct",
        "mean_signed_change_error_pct",
        "p90_abs_error_pct",
        "worst_abs_error_pct",
        "overlay_count",
    )
    payload = {key: result.get(key) for key in keys}
    if include_rows:
        payload["rows"] = result.get("rows") or []
    return payload


def _fmt(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    return "" if number is None else f"{number:.{digits}f}"


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 雪球学习项正式内化参数搜索",
        "",
        f"> 生成时间：{payload['generated_at']}",
        "> 口径：作者价格和正文不进入预测；proxy 特征和滚动回归逐上市日 walk-forward。",
        "",
        "## 参数选择",
        "",
        f"- 开发集：前 {payload['split']['development_count']} 只；留出集：后 {payload['split']['holdout_count']} 只。",
        "- 候选只在开发集排序，留出集不参与选参。",
        f"- 是否通过正式写回门槛：`{payload['deployment_gate']['passed']}`。",
        f"- 说明：{payload['deployment_gate']['reason']}",
        "",
        "## 同口径结果",
        "",
        "| 范围 | 方案 | 命中 | MAE | 平均偏差 | P90绝对误差 | 最差绝对误差 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for scope in ("development", "holdout", "full"):
        for name in ("formal", "previous_auto", "selected"):
            item = payload["comparisons"][scope][name]
            lines.append(
                "| {scope} | {name} | {hit}/{count} ({rate:.1%}) | {mae} | {bias} | {p90} | {worst} |".format(
                    scope={"development": "开发集", "holdout": "留出集", "full": "全样本"}[scope],
                    name={"formal": "正式参数", "previous_auto": "此前自动候选", "selected": "内化候选"}[name],
                    hit=item["hit_count"],
                    count=item["count"],
                    rate=item["hit_rate"],
                    mae=_fmt(item["mae_change_pct"]),
                    bias=_fmt(item["mean_signed_change_error_pct"]),
                    p90=_fmt(item["p90_abs_error_pct"]),
                    worst=_fmt(item["worst_abs_error_pct"]),
                )
            )
    lines.extend(["", "## 推荐参数", ""])
    for key, value in payload["selected_candidate"].items():
        lines.append(f"- `{key} = {value}`")
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 雪球作者数据只用于确定学习方向，没有进入任何待预测样本输入。",
            "- 同日上市样本互不读取实际结果。",
            "- 固定使用正式 ±10% 区间，命中改善不来自放宽区间。",
            "- 样本量仍小，留出集通过是写回必要条件，不代表长期收益保证。",
            "",
        ]
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    params = config_loader.load_params(args.params)
    tradeoff = json.loads(Path(args.tradeoff).read_text(encoding="utf-8"))
    optimized_params = dict(params)
    optimized_params.update(dict((tradeoff.get("inputs") or {}).get("optimized_overrides") or {}))
    rows, _predictions = _model_rows(dataset, params, optimized_params)
    codes = [str(row.get("code") or "") for row in rows if _safe_float(row.get("actual_change_pct")) is not None]
    split_index = max(int(len(codes) * float(args.train_ratio)), 1)
    split_index = min(split_index, len(codes) - 1)
    development_codes = set(codes[:split_index])
    holdout_codes = set(codes[split_index:])

    development_results = [_evaluate_candidate(candidate, rows, development_codes) for candidate in _candidate_grid()]
    development_results.sort(key=lambda item: (-_selection_score(item), -item["hit_count"], item["mae_change_pct"]))
    deployable_results = [item for item in development_results if _deployable_candidate(item["candidate"])]
    if not deployable_results:
        raise RuntimeError("no deployable local-center candidate")
    selected_candidate = dict(deployable_results[0]["candidate"])

    comparisons: dict[str, dict[str, dict[str, Any]]] = {}
    scopes = {
        "development": development_codes,
        "holdout": holdout_codes,
        "full": None,
    }
    for scope_name, scope_codes in scopes.items():
        comparisons[scope_name] = {
            "formal": _compact(_evaluate_candidate(_baseline_candidate("formal"), rows, scope_codes)),
            "previous_auto": _compact(_evaluate_candidate(_baseline_candidate("optimized"), rows, scope_codes)),
            "selected": _compact(_evaluate_candidate(selected_candidate, rows, scope_codes), include_rows=scope_name == "full"),
        }

    holdout_selected = comparisons["holdout"]["selected"]
    holdout_auto = comparisons["holdout"]["previous_auto"]
    full_selected = comparisons["full"]["selected"]
    full_auto = comparisons["full"]["previous_auto"]
    passed = bool(
        holdout_selected["hit_count"] >= holdout_auto["hit_count"]
        and float(holdout_selected["mae_change_pct"] or 1e9) < float(holdout_auto["mae_change_pct"] or 1e9)
        and full_selected["hit_count"] > full_auto["hit_count"]
        and float(full_selected["mae_change_pct"] or 1e9) < float(full_auto["mae_change_pct"] or 1e9)
        and float(full_selected["p90_abs_error_pct"] or 1e9) <= float(full_auto["p90_abs_error_pct"] or 1e9)
    )
    reason = (
        "留出集命中不低于此前自动候选，且留出集、全样本 MAE 与全样本 P90 尾部误差均改善。"
        if passed
        else "未同时满足留出集不降命中、留出/全样本 MAE 改善、全样本命中提高和 P90 尾部不恶化。"
    )
    payload = {
        "schema": "xueqiu_internalized_param_tuning_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {
            "dataset": str(Path(args.dataset)),
            "params": str(Path(args.params)),
            "tradeoff": str(Path(args.tradeoff)),
            "author_inputs_used": False,
            "walk_forward_proxy": True,
        },
        "split": {
            "train_ratio": float(args.train_ratio),
            "development_count": len(development_codes),
            "holdout_count": len(holdout_codes),
            "development_codes": [code for code in codes if code in development_codes],
            "holdout_codes": [code for code in codes if code in holdout_codes],
        },
        "candidate_count": len(development_results),
        "selected_candidate": selected_candidate,
        "top_development_candidates": [_compact(item) for item in development_results[:20]],
        "top_deployable_candidates": [_compact(item) for item in deployable_results[:20]],
        "comparisons": comparisons,
        "deployment_gate": {"passed": passed, "reason": reason},
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    json_path = output_dir / f"xueqiu_internalized_param_tuning_{timestamp}.json"
    md_path = output_dir / f"xueqiu_internalized_param_tuning_{timestamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(payload), encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "markdown": str(md_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tune walk-forward local-center parameters distilled from Xueqiu author differences.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--params", default=str(DEFAULT_PARAMS))
    parser.add_argument("--tradeoff", default=str(DEFAULT_TRADEOFF))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--train-ratio", type=float, default=0.70)
    return parser


def main() -> None:
    payload = run(build_parser().parse_args())
    print(json.dumps({
        "outputs": payload["outputs"],
        "selected_candidate": payload["selected_candidate"],
        "comparisons": payload["comparisons"],
        "deployment_gate": payload["deployment_gate"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
