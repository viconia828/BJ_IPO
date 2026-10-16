from __future__ import annotations

import copy
import sys
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "tools"))
sys.path.insert(0, str(ROOT_DIR / "code"))

import subscription_replay_timeline as timeline
import tune_subscription_prediction as tuning
import evaluate_account_pool_update_strategies as fractional


def row(code: str, applied: str, published: str) -> dict:
    return {
        "security_code": code, "apply_date": applied, "issue_result_date": published,
        "issue_price": 10, "online_issue_shares": 8000, "top_apply_amount_wan": 2.5,
        "top_apply_shares": 2500, "online_valid_shares": 80000, "frozen_funds_yi": 0.008,
        "subscription_multiple": 10, "allocation_rate_pct": 10,
        "guaranteed_threshold_amount_wan": 1, "top_apply_below_guaranteed": False,
        "manual_ladder": "1+0=1;2+0=2;2+1=2.5", "manual_ladder_label_ready": True,
        "model_ready": True, "allocation_fit_usable_for_tuning": True,
        "online_allocated_accounts": 50,
    }


def pool_row(source: dict) -> dict:
    result = {key: source[key] for key in ("security_code", "apply_date")}
    result["account_pool_snapshot_state"] = True
    for threshold, accounts in ((1, 45), (2, 25), (3, 15), (4, 5)):
        result[f"accounts_ge_{threshold}w_estimate"] = accounts
        result[f"accounts_ge_{threshold}w_basis"] = "observed_threshold"
    result["accounts_ge_999w_estimate"] = ""
    result["accounts_ge_999w_basis"] = ""
    return result


class SubscriptionReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.history = [
            row("920001", "2026-01-01", "2026-01-02"),
            row("920002", "2026-01-04", "2026-01-05"),
            row("920003", "2026-01-07", "2026-01-08"),
        ]
        self.target = row("920004", "2026-01-10", "2026-01-12")
        self.params = {
            "subscription_prediction_account_pool_rows": [pool_row(item) for item in self.history],
            "subscription_prediction_frozen_funds_floor_enabled": False,
            "subscription_prediction_frozen_funds_cap_enabled": False,
            "subscription_prediction_similar_top_apply_frozen_enabled": False,
            "subscription_prediction_account_pool_prior_weight": 0,
            "subscription_prediction_lock_factor_exponent": 0,
            "subscription_prediction_cap_factor_exponent": 0,
            "subscription_prediction_issue_factor_exponent": 0,
        }

    def test_same_day_unpublished_self_and_undated_rows_are_excluded(self) -> None:
        self.assertTrue(timeline.known_before(self.history[0], self.target))
        for source in (
            self.target, row("same", "2026-01-10", "2026-01-10"),
            row("pending", "2026-01-01", "2026-01-11"),
            row("same_result_day", "2026-01-01", "2026-01-10"),
            row("unknown", "2026-01-01", ""),
            {**self.history[0], "manual_label_available_date": "2026-01-11"},
        ):
            with self.subTest(code=source["security_code"]):
                self.assertFalse(timeline.known_before(source, self.target))
        fallback = row("listed", "2026/01/01", "")
        fallback["listing_date"] = "20260109"
        self.assertTrue(timeline.known_before(fallback, self.target))

    def test_cumulative_snapshot_waits_for_all_dependencies_and_trims_future_knots(self) -> None:
        sources = copy.deepcopy(self.history)
        sources[1]["issue_result_date"] = "2026-01-20"
        snapshots = timeline.account_pool_timeline([pool_row(item) for item in sources], sources)
        available = timeline.account_pool_before(snapshots, self.target)
        self.assertEqual([item["security_code"] for item in available], ["920001"])
        self.assertNotIn("accounts_ge_999w_estimate", available[0])
        later = {**self.target, "apply_date": "2026-01-21"}
        self.assertEqual(len(timeline.account_pool_before(snapshots, later)), 3)
        sources[0]["issue_result_date"] = ""
        self.assertEqual(timeline.account_pool_timeline([pool_row(item) for item in sources], sources), [])

    def test_future_data_cannot_change_prior_prediction(self) -> None:
        sources = self.history + [self.target]
        before = tuning.evaluate_subscription_prediction(sources, params=copy.deepcopy(self.params))
        future = row("920005", "2026-01-15", "2026-01-18")
        altered = copy.deepcopy(self.params)
        future_pool = pool_row(future)
        future_pool["accounts_ge_1w_estimate"] = 99999999
        altered["subscription_prediction_account_pool_rows"] += [pool_row(self.target), future_pool]
        after = tuning.evaluate_subscription_prediction(sources + [future], params=altered)
        initial = before["details"][0]
        repeated = next(item for item in after["details"] if item["security_code"] == self.target["security_code"])
        for field in ("predicted_frozen_funds_yi", "manual_ladder_errors", "account_pool_source_codes", "history_source_codes"):
            self.assertEqual(initial[field], repeated[field], field)
        self.assertEqual(repeated["history_source_codes"], ["920001", "920002", "920003"])
        self.assertTrue(repeated["account_pool_source_codes"])
        self.assertTrue(set(repeated["account_pool_source_codes"]) <= set(repeated["history_source_codes"]))

    def test_unpublished_history_does_not_count_toward_warmup(self) -> None:
        sources = copy.deepcopy(self.history)
        sources[2]["issue_result_date"] = "2026-01-11"
        summary = tuning.evaluate_subscription_prediction(sources + [self.target], params=self.params)
        self.assertEqual(summary["evaluated_rows"], 0)

    def test_recency_weights_and_same_day_invariance(self) -> None:
        details = [
            {"replay_as_of_date": day, "actual_frozen_funds_yi": 10,
             "frozen_funds_pct_error": error, "frozen_funds_abs_error_yi": error * 10}
            for day, error in (("2026-01-01", 0.3), ("2026-01-02", 0.2), ("2026-01-03", 0.1))
        ]
        metrics = tuning._frozen_funds_metrics(details, 1)
        self.assertEqual([item["frozen_score_weight"] for item in details], [0.25, 0.5, 1])
        self.assertAlmostEqual(metrics["frozen_funds_weighted_mape"], (0.3 * .25 + .2 * .5 + .1) / 1.75)
        details[0]["replay_as_of_date"] = details[1]["replay_as_of_date"]
        metrics = tuning._frozen_funds_metrics(details, 1)
        self.assertEqual(details[0]["frozen_score_weight"], details[1]["frozen_score_weight"])
        self.assertEqual(metrics, tuning._frozen_funds_metrics(list(reversed(details)), 1))
        for invalid in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                tuning._frozen_funds_metrics(details, invalid)

    def test_funding_metrics_include_unreachable_regular_thresholds(self) -> None:
        target = {**self.target, "top_apply_amount_wan": 0.1, "top_apply_below_guaranteed": True,
                  "guaranteed_threshold_amount_wan": None}
        summary = tuning.evaluate_subscription_prediction(self.history + [target], params=self.params)
        self.assertEqual(summary["guaranteed_amount_metric_rows"], 0)
        self.assertEqual(summary["frozen_funds_metric_rows"], 1)

    def test_prior_recomputes_funding_and_fractional_outputs_together(self) -> None:
        params = {**self.params, "subscription_prediction_account_pool_prior_weight": 1}
        pred = tuning.subscription_predictor.build_subscription_prediction(
            {**tuning._target_from_row(self.target), "ONLINE_VA_SHARES": 80000}, params=params,
        )
        prior = {"valid_subscription_shares": 400000, "sample_count": 3, "source_codes": ["920001"]}
        with patch.object(tuning.subscription_context, "_account_pool_prior_from_history", return_value=prior):
            updated = tuning._apply_account_pool_prior(pred, self.target, self.history, params)
        expected = tuning.subscription_predictor.build_subscription_prediction(
            {**tuning._target_from_row(self.target), "ONLINE_VA_SHARES": 400000}, params=params,
        )
        self.assertTrue(updated["account_pool_prior"]["applied"])
        for key in ("frozen_funds_yi", "fractional_threshold_amount_wan", "lot_thresholds"):
            self.assertEqual(updated[key], expected[key], key)

    def test_adaptive_calibration_uses_the_historical_subscription_cutoff(self) -> None:
        # The delayed result predates the target's result, but was unknown at
        # subscription time. It must not enter the target's calibration fit.
        history = self.history + [row("delayed", "2026-01-06", "2026-01-11"), self.target]
        seen = {}
        def estimate(target, recent, *args):
            seen[target["SECURITY_CODE"]] = [item["SECURITY_CODE"] for item in recent]
            return {"recent_market_level": {"pre_adjustment_frozen_funds_yi": 0.008}}
        with patch.object(tuning.subscription_predictor, "_estimate_valid_subscription_shares", side_effect=estimate):
            tuning.subscription_predictor._adaptive_recent_market_level_from_history(
                [tuning._recent_ipo_from_row(item) for item in history],
                {"subscription_prediction_recent_market_level_adaptive_enabled": True,
                 "subscription_prediction_min_samples": 3}, 1.0,
            )
        self.assertEqual(set(seen["920004"]), {"920001", "920002", "920003"})

    def test_independent_fractional_replay_updates_only_published_history(self) -> None:
        delayed = {**self.history[1], "issue_result_date": "2026-01-20"}
        rows = [self.target, delayed, self.history[0], self.history[2]]
        result = fractional._evaluate_strategy(rows, name="test", update_mode="full_interpolation")
        target = next(item for item in result["details"] if item["security_code"] == "920004")
        self.assertEqual(target["history_source_codes"], ["920001", "920003"])
        self.assertEqual(result["evaluation_scope"], "fractional_interpolation_given_actual_allocation_ratio")
        changed = copy.deepcopy(rows)
        changed[1]["manual_ladder"] = "0+1=99999"
        changed_result = fractional._evaluate_strategy(changed, name="test", update_mode="full_interpolation")
        changed_target = next(item for item in changed_result["details"] if item["security_code"] == "920004")
        self.assertEqual(target, changed_target)


if __name__ == "__main__":
    unittest.main(verbosity=2)
