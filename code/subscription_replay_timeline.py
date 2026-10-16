from __future__ import annotations

from datetime import date
from typing import Any


def normalized_date(value: Any) -> str:
    text = str(value or "").strip().split(" ", 1)[0].split("T", 1)[0].replace("/", "-")
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def result_available_date(row: dict[str, Any]) -> str:
    # A subscription date alone never establishes when its outcome was known.
    raw = row.get("issue_result_date") or row.get("listing_date")
    available = normalized_date(raw)
    applied = normalized_date(row.get("apply_date"))
    if not available or not applied or available < applied:
        return ""
    label_date = normalized_date(row.get("manual_label_available_date"))
    return max(available, label_date)


def known_before(row: dict[str, Any], target: dict[str, Any]) -> bool:
    cutoff = normalized_date(target.get("apply_date"))
    available = result_available_date(row)
    return bool(
        cutoff and available and available < cutoff
        and normalized_date(row.get("apply_date")) < cutoff
        and str(row.get("security_code") or "") != str(target.get("security_code") or "")
    )


def account_pool_timeline(
    pool_rows: list[dict[str, Any]], history_rows: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    """Date cumulative snapshots by all their dependencies, including carried points."""
    by_code = {str(row.get("security_code") or ""): row for row in history_rows}
    timeline: list[tuple[str, dict[str, Any]]] = []
    prefix_available = ""
    for row in sorted(pool_rows, key=lambda item: (
        normalized_date(item.get("apply_date")), str(item.get("security_code") or ""),
    )):
        code = str(row.get("security_code") or "")
        source = {**row, **by_code.get(code, {})}
        available = result_available_date(source)
        # These snapshots contain the entire prefix. An undated prefix cannot
        # become safe merely because a later snapshot has a publication date.
        if not available:
            break
        prefix_available = max(prefix_available, available)
        valid_prefixes = {
            key.removesuffix("_estimate") for key, value in row.items()
            if key.startswith("accounts_ge_") and key.endswith("_estimate")
            and value not in (None, "", "--")
        }
        clean = {
            key: value for key, value in row.items()
            if not key.startswith("accounts_ge_") or key.rsplit("_", 1)[0] in valid_prefixes
        }
        # The CSV header is a union of all future cutpoints. Empty columns must
        # not introduce future interpolation knots into historical predictions.
        timeline.append((prefix_available, clean))
    return timeline


def account_pool_before(
    timeline: list[tuple[str, dict[str, Any]]], target: dict[str, Any],
) -> list[dict[str, Any]]:
    cutoff = normalized_date(target.get("apply_date"))
    if not cutoff:
        return []
    return [
        row for available, row in timeline
        if available < cutoff
        and normalized_date(row.get("apply_date")) < cutoff
        and str(row.get("security_code") or "") != str(target.get("security_code") or "")
    ]
