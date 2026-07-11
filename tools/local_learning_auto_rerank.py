from __future__ import annotations

import importlib.util
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import param_tuning


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proxy = _load_module("evaluate_local_proxy_strategy_for_auto_rerank", ROOT_DIR / "tools" / "evaluate_local_proxy_strategy.py")
distill = proxy.distill
blend = proxy.blend


DEFAULT_WEIGHTS = {
    "core": 0.45,
    "conservative": 0.35,
    "regime": 0.15,
    "rolling": 0.05,
}
WIDTH_BUDGET = 0.17
LOCAL_MAE_PENALTY = 0.0015
UNAVAILABLE_WEIGHT_PENALTY = 0.10


CONSERVATIVE_STRATEGY = {
    "name": "auto_candidate_model_conservative_recent_mood",
    "model": "scan_best",
    "center_policy": "model",
    "center_condition": "never",
    "center_alpha": 0.0,
    "width_policy": "conservative",
    "fallback_policy": "recent_mood",
    "research_only": False,
}
REGIME_STRATEGY = {
    **CONSERVATIVE_STRATEGY,
    "name": "auto_candidate_model_conservative_recent_mood_regime_blend50",
    "fallback_policy": "recent_mood_regime_blend50",
}
ROLLING_STRATEGY = {
    "name": "auto_candidate_all_rolling50_layered_v1_recent_mood",
    "model": "scan_best",
    "center_policy": "all_rolling50",
    "center_condition": "all",
    "center_alpha": 0.50,
    "width_policy": "layered_v1",
    "fallback_policy": "recent_mood",
    "research_only": True,
}


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _candidate_signature(entry: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), repr(value)) for key, value in dict(entry.get("overrides") or {}).items()))


def _reference_date(result: dict[str, Any], dataset: dict[str, Any]) -> date:
    parsed = param_tuning._parse_date(result.get("reference_date"))
    if parsed is not None:
        return parsed
    dates = [param_tuning._parse_date(item.get("LISTING_DATE")) for item in dataset.get("items") or []]
    valid = [item for item in dates if item is not None]
    return max(valid) if valid else date.today()


def _history_and_target_codes(dataset: dict[str, Any], reference_date: date) -> tuple[list[str], list[str]]:
    rows: list[tuple[date, str]] = []
    for item in dataset.get("items") or []:
        code = str(item.get("SECURITY_CODE") or "").strip()
        listing_date = param_tuning._parse_date(item.get("LISTING_DATE"))
        if not code or listing_date is None or listing_date > reference_date:
            continue
        if param_tuning._actual_interval_price(item) is None:
            continue
        rows.append((listing_date, code))
    rows.sort(key=lambda item: (item[0], item[1]))
    history_codes = [code for _, code in rows]
    target_codes = [
        code
        for listing_date, code in rows
        if 0 <= (reference_date - listing_date).days < param_tuning.AUTO_TUNE_LOOKBACK_DAYS
    ]
    return history_codes, target_codes


def _candidate_pool(result: dict[str, Any], pool_size: int) -> list[dict[str, Any]]:
    candidates = [
        result.get("baseline") or {},
        result.get("stage_start") or {},
        result.get("best") or {},
        *(result.get("top_candidates") or [])[: max(pool_size, 0)],
    ]
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for entry in candidates:
        if not entry or not isinstance(entry.get("metrics"), dict):
            continue
        signature = _candidate_signature(entry)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(entry)
    return deduped


def _attach_walk_forward_proxy_features(rows: list[dict[str, Any]], params: dict[str, Any]) -> None:
    ordered = sorted(rows, key=lambda row: (str(row.get("listing_date") or ""), str(row.get("code") or "")))
    completed: list[dict[str, Any]] = []
    index = 0
    while index < len(ordered):
        listing_date = str(ordered[index].get("listing_date") or "")[:10]
        group: list[dict[str, Any]] = []
        while index < len(ordered) and str(ordered[index].get("listing_date") or "")[:10] == listing_date:
            group.append(ordered[index])
            index += 1

        threshold_rows = completed if len(completed) >= 4 else completed + group
        thresholds = distill._build_thresholds(threshold_rows)
        for row in group:
            score, components, reasons = distill._score_proxy(row, thresholds)
            row["proxy_score"] = score
            row["proxy_components"] = components
            row["proxy_reasons"] = reasons
            row["model_uncertainty_score"] = components.get("model_uncertainty")

        reference = completed + group
        reference_scores = [
            float(value)
            for value in (_safe_float(row.get("proxy_score")) for row in reference)
            if value is not None
        ]
        denominator = max(len(reference_scores) - 1, 1)
        for row in group:
            score = _safe_float(row.get("proxy_score"))
            if score is None or not reference_scores:
                row["proxy_rank_pct"] = None
                row["proxy_tier"] = "unknown"
                continue
            lower = sum(value < score for value in reference_scores)
            equal = sum(value == score for value in reference_scores)
            average_zero_based_rank = lower + max(equal - 1, 0) / 2
            rank_pct = average_zero_based_rank / denominator
            row["proxy_rank_pct"] = rank_pct
            if rank_pct <= 1 / 3:
                row["proxy_tier"] = "low"
            elif rank_pct <= 2 / 3:
                row["proxy_tier"] = "mid"
            else:
                row["proxy_tier"] = "high"
        completed.extend(group)

    # Rebuild rolling estimates by date so same-day samples cannot use each other's outcomes.
    completed = []
    baseline = _safe_float(params.get("sentiment_first_day_baseline_pct"))
    index = 0
    while index < len(ordered):
        listing_date = str(ordered[index].get("listing_date") or "")[:10]
        group = []
        while index < len(ordered) and str(ordered[index].get("listing_date") or "")[:10] == listing_date:
            group.append(ordered[index])
            index += 1
        for row in group:
            score = _safe_float(row.get("proxy_score")) or 0.0
            predicted, source = distill._linear_proxy_prediction(completed, score)
            if predicted is None:
                predicted = distill._fallback_base_change(row, params)
                source = "recent_mood_or_param_baseline" if predicted is not None else source
            row["rolling_proxy_expected_change_pct"] = predicted
            row["rolling_proxy_expected_source"] = source
            row["rolling_proxy_history_count"] = len(completed)
            if predicted is None and baseline is not None:
                row["rolling_proxy_expected_change_pct"] = baseline
                row["rolling_proxy_expected_source"] = "param_baseline"
        completed.extend(row for row in group if _safe_float(row.get("actual_change_pct")) is not None)


