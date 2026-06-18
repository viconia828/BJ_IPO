from __future__ import annotations

import csv
import re
from pathlib import Path
from statistics import median
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LABEL_PATH = ROOT_DIR / "data" / "offline_tuning" / "subscription_ladder_labels.csv"

LADDER_LABEL_COLUMNS = (
    "security_code",
    "security_name_abbr",
    "apply_date",
    "issue_price",
    "online_issue_shares",
    "top_apply_amount_wan",
    "manual_ladder",
    "manual_note",
)

LADDER_ITEM_PATTERN = re.compile(
    r"(?P<regular>[0-9]+)\s*\+\s*(?P<fractional>[0-9]+)\s*=\s*(?P<threshold>[^;；\n\r]+)"
)


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _normalize_code(value: Any) -> str:
    return str(value or "").strip().split(".", 1)[0]


def _clean_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text.split(" ", 1)[0].replace("/", "-")


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text != "-0" else "0"
    return str(value)


def _sample_value(sample: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = sample.get(key)
        if value not in (None, "", "--"):
            return value
    return ""


def label_row_from_sample(sample: dict[str, Any]) -> dict[str, str]:
    code = _normalize_code(_sample_value(sample, "security_code", "SECURITY_CODE", "code"))
    return {
        "security_code": code,
        "security_name_abbr": str(_sample_value(sample, "security_name_abbr", "SECURITY_NAME_ABBR") or ""),
        "apply_date": _clean_date(_sample_value(sample, "apply_date", "APPLY_DATE")),
        "issue_price": _format_value(_safe_float(_sample_value(sample, "issue_price", "ISSUE_PRICE"))),
        "online_issue_shares": _format_value(
            _safe_float(_sample_value(sample, "online_issue_shares", "ONLINE_ISSUE_NUM"))
        ),
        "top_apply_amount_wan": _format_value(
            _safe_float(_sample_value(sample, "top_apply_amount_wan", "TOP_APPLY_MARKETCAP"))
        ),
        "manual_ladder": "",
        "manual_note": "",
    }


def load_label_rows(path: str | Path = DEFAULT_LABEL_PATH) -> list[dict[str, str]]:
    label_path = Path(path)
    if not label_path.exists():
        return []
    with label_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        return [
            {column: str(row.get(column) or "") for column in LADDER_LABEL_COLUMNS}
            for row in csv.DictReader(file_obj)
            if _normalize_code(row.get("security_code"))
        ]


def write_label_rows(rows: list[dict[str, Any]], path: str | Path = DEFAULT_LABEL_PATH) -> Path:
    label_path = Path(path)
    label_path.parent.mkdir(parents=True, exist_ok=True)
    clean_rows = sorted(rows, key=lambda row: _normalize_code(row.get("security_code")))
    with label_path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=list(LADDER_LABEL_COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in clean_rows:
            writer.writerow({column: _format_value(row.get(column)) for column in LADDER_LABEL_COLUMNS})
    return label_path


def sync_label_rows(
    samples: list[dict[str, Any]],
    path: str | Path = DEFAULT_LABEL_PATH,
) -> dict[str, Any]:
    existing_rows = load_label_rows(path)
    existing_by_code = {_normalize_code(row.get("security_code")): dict(row) for row in existing_rows}
    added_codes: list[str] = []
    updated_rows: list[dict[str, Any]] = []
    for sample in samples:
        sample_row = label_row_from_sample(sample)
        code = _normalize_code(sample_row.get("security_code"))
        if not code:
            continue
        if code not in existing_by_code:
            existing_by_code[code] = sample_row
            added_codes.append(code)
            continue
        current = existing_by_code[code]
        manual_ladder = current.get("manual_ladder", "")
        manual_note = current.get("manual_note", "")
        for key in ("security_name_abbr", "apply_date", "issue_price", "online_issue_shares", "top_apply_amount_wan"):
            if sample_row.get(key):
                current[key] = sample_row[key]
        current["manual_ladder"] = manual_ladder
        current["manual_note"] = manual_note

    updated_rows = list(existing_by_code.values())
    if added_codes or len(updated_rows) != len(existing_rows):
        write_label_rows(updated_rows, path)
    else:
        for old_row, new_row in zip(sorted(existing_rows, key=lambda r: r["security_code"]), sorted(updated_rows, key=lambda r: r["security_code"])):
            if any(str(old_row.get(column) or "") != str(new_row.get(column) or "") for column in LADDER_LABEL_COLUMNS):
                write_label_rows(updated_rows, path)
                break
    return {
        "path": str(Path(path)),
        "row_count": len(updated_rows),
        "filled_count": sum(1 for row in updated_rows if str(row.get("manual_ladder") or "").strip()),
        "added_codes": added_codes,
    }


def parse_manual_ladder(text: Any, top_apply_amount_wan: Any = None) -> list[dict[str, Any]]:
    raw_text = str(text or "").strip()
    if not raw_text:
        return []
    top_apply = _safe_float(top_apply_amount_wan)
    items: list[dict[str, Any]] = []
    for match in LADDER_ITEM_PATTERN.finditer(raw_text):
        regular_lots = int(match.group("regular"))
        fractional_lots = int(match.group("fractional"))
        threshold_text = match.group("threshold").strip()
        is_top_apply = "顶格" in threshold_text
        time_priority = "抢时间" in threshold_text
        amount = top_apply if is_top_apply else _safe_float(threshold_text)
        threshold_kind = "guaranteed" if fractional_lots == 0 else "fractional"
        if is_top_apply:
            threshold_kind = "top_apply_time_priority" if time_priority else "top_apply"
        items.append(
            {
                "regular_lots": regular_lots,
                "fractional_lots": fractional_lots,
                "total_lots": regular_lots + fractional_lots,
                "threshold_amount_wan": amount,
                "threshold_text": threshold_text,
                "threshold_kind": threshold_kind,
                "time_priority_required": time_priority,
            }
        )
    return items


def derive_fields_from_ladder(row: dict[str, Any]) -> dict[str, Any]:
    labels = parse_manual_ladder(row.get("manual_ladder"), row.get("top_apply_amount_wan"))
    if not labels:
        return {"manual_ladder_label_ready": False, "manual_ladder_items": []}

    issue_price = _safe_float(row.get("issue_price"))
    online_issue_shares = _safe_float(row.get("online_issue_shares"))
    top_apply = _safe_float(row.get("top_apply_amount_wan"))
    one_lot_estimates: list[float] = []
    for item in labels:
        regular_lots = int(item.get("regular_lots") or 0)
        fractional_lots = int(item.get("fractional_lots") or 0)
        amount = _safe_float(item.get("threshold_amount_wan"))
        if regular_lots > 0 and fractional_lots == 0 and amount and amount > 0:
            one_lot_estimates.append(amount / regular_lots)

    derived: dict[str, Any] = {
        "manual_ladder_label_ready": True,
        "manual_ladder_items": labels,
        "manual_ladder_item_count": len(labels),
    }
    if not one_lot_estimates:
        return derived

    one_lot_amount = median(one_lot_estimates)
    derived["manual_ladder_guaranteed_amount_wan"] = one_lot_amount
    if top_apply is not None:
        derived["manual_ladder_top_apply_below_guaranteed"] = top_apply < one_lot_amount
    if issue_price and issue_price > 0 and online_issue_shares and online_issue_shares > 0:
        threshold_shares = one_lot_amount * 10000 / issue_price
        if threshold_shares > 0:
            allocation_ratio = 100 / threshold_shares
            valid_shares = online_issue_shares / allocation_ratio
            frozen_funds_yi = valid_shares * issue_price / 100000000
            derived.update(
                {
                    "manual_ladder_valid_subscription_shares": valid_shares,
                    "manual_ladder_frozen_funds_yi": frozen_funds_yi,
                    "manual_ladder_allocation_rate_pct": allocation_ratio * 100,
                    "manual_ladder_subscription_multiple": valid_shares / online_issue_shares,
                }
            )
    return derived


def apply_labels_to_history_rows(
    history_rows: list[dict[str, Any]],
    label_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows_by_code = {_normalize_code(row.get("security_code")): dict(row) for row in history_rows}
    for label_row in label_rows:
        code = _normalize_code(label_row.get("security_code"))
        if not code:
            continue
        row = rows_by_code.get(code, {})
        row.update(
            {
                "security_code": code,
                "security_name_abbr": row.get("security_name_abbr") or label_row.get("security_name_abbr", ""),
                "apply_date": row.get("apply_date") or label_row.get("apply_date", ""),
                "issue_price": row.get("issue_price") or label_row.get("issue_price", ""),
                "online_issue_shares": row.get("online_issue_shares") or label_row.get("online_issue_shares", ""),
                "top_apply_amount_wan": row.get("top_apply_amount_wan") or label_row.get("top_apply_amount_wan", ""),
                "manual_ladder": label_row.get("manual_ladder", ""),
                "manual_note": label_row.get("manual_note", ""),
            }
        )
        derived = derive_fields_from_ladder(row)
        if derived.get("manual_ladder_label_ready"):
            row["manual_ladder_label_ready"] = "true"
            row["manual_ladder_item_count"] = _format_value(derived.get("manual_ladder_item_count"))
            one_lot_amount = _safe_float(derived.get("manual_ladder_guaranteed_amount_wan"))
            if one_lot_amount is not None:
                row["guaranteed_threshold_amount_wan"] = _format_value(one_lot_amount)
                row["guaranteed_label_ready"] = "true"
                row["model_ready"] = "true"
                row["allocation_fit_usable_for_tuning"] = "true"
                row["data_quality"] = "manual_ladder"
                top_below = derived.get("manual_ladder_top_apply_below_guaranteed")
                if top_below is not None:
                    row["top_apply_below_guaranteed"] = "true" if bool(top_below) else "false"
                for source_key, target_key in (
                    ("manual_ladder_valid_subscription_shares", "online_valid_shares"),
                    ("manual_ladder_frozen_funds_yi", "frozen_funds_yi"),
                    ("manual_ladder_allocation_rate_pct", "allocation_rate_pct"),
                    ("manual_ladder_subscription_multiple", "subscription_multiple"),
                ):
                    value = _safe_float(derived.get(source_key))
                    if value is not None:
                        row[target_key] = _format_value(value)
                row["online_valid_source"] = "manual_ladder"
            row["fractional_label_ready"] = "true"
        elif row.get("manual_ladder"):
            row["manual_ladder_label_ready"] = "true"
        rows_by_code[code] = row
    return sorted(rows_by_code.values(), key=lambda row: (_clean_date(row.get("apply_date")), _normalize_code(row.get("security_code"))))
