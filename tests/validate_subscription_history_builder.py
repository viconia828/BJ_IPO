from __future__ import annotations

import csv
import json
from pathlib import Path
import sys
import tempfile
from typing import Any


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
TOOLS_DIR = ROOT_DIR / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import build_subscription_history


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _assert_close(actual: Any, expected: float, message: str, failures: list[str], tolerance: float = 1e-6) -> None:
    try:
        current = float(actual)
    except (TypeError, ValueError):
        failures.append(f"{message}: got {actual!r}")
        return
    if abs(current - expected) > tolerance:
        failures.append(f"{message}: expected {expected}, got {current}")


def _run_ready_row_case(failures: list[str]) -> None:
    rows = build_subscription_history.build_subscription_history_rows(
        [
            {
                "SECURITY_CODE": "920001",
                "SECURITY_NAME_ABBR": "Sample IPO",
                "APPLY_DATE": "2026-06-01 00:00:00",
                "ISSUE_RESULT_DATE": "2026-06-04",
                "LISTING_DATE": "2026-06-10",
                "ISSUE_PRICE": 10.0,
                "TOTAL_ISSUE_NUM": 100.0,
                "ONLINE_ISSUE_NUM": 1000000.0,
                "TOP_APPLY_MARKETCAP": 10.0,
                "ONLINE_VA_NUM": 1234,
                "ONLINE_VA_SHARES": 60000000.0,
                "FROZEN_FUNDS_YI": 6.0,
                "FRACTIONAL_THRESHOLD_SHARES": 5000.0,
                "FRACTIONAL_TIME_PRIORITY_REQUIRED": True,
                "SUBSCRIPTION_AMOUNT_DISTRIBUTION": [
                    {"apply_shares": 5000, "accounts": 200},
                    {"apply_shares": 4900, "accounts": 300},
                ],
            }
        ],
        pdf_dir=Path(tempfile.gettempdir()) / "missing_subscription_history_pdf_dir",
    )
    _assert(len(rows) == 1, "ready row: row count mismatch", failures)
    row = rows[0]
    _assert(row.get("model_ready") is True, "ready row: model_ready should be true", failures)
    _assert(row.get("guaranteed_label_ready") is True, "ready row: guaranteed label should be true", failures)
    _assert(row.get("fractional_label_ready") is True, "ready row: fractional label should be true", failures)
    _assert(row.get("data_quality") == "ready_fractional", "ready row: quality mismatch", failures)
    _assert_close(row.get("subscription_multiple"), 60.0, "ready row: multiple mismatch", failures)
    _assert_close(row.get("guaranteed_threshold_amount_wan"), 6.0, "ready row: guaranteed amount mismatch", failures)
    _assert_close(row.get("fractional_threshold_amount_wan"), 5.0, "ready row: fractional amount mismatch", failures)
    _assert(row.get("distribution_bucket_count") == 2, "ready row: distribution bucket mismatch", failures)

    with tempfile.TemporaryDirectory() as temp_dir:
        output_path = Path(temp_dir) / "subscription_history_sample.csv"
        build_subscription_history.write_subscription_history_csv(rows, output_path)
        with output_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
            loaded = list(csv.DictReader(file_obj))
        _assert(len(loaded) == 1, "ready row csv: row count mismatch", failures)
        csv_row = loaded[0]
        _assert(csv_row.get("model_ready") == "true", "ready row csv: bool format mismatch", failures)
        distribution = json.loads(csv_row.get("subscription_distribution_json") or "[]")
        _assert(len(distribution) == 2, "ready row csv: distribution json mismatch", failures)


