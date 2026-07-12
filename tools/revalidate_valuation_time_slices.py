from __future__ import annotations

import argparse
import hashlib
import json
import random
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
import revalidate_latest_valuation_auto_optimization as formal_revalidation


DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_PARAMS = ROOT_DIR / "策略参数.txt"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _ordered_items(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        list(dataset.get("items") or []),
        key=lambda item: (
            str(item.get("LISTING_DATE") or "")[:10],
            str(item.get("SECURITY_CODE") or ""),
        ),
    )


def _item_code(item: dict[str, Any]) -> str:
    return str(item.get("SECURITY_CODE") or "").strip()


def _item_date(item: dict[str, Any]) -> str:
    return str(item.get("LISTING_DATE") or "")[:10]


def _dataset_subset(dataset: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    payload = dict(dataset)
    payload["items"] = list(items)
    payload["sample_codes"] = [_item_code(item) for item in items]
    payload["available_count"] = len(items)
    return payload


def _advance_same_day(items: list[dict[str, Any]], index: int) -> int:
    index = min(max(index, 0), len(items))
    if index <= 0 or index >= len(items):
        return index
    previous_date = _item_date(items[index - 1])
    while index < len(items) and _item_date(items[index]) == previous_date:
        index += 1
    return index


def build_anchored_folds(
    dataset: dict[str, Any],
    *,
    initial_train_size: int = 20,
    fold_size: int = 7,
    fold_count: int = 3,
) -> list[dict[str, Any]]:
    items = _ordered_items(dataset)
    if len(items) < initial_train_size + 1:
        raise ValueError("样本不足，无法建立首个时间切片验证集")
    train_end = _advance_same_day(items, initial_train_size)
    folds: list[dict[str, Any]] = []
    for fold_index in range(1, fold_count + 1):
        if train_end >= len(items):
            break
        validation_end = len(items) if fold_index == fold_count else min(train_end + fold_size, len(items))
        validation_end = _advance_same_day(items, validation_end)
        train_items = items[:train_end]
        validation_items = items[train_end:validation_end]
        if not validation_items:
            break
        folds.append(
            {
                "fold": fold_index,
                "train_items": train_items,
                "validation_items": validation_items,
                "train_codes": [_item_code(item) for item in train_items],
                "validation_codes": [_item_code(item) for item in validation_items],
                "train_date_start": _item_date(train_items[0]),
                "train_date_end": _item_date(train_items[-1]),
                "validation_date_start": _item_date(validation_items[0]),
                "validation_date_end": _item_date(validation_items[-1]),
            }
        )
        train_end = validation_end
    if len(folds) != fold_count:
        raise ValueError(f"只能建立 {len(folds)} 折，少于要求的 {fold_count} 折")
    return folds


def _is_interval_hit(row: dict[str, Any]) -> bool:
    actual = _safe_float(row.get("actual_interval_price"))
    low = _safe_float(row.get("range_low"))
    high = _safe_float(row.get("range_high"))
    return actual is not None and low is not None and high is not None and low <= actual <= high


def _quantile(values: list[float], quantile: float) -> float | None:
    return param_tuning._quantile(values, quantile)


def aggregate_fold_metrics(metrics_rows: list[dict[str, Any]]) -> dict[str, Any]:
    available_rows = [
        row
        for metrics in metrics_rows
        for row in list(metrics.get("available_results") or [])
    ]
    unavailable_rows = [
        row
        for metrics in metrics_rows
        for row in list(metrics.get("unavailable_results") or [])
    ]
    change_rows = [row for row in available_rows if _safe_float(row.get("change_abs_error")) is not None]
    change_abs_errors = [float(row["change_abs_error"]) for row in change_rows]
    signed_errors = [
        float(row["predicted_change_pct"]) - float(row["actual_interval_change_pct"])
        for row in change_rows
        if _safe_float(row.get("predicted_change_pct")) is not None
        and _safe_float(row.get("actual_interval_change_pct")) is not None
    ]
    price_rows = [
        row
        for row in available_rows
        if _safe_float(row.get("actual_interval_price")) is not None
        and _safe_float(row.get("predicted_target_price")) is not None
    ]
    total_count = len(available_rows) + len(unavailable_rows)
    hit_count = sum(_is_interval_hit(row) for row in price_rows)
    over_errors = [value for value in signed_errors if value > 1e-12]
    under_errors = [value for value in signed_errors if value < -1e-12]
    return {
        "target_count": total_count,
        "available_count": len(available_rows),
        "available_rate": len(available_rows) / total_count if total_count else 0.0,
        "price_eval_count": len(price_rows),
        "hit_count": hit_count,
        "interval_hit_rate": hit_count / len(price_rows) if price_rows else 0.0,
        "change_eval_count": len(change_abs_errors),
        "mae_change_pct": statistics.fmean(change_abs_errors) if change_abs_errors else None,
        "median_change_abs_error_pct": statistics.median(change_abs_errors) if change_abs_errors else None,
        "p90_change_abs_error_pct": _quantile(change_abs_errors, 0.90),
        "worst_change_abs_error_pct": max(change_abs_errors) if change_abs_errors else None,
        "mean_signed_error_pct": statistics.fmean(signed_errors) if signed_errors else None,
        "overestimate_count": len(over_errors),
        "underestimate_count": len(under_errors),
        "average_overestimate_pct": statistics.fmean(over_errors) if over_errors else None,
        "average_underestimate_pct": statistics.fmean(under_errors) if under_errors else None,
        "available_results": available_rows,
        "unavailable_results": unavailable_rows,
    }


def _compact_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
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
        "average_overestimate_pct",
        "average_underestimate_pct",
    )
    return {key: metrics.get(key) for key in keys}


