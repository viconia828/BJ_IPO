from __future__ import annotations

import argparse
import csv
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


def _cumulative_accounts_by_lot(buckets: list[dict[str, Any]]) -> dict[int, float]:
    if not buckets:
        return {}
    max_lots = max(int(bucket.get("allocated_lots") or 0) for bucket in buckets)
    result: dict[int, float] = {}
    for lot_level in range(1, max_lots + 1):
        result[lot_level] = sum(
            float(bucket.get("accounts") or 0.0)
            for bucket in buckets
            if int(bucket.get("allocated_lots") or 0) >= lot_level
        )
    return result


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


def _point_quality(fit: dict[str, Any], source: str, accounts: float | None) -> str:
    fit_quality = str(fit.get("fit_quality") or fit.get("method") or "")
    confidence = _safe_float(fit.get("fit_confidence"))
    if accounts is None:
        return "threshold_only"
    if "manual_ladder" in source and confidence is not None and confidence >= 0.75:
        return "strong_manual_fit"
    if fit_quality.startswith("time_priority"):
        return "strong_time_priority"
    if confidence is not None and confidence >= 0.65:
        return "rough_fit"
    return "low_confidence_fit"


def _build_points_for_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    code = _row_code(row)
    fit = _parse_json_object(row.get("allocation_fit_json"))
    buckets = _bucket_rows(fit)
    cumulative = _cumulative_accounts_by_lot(buckets)
    manual_by_lot = _manual_points(row)
    fit_by_lot = _fit_points_by_lot(buckets)
    lot_levels = sorted(set(manual_by_lot) | set(fit_by_lot))

    online_issue_shares = _safe_float(row.get("online_issue_shares"))
    online_lots_total = online_issue_shares / 100.0 if online_issue_shares is not None else None
    top_apply_below = _truthy(row.get("top_apply_below_guaranteed"))
    if not lot_levels and top_apply_below and online_lots_total:
        allocated_accounts = _safe_float(row.get("online_allocated_accounts"))
        if allocated_accounts is None or abs(allocated_accounts - online_lots_total) <= max(2.0, online_lots_total * 0.02):
            lot_levels = [1]
            cumulative[1] = online_lots_total
            fit_by_lot[1] = {
                "lot_level": 1,
                "threshold_amount_wan": _safe_float(row.get("top_apply_amount_wan")),
                "basis": "top_apply_time_priority_fallback",
            }

    points: list[dict[str, Any]] = []
    for lot_level in lot_levels:
        manual = manual_by_lot.get(lot_level)
        fit_point = fit_by_lot.get(lot_level)
        threshold_amount = (
            _safe_float(manual.get("threshold_amount_wan")) if manual else _safe_float((fit_point or {}).get("threshold_amount_wan"))
        )
        if threshold_amount is None or threshold_amount <= 0:
            continue
        accounts = cumulative.get(lot_level)
        source_parts = []
        if manual:
            source_parts.append("manual_ladder")
        if accounts is not None:
            source_parts.append("allocation_fit")
        else:
            source_parts.append("manual_only")
        source = "+".join(source_parts)
        notes: list[str] = []
        if not buckets and accounts is None:
            notes.append("missing_allocation_fit_counts")
        elif fit.get("fit_quality") == "rough_lot_account_fit":
            notes.append("allocation_counts_are_model_fit")
        if manual and fit_point:
            fit_amount = _safe_float(fit_point.get("threshold_amount_wan"))
            if fit_amount is not None and abs(fit_amount - threshold_amount) > 1.0:
                notes.append(f"manual_threshold_overrides_fit:{_format_value(fit_amount)}")
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
            "account_count_basis": "cumulative_allocated_lots" if accounts is not None else "",
            "lot_level": lot_level,
            "manual_ladder_item": (manual or {}).get("manual_ladder_item", ""),
            "manual_threshold_kind": (manual or {}).get("manual_threshold_kind", ""),
            "time_priority_required": bool((manual or {}).get("time_priority_required")),
            "fit_quality": fit.get("fit_quality") or fit.get("method") or "",
            "fit_confidence": _safe_float(fit.get("fit_confidence")),
            "source": source,
            "basis": (fit_point or {}).get("basis", ""),
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
        state[key] = {
            "threshold_amount_wan": observation["threshold_amount_wan"],
            "estimate": observation["estimate"],
            "basis": "observed_threshold",
            "source": observation.get("source", ""),
            "observation_count": int(_safe_float(current.get("observation_count")) or 0) + 1,
            "last_update_code": code,
        }
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
        "fit_quality": fit.get("fit_quality") or fit.get("method") or "",
        "fit_confidence": _safe_float(fit.get("fit_confidence")),
        "point_count": len(points),
        "manual_point_count": sum(1 for point in points if "manual_ladder" in str(point.get("source") or "")),
        "usable_point_count": len(usable_points),
        "max_observed_threshold_wan": _safe_float(max_point.get("threshold_amount_wan")),
        "max_observed_accounts": _safe_float(max_point.get("accounts_ge_threshold")),
        "source": "manual_ladder+allocation_fit" if any("manual_ladder" in str(point.get("source") or "") for point in points) else ("allocation_fit" if points else ""),
        "notes": "snapshot uses observed cutpoints only; runtime callers interpolate between cutpoints",
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
    for key, item in sorted(state.items(), key=lambda entry: float(entry[1].get("threshold_amount_wan") or 0.0)):
        threshold = _safe_float(item.get("threshold_amount_wan"))
        estimate = _safe_float(item.get("estimate"))
        if threshold is None or estimate is None:
            continue
        prefix = _threshold_column_prefix(threshold)
        basis = str(item.get("basis") or "observed_threshold")
        if key not in touched_keys:
            basis = "carry_forward"
        output[f"{prefix}_estimate"] = estimate
        output[f"{prefix}_lb"] = estimate
        output[f"{prefix}_ub"] = estimate
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
) -> dict[str, Any]:
    history_rows = _load_csv_rows(history_path)
    label_rows = subscription_ladder_labels.load_label_rows(ladder_label_path)
    merged_rows = _merge_rows(history_rows, label_rows)
    points_by_code: dict[str, list[dict[str, Any]]] = {}
    point_rows: list[dict[str, Any]] = []
    threshold_rows: list[dict[str, Any]] = []
    threshold_state: dict[str, dict[str, Any]] = {}
    all_thresholds_seen: set[float] = set()
    for row in merged_rows:
        code = _row_code(row)
        points = _build_points_for_row(row)
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
