from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import subscription_predictor


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


def _run_actual_distribution_case(failures: list[str]) -> None:
    prediction = subscription_predictor.build_subscription_prediction(
        {
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 1000000.0,
            "TOP_APPLY_MARKETCAP": 10.0,
            "ONLINE_VA_NUM": 1000.0,
            "ONLINE_VA_SHARES": 60000000.0,
            "APPLY_DATE": "2026-06-01",
            "ISSUE_RESULT_DATE": "2026-06-04",
            "SUBSCRIPTION_AMOUNT_DISTRIBUTION": [
                {"apply_shares": 5000, "accounts": 20000},
                {"apply_shares": 4900, "accounts": 20000},
                {"apply_shares": 3000, "accounts": 20000},
            ],
        },
        recent_ipos=[],
        params={},
    )
    _assert(prediction.get("available") is True, "actual distribution: prediction unavailable", failures)
    _assert(prediction.get("mode") == "actual", "actual distribution: mode mismatch", failures)
    _assert_close(prediction.get("subscription_multiple"), 60.0, "actual distribution: multiple mismatch", failures)
    _assert_close(
        prediction.get("guaranteed_threshold_amount_wan"),
        6.0,
        "actual distribution: guaranteed amount mismatch",
        failures,
    )
    _assert_close(
        prediction.get("fractional_threshold_amount_wan"),
        5.0,
        "actual distribution: fractional amount mismatch",
        failures,
    )
    _assert(prediction.get("fractional_time_priority_required") is True, "actual distribution: time priority mismatch", failures)


def _run_estimated_case(failures: list[str]) -> None:
    recent_ipos = [
        {
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 1000000.0,
            "ONLINE_VA_SHARES": 30000000.0,
            "TOP_APPLY_MARKETCAP": 10.0,
            "APPLY_DATE": "2026-05-10",
            "ISSUE_RESULT_DATE": "2026-05-13",
        },
        {
            "ISSUE_PRICE": 12.0,
            "ONLINE_ISSUE_NUM": 1200000.0,
            "ONLINE_ISSUE_LWR": 4.0,
            "TOP_APPLY_MARKETCAP": 12.0,
            "APPLY_DATE": "2026-05-20",
            "ISSUE_RESULT_DATE": "2026-05-23",
        },
        {
            "ISSUE_PRICE": 8.0,
            "ONLINE_ISSUE_NUM": 800000.0,
            "ONLINE_ES_MULTIPLE": 30.0,
            "TOP_APPLY_MARKETCAP": 8.0,
            "APPLY_DATE": "2026-05-25",
            "ISSUE_RESULT_DATE": "2026-05-28",
        },
    ]
    prediction = subscription_predictor.build_subscription_prediction(
        {
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 1000000.0,
            "TOP_APPLY_MARKETCAP": 10.0,
            "APPLY_DATE": "2026-06-01",
            "ISSUE_RESULT_DATE": "2026-06-04",
        },
        recent_ipos=recent_ipos,
        params={"subscription_prediction_min_samples": 3},
    )
    _assert(prediction.get("available") is True, "estimated: prediction unavailable", failures)
    _assert(prediction.get("mode") == "estimated", "estimated: mode mismatch", failures)
    _assert_close(prediction.get("subscription_multiple"), 30.0, "estimated: multiple mismatch", failures, tolerance=0.01)
    _assert(prediction.get("fractional_time_priority_required") is True, "estimated: time priority should default true", failures)


def _run_top_apply_below_guaranteed_case(failures: list[str]) -> None:
    prediction = subscription_predictor.build_subscription_prediction(
        {
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 1000000.0,
            "TOP_APPLY_MARKETCAP": 10.0,
            "ONLINE_VA_NUM": 20000.0,
            "ONLINE_ALLOCATED_ACCOUNTS": 10000.0,
            "ONLINE_VA_SHARES": 200000000.0,
            "APPLY_DATE": "2026-06-01",
            "ISSUE_RESULT_DATE": "2026-06-04",
        },
        recent_ipos=[],
        params={},
    )
    _assert(prediction.get("available") is True, "top apply below guaranteed: prediction unavailable", failures)
    _assert(prediction.get("guaranteed_threshold_reachable") is False, "top apply below guaranteed: reachable mismatch", failures)
    _assert(prediction.get("top_apply_below_guaranteed") is True, "top apply below guaranteed: flag missing", failures)
    _assert(prediction.get("time_priority_scope") == "all_top_apply_accounts", "top apply below guaranteed: scope mismatch", failures)
    _assert_close(
        prediction.get("fractional_threshold_amount_wan"),
        10.0,
        "top apply below guaranteed: fractional amount mismatch",
        failures,
    )
    fit = prediction.get("allocation_fit") or {}
    _assert(fit.get("method") == "top_apply_below_guaranteed", "top apply below guaranteed: fit method mismatch", failures)
    _assert(fit.get("fit_quality") == "time_priority_label", "top apply below guaranteed: fit quality mismatch", failures)
    _assert_close(fit.get("fit_confidence"), 0.95, "top apply below guaranteed: confidence mismatch", failures)
    _assert(fit.get("fit_usable_for_tuning") is True, "top apply below guaranteed: tuning usability mismatch", failures)
    residuals = fit.get("fit_residuals") or {}
    _assert(residuals.get("allocated_account_residual") == 0, "top apply below guaranteed: account residual mismatch", failures)
    _assert(residuals.get("allocated_lot_residual") == 0, "top apply below guaranteed: lot residual mismatch", failures)


def main() -> int:
    failures: list[str] = []
    _run_actual_distribution_case(failures)
    _run_estimated_case(failures)
    _run_top_apply_below_guaranteed_case(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("OK subscription predictor validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
