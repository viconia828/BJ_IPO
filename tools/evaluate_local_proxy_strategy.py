from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from collections import Counter, defaultdict
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


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


distill = _load_module("analyze_xueqiu_author_logic_distillation", ROOT_DIR / "tools" / "analyze_xueqiu_author_logic_distillation.py")
blend = distill.blend


MODEL_NAMES = ("current_params", "scan_best")
WIDTH_POLICIES = {
    "layered_v1": {
        "research_only": False,
    },
    "conservative": {
        "tiers": {"low": 0.08, "mid": 0.12, "high": 0.15},
        "uncertain_min": 0.15,
        "fallback_min": 0.15,
        "strong_mood_min": 0.15,
        "cap": 0.20,
        "research_only": False,
    },
    "balanced": {
        "tiers": {"low": 0.10, "mid": 0.15, "high": 0.20},
        "uncertain_min": 0.20,
        "fallback_min": 0.20,
        "strong_mood_min": 0.20,
        "cap": 0.20,
        "research_only": False,
    },
    "observe_wide": {
        "tiers": {"low": 0.12, "mid": 0.20, "high": 0.25},
        "uncertain_min": 0.25,
        "fallback_min": 0.25,
        "strong_mood_min": 0.25,
        "cap": 0.25,
        "research_only": True,
    },
}
FALLBACK_POLICIES = (
    "none",
    "recent_mood",
    "recent_mood_regime_blend50",
    "recent_mood_regime_blend75",
    "recent_mood_regime_cap",
    "rolling_mood",
    "guarded_rolling_mood",
)
CENTER_POLICIES = (
    {"name": "model", "condition": "never", "alpha": 0.0, "research_only": False},
    {"name": "uncertain_rolling50", "condition": "uncertain", "alpha": 0.50, "research_only": False},
    {"name": "single_method_rolling50", "condition": "single_method", "alpha": 0.50, "research_only": False},
    {"name": "high_proxy_rolling50", "condition": "high_proxy", "alpha": 0.50, "research_only": False},
    {"name": "high_or_uncertain_rolling50", "condition": "high_or_uncertain", "alpha": 0.50, "research_only": False},
    {"name": "high_or_single_method_rolling50", "condition": "high_or_single_method", "alpha": 0.50, "research_only": False},
    {"name": "all_rolling50", "condition": "all", "alpha": 0.50, "research_only": True},
    {"name": "all_rolling75", "condition": "all", "alpha": 0.75, "research_only": True},
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _safe_float(value: Any) -> float | None:
    return distill._safe_float(value)


def _mean(values: list[float]) -> float | None:
    return distill._mean(values)


def _median(values: list[float]) -> float | None:
    return distill._median(values)


def _spearman_pairs(pairs: list[tuple[float | None, float | None]]) -> float | None:
    return distill._spearman_pairs(pairs)


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
    return distill._calc_change_pct(issue_price, target_price)


def _attach_proxy_ranks(rows: list[dict[str, Any]]) -> None:
    scored = [(index, _safe_float(row.get("proxy_score"))) for index, row in enumerate(rows)]
    scored = [(index, score) for index, score in scored if score is not None]
    if not scored:
        for row in rows:
            row["proxy_rank_pct"] = None
            row["proxy_tier"] = "unknown"
        return

    ordered = sorted(scored, key=lambda item: item[1])
    ranks: dict[int, float] = {}
    i = 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and ordered[j][1] == ordered[i][1]:
            j += 1
        avg_rank = (i + 1 + j) / 2
        for k in range(i, j):
            ranks[ordered[k][0]] = avg_rank
        i = j
    denominator = max(len(ordered) - 1, 1)
    for index, row in enumerate(rows):
        rank = ranks.get(index)
        if rank is None:
            row["proxy_rank_pct"] = None
            row["proxy_tier"] = "unknown"
            continue
        rank_pct = (rank - 1) / denominator
        row["proxy_rank_pct"] = rank_pct
        if rank_pct <= 1 / 3:
            tier = "low"
        elif rank_pct <= 2 / 3:
            tier = "mid"
        else:
            tier = "high"
        row["proxy_tier"] = tier


def _attach_regime_break_context(rows: list[dict[str, Any]], params: dict[str, Any]) -> None:
    ordered = sorted(rows, key=lambda row: (str(row.get("listing_date") or ""), str(row.get("code") or "")))
    completed: list[dict[str, Any]] = []
    index = 0
    while index < len(ordered):
        listing_date = str(ordered[index].get("listing_date") or "")[:10]
        group: list[dict[str, Any]] = []
        while index < len(ordered) and str(ordered[index].get("listing_date") or "")[:10] == listing_date:
            group.append(ordered[index])
            index += 1

        previous = completed[-1] if completed else None
        previous_expected = distill._fallback_base_change(previous, params) if previous else None
        previous_actual = _safe_float((previous or {}).get("actual_change_pct"))
        gap = (
            previous_expected - previous_actual
            if previous_expected is not None and previous_actual is not None
            else None
        )
        ratio = (
            previous_actual / previous_expected
            if previous_expected is not None and previous_expected > 0 and previous_actual is not None
            else None
        )
        triggered = bool(
            previous_expected is not None
            and previous_expected >= 80
            and previous_actual is not None
            and previous_actual <= previous_expected * 0.60
            and gap is not None
            and gap >= 50
        )
        for row in group:
            row["previous_regime_code"] = str((previous or {}).get("code") or "")
            row["previous_regime_expected_change_pct"] = previous_expected
            row["previous_regime_actual_change_pct"] = previous_actual
            row["previous_regime_gap_pct"] = gap
            row["previous_regime_actual_to_expected"] = ratio
            row["regime_break_triggered"] = triggered
        completed.extend(row for row in group if _safe_float(row.get("actual_change_pct")) is not None)


def _build_strategy_candidates() -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for model in MODEL_NAMES:
        for width_policy in WIDTH_POLICIES:
            for fallback_policy in FALLBACK_POLICIES:
                for center_policy in CENTER_POLICIES:
                    name = f"{model}_proxy_{center_policy['name']}_{width_policy}_{fallback_policy}"
                    candidates.append(
                        {
                            "name": name,
                            "model": model,
                            "center_policy": center_policy["name"],
                            "center_condition": center_policy["condition"],
                            "center_alpha": center_policy["alpha"],
                            "width_policy": width_policy,
                            "fallback_policy": fallback_policy,
                            "research_only": bool(WIDTH_POLICIES[width_policy]["research_only"] or center_policy["research_only"]),
                        }
                    )
    return candidates


def _model_available(row: dict[str, Any], model: str) -> bool:
    prefix = "current" if model == "current_params" else model
    return bool(row.get(f"{prefix}_available"))


def _model_value(row: dict[str, Any], model: str, field: str) -> Any:
    prefix = "current" if model == "current_params" else model
    return row.get(f"{prefix}_{field}")


def _dynamic_width(row: dict[str, Any], strategy: dict[str, Any], fallback_used: bool) -> tuple[float, list[str]]:
    policy_name = str(strategy["width_policy"])
    model = str(strategy.get("model") or "current_params")
    if policy_name == "layered_v1":
        score = abs(_safe_float(row.get("proxy_score")) or 0.0)
        uncertainty = _safe_float(row.get("model_uncertainty_score")) or 0.0
        recent5 = _safe_float(row.get("recent5_median_change"))
        if fallback_used or not _model_available(row, model) or uncertainty >= 2 or score >= 5:
            reasons = ["layered_v1_wide"]
            if fallback_used or not _model_available(row, model):
                reasons.append("fallback_or_model_unavailable")
            if uncertainty >= 2:
                reasons.append("high_model_uncertainty")
            if score >= 5:
                reasons.append("high_abs_proxy_score")
            return 0.20, reasons
        if score >= 3 or (recent5 is not None and recent5 >= 150):
            reasons = ["layered_v1_mid"]
            if score >= 3:
                reasons.append("mid_abs_proxy_score")
            if recent5 is not None and recent5 >= 150:
                reasons.append("strong_recent_mood")
            return 0.15, reasons
        return 0.10, ["layered_v1_narrow"]

    policy = WIDTH_POLICIES[policy_name]
    tier = str(row.get("proxy_tier") or "mid")
    tier_widths = policy["tiers"]
    width = float(tier_widths.get(tier, tier_widths["mid"]))
    reasons = [f"proxy_{tier}_tier"]
    cap = float(policy["cap"])
    uncertainty = _safe_float(row.get("model_uncertainty_score")) or 0.0
    single_method = 0 < int(row.get("current_method_count") or 0) <= 1
    recent5 = _safe_float(row.get("recent5_median_change"))
    if fallback_used or not _model_available(row, model):
        width = max(width, float(policy["fallback_min"]))
        reasons.append("fallback_or_model_unavailable")
    if uncertainty >= 2:
        width = max(width, float(policy["uncertain_min"]))
        reasons.append("high_model_uncertainty")
    if single_method:
        width = max(width, float(policy["uncertain_min"]))
        reasons.append("single_method_anchor")
    if recent5 is not None and recent5 >= 180:
        width = max(width, float(policy["strong_mood_min"]))
        reasons.append("very_strong_recent_mood")
    if recent5 is not None and recent5 < 50 and tier == "low" and not fallback_used:
        width = min(width, 0.10)
        reasons.append("weak_mood_low_proxy_cap")
    return min(width, cap), reasons


def _fallback_change(row: dict[str, Any], strategy: dict[str, Any], params: dict[str, Any]) -> tuple[float | None, str, str]:
    policy = str(strategy.get("fallback_policy") or "none")
    if policy == "none":
        return None, "", "fallback disabled"

    recent5 = _safe_float(row.get("recent5_median_change"))
    if policy == "guarded_rolling_mood":
        if row.get("proxy_tier") == "low" and (recent5 is None or recent5 < 80):
            return None, "", "guarded fallback blocked by low proxy and weak mood"

    if policy in {"rolling_mood", "guarded_rolling_mood"}:
        rolling = _safe_float(row.get("rolling_proxy_expected_change_pct"))
        if rolling is not None:
            return rolling, str(row.get("rolling_proxy_expected_source") or "rolling_proxy"), ""

    recent = distill._fallback_base_change(row, params)
    if recent is not None and policy.startswith("recent_mood_regime_") and row.get("regime_break_triggered"):
        previous_actual = _safe_float(row.get("previous_regime_actual_change_pct"))
        if previous_actual is not None:
            if policy == "recent_mood_regime_blend50":
                adjusted = recent * 0.50 + previous_actual * 0.50
            elif policy == "recent_mood_regime_blend75":
                adjusted = recent * 0.25 + previous_actual * 0.75
            elif policy == "recent_mood_regime_cap":
                adjusted = min(recent, previous_actual)
            else:
                adjusted = recent
            return adjusted, policy, ""
    if recent is not None:
        return recent, "recent_mood_fallback", ""
    return None, "", "local mood fallback unavailable"


def _center_override_change(row: dict[str, Any], strategy: dict[str, Any], base_change: float) -> tuple[float | None, str]:
    policy = str(strategy.get("center_condition") or "never")
    if policy == "never":
        return None, ""
    rolling = _safe_float(row.get("rolling_proxy_expected_change_pct"))
    if rolling is None:
        return None, "rolling proxy unavailable"
    uncertainty = _safe_float(row.get("model_uncertainty_score")) or 0.0
    single_method = 0 < int(row.get("current_method_count") or 0) <= 1
    recent5 = _safe_float(row.get("recent5_median_change"))
    proxy_score = _safe_float(row.get("proxy_score")) or 0.0
    high_proxy = row.get("proxy_tier") == "high" and ((recent5 is not None and recent5 >= 150) or proxy_score >= 8)
    uncertain = uncertainty >= 2
    should_override = (
        policy == "all"
        or (policy == "uncertain" and uncertain)
        or (policy == "single_method" and single_method)
        or (policy == "high_proxy" and high_proxy)
        or (policy == "high_or_uncertain" and (high_proxy or uncertain))
        or (policy == "high_or_single_method" and (high_proxy or single_method))
    )
    if not should_override:
        return None, ""
    alpha = float(strategy.get("center_alpha") or 0.5)
    predicted = base_change * (1 - alpha) + rolling * alpha
    return predicted, str(row.get("rolling_proxy_expected_source") or "rolling_proxy")


def _prediction_for_strategy(row: dict[str, Any], strategy: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    issue_price = _safe_float(row.get("issue_price"))
    if issue_price is None:
        return {"available": False, "reason": "issue price unavailable"}

    model = str(strategy.get("model") or "current_params")
    model_available = _model_available(row, model)
    fallback_used = False
    fallback_source = ""
    if model_available:
        target = _safe_float(_model_value(row, model, "target"))
        predicted_change = _safe_float(_model_value(row, model, "predicted_change_pct"))
        if target is None or predicted_change is None:
            return {"available": False, "reason": f"{model} target unavailable"}
        source = model
        action = "model_center_dynamic_width"
        override_change, override_source = _center_override_change(row, strategy, predicted_change)
        if override_change is not None:
            predicted_change = override_change
            target = issue_price * (1 + predicted_change / 100)
            source = f"{model}+{override_source}"
            action = "model_center_local_sentiment_blend"
    else:
        fallback_change, fallback_source, fallback_reason = _fallback_change(row, strategy, params)
        if fallback_change is None:
            return {"available": False, "reason": fallback_reason or f"{model} unavailable"}
        predicted_change = fallback_change
        target = issue_price * (1 + predicted_change / 100)
        fallback_used = True
        source = fallback_source
        action = "local_sentiment_fallback"

    width, width_reasons = _dynamic_width(row, strategy, fallback_used)
    return {
        "available": True,
        "source": source,
        "action": action,
        "target": target,
        "low": target * (1 - width),
        "high": target * (1 + width),
        "predicted_change_pct": predicted_change,
        "width": width,
        "width_reasons": width_reasons,
        "fallback_used": fallback_used,
        "fallback_source": fallback_source,
    }


def _rank_bucket_summary(teacher_rows: list[dict[str, Any]], result_rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    by_code = {row["code"]: row for row in result_rows or []}
    buckets: list[dict[str, Any]] = []
    for tier in ("low", "mid", "high"):
        rows = [row for row in teacher_rows if row.get("proxy_tier") == tier]
        result = [by_code.get(row["code"], {}) for row in rows]
        evaluated = [row for row in result if row.get("interval_hit") is not None]
        hits = [row for row in evaluated if row.get("interval_hit")]
        actual_changes = [_safe_float(row.get("actual_change_pct")) for row in rows]
        actual_changes = [float(value) for value in actual_changes if value is not None]
        buckets.append(
            {
                "tier": tier,
                "count": len(rows),
                "codes": [row["code"] for row in rows],
                "avg_proxy_score": _mean([row["proxy_score"] for row in rows if _safe_float(row.get("proxy_score")) is not None]),
                "avg_actual_change_pct": _mean(actual_changes),
                "median_actual_change_pct": _median(actual_changes),
                "evaluated_count": len(evaluated),
                "hit_count": len(hits),
                "hit_rate": len(hits) / len(evaluated) if evaluated else None,
            }
        )
    return buckets


def _evaluate_strategy(strategy: dict[str, Any], teacher_rows: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    result_rows: list[dict[str, Any]] = []
    hit_count = 0
    available_count = 0
    fallback_count = 0
    fallback_hit_count = 0
    model_center_count = 0
    model_center_hit_count = 0
    price_errors: list[float] = []
    change_errors: list[float] = []
    signed_errors: list[float] = []
    rank_pairs: list[tuple[float | None, float | None]] = []
    widths: list[float] = []
    width_counter: Counter[str] = Counter()

    for row in teacher_rows:
        prediction = _prediction_for_strategy(row, strategy, params)
        actual_price = _safe_float(row.get("actual_price"))
        actual_change = _safe_float(row.get("actual_change_pct"))
        target = _safe_float(prediction.get("target"))
        low = _safe_float(prediction.get("low"))
        high = _safe_float(prediction.get("high"))
        predicted_change = _safe_float(prediction.get("predicted_change_pct"))
        width = _safe_float(prediction.get("width"))
        available = bool(prediction.get("available") and target is not None and low is not None and high is not None)
        hit = None
        if available:
            available_count += 1
            if width is not None:
                widths.append(width)
                width_counter[f"{width:.2f}"] += 1
            if prediction.get("fallback_used"):
                fallback_count += 1
            else:
                model_center_count += 1
            if actual_price is not None:
                hit = min(low, high) <= actual_price <= max(low, high)
                hit_count += int(hit)
                price_errors.append(abs(target - actual_price))
                if prediction.get("fallback_used"):
                    fallback_hit_count += int(hit)
                else:
                    model_center_hit_count += int(hit)
            if predicted_change is not None and actual_change is not None:
                err = predicted_change - actual_change
                change_errors.append(abs(err))
                signed_errors.append(err)
                rank_pairs.append((predicted_change, actual_change))

        result_rows.append(
            {
                "code": row["code"],
                "name": row.get("name"),
                "listing_date": row.get("listing_date"),
                "industry_primary": row.get("industry_primary"),
                "actual_price": actual_price,
                "actual_change_pct": actual_change,
                "available": available,
                "reason": "" if available else prediction.get("reason"),
                "source": prediction.get("source"),
                "action": prediction.get("action"),
                "fallback_used": bool(prediction.get("fallback_used")),
                "fallback_source": prediction.get("fallback_source"),
                "target_price": target,
                "predicted_change_pct": predicted_change,
                "range_low": low,
                "range_high": high,
                "dynamic_width": width,
                "width_reasons": prediction.get("width_reasons") or [],
                "interval_hit": hit,
                "proxy_score": row.get("proxy_score"),
                "proxy_rank_pct": row.get("proxy_rank_pct"),
                "proxy_tier": row.get("proxy_tier"),
                "proxy_reasons": row.get("proxy_reasons") or [],
                "recent5_median_change": row.get("recent5_median_change"),
                "previous_regime_code": row.get("previous_regime_code"),
                "previous_regime_expected_change_pct": row.get("previous_regime_expected_change_pct"),
                "previous_regime_actual_change_pct": row.get("previous_regime_actual_change_pct"),
                "previous_regime_gap_pct": row.get("previous_regime_gap_pct"),
                "previous_regime_actual_to_expected": row.get("previous_regime_actual_to_expected"),
                "regime_break_triggered": bool(row.get("regime_break_triggered")),
                "raw_recent_mood_fallback_change_pct": distill._fallback_base_change(row, params),
                "model_uncertainty_score": row.get("model_uncertainty_score"),
                "category": row.get("category"),
            }
        )

    target_count = len(teacher_rows)
    evaluated_count = len([row for row in result_rows if row.get("interval_hit") is not None])
    return {
        "strategy": strategy,
        "target_count": target_count,
        "available_count": available_count,
        "unavailable_count": target_count - available_count,
        "evaluated_count": evaluated_count,
        "hit_count": hit_count,
        "full_hit_rate": hit_count / target_count if target_count else None,
        "available_hit_rate": hit_count / available_count if available_count else None,
        "mae_target_price": _mean(price_errors),
        "mae_change_pct": _mean(change_errors),
        "mean_signed_change_error_pct": _mean(signed_errors),
        "spearman_predicted_vs_actual_change": _spearman_pairs(rank_pairs),
        "avg_width": _mean(widths),
        "median_width": _median(widths),
        "width_distribution": dict(sorted(width_counter.items())),
        "fallback_count": fallback_count,
        "fallback_hit_count": fallback_hit_count,
        "fallback_hit_rate": fallback_hit_count / fallback_count if fallback_count else None,
        "model_center_count": model_center_count,
        "model_center_hit_count": model_center_hit_count,
        "model_center_hit_rate": model_center_hit_count / model_center_count if model_center_count else None,
        "rank_bucket_summary": _rank_bucket_summary(teacher_rows, result_rows),
        "hit_codes": [row["code"] for row in result_rows if row.get("interval_hit") is True],
        "miss_codes": [row["code"] for row in result_rows if row.get("interval_hit") is False],
        "unavailable_codes": [row["code"] for row in result_rows if not row.get("available")],
        "fallback_codes": [row["code"] for row in result_rows if row.get("fallback_used")],
        "rows": result_rows,
    }


def _strategy_sort_key(result: dict[str, Any]) -> tuple[float, float, float, float, str]:
    strategy = result.get("strategy") or {}
    research_penalty = 1.0 if strategy.get("research_only") else 0.0
    return (
        research_penalty,
        -(result.get("full_hit_rate") or 0.0),
        result.get("avg_width") if result.get("avg_width") is not None else 1e9,
        result.get("mae_change_pct") if result.get("mae_change_pct") is not None else 1e9,
        str(strategy.get("name") or ""),
    )


def _compact_strategy_result(result: dict[str, Any], include_rows: bool = False) -> dict[str, Any]:
    keys = [
        "strategy",
        "target_count",
        "available_count",
        "unavailable_count",
        "evaluated_count",
        "hit_count",
        "full_hit_rate",
        "available_hit_rate",
        "mae_target_price",
        "mae_change_pct",
        "mean_signed_change_error_pct",
        "spearman_predicted_vs_actual_change",
        "avg_width",
        "median_width",
        "width_distribution",
        "fallback_count",
        "fallback_hit_count",
        "fallback_hit_rate",
        "model_center_count",
        "model_center_hit_count",
        "model_center_hit_rate",
        "rank_bucket_summary",
        "hit_codes",
        "miss_codes",
        "unavailable_codes",
        "fallback_codes",
    ]
    payload = {key: result.get(key) for key in keys}
    if include_rows:
        payload["rows"] = result.get("rows") or []
    return payload


def _load_context(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path, list[str], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    dataset = param_tuning.load_replay_dataset(args.dataset)
    dataset_items = list(dataset.get("items") or [])
    by_code = distill._dataset_by_code(dataset)
    params = config_loader.load_params(args.params)
    scan_report = _read_json(Path(args.scan_report))
    author_report_path = Path(args.author_score_report) if args.author_score_report else distill._latest_author_score_report(Path(args.output_dir))
    author_payload = _read_json(author_report_path)
    author_predictions = blend._index_author_predictions(author_payload)
    target_codes = distill._target_codes(args.target, scan_report, by_code, author_predictions)
    history_codes = distill._target_codes("all_actual", scan_report, by_code, author_predictions)

    best_overrides = dict(((scan_report.get("top_candidates") or [{}])[0]).get("overrides") or {})
    current_metrics = param_tuning.evaluate_replay_targets(dataset, params, target_codes=history_codes)
    best_params = dict(params)
    best_params.update(best_overrides)
    scan_best_metrics = param_tuning.evaluate_replay_targets(dataset, best_params, target_codes=history_codes)
    model_predictions = {
        "current_params": blend._index_model_predictions(current_metrics, "current_params"),
        "scan_best": blend._index_model_predictions(scan_best_metrics, "scan_best"),
    }
    history_rows = distill._build_teacher_rows(history_codes, dataset_items, by_code, model_predictions, author_predictions, params)
    _attach_regime_break_context(history_rows, params)
    history_by_code = {row["code"]: row for row in history_rows}
    teacher_rows = [dict(history_by_code[code]) for code in target_codes if code in history_by_code]
    _attach_proxy_ranks(teacher_rows)
    return dataset, params, scan_report, author_report_path, target_codes, teacher_rows, by_code, model_predictions


def _reference_results(
    target_codes: list[str],
    by_code: dict[str, dict[str, Any]],
    model_predictions: dict[str, dict[str, dict[str, Any]]],
    author_report_path: Path,
) -> list[dict[str, Any]]:
    author_payload = _read_json(author_report_path)
    author_predictions = blend._index_author_predictions(author_payload)
    candidates = [
        {"name": "current_params", "kind": "model_only", "model": "current_params"},
        {"name": "scan_best", "kind": "model_only", "model": "scan_best"},
        {"name": "author_fixed10", "kind": "author_fixed10"},
        {"name": "author_weighted_interval", "kind": "author_weighted_interval"},
    ]
    return [
        blend._compact_result(blend._evaluate_candidate(candidate, target_codes, by_code, model_predictions, author_predictions))
        for candidate in candidates
    ]


def _legacy_proxy_reference(teacher_rows: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        {
            "name": "legacy_current_step0_layered_fallback",
            "kind": "proxy_layered_width",
            "model": "current_params",
            "step_pct": 0,
            "fallback": True,
        },
        {
            "name": "legacy_scan_best_step0_layered_fallback",
            "kind": "proxy_layered_width",
            "model": "scan_best",
            "step_pct": 0,
            "fallback": True,
        },
    ]
    return [distill._compact_result(distill._evaluate_proxy_candidate(candidate, teacher_rows, params)) for candidate in candidates]


def _best_from(results: list[dict[str, Any]], predicate) -> dict[str, Any] | None:
    filtered = [result for result in results if predicate(result)]
    return sorted(filtered, key=_strategy_sort_key)[0] if filtered else None


def _build_markdown(payload: dict[str, Any]) -> str:
    rec = payload["recommended_strategy"]
    overall = payload["best_overall_strategy"]
    lines = [
        "# Local Proxy Strategy Evaluation",
        "",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 目标样本：`{payload['target_universe']['name']}`，代码数 `{payload['target_universe']['count']}`",
        f"- author score 报告仅用于对照/归因：`{payload['inputs']['author_score_report']}`",
        "",
        "## 核心结论",
        "",
        f"- 推荐非观察档：`{rec['strategy']['name']}`，命中 `{rec['hit_count']}/{rec['target_count']}`，命中率 `{_fmt_rate(rec['full_hit_rate'])}`，平均宽度 `{_fmt_rate(rec['avg_width'])}`。",
        f"- 全部候选最佳：`{overall['strategy']['name']}`，命中 `{overall['hit_count']}/{overall['target_count']}`，命中率 `{_fmt_rate(overall['full_hit_rate'])}`，平均宽度 `{_fmt_rate(overall['avg_width'])}`。",
        f"- 推荐策略兜底：`{rec['fallback_hit_count']}/{rec['fallback_count']}`，兜底命中率 `{_fmt_rate(rec['fallback_hit_rate'])}`。",
    ]
    regime_rows = [row for row in payload.get("teacher_rows") or [] if row.get("regime_break_triggered")]
    lines.extend(
        [
            "",
            "## Regime-break 触发",
            "",
            "| 当前代码 | 上一只 | 上一只预期涨幅 | 上一只实际涨幅 | 实际/预期 | 当前scan_best可用 |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for row in regime_rows:
        lines.append(
            "| {code} | {previous} | {expected} | {actual} | {ratio} | {available} |".format(
                code=row.get("code") or "",
                previous=row.get("previous_regime_code") or "",
                expected=_fmt_pct(row.get("previous_regime_expected_change_pct")),
                actual=_fmt_pct(row.get("previous_regime_actual_change_pct")),
                ratio=_fmt_rate(row.get("previous_regime_actual_to_expected")),
                available="是" if row.get("scan_best_available") else "否",
            )
        )
    if not regime_rows:
        lines.append("| 无 |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## 基准对照",
            "",
            "| 方案 | 命中 | 全样本命中率 | 可用命中率 | 可用数 | MAE(涨幅) | Spearman |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in payload.get("reference_results") or []:
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
    lines.extend(["", "## 旧版 Proxy 对照", "", "| 方案 | 命中 | 命中率 | 可用数 | MAE(涨幅) | Spearman |", "|---|---:|---:|---:|---:|---:|"])
    for item in payload.get("legacy_proxy_results") or []:
        lines.append(
            "| `{name}` | {hit}/{target} | {full} | {available}/{target} | {mae} | {spear} |".format(
                name=item["candidate"]["name"],
                hit=item["hit_count"],
                target=item["target_count"],
                full=_fmt_rate(item.get("full_hit_rate")),
                available=item["available_count"],
                mae=_fmt_num(item.get("mae_change_pct"), 2),
                spear=_fmt_num(item.get("spearman_predicted_vs_actual_change"), 3),
            )
        )
    lines.extend(
        [
            "",
            "## 候选策略榜单",
            "",
            "| 排名 | 策略 | 命中 | 命中率 | 可用数 | 平均宽度 | 宽度分布 | 兜底命中 | MAE(涨幅) | Spearman |",
            "|---:|---|---:|---:|---:|---:|---|---:|---:|---:|",
        ]
    )
    for index, item in enumerate(payload.get("top_strategy_results") or [], start=1):
        lines.append(
            "| {rank} | `{name}` | {hit}/{target} | {full} | {available}/{target} | {width} | {dist} | {fallback_hit}/{fallback_count} | {mae} | {spear} |".format(
                rank=index,
                name=item["strategy"]["name"],
                hit=item["hit_count"],
                target=item["target_count"],
                full=_fmt_rate(item.get("full_hit_rate")),
                available=item["available_count"],
                width=_fmt_rate(item.get("avg_width")),
                dist=", ".join(f"{key}:{value}" for key, value in (item.get("width_distribution") or {}).items()),
                fallback_hit=item.get("fallback_hit_count"),
                fallback_count=item.get("fallback_count"),
                mae=_fmt_num(item.get("mae_change_pct"), 2),
                spear=_fmt_num(item.get("spearman_predicted_vs_actual_change"), 3),
            )
        )
    lines.extend(
        [
            "",
            "## Proxy 排序诊断",
            "",
            "| 分层 | 数量 | 代码 | 平均proxy分 | 平均实际涨幅 | 中位实际涨幅 | 推荐策略命中率 |",
            "|---|---:|---|---:|---:|---:|---:|",
        ]
    )
    for bucket in rec.get("rank_bucket_summary") or []:
        lines.append(
            "| {tier} | {count} | {codes} | {score} | {avg} | {median} | {hit_rate} |".format(
                tier=bucket["tier"],
                count=bucket["count"],
                codes=", ".join(bucket.get("codes") or []),
                score=_fmt_num(bucket.get("avg_proxy_score"), 2),
                avg=_fmt_pct(bucket.get("avg_actual_change_pct")),
                median=_fmt_pct(bucket.get("median_actual_change_pct")),
                hit_rate=_fmt_rate(bucket.get("hit_rate")),
            )
        )
    lines.extend(
        [
            "",
            "## 推荐策略逐样本",
            "",
            "| 代码 | 简称 | tier | 实际涨幅 | 中枢来源 | 预测涨幅 | 宽度 | 区间 | 命中 | 触发原因 |",
            "|---|---|---|---:|---|---:|---:|---:|---|---|",
        ]
    )
    for row in rec.get("rows") or []:
        hit = "" if row.get("interval_hit") is None else ("是" if row.get("interval_hit") else "否")
        reason = "、".join((row.get("proxy_reasons") or [])[:3] + (row.get("width_reasons") or [])[:2])
        lines.append(
            "| {code} | {name} | {tier} | {actual} | {source} | {pred} | {width} | {low}-{high} | {hit} | {reason} |".format(
                code=row.get("code"),
                name=row.get("name") or "",
                tier=row.get("proxy_tier") or "",
                actual=_fmt_pct(row.get("actual_change_pct")),
                source=row.get("source") or "",
                pred=_fmt_pct(row.get("predicted_change_pct")),
                width=_fmt_rate(row.get("dynamic_width")),
                low=_fmt_num(row.get("range_low")),
                high=_fmt_num(row.get("range_high")),
                hit=hit,
                reason=reason,
            )
        )
    lines.extend(
        [
            "",
            "## 口径",
            "",
            "- 本报告候选不使用作者目标价作为预测输入；作者报告只用于基准对照和类别解释。",
            "- 本地模型可用时，策略保留原模型中枢，只用 proxy 调整宽度。",
            "- 本地模型不可用时，兜底只使用上市日前已知的本地近端情绪/滚动历史关系。",
            "- `observe_wide` 是研究上限，不作为默认候选。",
            "- `recent_mood_regime_*` 只在模型不可用且上一只实际涨幅显著低于其上市前情绪预期时调整兜底中枢。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    _dataset, params, _scan_report, author_report_path, target_codes, teacher_rows, by_code, model_predictions = _load_context(args)
    strategies = _build_strategy_candidates()
    if str(params.get("local_center_overlay_enabled", "")).strip().lower() not in {"", "0", "false", "no", "off", "否", "关闭"}:
        strategies = [strategy for strategy in strategies if strategy.get("center_condition") == "never"]
    evaluated = [_evaluate_strategy(strategy, teacher_rows, params) for strategy in strategies]
    evaluated.sort(key=_strategy_sort_key)
    recommended = _best_from(evaluated, lambda item: not (item.get("strategy") or {}).get("research_only"))
    best_overall = sorted(evaluated, key=lambda item: (-(item.get("full_hit_rate") or 0.0), item.get("avg_width") or 1e9))[0]
    if recommended is None:
        recommended = best_overall

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    payload = {
        "schema": "local_proxy_strategy_evaluation_v1",
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
        "reference_results": _reference_results(target_codes, by_code, model_predictions, author_report_path),
        "legacy_proxy_results": _legacy_proxy_reference(teacher_rows, params),
        "recommended_strategy": _compact_strategy_result(recommended, include_rows=True),
        "best_overall_strategy": _compact_strategy_result(best_overall, include_rows=True),
        "top_strategy_results": [_compact_strategy_result(item) for item in evaluated[:20]],
        "all_strategy_results": [_compact_strategy_result(item) for item in evaluated],
        "teacher_rows": teacher_rows,
    }
    output_dir = Path(args.output_dir)
    json_path = output_dir / f"local_proxy_strategy_{args.target}_{timestamp}.json"
    md_path = output_dir / f"local_proxy_strategy_{args.target}_{timestamp}.md"
    _write_json(json_path, payload)
    md_path.write_text(_build_markdown(payload), encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "markdown": str(md_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate local-only proxy ranking, dynamic-width, and mood-fallback strategies.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--params", default=str(DEFAULT_PARAMS))
    parser.add_argument("--scan-report", default=str(DEFAULT_SCAN_REPORT))
    parser.add_argument("--author-score-report", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--target", choices=["scan_sample", "author_scored", "all_actual"], default="scan_sample")
    return parser


def main() -> None:
    payload = run(build_parser().parse_args())
    rec = payload["recommended_strategy"]
    overall = payload["best_overall_strategy"]
    print(
        json.dumps(
            {
                "outputs": payload["outputs"],
                "target_universe": payload["target_universe"],
                "recommended_strategy": {
                    "strategy": rec["strategy"],
                    "hit_count": rec["hit_count"],
                    "target_count": rec["target_count"],
                    "available_count": rec["available_count"],
                    "full_hit_rate": rec["full_hit_rate"],
                    "avg_width": rec["avg_width"],
                    "fallback_hit_count": rec["fallback_hit_count"],
                    "fallback_count": rec["fallback_count"],
                    "mae_change_pct": rec["mae_change_pct"],
                    "spearman": rec["spearman_predicted_vs_actual_change"],
                },
                "best_overall_strategy": {
                    "strategy": overall["strategy"],
                    "hit_count": overall["hit_count"],
                    "target_count": overall["target_count"],
                    "full_hit_rate": overall["full_hit_rate"],
                    "avg_width": overall["avg_width"],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
