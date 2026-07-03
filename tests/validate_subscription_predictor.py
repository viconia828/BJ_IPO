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
        prediction.get("protected_guaranteed_amount_min_wan"),
        56.0,
        "actual distribution: protected min mismatch",
        failures,
    )
    _assert_close(
        prediction.get("protected_guaranteed_amount_max_wan"),
        106.0,
        "actual distribution: protected max mismatch",
        failures,
    )
    _assert(
        prediction.get("protected_guaranteed_threshold_exceeds_top_apply") is True,
        "actual distribution: protected amount should exceed top apply",
        failures,
    )
    _assert(
        prediction.get("top_apply_time_priority_required") is False,
        "actual distribution: protected exceed should not be mandatory time priority",
        failures,
    )
    _assert(
        prediction.get("top_apply_time_priority_note") == "可能需要抢时间（保护后建议金额超过顶格）",
        "actual distribution: protected exceed time note mismatch",
        failures,
    )
    _assert_close(
        prediction.get("fractional_threshold_amount_wan"),
        5.0,
        "actual distribution: fractional amount mismatch",
        failures,
    )
    _assert(prediction.get("fractional_time_priority_required") is True, "actual distribution: time priority mismatch", failures)
    _assert(
        prediction.get("fractional_time_priority_note") == "0+1以下碎股可能需要抢时间",
        "actual distribution: fractional time note mismatch",
        failures,
    )
    _assert(
        prediction.get("fractional_time_priority_overview_text") == "0+1以下可能",
        "actual distribution: fractional overview mismatch",
        failures,
    )


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


def _run_frozen_funds_floor_case(failures: list[str]) -> None:
    recent_ipos = [
        {
            "SECURITY_CODE": "920101",
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 10000000.0,
            "ONLINE_ISSUE_LWR": 0.02,
            "TOP_APPLY_MARKETCAP": 100.0,
            "APPLY_DATE": "2026-05-10",
            "ISSUE_RESULT_DATE": "2026-05-13",
        },
        {
            "SECURITY_CODE": "920102",
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 10000000.0,
            "ONLINE_ISSUE_LWR": 0.025,
            "TOP_APPLY_MARKETCAP": 100.0,
            "APPLY_DATE": "2026-05-20",
            "ISSUE_RESULT_DATE": "2026-05-23",
        },
        {
            "SECURITY_CODE": "920103",
            "ISSUE_PRICE": 8.0,
            "ONLINE_ISSUE_NUM": 10000000.0,
            "ONLINE_ISSUE_LWR": 0.02,
            "TOP_APPLY_MARKETCAP": 80.0,
            "APPLY_DATE": "2026-05-25",
            "ISSUE_RESULT_DATE": "2026-05-28",
        },
    ]
    prediction = subscription_predictor.build_subscription_prediction(
        {
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 1000000.0,
            "TOP_APPLY_MARKETCAP": 5000.0,
            "APPLY_DATE": "2026-06-01",
            "ISSUE_RESULT_DATE": "2026-06-04",
        },
        recent_ipos=recent_ipos,
        params={
            "subscription_prediction_min_samples": 3,
            "subscription_prediction_frozen_funds_floor_weight": 0.95,
        },
    )
    floor = prediction.get("frozen_funds_floor") or {}
    _assert(prediction.get("available") is True, "frozen floor: prediction unavailable", failures)
    _assert(floor.get("applied") is True, "frozen floor: floor not applied", failures)
    _assert_close(prediction.get("frozen_funds_yi"), 3800.0, "frozen floor: frozen funds mismatch", failures)
    _assert_close(
        prediction.get("guaranteed_threshold_amount_wan"),
        3800.0,
        "frozen floor: guaranteed threshold mismatch",
        failures,
    )


