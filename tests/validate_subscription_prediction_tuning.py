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

import tune_subscription_prediction


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


def _sample_row(code: str, apply_date: str) -> dict[str, Any]:
    return {
        "security_code": code,
        "apply_date": apply_date,
        "issue_result_date": "2026-06-04",
        "issue_price": "10",
        "online_issue_shares": "1000000",
        "top_apply_amount_wan": "10",
        "online_valid_shares": "30000000",
        "frozen_funds_yi": "3",
        "allocation_rate_pct": "3.333333",
        "subscription_multiple": "30",
        "guaranteed_threshold_amount_wan": "3",
        "top_apply_below_guaranteed": "false",
        "model_ready": "true",
        "allocation_fit_usable_for_tuning": "true",
        "allocation_fit_confidence": "0.7",
        "allocation_fit_residual_json": json.dumps(
            {
                "allocated_account_residual": 0,
                "allocated_lot_residual": 0,
                "valid_subscription_balance_residual_shares": 0,
                "unallocated_avg_over_cap_shares": 0,
                "unallocated_avg_under_zero_shares": 0,
                "unallocated_cap_utilization": 0.1,
            },
            ensure_ascii=False,
        ),
    }


def _sample_pool_row(code: str, apply_date: str) -> dict[str, Any]:
    row = _sample_row(code, apply_date)
    row["allocation_fit_json"] = json.dumps(
        {
            "available": True,
            "fit_quality": "rough_lot_account_fit",
            "fit_confidence": 0.7,
            "top_apply_below_guaranteed": False,
            "buckets": [
                {"threshold_amount_wan": 8.0, "accounts": 10, "allocated_lots": 3},
                {"threshold_amount_wan": 3.0, "accounts": 30, "allocated_lots": 1},
            ],
        },
        ensure_ascii=False,
    )
    return row


def _sample_prior_row(code: str, apply_date: str) -> dict[str, Any]:
    row = _sample_row(code, apply_date)
    row["allocation_fit_confidence"] = "1.0"
    row["allocation_fit_json"] = json.dumps(
        {
            "available": True,
            "fit_quality": "rough_lot_account_fit",
            "fit_confidence": 1.0,
            "top_apply_below_guaranteed": False,
            "unallocated_accounts": 0,
            "unallocated_avg_amount_wan": 0,
            "buckets": [
                {"threshold_amount_wan": 10.0, "accounts": 20000, "allocated_lots": 1},
            ],
        },
        ensure_ascii=False,
    )
    return row


def _run_baseline_evaluation_case(failures: list[str]) -> None:
    rows = [
        _sample_row("920001", "2026-06-01"),
        _sample_row("920002", "2026-06-01"),
        _sample_row("920003", "2026-06-01"),
        _sample_row("920004", "2026-06-01"),
    ]
    summary = tune_subscription_prediction.evaluate_subscription_prediction(rows, min_history_samples=3)
    _assert(summary.get("eligible_rows") == 4, "baseline: eligible row count mismatch", failures)
    _assert(summary.get("skipped_for_history") == 3, "baseline: skipped history count mismatch", failures)
    _assert(summary.get("evaluated_rows") == 1, "baseline: evaluated row count mismatch", failures)
    _assert_close(summary.get("guaranteed_amount_mae_wan"), 0.0, "baseline: MAE mismatch", failures)
    _assert_close(summary.get("guaranteed_amount_mape"), 0.0, "baseline: MAPE mismatch", failures)
    _assert(summary.get("top_apply_classification_total") == 1, "baseline: classification total mismatch", failures)
    _assert(summary.get("top_apply_classification_correct") == 1, "baseline: classification correct mismatch", failures)
    residuals = (summary.get("fit_residuals_weighted") or {}).get("averages") or {}
    _assert_close(residuals.get("unallocated_cap_utilization"), 0.1, "baseline: residual average mismatch", failures)


