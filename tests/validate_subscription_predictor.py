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
    _assert_close(similar_anchor.get("anchor_frozen_funds_yi"), 9600.0, "similar frozen anchor: anchor mismatch", failures)
    _assert_close(prediction.get("frozen_funds_yi"), 9600.0, "similar frozen anchor: frozen funds mismatch", failures)
    _assert(float(similar_anchor.get("base_frozen_funds_yi") or 0.0) > 15000.0, "similar frozen anchor: base should be higher", failures)


def _run_frozen_funds_cap_case(failures: list[str]) -> None:
    recent_ipos = [
        {
            "SECURITY_CODE": "920201",
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 10000000.0,
            "FROZEN_FUNDS_YI": 9000.0,
            "TOP_APPLY_MARKETCAP": 1000.0,
            "APPLY_DATE": "2026-05-10",
            "ISSUE_RESULT_DATE": "2026-05-13",
        },
        {
            "SECURITY_CODE": "920202",
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 10000000.0,
            "FROZEN_FUNDS_YI": 9600.0,
            "TOP_APPLY_MARKETCAP": 1000.0,
            "APPLY_DATE": "2026-05-20",
            "ISSUE_RESULT_DATE": "2026-05-23",
        },
        {
            "SECURITY_CODE": "920203",
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 10000000.0,
            "FROZEN_FUNDS_YI": 11000.0,
            "TOP_APPLY_MARKETCAP": 1000.0,
            "APPLY_DATE": "2026-05-25",
            "ISSUE_RESULT_DATE": "2026-05-28",
        },
    ]
    prediction = subscription_predictor.build_subscription_prediction(
        {
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 20000000.0,
            "TOP_APPLY_MARKETCAP": 3200.0,
            "APPLY_DATE": "2026-06-01",
            "ISSUE_RESULT_DATE": "2026-06-04",
        },
        recent_ipos=recent_ipos,
        params={
            "subscription_prediction_min_samples": 3,
            "subscription_prediction_similar_top_apply_frozen_enabled": False,
            "subscription_prediction_frozen_funds_floor_enabled": False,
            "subscription_prediction_frozen_funds_cap_weight": 1.0,
        },
    )
    cap = prediction.get("frozen_funds_cap") or {}
    _assert(prediction.get("available") is True, "frozen cap: prediction unavailable", failures)
    _assert(cap.get("applied") is True, "frozen cap: cap not applied", failures)
    _assert_close(cap.get("cap_frozen_funds_yi"), 11000.0, "frozen cap: cap mismatch", failures)
    _assert_close(prediction.get("frozen_funds_yi"), 11000.0, "frozen cap: frozen funds mismatch", failures)
    _assert(float(cap.get("pre_cap_frozen_funds_yi") or 0.0) > 18000.0, "frozen cap: pre-cap should be higher", failures)


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