def _run_similar_top_apply_frozen_anchor_case(failures: list[str]) -> None:
    recent_ipos = [
        {
            "SECURITY_CODE": "920151",
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 10000000.0,
            "FROZEN_FUNDS_YI": 9000.0,
            "TOP_APPLY_MARKETCAP": 1800.0,
            "APPLY_DATE": "2026-05-10",
            "ISSUE_RESULT_DATE": "2026-05-13",
        },
        {
            "SECURITY_CODE": "920152",
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 10000000.0,
            "FROZEN_FUNDS_YI": 9600.0,
            "TOP_APPLY_MARKETCAP": 1822.0,
            "APPLY_DATE": "2026-05-20",
            "ISSUE_RESULT_DATE": "2026-05-23",
        },
        {
            "SECURITY_CODE": "920153",
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 10000000.0,
            "FROZEN_FUNDS_YI": 20000.0,
            "TOP_APPLY_MARKETCAP": 500.0,
            "APPLY_DATE": "2026-05-25",
            "ISSUE_RESULT_DATE": "2026-05-28",
        },
    ]
    prediction = subscription_predictor.build_subscription_prediction(
        {
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 20000000.0,
            "TOP_APPLY_MARKETCAP": 1802.0,
            "APPLY_DATE": "2026-06-01",
            "ISSUE_RESULT_DATE": "2026-06-04",
        },
        recent_ipos=recent_ipos,
        params={
            "subscription_prediction_min_samples": 3,
            "subscription_prediction_similar_top_apply_frozen_weight": 1.0,
            "subscription_prediction_similar_top_apply_frozen_max_relative_distance": 0.05,
            "subscription_prediction_frozen_funds_floor_enabled": False,
        },
    )
    similar_anchor = (prediction.get("estimate") or {}).get("similar_top_apply_frozen_funds") or {}
    _assert(prediction.get("available") is True, "similar frozen anchor: prediction unavailable", failures)
    _assert(similar_anchor.get("applied") is True, "similar frozen anchor: anchor not applied", failures)
    _assert_close(similar_anchor.get("anchor_frozen_funds_yi"), 9000.0, "similar frozen anchor: anchor mismatch", failures)
    _assert_close(prediction.get("frozen_funds_yi"), 9000.0, "similar frozen anchor: frozen funds mismatch", failures)
    _assert(float(similar_anchor.get("base_frozen_funds_yi") or 0.0) > 15000.0, "similar frozen anchor: base should be higher", failures)


def _run_920126_lot_threshold_case(failures: list[str]) -> None:
    issue_price = 7.79
    online_issue_shares = 41868000.0
    actual_amounts = {
        1: 274.4,
        2: 548.8,
        3: 571.78,
        4: 823.2,
        5: 1097.46,
        6: 1372.0,
    }
    allocation_ratio = 100 / (actual_amounts[1] * 10000 / issue_price)
    valid_subscription_shares = online_issue_shares / allocation_ratio
    frozen_funds_yi = valid_subscription_shares * issue_price / 100000000
    prediction = subscription_predictor.build_subscription_prediction(
        {
            "SECURITY_CODE": "920126",
            "SECURITY_NAME_ABBR": "永大股份",
            "ISSUE_PRICE": issue_price,
            "ONLINE_ISSUE_NUM": online_issue_shares,
            "TOP_APPLY_MARKETCAP": 1630.7586,
            "FROZEN_FUNDS_YI": frozen_funds_yi,
            "FRACTIONAL_THRESHOLD_SHARES": actual_amounts[3] * 10000 / issue_price,
            "FRACTIONAL_TIME_PRIORITY_REQUIRED": True,
        },
        recent_ipos=[],
        params={},
    )
    lot_thresholds = prediction.get("lot_thresholds") or []
    _assert(len(lot_thresholds) == 10, "920126 lot thresholds: expected 10 rows", failures)
    by_label = {str(item.get("ladder_label") or ""): item for item in lot_thresholds}
    display_labels = [
        str(item.get("ladder_label") or "")
        for item in lot_thresholds
        if item.get("display") is not False
    ]
    expected_by_label = {
        "1+0": actual_amounts[1],
        "1+1": actual_amounts[3],
        "2+0": actual_amounts[2],
        "2+1": actual_amounts[3],
        "3+0": actual_amounts[4],
        "3+1": actual_amounts[4],
        "4+0": actual_amounts[5],
        "4+1": actual_amounts[5],
        "5+0": actual_amounts[6],
        "5+1": actual_amounts[6],
    }
    for label, actual_amount in expected_by_label.items():
        item = by_label.get(label) or {}
        _assert_close(
            item.get("threshold_amount_wan"),
            actual_amount,
            f"920126 lot thresholds: {label} mismatch",
            failures,
            tolerance=0.2,
        )
    _assert(by_label["2+1"].get("basis") == "issue_result_threshold", "920126: 2+1 should use fractional threshold", failures)
    _assert(by_label["5+1"].get("basis") == "issue_result_threshold", "920126: 5+1 should use fractional threshold", failures)
    _assert(by_label["2+1"].get("time_priority_required") is True, "920126: 2+1 should require time priority", failures)
    _assert(by_label["3+1"].get("time_priority_required") is False, "920126: 3+1 should not require time priority", failures)
    _assert("1+1" not in display_labels, "920126: dominated fractional row should be hidden", failures)
    _assert("2+0" in display_labels, "920126: lower regular row should display", failures)
    _assert("2+1" in display_labels, "920126: fractional row should display for the next total lot", failures)
    _assert("3+0" not in display_labels, "920126: dominated regular row should be hidden", failures)
    _assert("3+1" in display_labels, "920126: covered regular row should display as fractional", failures)
    _assert(by_label["3+0"].get("display") is False, "920126: covered regular display flag mismatch", failures)
    _assert(prediction.get("fractional_time_priority_required") is True, "920126: fractional time priority missing", failures)


