from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import sys
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT_DIR / "tools"
CODE_DIR = ROOT_DIR / "code"
for path in (TOOLS_DIR, CODE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import build_account_pool_history as account_pool
import subscription_ladder_labels
import subscription_predictor


DEFAULT_HISTORY_PATH = ROOT_DIR / "data" / "offline_tuning" / "subscription_history_sample.csv"
DEFAULT_LABEL_PATH = ROOT_DIR / "data" / "offline_tuning" / "subscription_ladder_labels.csv"


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        return [dict(row) for row in csv.DictReader(file_obj)]


def _clip_only_update(
    state: dict[str, dict[str, Any]],
    points: list[dict[str, Any]],
    code: str,
) -> set[str]:
    observations = account_pool._observed_state_points(points)
    touched_keys: set[str] = set()
    if not observations:
        return touched_keys

    for observation in observations:
        key = str(observation["key"])
        current = state.get(key) or {}
        old_estimate = _safe_float(current.get("estimate"))
        old_deviation = _safe_float(current.get("deviation")) or 0.0
        new_estimate = float(observation["estimate"])
        deviation = max(new_estimate * 0.03, old_deviation * 0.8, 1000.0)
        if old_estimate is not None:
            deviation = max(deviation, abs(new_estimate - old_estimate) * 0.5)
        state[key] = {
            "threshold_amount_wan": observation["threshold_amount_wan"],
            "estimate": new_estimate,
            "deviation": deviation,
            "basis": "observed_threshold",
            "source": observation.get("source", ""),
            "observation_count": int(_safe_float(current.get("observation_count")) or 0) + 1,
            "last_update_code": code,
        }
        observation["deviation"] = deviation
        touched_keys.add(key)

    # Preserve old points that remain feasible.  Only points that contradict
    # a newer observation are clipped to that observation's boundary.
    for observation in observations:
        obs_threshold = float(observation["threshold_amount_wan"])
        obs_estimate = float(observation["estimate"])
        obs_key = str(observation["key"])
        for key, item in state.items():
            if key == obs_key:
                continue
            threshold = _safe_float(item.get("threshold_amount_wan"))
            estimate = _safe_float(item.get("estimate"))
            if threshold is None or estimate is None:
                continue
            if threshold < obs_threshold - 1e-9 and estimate < obs_estimate - 1e-9:
                account_pool._cover_state_item(item, observation, code)
                touched_keys.add(key)
            elif threshold > obs_threshold + 1e-9 and estimate > obs_estimate + 1e-9:
                account_pool._cover_state_item(item, observation, code)
                touched_keys.add(key)

    account_pool._enforce_state_monotone(state, touched_keys, code)
    return touched_keys


def _blended_interpolation_update(
    state: dict[str, dict[str, Any]],
    points: list[dict[str, Any]],
    code: str,
    *,
    blend_weight: float,
) -> set[str]:
    observations = account_pool._observed_state_points(points)
    touched_keys: set[str] = set()
    if not observations:
        return touched_keys

    blend_weight = min(max(float(blend_weight), 0.0), 1.0)
    for observation in observations:
        key = str(observation["key"])
        current = state.get(key) or {}
        old_estimate = _safe_float(current.get("estimate"))
        old_deviation = _safe_float(current.get("deviation")) or 0.0
        new_estimate = float(observation["estimate"])
        deviation = max(new_estimate * 0.03, old_deviation * 0.8, 1000.0)
        if old_estimate is not None:
            deviation = max(deviation, abs(new_estimate - old_estimate) * 0.5)
        state[key] = {
            "threshold_amount_wan": observation["threshold_amount_wan"],
            "estimate": new_estimate,
            "deviation": deviation,
            "basis": "observed_threshold",
            "source": observation.get("source", ""),
            "observation_count": int(_safe_float(current.get("observation_count")) or 0) + 1,
            "last_update_code": code,
        }
        observation["deviation"] = deviation
        touched_keys.add(key)

    for lower_observation, upper_observation in zip(observations, observations[1:]):
        lower_threshold = float(lower_observation["threshold_amount_wan"])
        upper_threshold = float(upper_observation["threshold_amount_wan"])
        lower_estimate = float(lower_observation["estimate"])
        upper_estimate = float(upper_observation["estimate"])
        if upper_threshold <= lower_threshold + 1e-9:
            continue
        for key, item in state.items():
            if key in {str(lower_observation["key"]), str(upper_observation["key"])}:
                continue
            threshold = _safe_float(item.get("threshold_amount_wan"))
            old_estimate = _safe_float(item.get("estimate"))
            if threshold is None or old_estimate is None or not (lower_threshold < threshold < upper_threshold):
                continue
            ratio = (threshold - lower_threshold) / (upper_threshold - lower_threshold)
            interpolated = max(lower_estimate + ratio * (upper_estimate - lower_estimate), 0.0)
            blended = old_estimate + blend_weight * (interpolated - old_estimate)
            lower_deviation = float(lower_observation.get("deviation") or 1000.0)
            upper_deviation = float(upper_observation.get("deviation") or 1000.0)
            interpolation_deviation = lower_deviation + ratio * (upper_deviation - lower_deviation)
            old_deviation = _safe_float(item.get("deviation")) or 0.0
            item["estimate"] = max(blended, 0.0)
            item["deviation"] = max(
                old_deviation * (1.0 - blend_weight) + interpolation_deviation * blend_weight,
                abs(interpolated - old_estimate) * 0.5,
                blended * 0.03,
                1000.0,
            )
            item["basis"] = "blended_by_newer_observations"
            item["covered_by_threshold_wan"] = upper_threshold
            item["last_update_code"] = code
            touched_keys.add(key)

    for observation in observations:
        obs_threshold = float(observation["threshold_amount_wan"])
        obs_estimate = float(observation["estimate"])
        obs_key = str(observation["key"])
        for key, item in state.items():
            if key == obs_key:
                continue
            threshold = _safe_float(item.get("threshold_amount_wan"))
            estimate = _safe_float(item.get("estimate"))
            if threshold is None or estimate is None:
                continue
            if threshold < obs_threshold - 1e-9 and estimate < obs_estimate - 1e-9:
                account_pool._cover_state_item(item, observation, code)
                touched_keys.add(key)
            elif threshold > obs_threshold + 1e-9 and estimate > obs_estimate + 1e-9:
                account_pool._cover_state_item(item, observation, code)
                touched_keys.add(key)

    account_pool._enforce_state_monotone(state, touched_keys, code)
    return touched_keys


def _pava_nonincreasing(values: list[float], weights: list[float]) -> list[float]:
    blocks: list[dict[str, Any]] = []
    for index, (value, weight) in enumerate(zip(values, weights)):
        blocks.append(
            {
                "start": index,
                "end": index,
                "weight": max(float(weight), 1e-12),
                "weighted_sum": float(value) * max(float(weight), 1e-12),
            }
        )
        while len(blocks) >= 2:
            left = blocks[-2]
            right = blocks[-1]
            left_mean = left["weighted_sum"] / left["weight"]
            right_mean = right["weighted_sum"] / right["weight"]
            if left_mean >= right_mean - 1e-12:
                break
            blocks[-2:] = [
                {
                    "start": left["start"],
                    "end": right["end"],
                    "weight": left["weight"] + right["weight"],
                    "weighted_sum": left["weighted_sum"] + right["weighted_sum"],
                }
            ]

    fitted = [0.0] * len(values)
    for block in blocks:
        mean = block["weighted_sum"] / block["weight"]
        for index in range(int(block["start"]), int(block["end"]) + 1):
            fitted[index] = max(float(mean), 0.0)
    return fitted


def _weighted_isotonic_state(
    observations: list[dict[str, Any]],
    *,
    current_index: int,
    half_life_samples: float | None,
    normalize_by_allocated_accounts: bool = False,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for observation in observations:
        threshold = float(observation["threshold_amount_wan"])
        key = account_pool._threshold_state_key(threshold)
        age = max(current_index - int(observation["sample_index"]), 0)
        recency_weight = (
            1.0
            if half_life_samples is None
            else 0.5 ** (age / max(float(half_life_samples), 1e-6))
        )
        confidence = max(float(observation.get("confidence") or 0.75), 0.05)
        weight = confidence * recency_weight
        item = grouped.setdefault(
            key,
            {
                "threshold_amount_wan": threshold,
                "weighted_sum": 0.0,
                "weight": 0.0,
                "count": 0,
                "codes": [],
                "latest_code": "",
                "latest_index": -1,
            },
        )
        scale_accounts = max(float(observation.get("allocated_accounts") or 0.0), 0.0)
        normalized_estimate = float(observation["estimate"])
        if normalize_by_allocated_accounts and scale_accounts > 0:
            normalized_estimate /= scale_accounts
        item["weighted_sum"] += normalized_estimate * weight
        item["weight"] += weight
        item["count"] += 1
        code = str(observation.get("code") or "")
        if code and code not in item["codes"]:
            item["codes"].append(code)
        if int(observation["sample_index"]) >= int(item["latest_index"]):
            item["latest_index"] = int(observation["sample_index"])
            item["latest_code"] = code

    ordered = sorted(grouped.values(), key=lambda item: float(item["threshold_amount_wan"]))
    raw_values = [item["weighted_sum"] / item["weight"] for item in ordered]
    weights = [item["weight"] for item in ordered]
    fitted = _pava_nonincreasing(raw_values, weights)
    current_scale = 1.0
    if normalize_by_allocated_accounts:
        latest_scales = [
            (int(item["sample_index"]), float(item.get("allocated_accounts") or 0.0))
            for item in observations
            if float(item.get("allocated_accounts") or 0.0) > 0
        ]
        if latest_scales:
            current_scale = max(latest_scales)[1]
    state: dict[str, dict[str, Any]] = {}
    for item, raw, estimate in zip(ordered, raw_values, fitted):
        raw *= current_scale
        estimate *= current_scale
        threshold = float(item["threshold_amount_wan"])
        key = account_pool._threshold_state_key(threshold)
        state[key] = {
            "threshold_amount_wan": threshold,
            "estimate": estimate,
            "deviation": max(abs(estimate - raw), estimate * 0.03, 1000.0),
            "basis": "weighted_isotonic_observations",
            "source": "manual_ladder+announcement_aggregate",
            "observation_count": int(item["count"]),
            "last_update_code": item["latest_code"],
            "contributor_codes": item["codes"],
        }
    return state


def _state_snapshot_row(state: dict[str, dict[str, Any]], code: str, apply_date: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "security_code": code,
        "apply_date": apply_date,
        "account_pool_snapshot_state": True,
    }
    for item in state.values():
        threshold = _safe_float(item.get("threshold_amount_wan"))
        estimate = _safe_float(item.get("estimate"))
        if threshold is None or estimate is None:
            continue
        prefix = account_pool._threshold_column_prefix(threshold)
        row[f"{prefix}_estimate"] = estimate
        row[f"{prefix}_basis"] = "observed_threshold"
    return row


def _actual_fractional_label(row: dict[str, Any], fractional_min_lots: int) -> dict[str, Any] | None:
    top_apply = _safe_float(row.get("top_apply_amount_wan"))
    for item in subscription_ladder_labels.parse_manual_ladder(row.get("manual_ladder"), top_apply):
        if int(item.get("fractional_lots") or 0) != 1:
            continue
        if int(item.get("total_lots") or 0) == fractional_min_lots:
            return item
    return None


def _predict_fractional_cutoff(
    state: dict[str, dict[str, Any]],
    row: dict[str, Any],
) -> dict[str, Any] | None:
    if not state:
        return None
    allocation_rate_pct = _safe_float(row.get("allocation_rate_pct"))
    issue_price = _safe_float(row.get("issue_price"))
    online_issue_shares = _safe_float(row.get("online_issue_shares"))
    top_apply_shares = _safe_float(row.get("top_apply_shares"))
    top_apply_amount = _safe_float(row.get("top_apply_amount_wan"))
    if (
        allocation_rate_pct is None
        or allocation_rate_pct <= 0
        or issue_price is None
        or issue_price <= 0
        or online_issue_shares is None
        or online_issue_shares <= 0
        or top_apply_shares is None
        or top_apply_shares <= 0
        or top_apply_amount is None
        or top_apply_amount <= 0
    ):
        return None
    allocation_ratio = allocation_rate_pct / 100.0
    fractional_min_lots = int(math.floor((top_apply_shares * allocation_ratio) / subscription_predictor.LOT_SIZE)) + 1
    actual_item = _actual_fractional_label(row, fractional_min_lots)
    actual_amount = _safe_float((actual_item or {}).get("threshold_amount_wan"))
    if actual_amount is None or actual_amount <= 0:
        return None

    snapshot = _state_snapshot_row(
        state,
        str(row.get("security_code") or "prior_state"),
        str(row.get("apply_date") or ""),
    )
    settings = {
        "subscription_prediction_account_pool_enabled": True,
        "subscription_prediction_account_pool_rows": [snapshot],
    }
    prediction = subscription_predictor._estimate_account_pool_fractional_cutoff(
        allocation_ratio=allocation_ratio,
        issue_price=issue_price,
        online_issue_shares=online_issue_shares,
        top_apply_shares=int(top_apply_shares),
        top_apply_amount_wan=top_apply_amount,
        settings=settings,
    )
    predicted_amount = _safe_float((prediction or {}).get("fractional_threshold_amount_wan"))
    if predicted_amount is None or predicted_amount <= 0:
        return None
    return {
        "fractional_min_lots": fractional_min_lots,
        "actual_amount_wan": actual_amount,
        "predicted_amount_wan": predicted_amount,
        "abs_error_wan": abs(predicted_amount - actual_amount),
        "pct_error": abs(predicted_amount - actual_amount) / actual_amount,
    }


def _one_step_constraint_errors(
    state: dict[str, dict[str, Any]],
    row: dict[str, Any],
) -> dict[str, Any]:
    if not state:
        return {}
    manual_by_lot = account_pool._manual_points(row)
    fit = account_pool._parse_json_object(row.get("allocation_fit_json"))
    fit_by_lot = account_pool._fit_points_by_lot(account_pool._bucket_rows(fit))
    issue_shares = _safe_float(row.get("online_issue_shares"))
    allocated_accounts = _safe_float(row.get("online_allocated_accounts"))
    if not manual_by_lot or issue_shares is None or allocated_accounts is None or allocated_accounts <= 0:
        return {}
    total_lots = issue_shares / 100.0
    average_lots = total_lots / allocated_accounts
    max_lots = max(max(manual_by_lot, default=1), max(fit_by_lot, default=1), int(math.ceil(average_lots)))
    thresholds_by_lot = account_pool._complete_lot_thresholds(
        manual_by_lot=manual_by_lot,
        fit_by_lot=fit_by_lot,
        max_lots=max_lots,
        top_apply_amount_wan=_safe_float(row.get("top_apply_amount_wan")),
    )
    predicted = {
        level: account_pool._estimate_state_accounts_ge(state, amount)
        for level, amount in thresholds_by_lot.items()
    }
    if any(value is None for value in predicted.values()):
        return {}
    predicted_area = sum(float(value or 0.0) for value in predicted.values())
    result: dict[str, Any] = {
        "area_abs_error_lots": abs(predicted_area - total_lots),
        "area_pct_error": abs(predicted_area - total_lots) / total_lots if total_lots > 0 else None,
    }
    first_manual = manual_by_lot.get(1)
    if first_manual is not None:
        first_estimate = account_pool._estimate_state_accounts_ge(
            state,
            float(first_manual["threshold_amount_wan"]),
        )
        if first_estimate is not None:
            result["allocated_abs_error"] = abs(first_estimate - allocated_accounts)
            result["allocated_pct_error"] = abs(first_estimate - allocated_accounts) / allocated_accounts
    return result


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _evaluate_strategy(
    rows: list[dict[str, Any]],
    *,
    name: str,
    update_mode: str,
    half_life_samples: float | None = None,
    blend_weight: float | None = None,
) -> dict[str, Any]:
    state: dict[str, dict[str, Any]] = {}
    direct_observations: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    fractional_abs: list[float] = []
    fractional_pct: list[float] = []
    allocated_pct: list[float] = []
    area_pct: list[float] = []

    for sample_index, row in enumerate(rows):
        code = str(row.get("security_code") or "")
        constraint_errors = _one_step_constraint_errors(state, row)
        fractional = _predict_fractional_cutoff(state, row)
        if fractional:
            fractional_abs.append(float(fractional["abs_error_wan"]))
            fractional_pct.append(float(fractional["pct_error"]))
        if constraint_errors.get("allocated_pct_error") is not None:
            allocated_pct.append(float(constraint_errors["allocated_pct_error"]))
        if constraint_errors.get("area_pct_error") is not None:
            area_pct.append(float(constraint_errors["area_pct_error"]))

        points = account_pool._build_points_for_row(row, state)
        if update_mode == "full_interpolation":
            account_pool._update_threshold_state(state, points, code)
        elif update_mode == "clip_only":
            _clip_only_update(state, points, code)
        elif update_mode == "blended_interpolation":
            _blended_interpolation_update(
                state,
                points,
                code,
                blend_weight=float(blend_weight or 0.0),
            )
        elif update_mode in {"weighted_isotonic", "weighted_isotonic_scaled"}:
            allocated_accounts = _safe_float(row.get("online_allocated_accounts"))
            for point in points:
                estimate = _safe_float(point.get("accounts_ge_threshold"))
                threshold = _safe_float(point.get("threshold_amount_wan"))
                if estimate is None or threshold is None:
                    continue
                direct_observations.append(
                    {
                        "threshold_amount_wan": threshold,
                        "estimate": estimate,
                        "confidence": _safe_float(point.get("fit_confidence")) or 0.75,
                        "sample_index": sample_index,
                        "code": code,
                        "allocated_accounts": allocated_accounts,
                    }
                )
            state = _weighted_isotonic_state(
                direct_observations,
                current_index=sample_index,
                half_life_samples=half_life_samples,
                normalize_by_allocated_accounts=update_mode == "weighted_isotonic_scaled",
            )
        else:
            raise ValueError(f"unknown update mode: {update_mode}")

        details.append(
            {
                "security_code": code,
                "apply_date": row.get("apply_date"),
                "prior_cutpoint_count": len(state),
                "point_count": len(points),
                "constraint_errors": constraint_errors,
                "fractional_cutoff": fractional,
            }
        )

    return {
        "name": name,
        "update_mode": update_mode,
        "half_life_samples": half_life_samples,
        "blend_weight": blend_weight,
        "sample_count": len(rows),
        "final_cutpoint_count": len(state),
        "fractional_metric_count": len(fractional_abs),
        "fractional_mae_wan": _mean(fractional_abs),
        "fractional_median_abs_error_wan": _median(fractional_abs),
        "fractional_mape": _mean(fractional_pct),
        "allocated_account_metric_count": len(allocated_pct),
        "allocated_account_mape": _mean(allocated_pct),
        "announcement_area_metric_count": len(area_pct),
        "announcement_area_mape": _mean(area_pct),
        "details": details,
    }


def evaluate(history_path: Path, label_path: Path) -> dict[str, Any]:
    history_rows = _load_csv(history_path)
    label_rows = subscription_ladder_labels.load_label_rows(label_path)
    rows = account_pool._merge_rows(history_rows, label_rows)
    candidates = [
        ("full_interpolation", "full_interpolation", None, None),
        ("clip_only", "clip_only", None, None),
        ("blend_25pct", "blended_interpolation", None, 0.25),
        ("blend_50pct", "blended_interpolation", None, 0.50),
        ("blend_75pct", "blended_interpolation", None, 0.75),
        ("weighted_isotonic_h0p5", "weighted_isotonic", 0.5, None),
        ("weighted_isotonic_h1", "weighted_isotonic", 1.0, None),
        ("weighted_isotonic_h2", "weighted_isotonic", 2.0, None),
        ("weighted_isotonic_h3", "weighted_isotonic", 3.0, None),
        ("weighted_isotonic_h4", "weighted_isotonic", 4.0, None),
        ("weighted_isotonic_h8", "weighted_isotonic", 8.0, None),
        ("weighted_isotonic_h12", "weighted_isotonic", 12.0, None),
        ("weighted_isotonic_h20", "weighted_isotonic", 20.0, None),
        ("weighted_isotonic_all", "weighted_isotonic", None, None),
        ("weighted_scaled_h0p5", "weighted_isotonic_scaled", 0.5, None),
        ("weighted_scaled_h1", "weighted_isotonic_scaled", 1.0, None),
        ("weighted_scaled_h2", "weighted_isotonic_scaled", 2.0, None),
        ("weighted_scaled_h4", "weighted_isotonic_scaled", 4.0, None),
        ("weighted_scaled_h8", "weighted_isotonic_scaled", 8.0, None),
        ("weighted_scaled_all", "weighted_isotonic_scaled", None, None),
    ]
    results = [
        _evaluate_strategy(
            rows,
            name=name,
            update_mode=mode,
            half_life_samples=half_life,
            blend_weight=blend_weight,
        )
        for name, mode, half_life, blend_weight in candidates
    ]
    return {
        "history_path": str(history_path),
        "label_path": str(label_path),
        "walk_forward": True,
        "sample_count": len(rows),
        "results": results,
    }


def _summary(payload: dict[str, Any]) -> list[dict[str, Any]]:
    fields = (
        "name",
        "fractional_metric_count",
        "fractional_mae_wan",
        "fractional_median_abs_error_wan",
        "fractional_mape",
        "allocated_account_metric_count",
        "allocated_account_mape",
        "announcement_area_metric_count",
        "announcement_area_mape",
        "final_cutpoint_count",
    )
    return [{field: result.get(field) for field in fields} for result in payload.get("results") or []]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Walk-forward comparison of account-pool state update strategies.")
    parser.add_argument("--history-path", type=Path, default=DEFAULT_HISTORY_PATH)
    parser.add_argument("--label-path", type=Path, default=DEFAULT_LABEL_PATH)
    parser.add_argument("--details", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = evaluate(args.history_path, args.label_path)
    output = payload if args.details else {"summary": _summary(payload)}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
