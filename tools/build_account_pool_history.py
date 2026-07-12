from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
from statistics import median
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import subscription_ladder_labels


DEFAULT_HISTORY_PATH = ROOT_DIR / "data" / "offline_tuning" / "subscription_history_sample.csv"
DEFAULT_LADDER_LABEL_PATH = ROOT_DIR / "data" / "offline_tuning" / "subscription_ladder_labels.csv"
DEFAULT_POINTS_PATH = ROOT_DIR / "data" / "offline_tuning" / "account_pool_history_points.csv"
DEFAULT_THRESHOLDS_PATH = ROOT_DIR / "data" / "offline_tuning" / "account_pool_history_thresholds.csv"
DEFAULT_SUMMARY_PATH = ROOT_DIR / "data" / "offline_tuning" / "account_pool_history_summary.json"
DEFAULT_THRESHOLDS_WAN: tuple[float, ...] = ()
ACCOUNT_POOL_BUILD_VERSION = 2
UNINFORMATIVE_THRESHOLD_BASES = {
    "",
    "no_observed_points",
    "above_top_observed_threshold",
    "above_top_apply_zero",
}
POINT_COLUMNS = (
    "security_code",
    "security_name_abbr",
    "apply_date",
    "listing_date",
    "issue_price",
    "online_issue_shares",
    "online_lots_total",
    "online_valid_accounts",
    "online_allocated_accounts",
    "top_apply_amount_wan",
    "threshold_amount_wan",
    "accounts_ge_threshold",
    "account_count_basis",
    "lot_level",
    "manual_ladder_item",
    "manual_threshold_kind",
    "time_priority_required",
    "fit_quality",
    "fit_confidence",
    "point_quality",
    "source",
    "basis",
    "notes",
)