def _run_fractional_between_one_and_two_display_case(failures: list[str]) -> None:
    issue_price = 17.83
    first_regular_amount = 586.96
    fractional_amount = 1007.93
    allocation_ratio = 100 / (first_regular_amount * 10000 / issue_price)
    online_issue_shares = 10000000.0
    prediction = subscription_predictor.build_subscription_prediction(
        {
            "SECURITY_CODE": "920117",
            "SECURITY_NAME_ABBR": "Longxin",
            "ISSUE_PRICE": issue_price,
            "ONLINE_ISSUE_NUM": online_issue_shares,
            "ONLINE_VA_SHARES": online_issue_shares / allocation_ratio,
            "TOP_APPLY_MARKETCAP": 1802.08,
            "FRACTIONAL_THRESHOLD_SHARES": fractional_amount * 10000 / issue_price,
            "FRACTIONAL_TIME_PRIORITY_REQUIRED": False,
        },
        recent_ipos=[],
        params={"subscription_prediction_lot_threshold_max_lots": 4},
    )
    display_labels = [
        str(item.get("ladder_label") or "")
        for item in (prediction.get("lot_thresholds") or [])
        if item.get("display") is not False
    ]
    _assert(display_labels == ["1+0", "1+1", "2+1", "3+1"], "fractional 1-2 display labels mismatch", failures)
    table_labels = [str(row[0]) for row in (prediction.get("table_rows") or []) if "建议申购门槛" in str(row[0])]
    _assert("2+0建议申购门槛" not in table_labels, "fractional 1-2 table should hide 2+0", failures)
    _assert("3+0建议申购门槛" not in table_labels, "fractional 1-2 table should hide 3+0", failures)
    _assert("2+1建议申购门槛" in table_labels, "fractional 1-2 table should keep 2+1", failures)
    _assert("3+1建议申购门槛" in table_labels, "fractional 1-2 table should keep 3+1", failures)