def _run_fractional_equals_second_guaranteed_case(failures: list[str]) -> None:
    prediction = subscription_predictor.build_subscription_prediction(
        {
            "SECURITY_CODE": "920138",
            "SECURITY_NAME_ABBR": "EqualBoundary",
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 1000000.0,
            "ONLINE_VA_SHARES": 1000000000.0,
            "TOP_APPLY_MARKETCAP": 500.0,
            "FRACTIONAL_THRESHOLD_SHARES": 200000.0,
            "FRACTIONAL_TIME_PRIORITY_REQUIRED": True,
        },
        recent_ipos=[],
        params={"subscription_prediction_lot_threshold_max_lots": 4},
    )
    lot_thresholds = prediction.get("lot_thresholds") or []
    by_label = {str(item.get("ladder_label") or ""): item for item in lot_thresholds}
    display_labels = [
        str(item.get("ladder_label") or "")
        for item in lot_thresholds
        if item.get("display") is not False
    ]
    _assert_close(by_label.get("1+1", {}).get("threshold_amount_wan"), 200.0, "equal boundary: 1+1 amount mismatch", failures)
    _assert_close(by_label.get("2+0", {}).get("threshold_amount_wan"), 200.0, "equal boundary: 2+0 amount mismatch", failures)
    _assert_close(by_label.get("2+1", {}).get("threshold_amount_wan"), 200.0, "equal boundary: 2+1 amount mismatch", failures)
    _assert(by_label.get("1+1", {}).get("display") is False, "equal boundary: dominated 1+1 should be hidden", failures)
    _assert(by_label.get("2+0", {}).get("display") is True, "equal boundary: 2+0 should display", failures)
    _assert(by_label.get("2+1", {}).get("display") is True, "equal boundary: 2+1 should display", failures)
    _assert(by_label.get("2+1", {}).get("time_priority_required") is True, "equal boundary: third lot should require time priority", failures)
    _assert("1+1" not in display_labels, "equal boundary: 1+1 display label should be removed", failures)
    _assert("2+0" in display_labels and "2+1" in display_labels, "equal boundary: 2+0 and 2+1 should both display", failures)
    _assert(
        prediction.get("fractional_time_priority_note") == "2+1以下碎股可能需要抢时间",
        "equal boundary: time-priority note should point to the third lot",
        failures,
    )
    table_labels = [str(row[0]) for row in (prediction.get("table_rows") or []) if "建议申购门槛" in str(row[0])]
    _assert("1+1建议申购门槛" not in table_labels, "equal boundary: table should hide 1+1", failures)
    _assert("2+0建议申购门槛" in table_labels, "equal boundary: table should keep 2+0", failures)
    _assert("2+1建议申购门槛" in table_labels, "equal boundary: table should keep 2+1", failures)


def _run_target_frozen_funds_override_case(failures: list[str]) -> None:
    target = {
        "SECURITY_CODE": "920138",
        "SECURITY_NAME_ABBR": "TargetScenario",
        "ISSUE_PRICE": 10.0,
        "ONLINE_ISSUE_NUM": 1000000.0,
        "TOP_APPLY_MARKETCAP": 500.0,
    }
    params = {
        "subscription_prediction_target_frozen_funds_code": 920138,
        "subscription_prediction_target_frozen_funds_yi": 100.0,
        "subscription_prediction_target_frozen_funds_low_yi": 95.0,
        "subscription_prediction_target_frozen_funds_high_yi": 105.0,
        "subscription_prediction_target_frozen_funds_reason": "明星股情景",
        "subscription_prediction_lot_threshold_max_lots": 4,
    }
    prediction = subscription_predictor.build_subscription_prediction(target, recent_ipos=[], params=params)
    override = prediction.get("target_frozen_funds_override") or {}
    _assert(prediction.get("available") is True, "target frozen override: prediction unavailable", failures)
    _assert_close(prediction.get("frozen_funds_yi"), 100.0, "target frozen override: midpoint mismatch", failures)
    _assert_close(prediction.get("subscription_multiple"), 1000.0, "target frozen override: multiple mismatch", failures)
    _assert(override.get("security_code") == "920138", "target frozen override: code mismatch", failures)
    _assert(override.get("reason") == "明星股情景", "target frozen override: reason mismatch", failures)
    table_by_label = {str(row[0]): row for row in prediction.get("table_rows") or []}
    _assert(
        (table_by_label.get("个股冻资情景区间") or [None, None])[1] == "95.00-105.00 亿元",
        "target frozen override: range row mismatch",
        failures,
    )
    _assert(
        (table_by_label.get("个股情景说明") or [None, None])[1] == "明星股情景",
        "target frozen override: reason row mismatch",
        failures,
    )

    actual_prediction = subscription_predictor.build_subscription_prediction(
        {**target, "ONLINE_VA_SHARES": 2000000.0},
        recent_ipos=[],
        params=params,
    )
    _assert(
        actual_prediction.get("target_frozen_funds_override") is None,
        "target frozen override: published actual data must not be overridden",
        failures,
    )
    non_target_prediction = subscription_predictor.build_subscription_prediction(
        {**target, "SECURITY_CODE": "920139"},
        recent_ipos=[],
        params=params,
    )
    _assert(
        non_target_prediction.get("target_frozen_funds_override") is None,
        "target frozen override: non-target stock must not be overridden",
        failures,
    )
    _assert(
        non_target_prediction.get("available") is False,
        "target frozen override: non-target stock must keep its original insufficient-data result",
        failures,
    )


