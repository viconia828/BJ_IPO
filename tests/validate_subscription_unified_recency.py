from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT / "tools"))
import subscription_predictor as p
import subscription_prediction_context as c
import tune_subscription_prediction as t
from validate_subscription_replay_timeline import row, pool_row


class UnifiedRecencyTests(unittest.TestCase):
    def setUp(self):
        self.rows = [row(str(i), f"2026-07-{i:02d}", f"2026-07-{i:02d}") for i in range(1, 10)]
        self.params = {"subscription_prediction_unified_recency_enabled": True,
                       "subscription_prediction_sample_decay_half_life_days": 1,
                       "subscription_prediction_account_pool_rows": [pool_row(r) for r in self.rows],
                       "subscription_prediction_recent_market_level_adaptive_enabled": True,
                       "subscription_prediction_similar_top_apply_frozen_min_samples": 1,
                       "subscription_prediction_account_pool_prior_weight": 1,
                       "subscription_prediction_frozen_funds_floor_quantile": .1,
                       "subscription_prediction_frozen_funds_cap_quantile": .9}
        self.target = c._target_from_row(row("target", "2026-07-11", "2026-07-14"))

    def test_all_base_statistics_follow_the_same_recent_weights(self):
        records = [c._recent_ipo_from_row(r) for r in self.rows]
        latest = records[-1]
        latest.update(TOP_APPLY_MARKETCAP=100, ONLINE_ISSUE_NUM=16000,
                      ONLINE_VA_SHARES=640000, ISSUE_RESULT_DATE="2026-07-10")
        result = p.build_subscription_prediction(self.target, records, self.params)
        estimate = result["estimate"]
        for field, expected in (("median_subscription_multiple", 40), ("median_top_apply_wan", 100),
                                ("median_issue_amount_yi", .0016), ("median_lock_days", 1)):
            self.assertAlmostEqual(estimate[field], expected, msg=field)
        self.assertEqual(estimate["recency_weights"]["9"], 1)

    def test_component_filters_do_not_reset_sample_ages(self):
        records = [c._recent_ipo_from_row(r) for r in self.rows]
        expected = p._history_recency_weights(records, self.params)
        result = c.build_prediction(self.target, history_rows=self.rows, params=self.params)
        level = result["estimate"]["recent_market_level"]
        for sample in level["samples"]:
            self.assertEqual(sample["weight"], expected[sample["security_code"]])
        for sample in result["estimate"]["similar_top_apply_frozen_funds"]["samples"]:
            self.assertEqual(sample["recency_weight"], expected[sample["security_code"]])
        def demand(r, cap):
            return None if r["security_code"] == "8" else {"security_code": r["security_code"], "estimated_demand_wan": 100, "confidence": 1}
        with patch.object(c, "_repaired_account_demand_wan_from_row", side_effect=demand):
            prior_rows=[{**r,"account_distribution_available_date":r["issue_result_date"]} for r in self.rows]
            prior = c._account_pool_prior_from_history(prior_rows, c.target_row_from_ipo(self.target),
                recent_samples=0, half_life_samples=1, recency_weights=expected)
        for sample in prior["samples"]:
            self.assertEqual(sample["weight"], expected[sample["security_code"]])

    def test_old_component_windows_and_half_lives_are_inactive(self):
        initial = c.build_prediction(self.target, history_rows=self.rows, params=self.params)
        changed = {**self.params, **{key: 1 for key in t.UNIFIED_RECENCY_INACTIVE_KEYS},
                   "subscription_prediction_frozen_funds_floor_recent_samples": 1,
                   "subscription_prediction_frozen_funds_cap_recent_samples": 1}
        self.assertEqual(initial, c.build_prediction(self.target, history_rows=self.rows, params=changed))

    def test_pool_interpolation_uses_global_ages_with_missing_points(self):
        rows = [pool_row(r) for r in self.rows]
        rows[-1]["accounts_ge_1w_estimate"] = 100
        settings = {**self.params, "subscription_prediction_account_pool_rows": rows,
                    "subscription_prediction_recency_weights": p._history_recency_weights(
                        [c._recent_ipo_from_row(r) for r in self.rows], self.params)}
        index = p._prepare_account_pool_index(settings)
        kw = dict(amount_wan=1, rows=rows, thresholds=index["thresholds"], top_apply_amount_wan=10, settings=settings)
        direct = p._estimate_account_pool_accounts_ge(**kw)
        indexed = p._estimate_account_pool_accounts_ge(**kw, account_pool_index=index)
        self.assertEqual(direct, indexed)
        self.assertEqual(indexed["estimate"], 100)
        self.assertEqual(indexed["sample_count"], 9)

    def test_same_day_results_receive_equal_age_and_weight(self):
        samples = [{"sample_date_ordinal": day} for day in (5, 4, 4, 3)]
        weighted = p._with_recency_weights(samples, self.params)
        self.assertEqual([s["recency_weight"] for s in weighted], [1, .5, .5, .125])

    def test_old_extreme_no_longer_dominates_weighted_bounds(self):
        samples = [{"sample_date_ordinal": 10-i, "frozen_funds_yi": 100 if i<8 else 100000,
                    "security_code": str(i)} for i in range(9)]
        cap = p._frozen_funds_cap_from_samples(samples, 10, 8000, self.params)
        self.assertEqual(cap["raw_cap_frozen_funds_yi"], 100)
        samples[-1]["frozen_funds_yi"] = .001
        floor = p._frozen_funds_floor_from_samples(samples, 10, 8000, self.params)
        self.assertEqual(floor["raw_floor_frozen_funds_yi"], 100)

    def test_tuning_deduplicates_inactive_time_parameters(self):
        grid = {"subscription_prediction_sample_decay_half_life_days": [1, 3],
                "subscription_prediction_recent_market_level_adaptive_half_life_samples": [1, 50],
                "subscription_prediction_similar_top_apply_frozen_recent_samples": [3, 30]}
        candidates = t._candidate_params_from_grid(grid, self.params)
        self.assertEqual(len(candidates), 2)
        self.assertFalse(set(t._subscription_auto_param_snapshot(self.params, include_prior=True)) & t.UNIFIED_RECENCY_INACTIVE_KEYS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
