from __future__ import annotations

import argparse
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
DEFAULT_SCAN_REPORT = ROOT_DIR / "调参" / "valuation_hit_rate_scan_202603plus_20260710_001437.json"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"

ALPHAS = [round(value / 10, 1) for value in range(1, 10)]
WIDTHS = [0.08, 0.10, 0.12, 0.15, 0.20]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _fmt_num(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:.{digits}f}"


def _fmt_pct(value: Any, digits: int = 1) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:.{digits}f}%"


def _fmt_rate(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number * 100:.1f}%"


def _calc_change_pct(issue_price: Any, target_price: Any) -> float | None:
    issue = _safe_float(issue_price)
    target = _safe_float(target_price)
    if not issue or target is None:
        return None
    return (target / issue - 1) * 100


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _rank(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i + 1
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + 1 + j) / 2
        for k in range(i, j):
            ranks[indexed[k][0]] = avg_rank
        i = j
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx = _mean(xs)
    my = _mean(ys)
    if mx is None or my is None:
        return None
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    sx = math.sqrt(sum(x * x for x in dx))
    sy = math.sqrt(sum(y * y for y in dy))
    if sx <= 0 or sy <= 0:
        return None
    return sum(x * y for x, y in zip(dx, dy)) / (sx * sy)


def _spearman(pairs: list[tuple[float, float]]) -> float | None:
    clean = [(x, y) for x, y in pairs if _safe_float(x) is not None and _safe_float(y) is not None]
    if len(clean) < 3:
        return None
    xs = [x for x, _ in clean]
    ys = [y for _, y in clean]
    return _pearson(_rank(xs), _rank(ys))


def _latest_author_score_report(output_dir: Path) -> Path:
    reports = sorted(output_dir.glob("xueqiu_author_rule_score_*.json"), key=lambda path: path.stat().st_mtime)
    if not reports:
        raise FileNotFoundError(f"未找到 author score 报告：{output_dir / 'xueqiu_author_rule_score_*.json'}")
    return reports[-1]


def _dataset_by_code(dataset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("SECURITY_CODE") or "").strip(): item
        for item in dataset.get("items") or []
        if str(item.get("SECURITY_CODE") or "").strip()
    }


def _actual_interval_price(item: dict[str, Any]) -> float | None:
    return param_tuning._actual_interval_price(item)


def _actual_interval_change_pct(item: dict[str, Any]) -> float | None:
    return param_tuning._actual_interval_change_pct(item)