def _run_recent_market_level_factor_case(failures: list[str]) -> None:
    recent_ipos = [
        {
            "SECURITY_CODE": f"92010{index}",
            "APPLY_DATE": f"2026-07-0{index}",
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 1000000.0,
            "ONLINE_VA_SHARES": 10000000.0,
        }
        for index in range(1, 4)
    ]
    target = {
        "SECURITY_CODE": "920200",
        "ISSUE_PRICE": 10.0,
        "ONLINE_ISSUE_NUM": 1000000.0,
        "TOP_APPLY_MARKETCAP": 500.0,
    }
    params = {
        "subscription_prediction_recent_market_level_factor": 1.10,
        "subscription_prediction_similar_top_apply_frozen_enabled": False,
        "subscription_prediction_frozen_funds_floor_enabled": False,
        "subscription_prediction_frozen_funds_cap_enabled": False,
        "subscription_prediction_cap_factor_exponent": 0.0,
        "subscription_prediction_issue_factor_exponent": 0.0,
        "subscription_prediction_lock_factor_exponent": 0.0,
        "subscription_prediction_multiple_scale": 1.0,
    }
    prediction = subscription_predictor.build_subscription_prediction(target, recent_ipos, params)
    recent_level = (prediction.get("estimate") or {}).get("recent_market_level") or {}
    _assert_close(prediction.get("frozen_funds_yi"), 1.10, "recent market level: frozen funds mismatch", failures)
    _assert_close(prediction.get("subscription_multiple"), 11.0, "recent market level: multiple mismatch", failures)
    _assert(recent_level.get("applied") is True, "recent market level: adjustment not applied", failures)
    table_labels = [str(row[0]) for row in prediction.get("table_rows") or []]
    _assert("近期资金水位修正" in table_labels, "recent market level: report row missing", failures)

    actual_prediction = subscription_predictor.build_subscription_prediction(
        {**target, "ONLINE_VA_SHARES": 12000000.0},
        recent_ipos,
        params,
    )
    _assert_close(
        actual_prediction.get("subscription_multiple"),
        12.0,
        "recent market level: published actual data must not be adjusted",
        failures,
    )


def _run_adaptive_recent_market_level_case(failures: list[str]) -> None:
    recent_ipos: list[dict[str, Any]] = []
    for index in range(1, 14):
        valid_shares = 12000000.0 if index >= 11 else 10000000.0
        recent_ipos.append(
            {
                "SECURITY_CODE": f"9201{index:02d}",
                "APPLY_DATE": f"2026-07-{index:02d}",
                "ISSUE_RESULT_DATE": f"2026-07-{index:02d}",
                "ISSUE_PRICE": 10.0,
                "ONLINE_ISSUE_NUM": 1000000.0,
                "TOP_APPLY_MARKETCAP": 500.0,
                "ONLINE_VA_SHARES": valid_shares,
            }
        )
    params = {
        "subscription_prediction_recent_market_level_factor": 1.03,
        "subscription_prediction_recent_market_level_adaptive_enabled": True,
        "subscription_prediction_recent_market_level_adaptive_recent_samples": 3,
        "subscription_prediction_recent_market_level_adaptive_min_samples": 3,
        "subscription_prediction_recent_market_level_adaptive_half_life_samples": 2.0,
        "subscription_prediction_recent_market_level_adaptive_weight": 1.0,
        "subscription_prediction_recent_market_level_adaptive_factor_min": 0.80,
        "subscription_prediction_recent_market_level_adaptive_factor_max": 1.30,
        "subscription_prediction_sample_decay_half_life_days": 40,
        "subscription_prediction_similar_top_apply_frozen_enabled": False,
        "subscription_prediction_frozen_funds_floor_enabled": False,
        "subscription_prediction_frozen_funds_cap_enabled": False,
        "subscription_prediction_cap_factor_exponent": 0.0,
        "subscription_prediction_issue_factor_exponent": 0.0,
        "subscription_prediction_lock_factor_exponent": 0.0,
        "subscription_prediction_multiple_scale": 1.0,
    }
    prediction = subscription_predictor.build_subscription_prediction(
        {
            "SECURITY_CODE": "920199",
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 1000000.0,
            "TOP_APPLY_MARKETCAP": 500.0,
        },
        recent_ipos,
        params,
    )
    recent_level = (prediction.get("estimate") or {}).get("recent_market_level") or {}
    _assert(recent_level.get("available") is True, "adaptive market level: calibration unavailable", failures)
    _assert_close(recent_level.get("observed_factor"), 1.20, "adaptive market level: observed factor mismatch", failures)
    _assert_close(recent_level.get("factor"), 1.20, "adaptive market level: applied factor mismatch", failures)
    _assert_close(prediction.get("subscription_multiple"), 12.0, "adaptive market level: multiple mismatch", failures)
    _assert(
        recent_level.get("source_codes") == ["920113", "920112", "920111"],
        f"adaptive market level: source order mismatch {recent_level.get('source_codes')}",
        failures,
    )
    table_labels = [str(row[0]) for row in prediction.get("table_rows") or []]
    _assert("近期资金水位自适应" in table_labels, "adaptive market level: report row missing", failures)