def _run_csv_load_case(failures: list[str]) -> None:
    rows = [_sample_row("920001", "2026-06-01")]
    with tempfile.TemporaryDirectory() as temp_dir:
        csv_path = Path(temp_dir) / "subscription_history_sample.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as file_obj:
            writer = csv.DictWriter(file_obj, fieldnames=list(rows[0].keys()), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        loaded = tune_subscription_prediction._load_history_rows(csv_path)
    _assert(len(loaded) == 1, "csv load: row count mismatch", failures)
    _assert(loaded[0].get("security_code") == "920001", "csv load: code mismatch", failures)


def _run_candidate_search_case(failures: list[str]) -> None:
    rows = [
        _sample_row("920001", "2026-06-01"),
        _sample_row("920002", "2026-06-01"),
        _sample_row("920003", "2026-06-01"),
        _sample_row("920004", "2026-06-01"),
    ]
    result = tune_subscription_prediction.evaluate_candidate_grid(rows, min_history_samples=3, top_n=2, max_candidates=3)
    _assert(result.get("candidate_count") == 3, "search: candidate count mismatch", failures)
    _assert(len(result.get("top_candidates") or []) == 2, "search: top candidate count mismatch", failures)
    best = result.get("best") or {}
    _assert(best.get("params"), "search: best params missing", failures)
    _assert("rank_key" in best, "search: rank key missing", failures)


def _run_window_and_robustness_case(failures: list[str]) -> None:
    rows = [
        _sample_row("920001", "2026-06-01"),
        _sample_row("920002", "2026-06-01"),
        _sample_row("920003", "2026-06-01"),
        _sample_row("920004", "2026-06-01"),
        _sample_row("920005", "2026-06-01"),
    ]
    windowed = tune_subscription_prediction.evaluate_subscription_prediction(
        rows,
        min_history_samples=3,
        max_history_samples=3,
    )
    _assert(windowed.get("max_history_samples") == 3, "window: max history mismatch", failures)
    robust = tune_subscription_prediction.evaluate_robustness(
        rows,
        min_history_values=[3],
        history_windows=[None, 3],
        top_n=2,
    )
    _assert(robust.get("case_count") == 2, "robust: case count mismatch", failures)
    _assert(robust.get("selected_best_params"), "robust: selected params missing", failures)
    _assert(robust.get("top_candidate_cluster"), "robust: cluster missing", failures)


def _run_large_account_pool_case(failures: list[str]) -> None:
    rows = [
        _sample_pool_row("920001", "2026-06-01"),
        _sample_pool_row("920002", "2026-06-02"),
    ]
    result = tune_subscription_prediction.evaluate_large_account_pool(
        rows,
        thresholds_wan=[3.0, 8.0, 12.0],
        recent_samples=2,
        half_life_samples=2,
    )
    summaries = {item.get("threshold_wan"): item for item in result.get("summaries") or []}
    _assert_close(summaries[3.0].get("latest_accounts"), 40.0, "pool: 3 wan latest mismatch", failures)
    _assert_close(summaries[8.0].get("latest_accounts"), 10.0, "pool: 8 wan latest mismatch", failures)
    _assert_close(summaries[12.0].get("latest_accounts"), 0.0, "pool: 12 wan latest mismatch", failures)


def _run_account_pool_prior_case(failures: list[str]) -> None:
    rows = [
        _sample_prior_row("920001", "2026-06-01"),
        _sample_prior_row("920002", "2026-06-02"),
        _sample_prior_row("920003", "2026-06-03"),
        _sample_prior_row("920004", "2026-06-04"),
    ]
    rows[-1]["top_apply_below_guaranteed"] = "true"
    without_prior = tune_subscription_prediction.evaluate_subscription_prediction(rows, min_history_samples=3)
    with_prior = tune_subscription_prediction.evaluate_subscription_prediction(
        rows,
        min_history_samples=3,
        params={
            "subscription_prediction_account_pool_prior_weight": 1.0,
            "subscription_prediction_account_pool_recent_samples": 3,
            "subscription_prediction_account_pool_half_life_samples": 2,
        },
    )
    _assert(
        without_prior.get("top_apply_false_negative_codes") == ["920004"],
        "prior: expected no-prior false negative",
        failures,
    )
    _assert(with_prior.get("top_apply_false_negative_codes") == [], "prior: false negative not fixed", failures)
    detail = (with_prior.get("details") or [{}])[0]
    _assert(detail.get("account_pool_prior_applied"), "prior: detail should mark prior applied", failures)

    search = tune_subscription_prediction.evaluate_account_pool_prior(
        rows,
        min_history_samples=3,
        weights=[1.0],
        recent_sample_values=[3],
        half_life_values=[2.0],
        top_n=1,
    )
    _assert(search.get("candidate_count") == 1, "prior search: candidate count mismatch", failures)
    _assert(
        (search.get("best") or {}).get("account_pool_prior_applied_count") == 1,
        "prior search: applied count mismatch",
        failures,
    )


def main() -> int:
    failures: list[str] = []
    _run_baseline_evaluation_case(failures)
    _run_csv_load_case(failures)
    _run_candidate_search_case(failures)
    _run_window_and_robustness_case(failures)
    _run_large_account_pool_case(failures)
    _run_account_pool_prior_case(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("OK subscription prediction tuning validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