def _run_missing_result_case(failures: list[str]) -> None:
    rows = build_subscription_history.build_subscription_history_rows(
        [
            {
                "SECURITY_CODE": "920002",
                "SECURITY_NAME_ABBR": "Missing Result",
                "APPLY_DATE": "2026-06-01",
                "LISTING_DATE": "2026-06-10",
                "ISSUE_PRICE": 10.0,
                "TOTAL_ISSUE_NUM": 100.0,
            }
        ],
        pdf_dir=Path(tempfile.gettempdir()) / "missing_subscription_history_pdf_dir",
    )
    row = rows[0]
    _assert(row.get("model_ready") is False, "missing result: model_ready should be false", failures)
    _assert(row.get("data_quality") == "needs_result_announcement", "missing result: quality mismatch", failures)
    _assert_close(row.get("online_issue_shares"), 1000000.0, "missing result: online issue fallback mismatch", failures)
    _assert(row.get("online_issue_source") == "TOTAL_ISSUE_NUM fallback", "missing result: fallback source mismatch", failures)
    _assert_close(row.get("issue_amount_yi"), 0.1, "missing result: issue amount mismatch", failures)
    _assert("ONLINE_VA_SHARES/FROZEN_FUNDS_YI" in str(row.get("missing_fields")), "missing result: missing field absent", failures)


def _run_implausible_online_issue_case(failures: list[str]) -> None:
    rows = build_subscription_history.build_subscription_history_rows(
        [
            {
                "SECURITY_CODE": "920003",
                "SECURITY_NAME_ABBR": "Bad Online",
                "APPLY_DATE": "2026-06-01",
                "LISTING_DATE": "2026-06-10",
                "ISSUE_PRICE": 10.0,
                "TOTAL_ISSUE_NUM": 1000.0,
                "ONLINE_ISSUE_NUM": 100.0,
                "ONLINE_VA_SHARES": 50000000.0,
            }
        ],
        pdf_dir=Path(tempfile.gettempdir()) / "missing_subscription_history_pdf_dir",
    )
    row = rows[0]
    _assert_close(row.get("online_issue_shares"), 10000000.0, "bad online: fallback mismatch", failures)
    _assert(row.get("online_issue_source") == "TOTAL_ISSUE_NUM fallback", "bad online: fallback source mismatch", failures)
    _assert("ONLINE_ISSUE_NUM implausible" in str(row.get("parse_errors")), "bad online: warning missing", failures)


def _run_top_apply_time_priority_label_case(failures: list[str]) -> None:
    rows = build_subscription_history.build_subscription_history_rows(
        [
            {
                "SECURITY_CODE": "920004",
                "SECURITY_NAME_ABBR": "Time Race",
                "APPLY_DATE": "2026-06-01",
                "ISSUE_RESULT_DATE": "2026-06-04",
                "LISTING_DATE": "2026-06-10",
                "ISSUE_PRICE": 10.0,
                "ONLINE_ISSUE_NUM": 1000000.0,
                "TOP_APPLY_MARKETCAP": 10.0,
                "ONLINE_VA_NUM": 20000.0,
                "ONLINE_ALLOCATED_ACCOUNTS": 10000.0,
                "ONLINE_VA_SHARES": 200000000.0,
            }
        ],
        pdf_dir=Path(tempfile.gettempdir()) / "missing_subscription_history_pdf_dir",
    )
    row = rows[0]
    _assert(row.get("top_apply_below_guaranteed") is True, "time race: top apply flag mismatch", failures)
    _assert(row.get("fractional_label_ready") is True, "time race: fractional label should be ready", failures)
    _assert(row.get("time_priority_scope") == "all_top_apply_accounts", "time race: scope mismatch", failures)
    _assert(row.get("allocation_fit_ready") is True, "time race: allocation fit should be ready", failures)
    _assert(row.get("allocation_fit_quality") == "time_priority_label", "time race: fit quality mismatch", failures)


def _run_result_date_text_case(failures: list[str]) -> None:
    text = "发行人：示例股份有限公司\n日期：2026年1月9日"
    _assert(
        build_subscription_history._extract_issue_result_date_from_text(text) == "2026-01-09",
        "result date: chinese date mismatch",
        failures,
    )


def main() -> int:
    failures: list[str] = []
    _run_ready_row_case(failures)
    _run_missing_result_case(failures)
    _run_implausible_online_issue_case(failures)
    _run_top_apply_time_priority_label_case(failures)
    _run_result_date_text_case(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("OK subscription history builder validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