def _evaluate(dataset: dict[str, Any], params: dict[str, Any], codes: list[str]) -> dict[str, Any]:
    raw = param_tuning.evaluate_replay_targets(dataset, params, target_codes=codes)
    return aggregate_fold_metrics([raw])


def _params_with_overrides(base_params: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    params = dict(base_params)
    params.update(overrides)
    return params


def _run_tuning_path(
    train_dataset: dict[str, Any],
    base_params: dict[str, Any],
    *,
    stages: int,
    candidate_limit: int,
    time_limit_seconds: float,
    pool_size: int,
    local_rerank: bool,
) -> dict[str, Any]:
    return formal_revalidation._run_path(
        train_dataset,
        base_params,
        stages=stages,
        candidate_limit=candidate_limit,
        time_limit_seconds=time_limit_seconds,
        pool_size=pool_size,
        local_rerank=local_rerank,
    )


def _fold_result(
    dataset: dict[str, Any],
    base_params: dict[str, Any],
    fold: dict[str, Any],
    *,
    stages: int,
    candidate_limit: int,
    time_limit_seconds: float,
    pool_size: int,
) -> dict[str, Any]:
    train_dataset = _dataset_subset(dataset, list(fold["train_items"]))
    baseline_metrics = _evaluate(dataset, base_params, list(fold["validation_codes"]))
    paths: dict[str, Any] = {}
    for name, local_rerank in (("core", False), ("two_level", True)):
        tuning = _run_tuning_path(
            train_dataset,
            base_params,
            stages=stages,
            candidate_limit=candidate_limit,
            time_limit_seconds=time_limit_seconds,
            pool_size=pool_size,
            local_rerank=local_rerank,
        )
        overrides = dict(tuning.get("final_overrides") or {})
        candidate_params = _params_with_overrides(base_params, overrides)
        validation_metrics = _evaluate(dataset, candidate_params, list(fold["validation_codes"]))
        paths[name] = {
            "overrides": overrides,
            "train_metrics": tuning.get("final") or {},
            "validation_metrics": validation_metrics,
            "stages": tuning.get("stages") or [],
        }
    return {
        "fold": fold["fold"],
        "train_count": len(fold["train_codes"]),
        "validation_count": len(fold["validation_codes"]),
        "train_date_start": fold["train_date_start"],
        "train_date_end": fold["train_date_end"],
        "validation_date_start": fold["validation_date_start"],
        "validation_date_end": fold["validation_date_end"],
        "train_codes": fold["train_codes"],
        "validation_codes": fold["validation_codes"],
        "baseline": baseline_metrics,
        **paths,
    }


def _paired_rows(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    baseline_by_code = {str(row.get("code")): row for row in baseline.get("available_results") or []}
    candidate_by_code = {str(row.get("code")): row for row in candidate.get("available_results") or []}
    paired: list[dict[str, Any]] = []
    for code in sorted(set(baseline_by_code) & set(candidate_by_code)):
        base_row = baseline_by_code[code]
        candidate_row = candidate_by_code[code]
        base_error = _safe_float(base_row.get("change_abs_error"))
        candidate_error = _safe_float(candidate_row.get("change_abs_error"))
        if base_error is None or candidate_error is None:
            continue
        paired.append(
            {
                "code": code,
                "baseline_abs_error": base_error,
                "candidate_abs_error": candidate_error,
                "baseline_hit": int(_is_interval_hit(base_row)),
                "candidate_hit": int(_is_interval_hit(candidate_row)),
                "mae_improvement": base_error - candidate_error,
            }
        )
    return paired


def _paired_robustness(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    bootstrap_samples: int = 5000,
    seed: int = 20260712,
) -> dict[str, Any]:
    paired = _paired_rows(baseline, candidate)
    if not paired:
        return {"paired_count": 0}
    largest_contributor = max(paired, key=lambda row: float(row["mae_improvement"]))
    trimmed = [row for row in paired if row is not largest_contributor]
    trimmed_candidate_mae = statistics.fmean(float(row["candidate_abs_error"]) for row in trimmed) if trimmed else None
    trimmed_baseline_mae = statistics.fmean(float(row["baseline_abs_error"]) for row in trimmed) if trimmed else None
    rng = random.Random(seed)
    mae_wins = 0
    hit_wins = 0
    for _ in range(max(bootstrap_samples, 1)):
        sample = [paired[rng.randrange(len(paired))] for _ in range(len(paired))]
        baseline_mae = statistics.fmean(float(row["baseline_abs_error"]) for row in sample)
        candidate_mae = statistics.fmean(float(row["candidate_abs_error"]) for row in sample)
        baseline_hits = sum(int(row["baseline_hit"]) for row in sample)
        candidate_hits = sum(int(row["candidate_hit"]) for row in sample)
        mae_wins += int(candidate_mae <= baseline_mae)
        hit_wins += int(candidate_hits >= baseline_hits)
    return {
        "paired_count": len(paired),
        "largest_positive_mae_contributor": largest_contributor,
        "trimmed_baseline_mae": trimmed_baseline_mae,
        "trimmed_candidate_mae": trimmed_candidate_mae,
        "trimmed_mae_not_higher": (
            trimmed_candidate_mae is not None
            and trimmed_baseline_mae is not None
            and trimmed_candidate_mae <= trimmed_baseline_mae + 1e-12
        ),
        "bootstrap_samples": bootstrap_samples,
        "bootstrap_probability_mae_not_higher": mae_wins / max(bootstrap_samples, 1),
        "bootstrap_probability_hit_count_not_lower": hit_wins / max(bootstrap_samples, 1),
    }


def _path_acceptance(fold_rows: list[dict[str, Any]], path_name: str) -> dict[str, Any]:
    baseline = aggregate_fold_metrics([row["baseline"] for row in fold_rows])
    candidate = aggregate_fold_metrics([row[path_name]["validation_metrics"] for row in fold_rows])
    fold_checks: list[dict[str, Any]] = []
    mae_non_worse_count = 0
    catastrophic_folds: list[int] = []
    for row in fold_rows:
        fold_baseline = row["baseline"]
        fold_candidate = row[path_name]["validation_metrics"]
        baseline_mae = _safe_float(fold_baseline.get("mae_change_pct"))
        candidate_mae = _safe_float(fold_candidate.get("mae_change_pct"))
        mae_not_worse = baseline_mae is not None and candidate_mae is not None and candidate_mae <= baseline_mae + 1e-12
        mae_non_worse_count += int(mae_not_worse)
        mae_catastrophe = (
            baseline_mae is not None
            and candidate_mae is not None
            and candidate_mae > baseline_mae * 1.10 + 1e-12
        )
        hit_loss = int(fold_baseline.get("hit_count") or 0) - int(fold_candidate.get("hit_count") or 0)
        catastrophic = mae_catastrophe or hit_loss > 1
        if catastrophic:
            catastrophic_folds.append(int(row["fold"]))
        fold_checks.append(
            {
                "fold": row["fold"],
                "mae_not_worse": mae_not_worse,
                "mae_deterioration_over_10pct": mae_catastrophe,
                "hit_loss_count": hit_loss,
                "catastrophic": catastrophic,
            }
        )
    robustness = _paired_robustness(baseline, candidate)
    checks = {
        "pooled_hit_count_not_lower": int(candidate.get("hit_count") or 0) >= int(baseline.get("hit_count") or 0),
        "pooled_mae_not_higher": (
            _safe_float(candidate.get("mae_change_pct")) is not None
            and _safe_float(baseline.get("mae_change_pct")) is not None
            and float(candidate["mae_change_pct"]) <= float(baseline["mae_change_pct"]) + 1e-12
        ),
        "pooled_p90_not_higher": (
            _safe_float(candidate.get("p90_change_abs_error_pct")) is not None
            and _safe_float(baseline.get("p90_change_abs_error_pct")) is not None
            and float(candidate["p90_change_abs_error_pct"]) <= float(baseline["p90_change_abs_error_pct"]) + 1e-12
        ),
        "availability_not_lower": float(candidate.get("available_rate") or 0.0) + 1e-12 >= float(baseline.get("available_rate") or 0.0),
        "mae_not_worse_in_at_least_two_folds": mae_non_worse_count >= 2,
        "no_catastrophic_fold": not catastrophic_folds,
        "improvement_not_driven_by_one_sample": robustness.get("trimmed_mae_not_higher") is True,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "fold_checks": fold_checks,
        "catastrophic_folds": catastrophic_folds,
        "baseline": baseline,
        "candidate": candidate,
        "paired_robustness": robustness,
    }


def _fixed_blocks(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    boundaries = [0, min(16, len(items)), min(30, len(items)), len(items)]
    blocks: list[dict[str, Any]] = []
    labels = ["早段", "中段", "近段"]
    for index, label in enumerate(labels):
        start = boundaries[index]
        end = boundaries[index + 1]
        block_items = items[start:end]
        if not block_items:
            continue
        blocks.append(
            {
                "label": label,
                "codes": [_item_code(item) for item in block_items],
                "date_start": _item_date(block_items[0]),
                "date_end": _item_date(block_items[-1]),
            }
        )
    return blocks


def _parameter_stability(
    base_params: dict[str, Any],
    full_overrides: dict[str, Any],
    fold_rows: list[dict[str, Any]],
    path_name: str,
) -> dict[str, Any]:
    fold_overrides = [dict(row[path_name].get("overrides") or {}) for row in fold_rows]
    keys = sorted(set(full_overrides) | {key for overrides in fold_overrides for key in overrides})
    rows: list[dict[str, Any]] = []
    direction_conflicts: list[str] = []
    stable_full_keys: list[str] = []
    for key in keys:
        base_value = base_params.get(key)
        full_value = full_overrides.get(key, base_value)
        fold_values = [overrides.get(key, base_value) for overrides in fold_overrides]
        same_as_full_count = sum(value == full_value for value in fold_values)
        directions: set[int] = set()
        if isinstance(base_value, (int, float)) and not isinstance(base_value, bool):
            if isinstance(full_value, (int, float)) and not isinstance(full_value, bool):
                full_delta = float(full_value) - float(base_value)
                if key in full_overrides and abs(full_delta) > 1e-12:
                    directions.add(1 if full_delta > 0 else -1)
            for value in fold_values:
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                delta = float(value) - float(base_value)
                if abs(delta) > 1e-12:
                    directions.add(1 if delta > 0 else -1)
        conflict = len(directions) > 1
        if conflict:
            direction_conflicts.append(key)
        if key in full_overrides and same_as_full_count >= 2:
            stable_full_keys.append(key)
        rows.append(
            {
                "parameter": key,
                "formal_value": base_value,
                "full_candidate_value": full_value,
                "fold_values": fold_values,
                "same_as_full_count": same_as_full_count,
                "direction_conflict": conflict,
            }
        )
    warning = bool(direction_conflicts) or (bool(full_overrides) and not stable_full_keys)
    return {
        "warning": warning,
        "direction_conflicts": direction_conflicts,
        "stable_full_candidate_keys": stable_full_keys,
        "rows": rows,
    }


def _fmt(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    return "-" if number is None else f"{number:.{digits}f}"


def _fmt_pct(value: Any) -> str:
    number = _safe_float(value)
    return "-" if number is None else f"{number:.2%}"


def _metrics_table_row(name: str, metrics: dict[str, Any]) -> str:
    return "| {name} | {hits}/{count} | {rate} | {mae} | {median} | {p90} | {bias} | {over}/{under} | {available} |".format(
        name=name,
        hits=metrics.get("hit_count", 0),
        count=metrics.get("price_eval_count", 0),
        rate=_fmt_pct(metrics.get("interval_hit_rate")),
        mae=_fmt(metrics.get("mae_change_pct")),
        median=_fmt(metrics.get("median_change_abs_error_pct")),
        p90=_fmt(metrics.get("p90_change_abs_error_pct")),
        bias=_fmt(metrics.get("mean_signed_error_pct")),
        over=metrics.get("overestimate_count", 0),
        under=metrics.get("underestimate_count", 0),
        available=_fmt_pct(metrics.get("available_rate")),
    )


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 估值自动调参时间切片稳定性复核",
        "",
        f"> 生成时间：{payload['generated_at']}",
        f"> 结论：**{payload['verdict']}**",
        f"> 样本：{payload['inputs']['sample_count']}；评估口径：`{payload['inputs']['evaluation_scope']}`；replay 缓存版本：`{payload['inputs']['replay_item_cache_version']}`",
        "",
        "## 一、复核口径",
        "",
        "- 当前全样本候选分段结果只用于检查市场阶段敏感性，不视为样本外证据。",
        "- 三折 walk-forward 每折只用历史前缀调参，冻结参数后评价下一时间段。",
        "- 后一折可使用前面已经上市并已产生真实结果的样本；同日上市样本不互读结果。",
        "- 单折仅 7—8 只，P90 只在三折合并样本上作硬门槛。",
        "",
        "## 二、三折边界",
        "",
        "| 折次 | 训练期 | 训练数 | 验证期 | 验证数 |",
        "|---|---|---:|---|---:|",
    ]
    for row in payload["folds"]:
        lines.append(
            f"| 第{row['fold']}折 | {row['train_date_start']}—{row['train_date_end']} | {row['train_count']} | {row['validation_date_start']}—{row['validation_date_end']} | {row['validation_count']} |"
        )
    lines.extend(
        [
            "",
            "## 三、当前全样本候选固定分段诊断",
            "",
            f"当前候选 overrides：`{payload['full_candidate']['overrides']}`。",
            "",
            "| 分段 | 日期 | 正式命中 | 候选命中 | 正式 MAE | 候选 MAE | 正式 P90 | 候选 P90 |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["fixed_candidate_blocks"]:
        baseline = row["baseline"]
        candidate = row["candidate"]
        lines.append(
            "| {label} | {start}—{end} | {bh}/{bc} | {ch}/{cc} | {bm} | {cm} | {bp} | {cp} |".format(
                label=row["label"],
                start=row["date_start"],
                end=row["date_end"],
                bh=baseline.get("hit_count"),
                bc=baseline.get("price_eval_count"),
                ch=candidate.get("hit_count"),
                cc=candidate.get("price_eval_count"),
                bm=_fmt(baseline.get("mae_change_pct")),
                cm=_fmt(candidate.get("mae_change_pct")),
                bp=_fmt(baseline.get("p90_change_abs_error_pct")),
                cp=_fmt(candidate.get("p90_change_abs_error_pct")),
            )
        )
    lines.extend(["", "## 四、逐折样本外结果", ""])
    for row in payload["folds"]:
        lines.extend(
            [
                f"### 第{row['fold']}折：{row['validation_date_start']}—{row['validation_date_end']}",
                "",
                f"- 核心参数：`{row['core']['overrides']}`。",
                f"- 两级参数：`{row['two_level']['overrides']}`。",
                "",
                "| 路径 | 命中 | 命中率 | MAE | 中位绝对误差 | P90 | 平均有符号误差 | 高估/低估 | 可用率 |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
                _metrics_table_row("正式参数", row["baseline"]),
                _metrics_table_row("核心调参", row["core"]["validation_metrics"]),
                _metrics_table_row("雪球两级排序", row["two_level"]["validation_metrics"]),
                "",
            ]
        )
    lines.extend(["## 五、三折合并样本外结果", ""])
    for path_name, label in (("core", "核心调参"), ("two_level", "雪球两级排序")):
        result = payload["acceptance"][path_name]
        lines.extend(
            [
                f"### {label}",
                "",
                "| 路径 | 命中 | 命中率 | MAE | 中位绝对误差 | P90 | 平均有符号误差 | 高估/低估 | 可用率 |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
                _metrics_table_row("正式参数", result["baseline"]),
                _metrics_table_row(label, result["candidate"]),
                "",
                f"- 合并门槛：`{'通过' if result['passed'] else '未通过'}`。",
                f"- 剔除最大单样本正贡献后，正式/候选 MAE：{_fmt(result['paired_robustness'].get('trimmed_baseline_mae'))} / {_fmt(result['paired_robustness'].get('trimmed_candidate_mae'))}。",
                f"- 配对 bootstrap：MAE 不劣概率 {_fmt_pct(result['paired_robustness'].get('bootstrap_probability_mae_not_higher'))}；命中只数不劣概率 {_fmt_pct(result['paired_robustness'].get('bootstrap_probability_hit_count_not_lower'))}。",
                "",
            ]
        )
    lines.extend(["## 六、验收门槛", ""])
    for path_name, label in (("core", "核心调参"), ("two_level", "雪球两级排序")):
        lines.append(f"### {label}")
        lines.append("")
        for check, passed in payload["acceptance"][path_name]["checks"].items():
            lines.append(f"- {'通过' if passed else '失败'}：{check}")
        lines.append("")
    lines.extend(["## 七、参数漂移", ""])
    stability = payload["parameter_stability"]["core"]
    lines.extend(
        [
            f"- 稳定复现全样本候选值的参数：`{stability['stable_full_candidate_keys']}`。",
            f"- 全样本候选/跨折方向冲突：`{stability['direction_conflicts']}`。",
            f"- 参数漂移警告：`{stability['warning']}`。",
            "",
            "| 参数 | 正式值 | 全样本候选 | 第1折 | 第2折 | 第3折 | 同全样本次数 | 方向冲突 |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in stability["rows"]:
        values = list(row["fold_values"]) + [None, None, None]
        lines.append(
            f"| {row['parameter']} | {row['formal_value']} | {row['full_candidate_value']} | {values[0]} | {values[1]} | {values[2]} | {row['same_as_full_count']} | {'是' if row['direction_conflict'] else '否'} |"
        )
    lines.extend(
        [
            "",
            "## 八、结论与边界",
            "",
            f"- 核心调参样本外门槛：`{payload['acceptance']['core']['passed']}`。",
            f"- 雪球两级排序样本外门槛：`{payload['acceptance']['two_level']['passed']}`。",
            f"- 参数漂移警告：`{payload['parameter_stability']['core']['warning']}`。",
            f"- 最终结论：{payload['verdict']}。",
            "- 本报告没有写入 `策略参数.txt`。42只样本仍然偏少，bootstrap 概率只作稳定性提示，不等同于传统统计显著性。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = Path(args.dataset)
    params_path = Path(args.params)
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    base_params = config_loader.load_params(params_path)
    items = _ordered_items(dataset)
    folds = build_anchored_folds(
        dataset,
        initial_train_size=args.initial_train_size,
        fold_size=args.fold_size,
        fold_count=args.fold_count,
    )

    if args.candidate_overrides_json:
        parsed_overrides = json.loads(args.candidate_overrides_json)
        if not isinstance(parsed_overrides, dict):
            raise ValueError("--candidate-overrides-json 必须是 JSON 对象")
        full_overrides = dict(parsed_overrides)
        provided_metrics = _evaluate(
            dataset,
            _params_with_overrides(base_params, full_overrides),
            [_item_code(item) for item in items],
        )
        full_core = {
            "path": "provided_candidate",
            "final_overrides": full_overrides,
            "final": _compact_metrics(provided_metrics),
            "stages": [],
        }
    else:
        full_core = _run_tuning_path(
            dataset,
            base_params,
            stages=args.stages,
            candidate_limit=args.candidate_limit,
            time_limit_seconds=args.time_limit_seconds,
            pool_size=args.pool_size,
            local_rerank=False,
        )
        full_overrides = dict(full_core.get("final_overrides") or {})
    full_candidate_params = _params_with_overrides(base_params, full_overrides)
    fixed_blocks: list[dict[str, Any]] = []
    for block in _fixed_blocks(items):
        baseline = _evaluate(dataset, base_params, block["codes"])
        candidate = _evaluate(dataset, full_candidate_params, block["codes"])
        fixed_blocks.append(
            {
                **block,
                "baseline": _compact_metrics(baseline),
                "candidate": _compact_metrics(candidate),
            }
        )

    fold_rows: list[dict[str, Any]] = []
    for fold in folds:
        row = _fold_result(
            dataset,
            base_params,
            fold,
            stages=args.stages,
            candidate_limit=args.candidate_limit,
            time_limit_seconds=args.time_limit_seconds,
            pool_size=args.pool_size,
        )
        fold_rows.append(row)

    acceptance = {
        "core": _path_acceptance(fold_rows, "core"),
        "two_level": _path_acceptance(fold_rows, "two_level"),
    }
    stability = {
        "core": _parameter_stability(base_params, full_overrides, fold_rows, "core"),
        "two_level": _parameter_stability(base_params, full_overrides, fold_rows, "two_level"),
    }
    if not acceptance["core"]["passed"]:
        verdict = "未通过：核心自动调参没有通过三折合并样本外门槛，不应写回当前候选"
    elif stability["core"]["warning"]:
        verdict = "有条件通过：样本外指标达标，但参数跨折漂移明显，建议继续观察而非立即写回"
    elif acceptance["two_level"]["passed"]:
        verdict = "通过：核心与两级路径通过样本外门槛，且未发现明显参数方向冲突"
    else:
        verdict = "核心通过、两级未通过：只可考虑核心候选，雪球二级排序不具备写回资格"

    payload = {
        "schema": "valuation_time_slice_stability_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "verdict": verdict,
        "inputs": {
            "dataset": str(dataset_path.resolve()),
            "params": str(params_path.resolve()),
            "sample_count": len(items),
            "evaluation_scope": dataset.get("evaluation_scope"),
            "replay_item_cache_version": dataset.get("replay_item_cache_version"),
            "initial_train_size": args.initial_train_size,
            "fold_size": args.fold_size,
            "fold_count": args.fold_count,
            "stages": args.stages,
            "candidate_limit": args.candidate_limit,
            "candidate_source": "provided" if args.candidate_overrides_json else "full_core_search",
            "dataset_sha256": _sha256(dataset_path),
            "params_sha256": _sha256(params_path),
            "source_sha256": {
                "code/param_tuning.py": _sha256(ROOT_DIR / "code" / "param_tuning.py"),
                "tools/local_learning_auto_rerank.py": _sha256(ROOT_DIR / "tools" / "local_learning_auto_rerank.py"),
                "tools/revalidate_valuation_time_slices.py": _sha256(Path(__file__)),
            },
        },
        "full_candidate": {
            "overrides": full_overrides,
            "training_metrics": full_core.get("final") or {},
            "stages": full_core.get("stages") or [],
        },
        "fixed_candidate_blocks": fixed_blocks,
        "folds": fold_rows,
        "acceptance": acceptance,
        "parameter_stability": stability,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"valuation_time_slice_stability_{timestamp}.json"
    markdown_path = output_dir / f"valuation_time_slice_stability_{timestamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_render_markdown(payload), encoding="utf-8")
    return {
        "verdict": verdict,
        "core_passed": acceptance["core"]["passed"],
        "two_level_passed": acceptance["two_level"]["passed"],
        "parameter_stability_warning": stability["core"]["warning"],
        "core_parameter_stability_warning": stability["core"]["warning"],
        "two_level_parameter_stability_warning": stability["two_level"]["warning"],
        "outputs": {"json": str(json_path.resolve()), "markdown": str(markdown_path.resolve())},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="估值自动调参三折时间切片稳定性复核（只读，不写参数）")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--params", default=str(DEFAULT_PARAMS))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--initial-train-size", type=int, default=20)
    parser.add_argument("--fold-size", type=int, default=7)
    parser.add_argument("--fold-count", type=int, default=3)
    parser.add_argument("--stages", type=int, default=3)
    parser.add_argument("--candidate-limit", type=int, default=650)
    parser.add_argument("--time-limit-seconds", type=float, default=180.0)
    parser.add_argument("--pool-size", type=int, default=20)
    parser.add_argument(
        "--candidate-overrides-json",
        default="",
        help="可选：直接诊断当前待写回候选，避免再次用全样本搜索候选。",
    )
    return parser


def main() -> int:
    result = run(build_parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