def _weighted_line_summary(line_result: dict[str, Any], reference_date: date) -> dict[str, Any]:
    rows = list(line_result.get("rows") or [])
    weighted, weight_summary = param_tuning._build_auto_weighted_results(
        rows,
        reference_date,
        use_recency_weight=True,
    )
    recency_fallback_used = False
    if not weighted:
        weighted, weight_summary = param_tuning._build_auto_weighted_results(
            rows,
            reference_date,
            use_recency_weight=False,
        )
        recency_fallback_used = True

    total_weight = sum(weight for _, weight in weighted)
    available_weight = sum(weight for row, weight in weighted if row.get("available"))
    hit_weight = sum(weight for row, weight in weighted if row.get("interval_hit") is True)
    error_items = [
        (abs(float(row["predicted_change_pct"]) - float(row["actual_change_pct"])), weight)
        for row, weight in weighted
        if _safe_float(row.get("predicted_change_pct")) is not None
        and _safe_float(row.get("actual_change_pct")) is not None
    ]
    error_weight = sum(weight for _, weight in error_items)
    weighted_mae = sum(error * weight for error, weight in error_items) / error_weight if error_weight else None
    width_items = [
        (float(row["dynamic_width"]), weight)
        for row, weight in weighted
        if row.get("available") and _safe_float(row.get("dynamic_width")) is not None
    ]
    width_weight = sum(weight for _, weight in width_items)
    avg_width = sum(width * weight for width, weight in width_items) / width_weight if width_weight else None
    hit_rate = hit_weight / total_weight if total_weight else 0.0
    available_rate = available_weight / total_weight if total_weight else 0.0
    width_penalty = max((avg_width or WIDTH_BUDGET) - WIDTH_BUDGET, 0.0)
    mae_penalty = (weighted_mae or 0.0) * LOCAL_MAE_PENALTY
    unavailable_penalty = max(1.0 - available_rate, 0.0) * UNAVAILABLE_WEIGHT_PENALTY
    local_score = hit_rate - mae_penalty - width_penalty - unavailable_penalty
    return {
        "score": local_score,
        "weighted_interval_hit_rate": hit_rate,
        "weighted_hit_score": hit_weight,
        "weighted_mae_change_pct": weighted_mae,
        "weighted_available_rate": available_rate,
        "weighted_avg_width": avg_width,
        "width_budget": WIDTH_BUDGET,
        "width_penalty": width_penalty,
        "mae_penalty": mae_penalty,
        "unavailable_penalty": unavailable_penalty,
        "recent_weight_share": weight_summary.get("recent_weight_share", 0.0),
        "recent_floor_applied": bool(weight_summary.get("recent_floor_applied")),
        "recency_fallback_used": recency_fallback_used,
        "target_count": line_result.get("target_count"),
        "available_count": line_result.get("available_count"),
        "hit_count": line_result.get("hit_count"),
        "fallback_count": line_result.get("fallback_count"),
        "fallback_hit_count": line_result.get("fallback_hit_count"),
    }