BASE_THRESHOLD_COLUMNS = (
    "security_code",
    "security_name_abbr",
    "apply_date",
    "listing_date",
    "issue_price",
    "online_issue_shares",
    "online_lots_total",
    "online_valid_accounts",
    "online_allocated_accounts",
    "top_apply_amount_wan",
    "fit_quality",
    "fit_confidence",
    "point_count",
    "manual_point_count",
    "usable_point_count",
    "account_pool_snapshot_state",
    "snapshot_cutpoint_count",
    "updated_cutpoint_count",
    "max_observed_threshold_wan",
    "max_observed_accounts",
    "source",
    "notes",
    "account_pool_build_version",
    "account_pool_input_signature",
    "account_pool_snapshot_json",
)


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    number = _safe_float(value)
    if number is None:
        return None
    return int(round(number))


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是"}


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text != "-0" else "0"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _canonical_signature(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _account_pool_input_signature(row: dict[str, Any]) -> str:
    return _canonical_signature(
        {
            "account_pool_build_version": ACCOUNT_POOL_BUILD_VERSION,
            "row": dict(row),
        }
    )


def _copy_threshold_state(state: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return json.loads(json.dumps(state, ensure_ascii=False)) if state else {}


def _threshold_state_from_row(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    parsed = _parse_json_object(row.get("account_pool_snapshot_json"))
    return {
        str(key): dict(value)
        for key, value in parsed.items()
        if isinstance(value, dict)
    }


def _incremental_rebuild_index(
    merged_rows: list[dict[str, Any]],
    existing_threshold_rows: list[dict[str, Any]],
    *,
    force_rebuild: bool,
) -> int | None:
    if force_rebuild or not existing_threshold_rows:
        return 0
    shared_count = min(len(merged_rows), len(existing_threshold_rows))
    for index in range(shared_count):
        row = merged_rows[index]
        existing = existing_threshold_rows[index]
        if _row_code(row) != _row_code(existing):
            return index
        try:
            existing_version = int(existing.get("account_pool_build_version") or 0)
        except (TypeError, ValueError):
            existing_version = 0
        if existing_version != ACCOUNT_POOL_BUILD_VERSION:
            return index
        if str(existing.get("account_pool_input_signature") or "") != _account_pool_input_signature(row):
            return index
    if len(merged_rows) != len(existing_threshold_rows):
        return shared_count
    return None


def _clean_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text.split(" ", 1)[0].replace("/", "-")


def _load_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        return [dict(row) for row in csv.DictReader(file_obj)]


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _row_code(row: dict[str, Any]) -> str:
    return str(row.get("security_code") or row.get("SECURITY_CODE") or "").strip()


def _merge_rows(
    history_rows: list[dict[str, Any]],
    label_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows_by_code = {_row_code(row): dict(row) for row in history_rows if _row_code(row)}
    for label_row in label_rows:
        code = _row_code(label_row)
        if not code:
            continue
        row = rows_by_code.get(code, {})
        for key in (
            "security_code",
            "security_name_abbr",
            "apply_date",
            "issue_price",
            "online_issue_shares",
            "top_apply_amount_wan",
        ):
            if not row.get(key) and label_row.get(key):
                row[key] = label_row.get(key)
        row["manual_ladder"] = label_row.get("manual_ladder", row.get("manual_ladder", ""))
        row["manual_note"] = label_row.get("manual_note", row.get("manual_note", ""))
        rows_by_code[code] = row
    return sorted(rows_by_code.values(), key=lambda row: (_clean_date(row.get("apply_date")), _row_code(row)))


def _bucket_rows(fit: dict[str, Any]) -> list[dict[str, Any]]:
    buckets = fit.get("buckets")
    if not isinstance(buckets, list):
        return []
    clean: list[dict[str, Any]] = []
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        allocated_lots = _safe_int(bucket.get("allocated_lots"))
        accounts = _safe_float(bucket.get("accounts"))
        threshold_amount = _safe_float(bucket.get("threshold_amount_wan"))
        if allocated_lots is None or allocated_lots <= 0 or accounts is None or accounts <= 0:
            continue
        clean.append(
            {
                "allocated_lots": allocated_lots,
                "accounts": accounts,
                "threshold_amount_wan": threshold_amount,
                "basis": str(bucket.get("basis") or ""),
            }
        )
    return clean


def _manual_points(row: dict[str, Any]) -> dict[int, dict[str, Any]]:
    points: dict[int, dict[str, Any]] = {}
    top_apply = _safe_float(row.get("top_apply_amount_wan"))
    for item in subscription_ladder_labels.parse_manual_ladder(row.get("manual_ladder"), top_apply):
        lot_level = _safe_int(item.get("total_lots"))
        amount = _safe_float(item.get("threshold_amount_wan"))
        if lot_level is None or lot_level <= 0 or amount is None or amount <= 0:
            continue
        points[lot_level] = {
            "lot_level": lot_level,
            "threshold_amount_wan": amount,
            "manual_ladder_item": "{regular}+{fractional}={text}".format(
                regular=int(item.get("regular_lots") or 0),
                fractional=int(item.get("fractional_lots") or 0),
                text=str(item.get("threshold_text") or ""),
            ),
            "manual_threshold_kind": str(item.get("threshold_kind") or ""),
            "time_priority_required": bool(item.get("time_priority_required")),
        }
    return points


def _fit_points_by_lot(buckets: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    points: dict[int, dict[str, Any]] = {}
    for bucket in buckets:
        lot_level = int(bucket.get("allocated_lots") or 0)
        amount = _safe_float(bucket.get("threshold_amount_wan"))
        if lot_level <= 0 or amount is None or amount <= 0:
            continue
        current = points.get(lot_level)
        if current is None or amount > float(current.get("threshold_amount_wan") or 0.0):
            points[lot_level] = {
                "lot_level": lot_level,
                "threshold_amount_wan": amount,
                "basis": str(bucket.get("basis") or ""),
            }
    return points


def _state_curve_points(state: dict[str, dict[str, Any]]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for item in state.values():
        threshold = _safe_float(item.get("threshold_amount_wan"))
        estimate = _safe_float(item.get("estimate"))
        if threshold is None or threshold <= 0 or estimate is None:
            continue
        points.append((float(threshold), max(float(estimate), 0.0)))
    return sorted(points)


def _estimate_state_accounts_ge(state: dict[str, dict[str, Any]], amount_wan: float) -> float | None:
    points = _state_curve_points(state)
    if not points:
        return None
    if amount_wan <= points[0][0]:
        return points[0][1]
    if amount_wan >= points[-1][0]:
        return points[-1][1]
    for (lower_amount, lower_accounts), (upper_amount, upper_accounts) in zip(points, points[1:]):
        if lower_amount <= amount_wan <= upper_amount:
            if abs(upper_amount - lower_amount) < 1e-9:
                return min(lower_accounts, upper_accounts)
            ratio = (amount_wan - lower_amount) / (upper_amount - lower_amount)
            return lower_accounts + ratio * (upper_accounts - lower_accounts)
    return None


def _estimate_state_prior_stats(
    state: dict[str, dict[str, Any]],
    amount_wan: float,
) -> tuple[float | None, float | None]:
    points: list[tuple[float, float, float]] = []
    for item in state.values():
        threshold = _safe_float(item.get("threshold_amount_wan"))
        estimate = _safe_float(item.get("estimate"))
        if threshold is None or threshold <= 0 or estimate is None:
            continue
        deviation = _safe_float(item.get("deviation"))
        if deviation is None:
            deviation = max(float(estimate) * 0.03, 1000.0)
        points.append((float(threshold), max(float(estimate), 0.0), max(float(deviation), 1000.0)))
    points.sort()
    if not points:
        return None, None
    if amount_wan <= points[0][0]:
        return points[0][1], points[0][2]
    if amount_wan >= points[-1][0]:
        return points[-1][1], points[-1][2]
    for lower, upper in zip(points, points[1:]):
        if lower[0] <= amount_wan <= upper[0]:
            ratio = (amount_wan - lower[0]) / max(upper[0] - lower[0], 1e-9)
            estimate = lower[1] + ratio * (upper[1] - lower[1])
            deviation = lower[2] + ratio * (upper[2] - lower[2])
            return estimate, deviation
    return None, None


def _complete_lot_thresholds(
    *,
    manual_by_lot: dict[int, dict[str, Any]],
    fit_by_lot: dict[int, dict[str, Any]],
    max_lots: int,
    top_apply_amount_wan: float | None,
) -> dict[int, float]:
    anchors: dict[int, float] = {}
    for lot_level, point in fit_by_lot.items():
        amount = _safe_float(point.get("threshold_amount_wan"))
        if amount is not None and amount > 0:
            anchors[lot_level] = float(amount)
    for lot_level, point in manual_by_lot.items():
        amount = _safe_float(point.get("threshold_amount_wan"))
        if amount is not None and amount > 0:
            anchors[lot_level] = float(amount)
    if top_apply_amount_wan is not None and top_apply_amount_wan > 0:
        anchors.setdefault(max_lots, float(top_apply_amount_wan))
    if not anchors:
        return {}

    levels = sorted(anchors)
    completed: dict[int, float] = {}
    for lot_level in range(1, max_lots + 1):
        if lot_level in anchors:
            completed[lot_level] = anchors[lot_level]
            continue
        lower_levels = [level for level in levels if level < lot_level]
        upper_levels = [level for level in levels if level > lot_level]
        lower = max(lower_levels) if lower_levels else None
        upper = min(upper_levels) if upper_levels else None
        if lower is not None and upper is not None:
            ratio = (lot_level - lower) / (upper - lower)
            completed[lot_level] = anchors[lower] + ratio * (anchors[upper] - anchors[lower])
        elif lower is not None:
            previous = max((level for level in levels if level < lower), default=None)
            step = (anchors[lower] - anchors[previous]) / (lower - previous) if previous is not None else anchors[lower]
            completed[lot_level] = anchors[lower] + max(step, 0.01) * (lot_level - lower)
        elif upper is not None:
            following = min((level for level in levels if level > upper), default=None)
            step = (anchors[following] - anchors[upper]) / (following - upper) if following is not None else anchors[upper]
            completed[lot_level] = max(0.01, anchors[upper] - max(step, 0.01) * (upper - lot_level))

    previous_amount = 0.0
    for lot_level in range(1, max_lots + 1):
        amount = max(float(completed.get(lot_level) or 0.0), previous_amount + 0.01)
        if top_apply_amount_wan is not None and lot_level == max_lots:
            amount = max(float(top_apply_amount_wan), previous_amount + 0.01)
        completed[lot_level] = amount
        previous_amount = amount
    return completed


def _announcement_constrained_cumulative(
    *,
    allocated_accounts: float,
    total_lots: float,
    thresholds_by_lot: dict[int, float],
    prior_state: dict[str, dict[str, Any]],
) -> tuple[dict[int, float], str]:
    max_lots = max(thresholds_by_lot, default=0)
    if max_lots <= 0 or allocated_accounts <= 0 or total_lots < allocated_accounts:
        return {}, "unavailable"

    first_accounts = float(allocated_accounts)
    target_extra = min(max(float(total_lots) - first_accounts, 0.0), first_accounts * max(max_lots - 1, 0))
    if max_lots == 1:
        return {1: first_accounts}, "announcement_exact"

    raw: list[float] = []
    deviations: list[float] = []
    used_history_prior = False
    first_amount = thresholds_by_lot.get(1, min(thresholds_by_lot.values()))
    top_amount = thresholds_by_lot.get(max_lots, max(thresholds_by_lot.values()))
    span = max(top_amount - first_amount, 0.01)
    for lot_level in range(2, max_lots + 1):
        amount = thresholds_by_lot[lot_level]
        prior, deviation = _estimate_state_prior_stats(prior_state, amount)
        if prior is not None:
            used_history_prior = True
            value = min(max(float(prior), 0.0), first_accounts)
            tail_flex = 1.0 + 0.75 * (lot_level - 2) / max(max_lots - 2, 1)
            deviations.append(min(max(float(deviation or 0.0) * tail_flex, 1000.0), first_accounts))
        else:
            progress = min(max((amount - first_amount) / span, 0.0), 1.0)
            value = first_accounts * max((1.0 - progress) ** 0.75, 0.01)
            deviations.append(max(value * 0.2, 1000.0))
        raw.append(max(value, first_accounts * 0.0001))

    previous = first_accounts
    for index, value in enumerate(raw):
        raw[index] = min(value, previous)
        previous = raw[index]

    if target_extra <= 0:
        scaled = [0.0 for _ in raw]
    elif used_history_prior and raw:
        def shifted_values(shift: float, expansion: float) -> list[float]:
            values = [
                min(
                    max(
                        center
                        + shift
                        * deviation
                        * (1.0 + (expansion - 1.0) * index / max(len(raw) - 1, 1)),
                        0.0,
                    ),
                    first_accounts,
                )
                for index, (center, deviation) in enumerate(zip(raw, deviations))
            ]
            previous_value = first_accounts
            for index, value in enumerate(values):
                values[index] = min(value, previous_value)
                previous_value = values[index]
            return values

        expansion = 1.0
        for _ in range(20):
            lower_total = sum(shifted_values(-1.0, expansion))
            upper_total = sum(shifted_values(1.0, expansion))
            if lower_total - 1e-6 <= target_extra <= upper_total + 1e-6:
                break
            expansion *= 2.0
        lower_shift = -1.0
        upper_shift = 1.0
        for _ in range(80):
            shift = (lower_shift + upper_shift) / 2.0
            current = sum(shifted_values(shift, expansion))
            if current < target_extra:
                lower_shift = shift
            else:
                upper_shift = shift
        scaled = shifted_values(upper_shift, expansion)
    else:
        upper_scale = 1.0
        while sum(min(first_accounts, value * upper_scale) for value in raw) < target_extra:
            upper_scale *= 2.0
            if upper_scale > 1e9:
                break
        lower_scale = 0.0
        for _ in range(80):
            scale = (lower_scale + upper_scale) / 2.0
            current = sum(min(first_accounts, value * scale) for value in raw)
            if current < target_extra:
                lower_scale = scale
            else:
                upper_scale = scale
        scaled = [min(first_accounts, value * upper_scale) for value in raw]

    result = {1: first_accounts}
    previous = first_accounts
    for lot_level, value in enumerate(scaled, start=2):
        result[lot_level] = min(max(value, 0.0), previous)
        previous = result[lot_level]
    return result, "history_prior" if used_history_prior else "cold_start"


def _point_quality(fit: dict[str, Any], source: str, accounts: float | None) -> str:
    fit_quality = str(fit.get("fit_quality") or fit.get("method") or "")
    confidence = _safe_float(fit.get("fit_confidence"))
    if accounts is None:
        return "threshold_only"
    if "announcement_aggregate" in source and "manual_ladder" in source:
        return "announcement_constrained_manual"
    if "manual_ladder" in source and confidence is not None and confidence >= 0.75:
        return "strong_manual_fit"
    if fit_quality.startswith("time_priority"):
        return "strong_time_priority"
    if confidence is not None and confidence >= 0.65:
        return "rough_fit"
    return "low_confidence_fit"


def _build_points_for_row(
    row: dict[str, Any],
    prior_state: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    code = _row_code(row)
    fit = _parse_json_object(row.get("allocation_fit_json"))
    buckets = _bucket_rows(fit)
    manual_by_lot = _manual_points(row)
    fit_by_lot = _fit_points_by_lot(buckets)
    if not manual_by_lot:
        return []

    online_issue_shares = _safe_float(row.get("online_issue_shares"))
    online_lots_total = online_issue_shares / 100.0 if online_issue_shares is not None else None
    allocated_accounts = _safe_float(row.get("online_allocated_accounts"))
    if online_lots_total is None or allocated_accounts is None or allocated_accounts <= 0:
        return []

    average_lots = online_lots_total / allocated_accounts
    max_lots = max(
        max(manual_by_lot, default=1),
        max(fit_by_lot, default=1),
        int(math.ceil(average_lots)),
    )
    thresholds_by_lot = _complete_lot_thresholds(
        manual_by_lot=manual_by_lot,
        fit_by_lot=fit_by_lot,
        max_lots=max_lots,
        top_apply_amount_wan=_safe_float(row.get("top_apply_amount_wan")),
    )
    cumulative, prior_basis = _announcement_constrained_cumulative(
        allocated_accounts=allocated_accounts,
        total_lots=online_lots_total,
        thresholds_by_lot=thresholds_by_lot,
        prior_state=prior_state or {},
    )

    points: list[dict[str, Any]] = []
    for lot_level in sorted(manual_by_lot):
        manual = manual_by_lot.get(lot_level)
        threshold_amount = _safe_float((manual or {}).get("threshold_amount_wan"))
        if threshold_amount is None or threshold_amount <= 0:
            continue
        accounts = cumulative.get(lot_level)
        source = "manual_ladder+announcement_aggregate"
        notes = [
            "announcement_accounts_and_lots_hard_constraints",
            f"shape_prior:{prior_basis}",
            "compressed_extra_lots_counts_excluded",
        ]
        point = {
            "security_code": code,
            "security_name_abbr": row.get("security_name_abbr") or "",
            "apply_date": _clean_date(row.get("apply_date")),
            "listing_date": _clean_date(row.get("listing_date")),
            "issue_price": _safe_float(row.get("issue_price")),
            "online_issue_shares": online_issue_shares,
            "online_lots_total": online_lots_total,
            "online_valid_accounts": _safe_float(row.get("online_valid_accounts")),
            "online_allocated_accounts": _safe_float(row.get("online_allocated_accounts")),
            "top_apply_amount_wan": _safe_float(row.get("top_apply_amount_wan")),
            "threshold_amount_wan": threshold_amount,
            "accounts_ge_threshold": accounts,
            "account_count_basis": "announcement_constrained_cumulative_lots" if accounts is not None else "",
            "lot_level": lot_level,
            "manual_ladder_item": (manual or {}).get("manual_ladder_item", ""),
            "manual_threshold_kind": (manual or {}).get("manual_threshold_kind", ""),
            "time_priority_required": bool((manual or {}).get("time_priority_required")),
            "fit_quality": "announcement_constrained_ladder",
            "fit_confidence": 0.85 if prior_basis == "history_prior" else 0.75,
            "source": source,
            "basis": f"announcement_constrained_{prior_basis}",
            "notes": "|".join(notes),
        }
        point["point_quality"] = _point_quality(fit, source, accounts)
        points.append(point)
    return sorted(points, key=lambda item: (_safe_float(item.get("threshold_amount_wan")) or 0.0, item.get("lot_level") or 0))


def _threshold_column_prefix(threshold: float) -> str:
    text = _format_value(float(threshold))
    safe_text = text.replace("-", "m").replace(".", "p")
    return f"accounts_ge_{safe_text}w"


def _threshold_state_key(threshold: float) -> str:
    return _format_value(float(threshold))


def _observed_state_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    observed_by_key: dict[str, dict[str, Any]] = {}
    for point in points:
        threshold = _safe_float(point.get("threshold_amount_wan"))
        accounts = _safe_float(point.get("accounts_ge_threshold"))
        if threshold is None or threshold <= 0 or accounts is None:
            continue
        key = _threshold_state_key(threshold)
        candidate = {
            "key": key,
            "threshold_amount_wan": float(threshold),
            "estimate": max(float(accounts), 0.0),
            "source": str(point.get("source") or ""),
        }
        current = observed_by_key.get(key)
        if current is None or float(candidate["estimate"]) > float(current["estimate"]):
            observed_by_key[key] = candidate
    return sorted(observed_by_key.values(), key=lambda item: float(item["threshold_amount_wan"]))


def _cover_state_item(
    item: dict[str, Any],
    observation: dict[str, Any],
    code: str,
) -> None:
    item["estimate"] = max(float(observation["estimate"]), 0.0)
    item["deviation"] = max(float(observation.get("deviation") or 1000.0), 1000.0)
    item["basis"] = "covered_by_newer_observation"
    item["covered_by_threshold_wan"] = observation.get("threshold_amount_wan")
    item["last_update_code"] = code


def _enforce_state_monotone(
    state: dict[str, dict[str, Any]],
    touched_keys: set[str],
    code: str,
) -> None:
    previous_estimate: float | None = None
    previous_threshold: float | None = None
    for key, item in sorted(state.items(), key=lambda entry: float(entry[1].get("threshold_amount_wan") or 0.0)):
        estimate = _safe_float(item.get("estimate"))
        if estimate is None:
            continue
        estimate = max(float(estimate), 0.0)
        if previous_estimate is not None and estimate > previous_estimate:
            item["estimate"] = previous_estimate
            item["basis"] = "monotone_adjusted_by_newer_observation"
            item["covered_by_threshold_wan"] = previous_threshold
            item["last_update_code"] = code
            touched_keys.add(key)
            estimate = previous_estimate
        else:
            item["estimate"] = estimate
        previous_estimate = estimate
        previous_threshold = _safe_float(item.get("threshold_amount_wan"))


def _update_threshold_state(
    state: dict[str, dict[str, Any]],
    points: list[dict[str, Any]],
    code: str,
) -> set[str]:
    observations = _observed_state_points(points)
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
            if threshold is None or not (lower_threshold < threshold < upper_threshold):
                continue
            ratio = (threshold - lower_threshold) / (upper_threshold - lower_threshold)
            old_estimate = _safe_float(item.get("estimate"))
            interpolated = max(lower_estimate + ratio * (upper_estimate - lower_estimate), 0.0)
            lower_deviation = float(lower_observation.get("deviation") or 1000.0)
            upper_deviation = float(upper_observation.get("deviation") or 1000.0)
            deviation = lower_deviation + ratio * (upper_deviation - lower_deviation)
            if old_estimate is not None:
                deviation = max(deviation, abs(interpolated - old_estimate) * 0.5)
            item["estimate"] = interpolated
            item["deviation"] = max(deviation, interpolated * 0.03, 1000.0)
            item["basis"] = "interpolated_by_newer_observations"
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
                _cover_state_item(item, observation, code)
                touched_keys.add(key)
            elif threshold > obs_threshold + 1e-9 and estimate > obs_estimate + 1e-9:
                _cover_state_item(item, observation, code)
                touched_keys.add(key)

    _enforce_state_monotone(state, touched_keys, code)
    return touched_keys


def _build_threshold_base_row(row: dict[str, Any], points: list[dict[str, Any]]) -> dict[str, Any]:
    fit = _parse_json_object(row.get("allocation_fit_json"))
    usable_points = [point for point in points if _safe_float(point.get("accounts_ge_threshold")) is not None]
    max_point = max(usable_points, key=lambda item: _safe_float(item.get("threshold_amount_wan")) or 0.0) if usable_points else {}
    representative_point = usable_points[0] if usable_points else {}
    top_apply_amount = _safe_float(row.get("top_apply_amount_wan"))
    online_issue_shares = _safe_float(row.get("online_issue_shares"))
    return {
        "security_code": _row_code(row),
        "security_name_abbr": row.get("security_name_abbr") or "",
        "apply_date": _clean_date(row.get("apply_date")),
        "listing_date": _clean_date(row.get("listing_date")),
        "issue_price": _safe_float(row.get("issue_price")),
        "online_issue_shares": online_issue_shares,
        "online_lots_total": online_issue_shares / 100.0 if online_issue_shares is not None else None,
        "online_valid_accounts": _safe_float(row.get("online_valid_accounts")),
        "online_allocated_accounts": _safe_float(row.get("online_allocated_accounts")),
        "top_apply_amount_wan": top_apply_amount,
        "fit_quality": representative_point.get("fit_quality") or fit.get("fit_quality") or fit.get("method") or "",
        "fit_confidence": _safe_float(representative_point.get("fit_confidence")) or _safe_float(fit.get("fit_confidence")),
        "point_count": len(points),
        "manual_point_count": sum(1 for point in points if "manual_ladder" in str(point.get("source") or "")),
        "usable_point_count": len(usable_points),
        "max_observed_threshold_wan": _safe_float(max_point.get("threshold_amount_wan")),
        "max_observed_accounts": _safe_float(max_point.get("accounts_ge_threshold")),
        "source": "manual_ladder+announcement_aggregate" if any("announcement_aggregate" in str(point.get("source") or "") for point in points) else "",
        "notes": "snapshot uses announcement-constrained manual cutpoints only; runtime callers interpolate between cutpoints",
        "account_pool_build_version": ACCOUNT_POOL_BUILD_VERSION,
        "account_pool_input_signature": _account_pool_input_signature(row),
    }


def _build_threshold_snapshot_row(
    row: dict[str, Any],
    points: list[dict[str, Any]],
    state: dict[str, dict[str, Any]],
    touched_keys: set[str],
) -> dict[str, Any]:
    output = _build_threshold_base_row(row, points)
    output["account_pool_snapshot_state"] = bool(state)
    output["snapshot_cutpoint_count"] = len(state)
    output["updated_cutpoint_count"] = len(touched_keys)
    output["account_pool_snapshot_json"] = _copy_threshold_state(state)
    for key, item in sorted(state.items(), key=lambda entry: float(entry[1].get("threshold_amount_wan") or 0.0)):
        threshold = _safe_float(item.get("threshold_amount_wan"))
        estimate = _safe_float(item.get("estimate"))
        if threshold is None or estimate is None:
            continue
        prefix = _threshold_column_prefix(threshold)
        basis = str(item.get("basis") or "observed_threshold")
        if key not in touched_keys:
            basis = "carry_forward"
        deviation = _safe_float(item.get("deviation")) or max(estimate * 0.03, 1000.0)
        output[f"{prefix}_estimate"] = estimate
        output[f"{prefix}_lb"] = max(estimate - deviation, 0.0)
        output[f"{prefix}_ub"] = estimate + deviation
        output[f"{prefix}_basis"] = basis
    return output


def _threshold_columns(thresholds: tuple[float, ...]) -> list[str]:
    columns = list(BASE_THRESHOLD_COLUMNS)
    for threshold in thresholds:
        prefix = _threshold_column_prefix(threshold)
        columns.extend((f"{prefix}_estimate", f"{prefix}_lb", f"{prefix}_ub", f"{prefix}_basis"))
    return columns

def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str] | tuple[str, ...]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _format_value(row.get(column)) for column in columns})
    return path


def build_account_pool_history(
    *,
    history_path: Path = DEFAULT_HISTORY_PATH,
    ladder_label_path: Path = DEFAULT_LADDER_LABEL_PATH,
    points_path: Path = DEFAULT_POINTS_PATH,
    thresholds_path: Path = DEFAULT_THRESHOLDS_PATH,
    summary_path: Path = DEFAULT_SUMMARY_PATH,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS_WAN,
    force_rebuild: bool = False,
) -> dict[str, Any]:
    history_rows = _load_csv_rows(history_path)
    label_rows = subscription_ladder_labels.load_label_rows(ladder_label_path)
    merged_rows = _merge_rows(history_rows, label_rows)
    existing_point_rows = _load_csv_rows(points_path)
    existing_threshold_rows = _load_csv_rows(thresholds_path)
    rebuild_index = _incremental_rebuild_index(
        merged_rows,
        existing_threshold_rows,
        force_rebuild=force_rebuild,
    )
    if rebuild_index is None:
        rebuild_index = len(merged_rows)
        rebuild_mode = "no_change"
    elif rebuild_index == 0:
        rebuild_mode = "full"
    else:
        rebuild_mode = "suffix"

    if rebuild_index > 0:
        threshold_state = _threshold_state_from_row(existing_threshold_rows[rebuild_index - 1])
        if not threshold_state:
            rebuild_index = 0
            rebuild_mode = "full"
    else:
        threshold_state = {}

    prefix_codes = {_row_code(row) for row in merged_rows[:rebuild_index]}
    point_rows: list[dict[str, Any]] = [
        dict(row) for row in existing_point_rows if _row_code(row) in prefix_codes
    ]
    threshold_rows: list[dict[str, Any]] = [
        dict(row) for row in existing_threshold_rows[:rebuild_index]
    ]
    points_by_code: dict[str, list[dict[str, Any]]] = {}
    for row in point_rows:
        points_by_code.setdefault(_row_code(row), []).append(row)
    all_thresholds_seen: set[float] = {
        float(item["threshold_amount_wan"])
        for item in threshold_state.values()
        if _safe_float(item.get("threshold_amount_wan")) is not None
    }
    for row in merged_rows[rebuild_index:]:
        code = _row_code(row)
        points = _build_points_for_row(row, threshold_state)
        points_by_code[code] = points
        point_rows.extend(points)
        touched_keys = _update_threshold_state(threshold_state, points, code)
        all_thresholds_seen.update(
            float(item["threshold_amount_wan"])
            for item in threshold_state.values()
            if _safe_float(item.get("threshold_amount_wan")) is not None
        )
        threshold_rows.append(_build_threshold_snapshot_row(row, points, threshold_state, touched_keys))

    observed_thresholds = tuple(sorted(all_thresholds_seen))
    _write_csv(points_path, point_rows, POINT_COLUMNS)
    _write_csv(thresholds_path, threshold_rows, _threshold_columns(observed_thresholds))

    usable_rows = [row for row in threshold_rows if int(_safe_float(row.get("usable_point_count")) or 0) > 0]
    recent_rows = sorted(
        [row for row in usable_rows if row.get("apply_date")],
        key=lambda row: (str(row.get("apply_date") or ""), str(row.get("security_code") or "")),
    )[-12:]
    recent_snapshot: dict[str, Any] = {}
    for threshold in observed_thresholds:
        prefix = _threshold_column_prefix(threshold)
        values = [_safe_float(row.get(f"{prefix}_estimate")) for row in recent_rows]
        informative_values = [
            _safe_float(row.get(f"{prefix}_estimate"))
            for row in recent_rows
            if str(row.get(f"{prefix}_basis") or "").strip() not in UNINFORMATIVE_THRESHOLD_BASES
        ]
        clean = [value for value in informative_values if value is not None]
        recent_snapshot[f"{prefix}_median_estimate"] = median(clean) if clean else None
        recent_snapshot[f"{prefix}_latest_usable_estimate"] = clean[-1] if clean else None
        recent_snapshot[f"{prefix}_recent_usable_count"] = len(clean)
        recent_snapshot[f"{prefix}_latest_sample_estimate"] = values[-1] if values else None
        recent_snapshot[f"{prefix}_latest_sample_basis"] = (
            recent_rows[-1].get(f"{prefix}_basis") if recent_rows else None
        )
    summary = {
        "history_path": str(history_path),
        "ladder_label_path": str(ladder_label_path),
        "points_path": str(points_path),
        "thresholds_path": str(thresholds_path),
        "summary_path": str(summary_path),
        "thresholds_wan": list(observed_thresholds),
        "requested_thresholds_wan": list(thresholds or ()),
        "sample_count": len(merged_rows),
        "history_row_count": len(history_rows),
        "label_row_count": len(label_rows),
        "point_count": len(point_rows),
        "usable_point_count": sum(1 for row in point_rows if _safe_float(row.get("accounts_ge_threshold")) is not None),
        "threshold_row_count": len(threshold_rows),
        "usable_threshold_row_count": len(usable_rows),
        "snapshot_cutpoint_count": len(threshold_state),
        "calibrated_threshold_count": len(threshold_state),
        "recent_snapshot_window": len(recent_rows),
        "recent_snapshot": recent_snapshot,
        "incremental": not force_rebuild,
        "force_rebuild": bool(force_rebuild),
        "rebuild_mode": rebuild_mode,
        "reused_prefix_count": rebuild_index,
        "rebuilt_suffix_count": max(len(merged_rows) - rebuild_index, 0),
        "rebuild_from_index": rebuild_index if rebuild_index < len(merged_rows) else None,
        "rebuild_from_code": (
            _row_code(merged_rows[rebuild_index])
            if rebuild_index < len(merged_rows)
            else ""
        ),
        "rebuild_from_date": (
            _clean_date(merged_rows[rebuild_index].get("apply_date"))
            or _clean_date(merged_rows[rebuild_index].get("listing_date"))
            if rebuild_index < len(merged_rows)
            else ""
        ),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def _parse_thresholds(raw_value: str | None) -> tuple[float, ...]:
    if not raw_value:
        return DEFAULT_THRESHOLDS_WAN
    values: list[float] = []
    for chunk in raw_value.split(","):
        text = chunk.strip()
        if not text:
            continue
        values.append(float(text))
    return tuple(values or DEFAULT_THRESHOLDS_WAN)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build historical account-pool threshold tables from subscription history.")
    parser.add_argument("--history-path", type=Path, default=DEFAULT_HISTORY_PATH)
    parser.add_argument("--ladder-label-path", type=Path, default=DEFAULT_LADDER_LABEL_PATH)
    parser.add_argument("--points-path", type=Path, default=DEFAULT_POINTS_PATH)
    parser.add_argument("--thresholds-path", type=Path, default=DEFAULT_THRESHOLDS_PATH)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY_PATH)
    parser.add_argument("--thresholds", default="", help="Deprecated: account-pool snapshots now use observed cutpoints only.")
    parser.add_argument("--force-rebuild", action="store_true", help="忽略快照签名，从第一只样本开始全量重建。")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_account_pool_history(
        history_path=args.history_path,
        ladder_label_path=args.ladder_label_path,
        points_path=args.points_path,
        thresholds_path=args.thresholds_path,
        summary_path=args.summary_path,
        thresholds=_parse_thresholds(args.thresholds),
        force_rebuild=args.force_rebuild,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            "account pool history: samples={sample_count}, points={point_count}, "
            "usable_points={usable_point_count}, threshold_rows={threshold_row_count}, output={thresholds_path}".format(
                **summary
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