def _run_historical_sample_date_order_case(failures: list[str]) -> None:
    recent_ipos = [
        {
            "SECURITY_CODE": f"92030{index}",
            "ISSUE_RESULT_DATE": result_date,
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 1000000.0,
            "ONLINE_VA_SHARES": valid_shares,
        }
        for index, (result_date, valid_shares) in enumerate(
            (
                ("2026-07-01", 10000000.0),
                ("2026-07-03", 30000000.0),
                ("2026-07-02", 20000000.0),
            ),
            start=1,
        )
    ]
    prediction = subscription_predictor.build_subscription_prediction(
        {
            "SECURITY_CODE": "920399",
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 1000000.0,
        },
        recent_ipos,
        {
            "subscription_prediction_frozen_funds_floor_recent_samples": 3,
            "subscription_prediction_frozen_funds_floor_min_samples": 3,
        },
    )
    floor = (prediction.get("estimate") or {}).get("frozen_funds_floor") or {}
    _assert(
        floor.get("source_codes") == ["920302", "920303", "920301"],
        f"historical sample order: expected newest issue result first, got {floor.get('source_codes')}",
        failures,
    )


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



def _run_account_pool_runtime_index_cache_case(failures: list[str]) -> None:
    account_pool_row: dict[str, Any] = {
        "security_code": "920920",
        "apply_date": "2026-06-30",
    }
    for threshold, accounts in {1: 45, 2: 25, 3: 15, 4: 5}.items():
        account_pool_row[f"accounts_ge_{threshold}w_estimate"] = accounts
        account_pool_row[f"accounts_ge_{threshold}w_basis"] = "exact_observed_threshold"

    params = {
        "subscription_prediction_account_pool_rows": [account_pool_row],
        "subscription_prediction_account_pool_recent_samples": 1,
        "subscription_prediction_lot_threshold_max_lots": 5,
    }
    target = {
        "SECURITY_CODE": "920921",
        "SECURITY_NAME_ABBR": "PoolCache",
        "ISSUE_PRICE": 10.0,
        "ONLINE_ISSUE_NUM": 8000.0,
        "ONLINE_VA_SHARES": 80000.0,
        "TOP_APPLY_MARKETCAP": 2.5,
    }
    first = subscription_predictor.build_subscription_prediction(target, recent_ipos=[], params=params)
    cache = params.get(subscription_predictor.ACCOUNT_POOL_RUNTIME_CACHE_KEY) or {}
    index = cache.get("account_pool_index") or {}
    memo = index.get("accounts_ge_memo") or {}
    _assert(index.get("source") == "params", "account pool cache: expected params source", failures)
    _assert(len(index.get("thresholds") or []) == 4, "account pool cache: threshold count mismatch", failures)
    _assert(bool(memo), "account pool cache: expected populated accounts_ge memo", failures)
    index_id = id(index)
    memo_size = len(memo)

    second = subscription_predictor.build_subscription_prediction(target, recent_ipos=[], params=params)
    cache_after = params.get(subscription_predictor.ACCOUNT_POOL_RUNTIME_CACHE_KEY) or {}
    index_after = cache_after.get("account_pool_index") or {}
    _assert(id(index_after) == index_id, "account pool cache: index should be reused", failures)
    _assert(
        len(index_after.get("accounts_ge_memo") or {}) == memo_size,
        "account pool cache: identical second prediction should reuse memo",
        failures,
    )
    _assert_close(
        first.get("fractional_threshold_amount_wan"),
        second.get("fractional_threshold_amount_wan"),
        "account pool cache: prediction amount changed",
        failures,
    )
