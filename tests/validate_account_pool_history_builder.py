from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil
import sys
from typing import Any


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
TOOLS_DIR = ROOT_DIR / "tools"
CODE_DIR = ROOT_DIR / "code"
for path in (TOOLS_DIR, CODE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import build_account_pool_history
import subscription_ladder_labels


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


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        return list(csv.DictReader(file_obj))


def _run_manual_ladder_merge_case(failures: list[str]) -> None:
    temp_dir = ROOT_DIR / ".tmp" / "validate_account_pool_history_builder"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        history_path = temp_dir / "subscription_history_sample.csv"
        label_path = temp_dir / "subscription_ladder_labels.csv"
        points_path = temp_dir / "account_pool_history_points.csv"
        thresholds_path = temp_dir / "account_pool_history_thresholds.csv"
        summary_path = temp_dir / "account_pool_history_summary.json"

        fit = {
            "fit_quality": "rough_lot_account_fit",
            "fit_confidence": 0.7,
            "buckets": [
                {"allocated_lots": 2, "accounts": 87700, "threshold_amount_wan": 650},
                {"allocated_lots": 1, "accounts": 15500, "threshold_amount_wan": 430},
            ],
        }
        with history_path.open("w", encoding="utf-8-sig", newline="") as file_obj:
            writer = csv.DictWriter(
                file_obj,
                fieldnames=[
                    "security_code",
                    "security_name_abbr",
                    "apply_date",
                    "listing_date",
                    "issue_price",
                    "online_issue_shares",
                    "online_valid_accounts",
                    "online_allocated_accounts",
                    "top_apply_amount_wan",
                    "allocation_fit_json",
                ],
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerow(
                {
                    "security_code": "920189",
                    "security_name_abbr": "Sample",
                    "apply_date": "2026-07-01",
                    "listing_date": "2026-07-10",
                    "issue_price": "8.14",
                    "online_issue_shares": "19089000",
                    "online_valid_accounts": "574968",
                    "online_allocated_accounts": "103206",
                    "top_apply_amount_wan": "900",
                    "allocation_fit_json": json.dumps(fit, ensure_ascii=False),
                }
            )

        subscription_ladder_labels.write_label_rows(
            [
                {
                    "security_code": "920189",
                    "security_name_abbr": "Sample",
                    "apply_date": "2026-07-01",
                    "issue_price": "8.14",
                    "online_issue_shares": "19089000",
                    "top_apply_amount_wan": "900",
                    "manual_ladder": "1+0=447;1+1=631",
                    "manual_note": "test",
                }
            ],
            label_path,
        )

        summary = build_account_pool_history.build_account_pool_history(
            history_path=history_path,
            ladder_label_path=label_path,
            points_path=points_path,
            thresholds_path=thresholds_path,
            summary_path=summary_path,
            thresholds=(500.0, 800.0, 1000.0),
        )
        points = _read_csv(points_path)
        thresholds = _read_csv(thresholds_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    points_by_lot = {row.get("lot_level"): row for row in points}
    _assert(summary.get("sample_count") == 1, "summary: sample count mismatch", failures)
    _assert(summary.get("usable_point_count") == 2, "summary: usable point count mismatch", failures)
    _assert(points_by_lot.get("1", {}).get("manual_ladder_item") == "1+0=447", "point: lot 1 manual item mismatch", failures)
    _assert_close(points_by_lot.get("1", {}).get("threshold_amount_wan"), 447.0, "point: lot 1 threshold mismatch", failures)
    _assert_close(points_by_lot.get("1", {}).get("accounts_ge_threshold"), 103200.0, "point: lot 1 accounts mismatch", failures)
    _assert_close(points_by_lot.get("2", {}).get("threshold_amount_wan"), 631.0, "point: lot 2 threshold mismatch", failures)
    _assert_close(points_by_lot.get("2", {}).get("accounts_ge_threshold"), 87700.0, "point: lot 2 accounts mismatch", failures)

    threshold_row = thresholds[0] if thresholds else {}
    _assert(
        threshold_row.get("accounts_ge_500w_basis") == "linear_between_observed_thresholds",
        "threshold: 500w basis mismatch",
        failures,
    )
    _assert_close(
        threshold_row.get("accounts_ge_500w_lb"),
        87700.0,
        "threshold: 500w lower bound mismatch",
        failures,
    )
    _assert_close(
        threshold_row.get("accounts_ge_500w_ub"),
        103200.0,
        "threshold: 500w upper bound mismatch",
        failures,
    )
    _assert(threshold_row.get("accounts_ge_800w_estimate") == "", "threshold: 800w estimate should be blank", failures)
    _assert(
        threshold_row.get("accounts_ge_800w_basis") == "above_top_observed_threshold",
        "threshold: 800w basis mismatch",
        failures,
    )
    _assert_close(
        threshold_row.get("accounts_ge_1000w_estimate"),
        0.0,
        "threshold: 1000w above top apply estimate mismatch",
        failures,
    )
    _assert(
        threshold_row.get("accounts_ge_1000w_basis") == "above_top_apply_zero",
        "threshold: 1000w basis mismatch",
        failures,
    )


def main() -> int:
    failures: list[str] = []
    _run_manual_ladder_merge_case(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("OK account pool history builder validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