def _evaluate_candidate(
    dataset: dict[str, Any],
    base_params: dict[str, Any],
    baseline_metrics: dict[str, Any],
    entry: dict[str, Any],
    history_codes: list[str],
    target_codes: list[str],
    reference_date: date,
    weights: dict[str, float],
) -> dict[str, Any]:
    candidate_params = dict(base_params)
    candidate_params.update(dict(entry.get("overrides") or {}))
    by_code = distill._dataset_by_code(dataset)
    model_predictions = {
        "current_params": blend._index_model_predictions(baseline_metrics, "current_params"),
        "scan_best": blend._index_model_predictions(entry.get("metrics") or {}, "scan_best"),
    }
    # Author inputs stay empty by design. The reranker only reuses local proxy rules learned earlier.
    history_rows = distill._build_teacher_rows(
        history_codes,
        list(dataset.get("items") or []),
        by_code,
        model_predictions,
        {},
        candidate_params,
    )
    _attach_walk_forward_proxy_features(history_rows, candidate_params)
    proxy._attach_regime_break_context(history_rows, candidate_params)
    history_by_code = {row["code"]: row for row in history_rows}
    target_rows = [dict(history_by_code[code]) for code in target_codes if code in history_by_code]

    conservative = _weighted_line_summary(
        proxy._evaluate_strategy(dict(CONSERVATIVE_STRATEGY), target_rows, candidate_params),
        reference_date,
    )
    regime = _weighted_line_summary(
        proxy._evaluate_strategy(dict(REGIME_STRATEGY), target_rows, candidate_params),
        reference_date,
    )
    rolling = _weighted_line_summary(
        proxy._evaluate_strategy(dict(ROLLING_STRATEGY), target_rows, candidate_params),
        reference_date,
    )
    core_score = _safe_float((entry.get("auto_score") or {}).get("auto_score")) or 0.0
    learning_score = (
        weights["core"] * core_score
        + weights["conservative"] * conservative["score"]
        + weights["regime"] * regime["score"]
        + weights["rolling"] * rolling["score"]
    )
    return {
        "name": str(entry.get("name") or ""),
        "group": str(entry.get("group") or ""),
        "overrides": dict(entry.get("overrides") or {}),
        "learning_score": learning_score,
        "core_auto_score": core_score,
        "conservative": conservative,
        "regime": regime,
        "rolling": rolling,
        "author_inputs_used": False,
        "walk_forward_proxy": True,
        "_entry": entry,
    }


def _ranking_key(item: dict[str, Any]) -> tuple[float, float, float, float, float, int, str]:
    conservative = item.get("conservative") or {}
    regime = item.get("regime") or {}
    rolling = item.get("rolling") or {}
    return (
        -float(item.get("learning_score") or 0.0),
        -float(conservative.get("weighted_interval_hit_rate") or 0.0),
        -float(regime.get("weighted_interval_hit_rate") or 0.0),
        -float(rolling.get("weighted_interval_hit_rate") or 0.0),
        float(conservative.get("weighted_mae_change_pct") or float("inf")),
        len(dict(item.get("overrides") or {})),
        str(item.get("name") or ""),
    )


def rerank_auto_tune_result(
    dataset: dict[str, Any],
    base_params: dict[str, Any],
    result: dict[str, Any],
    *,
    pool_size: int = 20,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    effective_weights = dict(DEFAULT_WEIGHTS)
    if weights:
        effective_weights.update(weights)
    if abs(sum(effective_weights.values()) - 1.0) > 1e-9:
        raise ValueError("local learning rerank weights must sum to 1")

    reference_date = _reference_date(result, dataset)
    history_codes, target_codes = _history_and_target_codes(dataset, reference_date)
    pool = _candidate_pool(result, pool_size)
    if not pool or not target_codes:
        result["local_learning_rerank"] = {
            "enabled": True,
            "applied": False,
            "reason": "no candidate pool or recent target samples",
            "target_codes": target_codes,
            "author_inputs_used": False,
        }
        return result

    core_best = result.get("best") or {}
    baseline_metrics = (result.get("baseline") or {}).get("metrics") or {}
    ranking = [
        _evaluate_candidate(
            dataset,
            base_params,
            baseline_metrics,
            entry,
            history_codes,
            target_codes,
            reference_date,
            effective_weights,
        )
        for entry in pool
    ]
    ranking.sort(key=_ranking_key)
    selected = ranking[0]
    selected_entry = selected.pop("_entry")
    for item in ranking[1:]:
        item.pop("_entry", None)

    result["core_best"] = core_best
    result["best"] = selected_entry
    result["changed_overrides"] = dict(selected_entry.get("overrides") or {})
    result["best_is_baseline"] = not bool(result["changed_overrides"])
    result["local_learning_rerank"] = {
        "enabled": True,
        "applied": True,
        "pool_size": len(pool),
        "requested_pool_size": pool_size,
        "reference_date": reference_date.isoformat(),
        "history_code_count": len(history_codes),
        "target_code_count": len(target_codes),
        "target_codes": target_codes,
        "weights": effective_weights,
        "width_budget": WIDTH_BUDGET,
        "author_inputs_used": False,
        "walk_forward_proxy": True,
        "core_best_name": str(core_best.get("name") or ""),
        "core_best_overrides": dict(core_best.get("overrides") or {}),
        "selected_name": str(selected_entry.get("name") or ""),
        "selected_overrides": dict(selected_entry.get("overrides") or {}),
        "selection_changed": _candidate_signature(core_best) != _candidate_signature(selected_entry),
        "selected": selected,
        "ranking": ranking,
    }
    return result