def _run_latest_calibrated_account_pool_snapshot_case(failures: list[str]) -> None:
    older_row = {
        "security_code": "920800",
        "apply_date": "2026-06-20",
        "accounts_ge_1w_estimate": 10,
        "accounts_ge_1w_basis": "exact_observed_threshold",
    }
    latest_row = {
        "security_code": "920801",
        "apply_date": "2026-06-30",
        "accounts_ge_1w_estimate": 90,
        "accounts_ge_1w_basis": "calibrated_exact_observed_threshold",
    }
    estimate = subscription_predictor._estimate_account_pool_accounts_ge(
        amount_wan=1.0,
        rows=[older_row, latest_row],
        thresholds=[1.0],
        top_apply_amount_wan=2.0,
        settings={},
    )
    _assert_close(estimate.get("estimate"), 90.0, "latest calibrated account pool: estimate mismatch", failures)
    _assert(
        estimate.get("basis") == "latest_calibrated_account_pool_snapshot",
        "latest calibrated account pool: basis mismatch",
        failures,
    )
    _assert(
        estimate.get("source_codes") == ["920801"],
        "latest calibrated account pool: source code mismatch",
        failures,
    )



def _run_observed_account_pool_snapshot_runtime_interpolation_case(failures: list[str]) -> None:
    snapshot_row = {
        "security_code": "920802",
        "apply_date": "2026-07-01",
        "account_pool_snapshot_state": "true",
        "accounts_ge_400w_estimate": 100,
        "accounts_ge_400w_basis": "observed_threshold",
        "accounts_ge_800w_estimate": 40,
        "accounts_ge_800w_basis": "observed_threshold",
    }
    estimate = subscription_predictor._estimate_account_pool_accounts_ge(
        amount_wan=600.0,
        rows=[snapshot_row],
        thresholds=[400.0, 800.0],
        top_apply_amount_wan=1000.0,
        settings={},
    )
    _assert_close(estimate.get("estimate"), 70.0, "observed snapshot interpolation: estimate mismatch", failures)
    _assert(
        estimate.get("basis") == "latest_account_pool_snapshot",
        "observed snapshot interpolation: basis mismatch",
        failures,
    )

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


def _run_top_apply_below_guaranteed_account_pool_cutoff_case(failures: list[str]) -> None:
    account_pool_row: dict[str, Any] = {
        "security_code": "920922",
        "apply_date": "2026-07-01",
        "account_pool_snapshot_state": "true",
    }
    for threshold, accounts in {1.0: 100, 2.0: 60, 2.5: 40}.items():
        prefix = subscription_predictor._account_pool_column_prefix(threshold)
        account_pool_row[f"{prefix}_estimate"] = accounts
        account_pool_row[f"{prefix}_basis"] = "carry_forward"

    prediction = subscription_predictor.build_subscription_prediction(
        {
            "SECURITY_CODE": "920923",
            "ISSUE_PRICE": 10.0,
            "ONLINE_ISSUE_NUM": 8000.0,
            "ONLINE_VA_SHARES": 800000.0,
            "TOP_APPLY_MARKETCAP": 2.5,
        },
        recent_ipos=[],
        params={
            "subscription_prediction_account_pool_rows": [account_pool_row],
            "subscription_prediction_account_pool_recent_samples": 1,
            "subscription_prediction_lot_threshold_max_lots": 3,
        },
    )
    _assert(prediction.get("top_apply_below_guaranteed") is True, "top apply pool cutoff: expected top apply below guaranteed", failures)
    _assert(
        prediction.get("top_apply_time_priority_required") is False,
        "top apply pool cutoff: top apply should not need time priority when cutoff is below top apply",
        failures,
    )
    _assert(
        prediction.get("time_priority_scope") == "none",
        "top apply pool cutoff: no time priority expected at interpolated boundary",
        failures,
    )
    _assert_close(
        prediction.get("fractional_threshold_amount_wan"),
        1.5,
        "top apply pool cutoff: fractional cutoff amount mismatch",
        failures,
    )
    pool_estimate = prediction.get("account_pool_fractional_estimate") or {}
    _assert_close(pool_estimate.get("full_allocated_lots_estimate"), 0.0, "top apply pool cutoff: full lots mismatch", failures)
    _assert_close(pool_estimate.get("leftover_lots"), 80.0, "top apply pool cutoff: leftover lots mismatch", failures)
    by_label = {str(item.get("ladder_label") or ""): item for item in prediction.get("lot_thresholds") or []}
    _assert_close(by_label.get("0+1", {}).get("threshold_amount_wan"), 1.5, "top apply pool cutoff: 0+1 amount mismatch", failures)


