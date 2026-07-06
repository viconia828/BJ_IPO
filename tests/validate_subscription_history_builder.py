from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
TOOLS_DIR = ROOT_DIR / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import build_subscription_history
import pdf_parser
import subscription_ladder_labels


def _reset_temp_dir(name: str) -> Path:
    temp_dir = ROOT_DIR / ".tmp" / name
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


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

    temp_dir = _reset_temp_dir("validate_subscription_history_builder")
    try:
        output_path = temp_dir / "subscription_history_sample.csv"
        build_subscription_history.write_subscription_history_csv(rows, output_path)
        with output_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
            loaded = list(csv.DictReader(file_obj))
        _assert(len(loaded) == 1, "ready row csv: row count mismatch", failures)
        csv_row = loaded[0]
        _assert(csv_row.get("model_ready") == "true", "ready row csv: bool format mismatch", failures)
        distribution = json.loads(csv_row.get("subscription_distribution_json") or "[]")
        _assert(len(distribution) == 2, "ready row csv: distribution json mismatch", failures)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


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


def _run_download_skip_codes_case(failures: list[str]) -> None:
    calls: list[tuple[str, str]] = []
    original_download = build_subscription_history._download_missing_document

    def fake_download(code: str, pdf_dir: Path, document: str, **kwargs: Any) -> tuple[Path | None, str]:
        calls.append((code, document))
        return None, "forced download call"

    build_subscription_history._download_missing_document = fake_download
    try:
        rows = build_subscription_history.build_subscription_history_rows(
            [
                {
                    "SECURITY_CODE": "920005",
                    "SECURITY_NAME_ABBR": "Skip Download",
                    "APPLY_DATE": "2026-06-01",
                    "LISTING_DATE": "2026-06-10",
                    "ISSUE_PRICE": 10.0,
                    "TOTAL_ISSUE_NUM": 100.0,
                }
            ],
            pdf_dir=Path(tempfile.gettempdir()) / "missing_subscription_history_pdf_dir",
            download_missing_issue=True,
            download_missing_result=True,
            download_skip_codes={"920005"},
        )
    finally:
        build_subscription_history._download_missing_document = original_download

    _assert(calls == [], f"download skip: should not call downloader, got {calls}", failures)
    row = rows[0]
    _assert(row.get("issue_pdf_found") is False, "download skip: issue should remain missing", failures)
    _assert(row.get("result_pdf_found") is False, "download skip: result should remain missing", failures)
    _assert(row.get("download_errors") == "", "download skip: should not record download error", failures)


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
    _assert_close(row.get("allocation_fit_confidence"), 0.95, "time race: fit confidence mismatch", failures)
    _assert(row.get("allocation_fit_usable_for_tuning") is True, "time race: tuning usability mismatch", failures)
    residuals = row.get("allocation_fit_residual_json") or {}
    _assert(residuals.get("allocated_account_residual") == 0, "time race: account residual mismatch", failures)
    _assert(residuals.get("allocated_lot_residual") == 0, "time race: lot residual mismatch", failures)


def _run_ladder_label_only_items_are_augmented_case(failures: list[str]) -> None:
    items = [{"SECURITY_CODE": "920010", "SECURITY_NAME_ABBR": "Replay"}]
    label_rows = [
        {"security_code": "920010", "manual_ladder": "1+0=100"},
        {
            "security_code": "920999",
            "security_name_abbr": "Label Only",
            "apply_date": "2026-07-01",
            "issue_price": "8.14",
            "online_issue_shares": "1000000",
            "top_apply_amount_wan": "500",
            "manual_ladder": "1+0=100",
        },
        {"security_code": "920998", "manual_ladder": ""},
    ]
    augmented, added_count = build_subscription_history._augment_items_with_ladder_labels(items, label_rows)
    by_code = {str(item.get("SECURITY_CODE") or ""): item for item in augmented}
    _assert(added_count == 1, "label-only augmentation: added count mismatch", failures)
    _assert(set(by_code) == {"920010", "920999"}, "label-only augmentation: code set mismatch", failures)
    _assert(by_code["920999"].get("SECURITY_NAME_ABBR") == "Label Only", "label-only augmentation: name missing", failures)
    _assert(by_code["920999"].get("ISSUE_PRICE") == "8.14", "label-only augmentation: issue price missing", failures)