def _index_model_predictions(metrics: dict[str, Any], model_name: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in metrics.get("available_results") or []:
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        result[code] = {
            "source": model_name,
            "available": True,
            "target": _safe_float(row.get("predicted_target_price")),
            "low": _safe_float(row.get("range_low")),
            "high": _safe_float(row.get("range_high")),
            "predicted_change_pct": _safe_float(row.get("predicted_change_pct")),
            "method1_available": bool(row.get("method1_available")),
            "method2_available": bool(row.get("method2_available")),
            "method3_available": bool(row.get("method3_available")),
        }
    for row in metrics.get("unavailable_results") or []:
        code = str(row.get("code") or "").strip()
        if not code or code in result:
            continue
        result[code] = {
            "source": model_name,
            "available": False,
            "reason": str(row.get("reason") or "").strip(),
        }
    return result


def _index_author_predictions(author_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in author_payload.get("rows") or []:
        code = str(row.get("code") or "").strip()
        target = _safe_float(row.get("weighted_mid"))
        low = _safe_float(row.get("weighted_low"))
        high = _safe_float(row.get("weighted_high"))
        if not code or target is None or low is None or high is None:
            continue
        result[code] = {
            "source": "author_rule",
            "available": True,
            "target": target,
            "low": low,
            "high": high,
            "fixed10_low": target * 0.9,
            "fixed10_high": target * 1.1,
            "score_pct": _safe_float(row.get("author_score_pct")),
            "phrase_score_pct": _safe_float(row.get("score_with_phrases_pct")),
            "authors": list(row.get("explicit_authors") or row.get("authors") or []),
            "evidence_count": row.get("explicit_evidence_count"),
        }
    return result


def _blend(a: float | None, b: float | None, alpha: float) -> float | None:
    if a is None or b is None:
        return None
    return a * (1 - alpha) + b * alpha


def _prediction_for_candidate(
    code: str,
    candidate: dict[str, Any],
    model_predictions: dict[str, dict[str, dict[str, Any]]],
    author_predictions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    kind = candidate["kind"]
    model_name = candidate.get("model")
    model = (model_predictions.get(model_name or "") or {}).get(code)
    author = author_predictions.get(code)

    if kind == "model_only":
        if model and model.get("available"):
            return {**model, "rule": candidate["name"]}
        return {"available": False, "reason": (model or {}).get("reason") or "model unavailable"}

    if kind == "author_fixed10":
        if author:
            return {
                "available": True,
                "source": "author_rule",
                "rule": candidate["name"],
                "target": author["target"],
                "low": author["fixed10_low"],
                "high": author["fixed10_high"],
                "authors": author.get("authors") or [],
            }
        return {"available": False, "reason": "author unavailable"}

    if kind == "author_weighted_interval":
        if author:
            return {
                "available": True,
                "source": "author_rule",
                "rule": candidate["name"],
                "target": author["target"],
                "low": author["low"],
                "high": author["high"],
                "authors": author.get("authors") or [],
            }
        return {"available": False, "reason": "author unavailable"}

    alpha = float(candidate.get("alpha", 0.5))
    fallback = bool(candidate.get("fallback_to_author"))
    width = _safe_float(candidate.get("width"))
    model_available = bool(model and model.get("available"))
    author_available = author is not None

    if model_available and author_available:
        target = _blend(_safe_float(model.get("target")), _safe_float(author.get("target")), alpha)
        if target is None:
            return {"available": False, "reason": "blend target unavailable"}
        if kind == "blend_bounds":
            low = _blend(_safe_float(model.get("low")), _safe_float(author.get("low")), alpha)
            high = _blend(_safe_float(model.get("high")), _safe_float(author.get("high")), alpha)
        else:
            use_width = width if width is not None else 0.10
            low = target * (1 - use_width)
            high = target * (1 + use_width)
        if low is None or high is None:
            return {"available": False, "reason": "blend range unavailable"}
        return {
            "available": True,
            "source": f"{model_name}+author",
            "rule": candidate["name"],
            "target": target,
            "low": min(low, high),
            "high": max(low, high),
            "model_target": model.get("target"),
            "author_target": author.get("target"),
            "authors": author.get("authors") or [],
        }

    if model_available:
        return {**model, "rule": candidate["name"], "fallback_source": "model_only_no_author"}

    if fallback and author_available:
        if kind == "blend_bounds":
            return {
                "available": True,
                "source": "author_rule_fallback",
                "rule": candidate["name"],
                "target": author["target"],
                "low": author["low"],
                "high": author["high"],
                "authors": author.get("authors") or [],
            }
        use_width = width if width is not None else 0.10
        return {
            "available": True,
            "source": "author_rule_fallback",
            "rule": candidate["name"],
            "target": author["target"],
            "low": author["target"] * (1 - use_width),
            "high": author["target"] * (1 + use_width),
            "authors": author.get("authors") or [],
        }

    return {"available": False, "reason": (model or {}).get("reason") or "model and author unavailable"}


def _evaluate_candidate(
    candidate: dict[str, Any],
    target_codes: list[str],
    dataset_by_code: dict[str, dict[str, Any]],
    model_predictions: dict[str, dict[str, dict[str, Any]]],
    author_predictions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    hit_count = 0
    available_count = 0
    price_errors: list[float] = []
    change_errors: list[float] = []
    signed_change_errors: list[float] = []
    rank_pairs: list[tuple[float, float]] = []

    for code in target_codes:
        item = dataset_by_code.get(code, {})
        actual_price = _actual_interval_price(item)
        actual_change = _actual_interval_change_pct(item)
        issue_price = _safe_float(item.get("ISSUE_PRICE"))
        prediction = _prediction_for_candidate(code, candidate, model_predictions, author_predictions)
        target = _safe_float(prediction.get("target"))
        low = _safe_float(prediction.get("low"))
        high = _safe_float(prediction.get("high"))
        predicted_change = _calc_change_pct(issue_price, target)
        available = bool(prediction.get("available") and target is not None and low is not None and high is not None)
        hit = None
        if available:
            available_count += 1
            if actual_price is not None:
                hit = low <= actual_price <= high
                hit_count += int(hit)
                price_errors.append(abs(target - actual_price))
            if predicted_change is not None and actual_change is not None:
                change_error = predicted_change - actual_change
                change_errors.append(abs(change_error))
                signed_change_errors.append(change_error)
                rank_pairs.append((predicted_change, actual_change))

        rows.append(
            {
                "code": code,
                "name": item.get("SECURITY_NAME_ABBR"),
                "listing_date": item.get("LISTING_DATE"),
                "issue_price": issue_price,
                "actual_price": actual_price,
                "actual_change_pct": actual_change,
                "available": available,
                "reason": "" if available else str(prediction.get("reason") or ""),
                "source": prediction.get("source"),
                "target_price": target,
                "predicted_change_pct": predicted_change,
                "range_low": low,
                "range_high": high,
                "interval_hit": hit,
                "price_abs_error": abs(target - actual_price) if available and actual_price is not None else None,
                "change_abs_error": abs(predicted_change - actual_change) if available and predicted_change is not None and actual_change is not None else None,
                "model_target": prediction.get("model_target"),
                "author_target": prediction.get("author_target"),
                "authors": prediction.get("authors") or [],
            }
        )

    target_count = len(target_codes)
    full_hit_rate = hit_count / target_count if target_count else None
    available_hit_rate = hit_count / available_count if available_count else None
    return {
        "candidate": candidate,
        "target_count": target_count,
        "available_count": available_count,
        "unavailable_count": target_count - available_count,
        "hit_count": hit_count,
        "full_hit_rate": full_hit_rate,
        "available_hit_rate": available_hit_rate,
        "mae_target_price": _mean(price_errors),
        "mae_change_pct": _mean(change_errors),
        "mean_signed_change_error_pct": _mean(signed_change_errors),
        "spearman_predicted_vs_actual_change": _spearman(rank_pairs),
        "rows": rows,
        "hit_codes": [row["code"] for row in rows if row.get("interval_hit") is True],
        "miss_codes": [row["code"] for row in rows if row.get("interval_hit") is False],
        "unavailable_codes": [row["code"] for row in rows if not row.get("available")],
    }


def _candidate_sort_key(result: dict[str, Any]) -> tuple[float, float, float, float, str]:
    return (
        -(result.get("full_hit_rate") or 0.0),
        -(result.get("available_hit_rate") or 0.0),
        result.get("mae_change_pct") if result.get("mae_change_pct") is not None else 1e9,
        result.get("mae_target_price") if result.get("mae_target_price") is not None else 1e9,
        str((result.get("candidate") or {}).get("name") or ""),
    )


def _is_fixed10_candidate(result: dict[str, Any]) -> bool:
    candidate = result.get("candidate") or {}
    if candidate.get("name") in {"current_params", "scan_best", "author_fixed10"}:
        return True
    return candidate.get("kind") == "blend_fixed_width" and abs(float(candidate.get("width") or 0.0) - 0.10) < 1e-9


def _is_no_fallback_candidate(result: dict[str, Any]) -> bool:
    candidate = result.get("candidate") or {}
    return not bool(candidate.get("fallback_to_author"))


def _best_from(results: list[dict[str, Any]], predicate) -> dict[str, Any] | None:
    filtered = [result for result in results if predicate(result)]
    if not filtered:
        return None
    filtered.sort(key=_candidate_sort_key)
    return filtered[0]


def _build_candidates(model_names: list[str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = [
        {"name": "author_fixed10", "kind": "author_fixed10"},
        {"name": "author_weighted_interval", "kind": "author_weighted_interval"},
    ]
    for model_name in model_names:
        candidates.append({"name": model_name, "kind": "model_only", "model": model_name})
        for alpha in ALPHAS:
            for width in WIDTHS:
                for fallback in (False, True):
                    candidates.append(
                        {
                            "name": f"{model_name}_author_blend_a{alpha:.1f}_w{width:.2f}" + ("_fallback" if fallback else ""),
                            "kind": "blend_fixed_width",
                            "model": model_name,
                            "alpha": alpha,
                            "width": width,
                            "fallback_to_author": fallback,
                        }
                    )
            for fallback in (False, True):
                candidates.append(
                    {
                        "name": f"{model_name}_author_blend_bounds_a{alpha:.1f}" + ("_fallback" if fallback else ""),
                        "kind": "blend_bounds",
                        "model": model_name,
                        "alpha": alpha,
                        "fallback_to_author": fallback,
                    }
                )
    return candidates


def _compact_result(result: dict[str, Any], include_rows: bool = False) -> dict[str, Any]:
    payload = {
        "candidate": result["candidate"],
        "target_count": result["target_count"],
        "available_count": result["available_count"],
        "unavailable_count": result["unavailable_count"],
        "hit_count": result["hit_count"],
        "full_hit_rate": result["full_hit_rate"],
        "available_hit_rate": result["available_hit_rate"],
        "mae_target_price": result["mae_target_price"],
        "mae_change_pct": result["mae_change_pct"],
        "mean_signed_change_error_pct": result["mean_signed_change_error_pct"],
        "spearman_predicted_vs_actual_change": result["spearman_predicted_vs_actual_change"],
        "hit_codes": result["hit_codes"],
        "miss_codes": result["miss_codes"],
        "unavailable_codes": result["unavailable_codes"],
    }
    if include_rows:
        payload["rows"] = result["rows"]
    return payload


def _top_bucket_summary(rows: list[dict[str, Any]], bucket_size: int = 10) -> dict[str, Any]:
    evaluated = [
        row for row in rows
        if _safe_float(row.get("predicted_change_pct")) is not None and _safe_float(row.get("actual_change_pct")) is not None
    ]
    if not evaluated:
        return {}
    ordered = sorted(evaluated, key=lambda row: _safe_float(row.get("predicted_change_pct")) or 0.0)
    size = min(bucket_size, max(1, len(ordered) // 3 if len(ordered) >= 9 else len(ordered)))
    low = ordered[:size]
    high = ordered[-size:]
    return {
        "bucket_size": size,
        "low_bucket_codes": [row["code"] for row in low],
        "high_bucket_codes": [row["code"] for row in high],
        "low_bucket_avg_actual_change_pct": _mean([row["actual_change_pct"] for row in low if row.get("actual_change_pct") is not None]),
        "low_bucket_median_actual_change_pct": _median([row["actual_change_pct"] for row in low if row.get("actual_change_pct") is not None]),
        "high_bucket_avg_actual_change_pct": _mean([row["actual_change_pct"] for row in high if row.get("actual_change_pct") is not None]),
        "high_bucket_median_actual_change_pct": _median([row["actual_change_pct"] for row in high if row.get("actual_change_pct") is not None]),
    }


def _build_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    best = payload["best_result"]
    references = summary["reference_results"]
    constrained = summary.get("constrained_bests") or {}
    lines = [
        "# 雪球 Author-Rule 与本地估值组合验证",
        "",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 目标样本：`{payload['target_universe']['name']}`，代码数 `{payload['target_universe']['count']}`",
        f"- author score 报告：`{payload['inputs']['author_score_report']}`",
        "",
        "## 核心结论",
        "",
        f"- 最优组合：`{best['candidate']['name']}`",
        f"- 最优组合命中：`{best['hit_count']}/{best['target_count']}`，全样本命中率 `{_fmt_rate(best['full_hit_rate'])}`，可用样本命中率 `{_fmt_rate(best['available_hit_rate'])}`",
        f"- 最优组合可用：`{best['available_count']}/{best['target_count']}`",
        f"- 最优组合 MAE(涨幅)：`{_fmt_num(best.get('mae_change_pct'), 2)}`，Spearman：`{_fmt_num(best.get('spearman_predicted_vs_actual_change'), 3)}`",
        "",
        "## 约束口径",
        "",
        "| 约束 | 方案 | 命中 | 全样本命中率 | 可用样本命中率 | 可用数 | MAE(涨幅) | Spearman |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, item in (
        ("固定 ±10%", constrained.get("best_fixed10")),
        ("不启用作者兜底", constrained.get("best_no_fallback")),
        ("固定 ±10% 且不兜底", constrained.get("best_fixed10_no_fallback")),
    ):
        if not item:
            continue
        lines.append(
            "| {label} | `{name}` | {hit}/{target} | {full} | {avail_rate} | {available}/{target} | {mae} | {spear} |".format(
                label=label,
                name=item["candidate"]["name"],
                hit=item["hit_count"],
                target=item["target_count"],
                full=_fmt_rate(item.get("full_hit_rate")),
                avail_rate=_fmt_rate(item.get("available_hit_rate")),
                available=item["available_count"],
                mae=_fmt_num(item.get("mae_change_pct"), 2),
                spear=_fmt_num(item.get("spearman_predicted_vs_actual_change"), 3),
            )
        )
    lines.extend(
        [
            "",
        "## 基准对照",
        "",
        "| 方案 | 命中 | 全样本命中率 | 可用样本命中率 | 可用数 | MAE(涨幅) | Spearman |",
        "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in references:
        lines.append(
            "| {name} | {hit}/{target} | {full} | {avail_rate} | {available}/{target} | {mae} | {spear} |".format(
                name=item["candidate"]["name"],
                hit=item["hit_count"],
                target=item["target_count"],
                full=_fmt_rate(item.get("full_hit_rate")),
                avail_rate=_fmt_rate(item.get("available_hit_rate")),
                available=item["available_count"],
                mae=_fmt_num(item.get("mae_change_pct"), 2),
                spear=_fmt_num(item.get("spearman_predicted_vs_actual_change"), 3),
            )
        )
    scan_ref = summary.get("scan_report_reference") or {}
    lines.extend(
        [
            "",
            "扫描报告原始参考：",
            "",
            f"- 原 baseline：`{scan_ref.get('baseline_hit_count')}/{scan_ref.get('sample_count')}`，全样本命中率 `{_fmt_rate(scan_ref.get('baseline_full_hit_rate'))}`。",
            f"- 原扫描最优：`{scan_ref.get('best_hit_count')}/{scan_ref.get('sample_count')}`，全样本命中率 `{_fmt_rate(scan_ref.get('best_full_hit_rate'))}`。",
            "",
            "## Top 组合",
            "",
            "| 排名 | 方案 | 命中 | 全样本命中率 | 可用命中率 | 可用数 | MAE(涨幅) | Spearman |",
            "|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for index, item in enumerate(payload["top_results"], start=1):
        lines.append(
            "| {rank} | `{name}` | {hit}/{target} | {full} | {avail_rate} | {available}/{target} | {mae} | {spear} |".format(
                rank=index,
                name=item["candidate"]["name"],
                hit=item["hit_count"],
                target=item["target_count"],
                full=_fmt_rate(item.get("full_hit_rate")),
                avail_rate=_fmt_rate(item.get("available_hit_rate")),
                available=item["available_count"],
                mae=_fmt_num(item.get("mae_change_pct"), 2),
                spear=_fmt_num(item.get("spearman_predicted_vs_actual_change"), 3),
            )
        )
    bucket = summary.get("best_rank_bucket") or {}
    lines.extend(
        [
            "",
            "## 最优组合排序分层",
            "",
            f"- 低分组平均/中位实际涨幅：`{_fmt_pct(bucket.get('low_bucket_avg_actual_change_pct'))}` / `{_fmt_pct(bucket.get('low_bucket_median_actual_change_pct'))}`",
            f"- 高分组平均/中位实际涨幅：`{_fmt_pct(bucket.get('high_bucket_avg_actual_change_pct'))}` / `{_fmt_pct(bucket.get('high_bucket_median_actual_change_pct'))}`",
            f"- 低分组代码：`{', '.join(bucket.get('low_bucket_codes') or [])}`",
            f"- 高分组代码：`{', '.join(bucket.get('high_bucket_codes') or [])}`",
            "",
            "## 最优组合逐样本",
            "",
            "| 代码 | 简称 | 来源 | 实际均价 | 预测中枢 | 区间 | 命中 | 实际涨幅 | 预测涨幅 | 作者 |",
            "|---|---|---|---:|---:|---:|---|---:|---:|---|",
        ]
    )
    for row in best.get("rows") or []:
        hit = "" if row.get("interval_hit") is None else ("是" if row.get("interval_hit") else "否")
        lines.append(
            "| {code} | {name} | {source} | {actual} | {target} | {low}-{high} | {hit} | {actual_chg} | {pred_chg} | {authors} |".format(
                code=row.get("code"),
                name=row.get("name") or "",
                source=row.get("source") or row.get("reason") or "",
                actual=_fmt_num(row.get("actual_price")),
                target=_fmt_num(row.get("target_price")),
                low=_fmt_num(row.get("range_low")),
                high=_fmt_num(row.get("range_high")),
                hit=hit,
                actual_chg=_fmt_pct(row.get("actual_change_pct")),
                pred_chg=_fmt_pct(row.get("predicted_change_pct")),
                authors="、".join(row.get("authors") or []),
            )
        )
    lines.extend(
        [
            "",
            "## 口径",
            "",
            "- 组合验证只读本地 replay 数据、调参扫描报告和已采集的雪球 author score，不写回正式参数。",
            "- `alpha` 表示作者中枢权重；`fallback` 表示当本地模型不可用时允许使用作者区间兜底。",
            "- 全样本命中率按目标样本代码总数作分母；可用样本命中率只按该方案能给出区间的样本作分母。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset = param_tuning.load_replay_dataset(args.dataset)
    dataset_items = _dataset_by_code(dataset)
    params = config_loader.load_params(args.params)
    scan_report = _read_json(Path(args.scan_report))
    author_report_path = Path(args.author_score_report) if args.author_score_report else _latest_author_score_report(Path(args.output_dir))
    author_payload = _read_json(author_report_path)
    author_predictions = _index_author_predictions(author_payload)

    target_codes = [str(code) for code in scan_report.get("sample_codes") or []]
    if args.target == "author_scored":
        target_codes = [code for code in target_codes if code in author_predictions]
    elif args.target == "all_actual":
        target_codes = [
            code for code, item in dataset_items.items()
            if _actual_interval_price(item) is not None
        ]

    best_overrides = dict(((scan_report.get("top_candidates") or [{}])[0]).get("overrides") or {})
    current_metrics = param_tuning.evaluate_replay_targets(dataset, params, target_codes=target_codes)
    best_params = dict(params)
    best_params.update(best_overrides)
    scan_best_metrics = param_tuning.evaluate_replay_targets(dataset, best_params, target_codes=target_codes)

    model_predictions = {
        "current_params": _index_model_predictions(current_metrics, "current_params"),
        "scan_best": _index_model_predictions(scan_best_metrics, "scan_best"),
    }
    candidates = _build_candidates(["current_params", "scan_best"])
    evaluated = [
        _evaluate_candidate(candidate, target_codes, dataset_items, model_predictions, author_predictions)
        for candidate in candidates
    ]
    evaluated.sort(key=_candidate_sort_key)
    best = evaluated[0]
    best_fixed10 = _best_from(evaluated, _is_fixed10_candidate)
    best_no_fallback = _best_from(evaluated, _is_no_fallback_candidate)
    best_fixed10_no_fallback = _best_from(evaluated, lambda result: _is_fixed10_candidate(result) and _is_no_fallback_candidate(result))
    reference_names = {"current_params", "scan_best", "author_fixed10", "author_weighted_interval"}
    reference_results = [
        _compact_result(result)
        for result in evaluated
        if result["candidate"]["name"] in reference_names
    ]
    reference_results.sort(key=lambda item: ["current_params", "scan_best", "author_fixed10", "author_weighted_interval"].index(item["candidate"]["name"]))

    scan_baseline = scan_report.get("baseline") or {}
    scan_best = (scan_report.get("top_candidates") or [{}])[0]
    scan_ref = {
        "sample_count": scan_report.get("sample_count"),
        "baseline_hit_count": (scan_baseline.get("exact_score") or {}).get("hit_count"),
        "baseline_full_hit_rate": (scan_baseline.get("exact_score") or {}).get("full_hit_rate"),
        "baseline_available_hit_rate": (scan_baseline.get("exact_score") or {}).get("available_hit_rate"),
        "best_hit_count": ((scan_best.get("exact_score") or scan_best.get("rough_score") or {}).get("hit_count")),
        "best_full_hit_rate": ((scan_best.get("exact_score") or scan_best.get("rough_score") or {}).get("full_hit_rate")),
        "best_available_hit_rate": ((scan_best.get("exact_score") or scan_best.get("rough_score") or {}).get("available_hit_rate")),
        "best_overrides": best_overrides,
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_dir = Path(args.output_dir)
    payload = {
        "schema": "xueqiu_author_model_blend_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {
            "dataset": str(Path(args.dataset)),
            "params": str(Path(args.params)),
            "scan_report": str(Path(args.scan_report)),
            "author_score_report": str(author_report_path),
        },
        "target_universe": {
            "name": args.target,
            "count": len(target_codes),
            "codes": target_codes,
        },
        "summary": {
            "scan_report_reference": scan_ref,
            "reference_results": reference_results,
            "constrained_bests": {
                "best_fixed10": _compact_result(best_fixed10) if best_fixed10 else None,
                "best_no_fallback": _compact_result(best_no_fallback) if best_no_fallback else None,
                "best_fixed10_no_fallback": _compact_result(best_fixed10_no_fallback) if best_fixed10_no_fallback else None,
            },
            "best_rank_bucket": _top_bucket_summary(best["rows"]),
        },
        "best_result": _compact_result(best, include_rows=True),
        "top_results": [_compact_result(result) for result in evaluated[:20]],
        "all_results": [_compact_result(result) for result in evaluated],
    }
    json_path = output_dir / f"xueqiu_author_model_blend_{args.target}_{timestamp}.json"
    md_path = output_dir / f"xueqiu_author_model_blend_{args.target}_{timestamp}.md"
    _write_json(json_path, payload)
    md_path.write_text(_build_markdown(payload), encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "markdown": str(md_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate Xueqiu author-rule score blended with local valuation model.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--params", default=str(DEFAULT_PARAMS))
    parser.add_argument("--scan-report", default=str(DEFAULT_SCAN_REPORT))
    parser.add_argument("--author-score-report", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--target", choices=["scan_sample", "author_scored", "all_actual"], default="scan_sample")
    return parser


def main() -> None:
    payload = run(build_parser().parse_args())
    best = payload["best_result"]
    print(
        json.dumps(
            {
                "outputs": payload["outputs"],
                "target_universe": payload["target_universe"],
                "best": {
                    "candidate": best["candidate"],
                    "hit_count": best["hit_count"],
                    "target_count": best["target_count"],
                    "available_count": best["available_count"],
                    "full_hit_rate": best["full_hit_rate"],
                    "available_hit_rate": best["available_hit_rate"],
                    "mae_change_pct": best["mae_change_pct"],
                    "spearman": best["spearman_predicted_vs_actual_change"],
                },
                "references": payload["summary"]["reference_results"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