def _run_manual_ladder_runtime_override_case(failures: list[str]) -> None:
    prediction = subscription_predictor.build_subscription_prediction(
        {
            "SECURITY_CODE": "920136",
            "ISSUE_PRICE": 19.28,
            "ONLINE_ISSUE_NUM": 18000000.0,
            "TOP_APPLY_MARKETCAP": 1735.2,
            "ONLINE_VA_SHARES": 56781496200.0,
            "SUBSCRIPTION_MANUAL_LADDER": "1+1=1131.3;2+1=1217.8",
        },
        recent_ipos=[],
        params={"subscription_prediction_lot_threshold_max_lots": 4},
    )
    by_label = {str(item.get("ladder_label") or ""): item for item in prediction.get("lot_thresholds") or []}
    display_labels = [
        str(item.get("ladder_label") or "")
        for item in (prediction.get("lot_thresholds") or [])
        if item.get("display") is not False
    ]
    _assert_close(by_label.get("1+1", {}).get("threshold_amount_wan"), 1131.3, "manual ladder: 1+1 mismatch", failures)
    _assert_close(by_label.get("2+1", {}).get("threshold_amount_wan"), 1217.8, "manual ladder: 2+1 mismatch", failures)
    _assert(by_label.get("1+1", {}).get("basis") == "manual_ladder", "manual ladder: 1+1 source mismatch", failures)
    _assert("1+1" in display_labels, "manual ladder: 1+1 should display", failures)
    _assert("2+0" not in display_labels, "manual ladder: estimated 2+0 should be hidden", failures)
    _assert_close(prediction.get("fractional_threshold_amount_wan"), 1131.3, "manual ladder: fractional amount mismatch", failures)
    overlay = prediction.get("manual_ladder_overlay") or {}
    _assert(int(overlay.get("override_count") or 0) >= 2, "manual ladder: override count mismatch", failures)


def main() -> int:
    failures: list[str] = []
    _run_actual_distribution_case(failures)
    _run_estimated_case(failures)
    _run_frozen_funds_floor_case(failures)
    _run_similar_top_apply_frozen_anchor_case(failures)
    _run_frozen_funds_cap_case(failures)
    _run_920126_lot_threshold_case(failures)
    _run_fractional_between_one_and_two_display_case(failures)
    _run_fractional_equals_second_guaranteed_case(failures)
    _run_target_frozen_funds_override_case(failures)
    _run_recent_market_level_factor_case(failures)
    _run_adaptive_recent_market_level_case(failures)
    _run_historical_sample_date_order_case(failures)
    _run_account_pool_fractional_threshold_case(failures)
    _run_account_pool_runtime_index_cache_case(failures)
    _run_latest_calibrated_account_pool_snapshot_case(failures)
    _run_observed_account_pool_snapshot_runtime_interpolation_case(failures)
    _run_account_pool_fully_covered_fractional_case(failures)
    _run_top_apply_below_guaranteed_case(failures)
    _run_top_apply_below_guaranteed_account_pool_cutoff_case(failures)
    _run_manual_ladder_runtime_override_case(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("OK subscription predictor validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