def _run_history_table_applies_manual_ladder_case(failures: list[str]) -> None:
    temp_dir = _reset_temp_dir("validate_subscription_history_manual_ladder")
    try:
        dataset_path = temp_dir / "replay_dataset.json"
        output_path = temp_dir / "subscription_history_sample.csv"
        label_path = temp_dir / "subscription_ladder_labels.csv"
        dataset_path.write_text(
            json.dumps(
                {
                    "schema": "offline_tuning_replay_v1",
                    "items": [
                        {
                            "SECURITY_CODE": "920777",
                            "SECURITY_NAME_ABBR": "Manual Ladder",
                            "APPLY_DATE": "2026-06-29",
                            "ISSUE_PRICE": 8.14,
                            "TOTAL_ISSUE_NUM": 2121.0,
                            "ONLINE_ISSUE_NUM": 19089000.0,
                            "TOP_APPLY_MARKETCAP": 776.8816,
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        subscription_ladder_labels.write_label_rows(
            [
                {
                    "security_code": "920777",
                    "security_name_abbr": "Manual Ladder",
                    "apply_date": "2026-06-29",
                    "issue_price": "8.14",
                    "online_issue_shares": "19089000",
                    "top_apply_amount_wan": "776.8816",
                    "manual_ladder": "1+0=447;1+1=631",
                }
            ],
            label_path,
        )
        build_subscription_history.build_subscription_history_table(
            dataset_path=dataset_path,
            output_path=output_path,
            ladder_label_path=label_path,
            pdf_dir=temp_dir / "missing_pdfs",
        )
        with output_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
            row = next(csv.DictReader(file_obj))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    _assert_close(row.get("guaranteed_threshold_amount_wan"), 447.0, "manual ladder table: guaranteed mismatch", failures)
    _assert_close(row.get("fractional_threshold_amount_wan"), 631.0, "manual ladder table: fractional mismatch", failures)
    _assert(row.get("fractional_threshold_source") == "manual_ladder", "manual ladder table: fractional source mismatch", failures)
    _assert(row.get("fractional_label_ready") == "true", "manual ladder table: fractional ready mismatch", failures)


def _run_new_model_ready_row_overrides_existing_case(failures: list[str]) -> None:
    new_row = {
        "security_code": "920006",
        "security_name_abbr": "New Result",
        "model_ready": "true",
        "online_valid_shares": "123000000",
    }
    existing_row = {
        "security_code": "920006",
        "security_name_abbr": "Old Result",
        "model_ready": "true",
        "guaranteed_label_ready": "true",
        "fractional_label_ready": "true",
        "online_valid_shares": "999000000",
        "frozen_funds_yi": "99",
        "allocation_fit_json": "{}",
    }
    merged = build_subscription_history._merge_existing_history_rows([new_row], [existing_row])
    row = merged[0] if merged else {}
    _assert(row.get("security_name_abbr") == "New Result", "merge: model_ready new row should override old row", failures)
    _assert(row.get("online_valid_shares") == "123000000", "merge: latest model_ready values should win", failures)


def _run_result_date_text_case(failures: list[str]) -> None:
    text = "发行人：示例股份有限公司\n日期：2026年1月9日"
    _assert(
        pdf_parser._extract_issue_result_date_from_text(text) == "2026-01-09",
        "result date: chinese date mismatch",
        failures,
    )


def main() -> int:
    failures: list[str] = []
    _run_ready_row_case(failures)
    _run_missing_result_case(failures)
    _run_download_skip_codes_case(failures)
    _run_implausible_online_issue_case(failures)
    _run_top_apply_time_priority_label_case(failures)
    _run_ladder_label_only_items_are_augmented_case(failures)
    _run_history_table_applies_manual_ladder_case(failures)
    _run_new_model_ready_row_overrides_existing_case(failures)
    _run_result_date_text_case(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("OK subscription history builder validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
