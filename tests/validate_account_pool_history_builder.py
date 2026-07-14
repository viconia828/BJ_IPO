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


def _write_history(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
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
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


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
        _write_history(
            history_path,
            [
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
            ],
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
        )
        points = _read_csv(points_path)
        thresholds = _read_csv(thresholds_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    points_by_lot = {row.get("lot_level"): row for row in points}
    _assert(summary.get("sample_count") == 1, "summary: sample count mismatch", failures)
    _assert(summary.get("usable_point_count") == 2, "summary: usable point count mismatch", failures)
    _assert(summary.get("thresholds_wan") == [447.0, 631.0], "summary: observed thresholds mismatch", failures)
    _assert(points_by_lot.get("1", {}).get("manual_ladder_item") == "1+0=447", "point: lot 1 manual item mismatch", failures)
    _assert_close(points_by_lot.get("1", {}).get("threshold_amount_wan"), 447.0, "point: lot 1 threshold mismatch", failures)
    _assert_close(points_by_lot.get("1", {}).get("accounts_ge_threshold"), 103206.0, "point: lot 1 accounts mismatch", failures)
    _assert_close(points_by_lot.get("2", {}).get("threshold_amount_wan"), 631.0, "point: lot 2 threshold mismatch", failures)
    _assert_close(points_by_lot.get("2", {}).get("accounts_ge_threshold"), 87684.0, "point: lot 2 accounts mismatch", failures)

    threshold_row = thresholds[0] if thresholds else {}
    _assert("accounts_ge_500w_estimate" not in threshold_row, "threshold: 500w should not be materialized", failures)
    _assert_close(threshold_row.get("accounts_ge_447w_estimate"), 103206.0, "threshold: 447w estimate mismatch", failures)
    _assert(threshold_row.get("accounts_ge_447w_basis") == "observed_threshold", "threshold: 447w basis mismatch", failures)
    _assert_close(threshold_row.get("accounts_ge_631w_estimate"), 87684.0, "threshold: 631w estimate mismatch", failures)
    _assert(threshold_row.get("accounts_ge_631w_basis") == "observed_threshold", "threshold: 631w basis mismatch", failures)


def _run_sequential_refinement_case(failures: list[str]) -> None:
    temp_dir = ROOT_DIR / ".tmp" / "validate_account_pool_history_refinement"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        history_path = temp_dir / "subscription_history_sample.csv"
        label_path = temp_dir / "subscription_ladder_labels.csv"
        points_path = temp_dir / "account_pool_history_points.csv"
        thresholds_path = temp_dir / "account_pool_history_thresholds.csv"
        summary_path = temp_dir / "account_pool_history_summary.json"

        rows = [
            {
                "security_code": "920001",
                "security_name_abbr": "First",
                "apply_date": "2026-01-01",
                "listing_date": "2026-01-10",
                "issue_price": "10",
                "online_issue_shares": "14000",
                "online_valid_accounts": "1000",
                "online_allocated_accounts": "100",
                "top_apply_amount_wan": "1000",
                "allocation_fit_json": json.dumps(
                    {
                        "fit_quality": "rough_lot_account_fit",
                        "fit_confidence": 0.7,
                        "buckets": [
                            {"allocated_lots": 2, "accounts": 40, "threshold_amount_wan": 600},
                            {"allocated_lots": 1, "accounts": 60, "threshold_amount_wan": 300},
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
            {
                "security_code": "920002",
                "security_name_abbr": "Second",
                "apply_date": "2026-01-02",
                "listing_date": "2026-01-11",
                "issue_price": "10",
                "online_issue_shares": "18000",
                "online_valid_accounts": "1000",
                "online_allocated_accounts": "120",
                "top_apply_amount_wan": "1000",
                "allocation_fit_json": json.dumps(
                    {
                        "fit_quality": "rough_lot_account_fit",
                        "fit_confidence": 0.7,
                        "buckets": [
                            {"allocated_lots": 2, "accounts": 60, "threshold_amount_wan": 700},
                            {"allocated_lots": 1, "accounts": 60, "threshold_amount_wan": 400},
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        _write_history(history_path, rows)
        subscription_ladder_labels.write_label_rows(
            [
                {
                    "security_code": "920001",
                    "security_name_abbr": "First",
                    "apply_date": "2026-01-01",
                    "issue_price": "10",
                    "online_issue_shares": "10000000",
                    "top_apply_amount_wan": "1000",
                    "manual_ladder": "1+0=300;1+1=600",
                    "manual_note": "",
                },
                {
                    "security_code": "920002",
                    "security_name_abbr": "Second",
                    "apply_date": "2026-01-02",
                    "issue_price": "10",
                    "online_issue_shares": "10000000",
                    "top_apply_amount_wan": "1000",
                    "manual_ladder": "1+0=400;1+1=700",
                    "manual_note": "",
                },
            ],
            label_path,
        )

        summary = build_account_pool_history.build_account_pool_history(
            history_path=history_path,
            ladder_label_path=label_path,
            points_path=points_path,
            thresholds_path=thresholds_path,
            summary_path=summary_path,
        )
        threshold_rows = _read_csv(thresholds_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    _assert(summary.get("thresholds_wan") == [300.0, 400.0, 600.0, 700.0], "refinement: cutpoints mismatch", failures)
    _assert(len(threshold_rows) == 2, "refinement: expected two threshold snapshots", failures)
    first_row = threshold_rows[0] if threshold_rows else {}
    second_row = threshold_rows[1] if len(threshold_rows) > 1 else {}
    _assert_close(first_row.get("accounts_ge_300w_estimate"), 100.0, "refinement: first 300w mismatch", failures)
    _assert_close(first_row.get("accounts_ge_600w_estimate"), 40.0, "refinement: first 600w mismatch", failures)
    _assert(first_row.get("accounts_ge_400w_estimate") == "", "refinement: first row should not backfill 400w", failures)
    _assert(first_row.get("accounts_ge_700w_estimate") == "", "refinement: first row should not backfill 700w", failures)
    _assert_close(second_row.get("accounts_ge_300w_estimate"), 120.0, "refinement: second 300w covered mismatch", failures)
    _assert(second_row.get("accounts_ge_300w_basis") == "covered_by_newer_observation", "refinement: second 300w basis mismatch", failures)
    _assert_close(second_row.get("accounts_ge_400w_estimate"), 120.0, "refinement: second 400w mismatch", failures)
    _assert_close(second_row.get("accounts_ge_600w_estimate"), 80.0, "refinement: second 600w interpolated mismatch", failures)
    _assert(second_row.get("accounts_ge_600w_basis") == "interpolated_by_newer_observations", "refinement: second 600w basis mismatch", failures)
    _assert_close(second_row.get("accounts_ge_700w_estimate"), 60.0, "refinement: second 700w mismatch", failures)


def _run_new_point_caps_conflicting_old_point_case(failures: list[str]) -> None:
    temp_dir = ROOT_DIR / ".tmp" / "validate_account_pool_history_conflict"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        history_path = temp_dir / "subscription_history_sample.csv"
        label_path = temp_dir / "subscription_ladder_labels.csv"
        points_path = temp_dir / "account_pool_history_points.csv"
        thresholds_path = temp_dir / "account_pool_history_thresholds.csv"
        summary_path = temp_dir / "account_pool_history_summary.json"

        rows = [
            {
                "security_code": "920101",
                "security_name_abbr": "Old",
                "apply_date": "2026-02-01",
                "listing_date": "2026-02-10",
                "issue_price": "10",
                "online_issue_shares": "10000000",
                "online_valid_accounts": "1000",
                "online_allocated_accounts": "50000",
                "top_apply_amount_wan": "1500",
                "allocation_fit_json": json.dumps(
                    {
                        "fit_quality": "rough_lot_account_fit",
                        "fit_confidence": 0.7,
                        "buckets": [{"allocated_lots": 1, "accounts": 50000, "threshold_amount_wan": 1100}],
                    },
                    ensure_ascii=False,
                ),
            },
            {
                "security_code": "920102",
                "security_name_abbr": "New",
                "apply_date": "2026-02-02",
                "listing_date": "2026-02-11",
                "issue_price": "10",
                "online_issue_shares": "10000000",
                "online_valid_accounts": "1000",
                "online_allocated_accounts": "40000",
                "top_apply_amount_wan": "1500",
                "allocation_fit_json": json.dumps(
                    {
                        "fit_quality": "rough_lot_account_fit",
                        "fit_confidence": 0.7,
                        "buckets": [{"allocated_lots": 1, "accounts": 40000, "threshold_amount_wan": 1000}],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        _write_history(history_path, rows)
        subscription_ladder_labels.write_label_rows(
            [
                {
                    "security_code": "920101",
                    "security_name_abbr": "Old",
                    "apply_date": "2026-02-01",
                    "issue_price": "10",
                    "online_issue_shares": "10000000",
                    "top_apply_amount_wan": "1500",
                    "manual_ladder": "1+0=1100",
                    "manual_note": "",
                },
                {
                    "security_code": "920102",
                    "security_name_abbr": "New",
                    "apply_date": "2026-02-02",
                    "issue_price": "10",
                    "online_issue_shares": "10000000",
                    "top_apply_amount_wan": "1500",
                    "manual_ladder": "1+0=1000",
                    "manual_note": "",
                },
            ],
            label_path,
        )

        build_account_pool_history.build_account_pool_history(
            history_path=history_path,
            ladder_label_path=label_path,
            points_path=points_path,
            thresholds_path=thresholds_path,
            summary_path=summary_path,
        )
        threshold_rows = _read_csv(thresholds_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    second_row = threshold_rows[1] if len(threshold_rows) > 1 else {}
    _assert_close(second_row.get("accounts_ge_1000w_estimate"), 40000.0, "conflict: new 1000w mismatch", failures)
    _assert_close(second_row.get("accounts_ge_1100w_estimate"), 40000.0, "conflict: old 1100w should be capped by new 1000w", failures)
    _assert(
        second_row.get("accounts_ge_1100w_basis") == "covered_by_newer_observation",
        "conflict: old 1100w basis mismatch",
        failures,
    )


def _run_announcement_constraints_with_history_prior_case(failures: list[str]) -> None:
    thresholds = {
        1: 293.0,
        2: 586.0,
        3: 879.0,
        4: 949.91,
        5: 1172.0,
        6: 1465.0,
        7: 1758.0,
        8: 2051.0,
        9: 2344.0,
        10: 2637.0,
        11: 2930.0,
        12: 3231.0,
    }
    prior_state = {
        "500": {"threshold_amount_wan": 500.0, "estimate": 87684.0},
        "597": {"threshold_amount_wan": 597.0, "estimate": 86029.0},
        "881.74": {"threshold_amount_wan": 881.74, "estimate": 73209.0},
        "1194": {"threshold_amount_wan": 1194.0, "estimate": 38704.0},
        "1800": {"threshold_amount_wan": 1800.0, "estimate": 24448.0},
        "3231": {"threshold_amount_wan": 3231.0, "estimate": 2000.0},
    }
    cumulative, basis = build_account_pool_history._announcement_constrained_cumulative(
        allocated_accounts=105233.0,
        total_lots=450000.0,
        thresholds_by_lot=thresholds,
        prior_state=prior_state,
    )

    values = [cumulative[level] for level in range(1, 13)]
    bucket_counts = [values[index] - (values[index + 1] if index + 1 < len(values) else 0.0) for index in range(len(values))]
    weighted_lots = sum((index + 1) * count for index, count in enumerate(bucket_counts))
    _assert(basis == "history_prior", "announcement constraints: history prior not used", failures)
    _assert_close(values[0], 105233.0, "announcement constraints: allocated account anchor mismatch", failures)
    _assert_close(sum(values), 450000.0, "announcement constraints: cumulative lot area mismatch", failures, tolerance=1e-4)
    _assert_close(sum(bucket_counts), 105233.0, "announcement constraints: bucket account equation mismatch", failures, tolerance=1e-4)
    _assert_close(weighted_lots, 450000.0, "announcement constraints: weighted lot equation mismatch", failures, tolerance=1e-4)
    _assert(all(values[index] >= values[index + 1] for index in range(len(values) - 1)), "announcement constraints: curve is not monotone", failures)
    _assert(values[1] > 50000.0, "announcement constraints: compressed 31343-account cliff leaked into result", failures)


def _run_partial_final_lot_case(failures: list[str]) -> None:
    cumulative, basis = build_account_pool_history._announcement_constrained_cumulative(
        allocated_accounts=101648.0,
        total_lots=101647.07,
        thresholds_by_lot={1: 340.0},
        prior_state={},
    )
    _assert(basis == "announcement_exact", "partial final lot: valid fractional tail rejected", failures)
    _assert_close(cumulative.get(1), 101648.0, "partial final lot: allocated account count mismatch", failures)

    invalid, invalid_basis = build_account_pool_history._announcement_constrained_cumulative(
        allocated_accounts=101649.0,
        total_lots=101647.0,
        thresholds_by_lot={1: 340.0},
        prior_state={},
    )
    _assert(invalid == {}, "partial final lot: impossible account count accepted", failures)
    _assert(invalid_basis == "unavailable", "partial final lot: impossible case basis mismatch", failures)


def _run_incremental_suffix_rebuild_case(failures: list[str]) -> None:
    temp_dir = ROOT_DIR / ".tmp" / "validate_account_pool_incremental_suffix"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        history_path = temp_dir / "subscription_history_sample.csv"
        label_path = temp_dir / "subscription_ladder_labels.csv"
        points_path = temp_dir / "account_pool_history_points.csv"
        thresholds_path = temp_dir / "account_pool_history_thresholds.csv"
        summary_path = temp_dir / "account_pool_history_summary.json"
        rows = [
            {
                "security_code": "920301",
                "security_name_abbr": "First",
                "apply_date": "2026-03-01",
                "listing_date": "2026-03-10",
                "issue_price": "10",
                "online_issue_shares": "14000",
                "online_valid_accounts": "1000",
                "online_allocated_accounts": "100",
                "top_apply_amount_wan": "1000",
                "allocation_fit_json": "{}",
            },
            {
                "security_code": "920302",
                "security_name_abbr": "Second",
                "apply_date": "2026-03-02",
                "listing_date": "2026-03-11",
                "issue_price": "10",
                "online_issue_shares": "18000",
                "online_valid_accounts": "1000",
                "online_allocated_accounts": "120",
                "top_apply_amount_wan": "1000",
                "allocation_fit_json": "{}",
            },
        ]
        _write_history(history_path, rows)

        def write_labels(first: str, second: str) -> None:
            subscription_ladder_labels.write_label_rows(
                [
                    {
                        "security_code": "920301",
                        "security_name_abbr": "First",
                        "apply_date": "2026-03-01",
                        "manual_ladder": first,
                    },
                    {
                        "security_code": "920302",
                        "security_name_abbr": "Second",
                        "apply_date": "2026-03-02",
                        "manual_ladder": second,
                    },
                ],
                label_path,
            )

        write_labels("1+0=300;1+1=600", "1+0=400;1+1=700")
        first_summary = build_account_pool_history.build_account_pool_history(
            history_path=history_path,
            ladder_label_path=label_path,
            points_path=points_path,
            thresholds_path=thresholds_path,
            summary_path=summary_path,
        )
        first_threshold_rows = _read_csv(thresholds_path)
        unchanged_summary = build_account_pool_history.build_account_pool_history(
            history_path=history_path,
            ladder_label_path=label_path,
            points_path=points_path,
            thresholds_path=thresholds_path,
            summary_path=summary_path,
        )
        write_labels("1+0=300;1+1=600", "1+0=450;1+1=750")
        suffix_summary = build_account_pool_history.build_account_pool_history(
            history_path=history_path,
            ladder_label_path=label_path,
            points_path=points_path,
            thresholds_path=thresholds_path,
            summary_path=summary_path,
        )
        suffix_threshold_rows = _read_csv(thresholds_path)
        suffix_points_text = points_path.read_text(encoding="utf-8-sig")
        suffix_thresholds_text = thresholds_path.read_text(encoding="utf-8-sig")
        forced_summary = build_account_pool_history.build_account_pool_history(
            history_path=history_path,
            ladder_label_path=label_path,
            points_path=points_path,
            thresholds_path=thresholds_path,
            summary_path=summary_path,
            force_rebuild=True,
        )
        forced_points_text = points_path.read_text(encoding="utf-8-sig")
        forced_thresholds_text = thresholds_path.read_text(encoding="utf-8-sig")
        write_labels("1+0=320;1+1=620", "1+0=450;1+1=750")
        earliest_summary = build_account_pool_history.build_account_pool_history(
            history_path=history_path,
            ladder_label_path=label_path,
            points_path=points_path,
            thresholds_path=thresholds_path,
            summary_path=summary_path,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    _assert(first_summary.get("rebuild_mode") == "full", "incremental pool: first run should be full", failures)
    _assert(unchanged_summary.get("rebuild_mode") == "no_change", "incremental pool: unchanged run should be no-op", failures)
    _assert(unchanged_summary.get("rebuilt_suffix_count") == 0, "incremental pool: unchanged run rebuilt rows", failures)
    _assert(suffix_summary.get("rebuild_mode") == "suffix", "incremental pool: second-row change should rebuild suffix", failures)
    _assert(suffix_summary.get("reused_prefix_count") == 1, "incremental pool: prefix reuse count mismatch", failures)
    _assert(suffix_summary.get("rebuilt_suffix_count") == 1, "incremental pool: suffix rebuild count mismatch", failures)
    _assert(suffix_summary.get("rebuild_from_code") == "920302", "incremental pool: rebuild start code mismatch", failures)
    _assert(
        first_threshold_rows[0].get("account_pool_snapshot_json")
        == suffix_threshold_rows[0].get("account_pool_snapshot_json"),
        "incremental pool: unchanged prefix snapshot was modified",
        failures,
    )
    _assert(forced_summary.get("rebuild_mode") == "full", "incremental pool: force flag should rebuild all", failures)
    _assert(suffix_points_text == forced_points_text, "incremental pool: suffix points differ from full rebuild", failures)
    _assert(suffix_thresholds_text == forced_thresholds_text, "incremental pool: suffix snapshots differ from full rebuild", failures)
    _assert(earliest_summary.get("rebuild_mode") == "full", "incremental pool: first-row change should rebuild all", failures)
    _assert(earliest_summary.get("rebuild_from_code") == "920301", "incremental pool: earliest changed code mismatch", failures)


def main() -> int:
    failures: list[str] = []
    _run_manual_ladder_merge_case(failures)
    _run_sequential_refinement_case(failures)
    _run_new_point_caps_conflicting_old_point_case(failures)
    _run_announcement_constraints_with_history_prior_case(failures)
    _run_partial_final_lot_case(failures)
    _run_incremental_suffix_rebuild_case(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("OK account pool history builder validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