def _run_account_pool_fractional_threshold_case(failures: list[str]) -> None:
    account_pool_row: dict[str, Any] = {
        "security_code": "920900",
        "apply_date": "2026-06-30",
    }
    for threshold, accounts in {1: 45, 2: 25, 3: 15, 4: 5}.items():
        account_pool_row[f"accounts_ge_{threshold}w_estimate"] = accounts
        account_pool_row[f"accounts_ge_{threshold}w_basis"] = "exact_observed_threshold"

    prediction = subscription_predictor.build_subscription_prediction(
        {
            "SECURITY_CODE": "920901",
            "SECURITY_NAME_ABBR": "PoolCase",
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 8000.0,
            "ONLINE_VA_SHARES": 80000.0,
            "TOP_APPLY_MARKETCAP": 2.5,
        },
        recent_ipos=[],
        params={
            "subscription_prediction_account_pool_rows": [account_pool_row],
            "subscription_prediction_account_pool_recent_samples": 1,
            "subscription_prediction_lot_threshold_max_lots": 5,
        },
    )
    lot_thresholds = prediction.get("lot_thresholds") or []
    by_label = {str(item.get("ladder_label") or ""): item for item in lot_thresholds}
    _assert_close(by_label.get("2+0", {}).get("threshold_amount_wan"), 2.0, "account pool: 2+0 mismatch", failures)
    _assert_close(by_label.get("2+1", {}).get("threshold_amount_wan"), 2.5, "account pool: 2+1 mismatch", failures)
    _assert(
        by_label.get("2+1", {}).get("basis") == "account_pool_fractional_estimate",
        "account pool: 2+1 should use account pool cutoff",
        failures,
    )
    _assert(
        prediction.get("fractional_time_priority_note") == "2+1以下碎股可能需要抢时间",
        "account pool: fractional time note mismatch",
        failures,
    )
    _assert(
        prediction.get("fractional_time_priority_overview_text") == "2+1以下可能",
        "account pool: fractional overview mismatch",
        failures,
    )
    pool_estimate = prediction.get("account_pool_fractional_estimate") or {}
    _assert_close(pool_estimate.get("full_allocated_lots_estimate"), 70.0, "account pool: full lots mismatch", failures)
    _assert_close(pool_estimate.get("leftover_lots"), 10.0, "account pool: leftover lots mismatch", failures)


def _run_account_pool_fully_covered_fractional_case(failures: list[str]) -> None:
    account_pool_row: dict[str, Any] = {
        "security_code": "920910",
        "apply_date": "2026-06-30",
    }
    for threshold, accounts in {1: 50, 2: 30, 4: 10}.items():
        account_pool_row[f"accounts_ge_{threshold}w_estimate"] = accounts
        account_pool_row[f"accounts_ge_{threshold}w_basis"] = "exact_observed_threshold"

    prediction = subscription_predictor.build_subscription_prediction(
        {
            "SECURITY_CODE": "920911",
            "SECURITY_NAME_ABBR": "CoveredPool",
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 10000.0,
            "ONLINE_VA_SHARES": 200000.0,
            "TOP_APPLY_MARKETCAP": 4.0,
        },
        recent_ipos=[],
        params={
            "subscription_prediction_account_pool_rows": [account_pool_row],
            "subscription_prediction_account_pool_recent_samples": 1,
            "subscription_prediction_lot_threshold_max_lots": 5,
        },
    )
    by_label = {str(item.get("ladder_label") or ""): item for item in prediction.get("lot_thresholds") or []}
    _assert(prediction.get("fractional_time_priority_required") is False, "covered account pool: global time priority mismatch", failures)
    _assert_close(by_label.get("0+1", {}).get("threshold_amount_wan"), 1.0, "covered account pool: 0+1 mismatch", failures)
    _assert_close(by_label.get("2+1", {}).get("threshold_amount_wan"), 4.0, "covered account pool: 2+1 mismatch", failures)
    _assert(by_label.get("2+1", {}).get("time_priority_required") is False, "covered account pool: 2+1 should not require time", failures)


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
    _assert(
        prediction.get("top_apply_time_priority_required") is True,
        "top apply below guaranteed: time priority flag missing",
        failures,
    )
    _assert(
        prediction.get("top_apply_time_priority_note") == "必须抢时间（顶格仍不足正股）",
        "top apply below guaranteed: time note mismatch",
        failures,
    )
    _assert(
        prediction.get("fractional_time_priority_note") == "必须抢时间（顶格账户正股/碎股均按时间优先）",
        "top apply below guaranteed: fractional time note mismatch",
        failures,
    )
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
    _run_frozen_funds_floor_case(failures)
    _run_similar_top_apply_frozen_anchor_case(failures)
    _run_920126_lot_threshold_case(failures)
    _run_fractional_between_one_and_two_display_case(failures)
    _run_account_pool_fractional_threshold_case(failures)
    _run_account_pool_fully_covered_fractional_case(failures)
    _run_top_apply_below_guaranteed_case(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("OK subscription predictor validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
