from __future__ import annotations

import copy
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT / "tools"))

import subscription_prediction_context as context
import subscription_predictor as predictor
import tune_subscription_prediction as tuning
import report_generator
from validate_subscription_replay_timeline import row, pool_row
from validate_report_dual_valuation_ranges import _payload


class HistoryContextTests(unittest.TestCase):
    def setUp(self):
        self.history = [row("one", "2025-01-01", "2025-01-03"),
                        row("two", "2026-01-01", "2026-01-03"),
                        row("three", "2026-03-01", "2026-03-03")]
        self.target = row("target", "2026-04-10", "2026-04-13")
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

    def predict(self, rows=None, params=None, **kwargs):
        return context.build_prediction(context._target_from_row(self.target),
            history_rows=self.history if rows is None else rows,
            params=self.params if params is None else params, **kwargs)

    def test_full_history_independent_of_sentiment_window(self):
        expected = self.predict(params={**self.params, "recent_days": 30})
        self.assertTrue(expected["available"])
        self.assertEqual(expected["history_context"]["history_source_codes"], ["one", "two", "three"])
        for days in (3, 90, 365):
            self.assertEqual(expected, self.predict(params={**self.params, "recent_days": days}))

    def test_analysis_date_and_apply_date_both_limit_known_history(self):
        later = row("later", "2026-04-02", "2026-04-09")
        same = row("same", "2026-04-03", "2026-04-10")
        missing = row("undated", "2026-02-01", "")
        sources = self.history + [later, same, missing, self.target]
        full = self.predict(sources)
        early = self.predict(sources, as_of_date=date(2026, 4, 8))
        self.assertEqual(full["history_context"]["history_source_codes"], ["one", "two", "three", "later"])
        self.assertEqual(early["history_context"]["history_source_codes"], ["one", "two", "three"])
        self.assertEqual(self.predict(sources, as_of_date="2026-04-09")["frozen_funds_yi"], full["frozen_funds_yi"])
        params = {**self.params, "subscription_prediction_account_pool_rows": [pool_row(r) for r in sources if r != missing]}
        early_pool = self.predict(sources, params, as_of_date="2026-04-08")
        self.assertEqual(early_pool["history_context"]["account_pool_snapshot_codes"], ["one", "two", "three"])

    def test_recency_weighting_still_affects_prediction(self):
        sources = copy.deepcopy(self.history)
        sources[0]["online_valid_shares"] *= 3
        sources[1]["online_valid_shares"] *= 2
        short = self.predict(sources, {**self.params, "subscription_prediction_sample_decay_half_life_days": 1})
        long = self.predict(sources, {**self.params, "subscription_prediction_sample_decay_half_life_days": 100})
        self.assertLess(short["frozen_funds_yi"], long["frozen_funds_yi"])

    def test_tuning_and_production_share_prior_and_threshold_calculation(self):
        params = {**self.params, "subscription_prediction_account_pool_prior_weight": 1}
        prior = {"valid_subscription_shares": 400000, "sample_count": 3, "source_codes": ["one"]}
        with patch.object(context, "_account_pool_prior_from_history", return_value=prior):
            production = self.predict(params=params)
            replay = tuning.evaluate_subscription_prediction(self.history + [self.target], params=params)["details"][0]
        self.assertTrue(production["account_pool_prior"]["applied"])
        self.assertEqual(production["frozen_funds_yi"], replay["predicted_frozen_funds_yi"])
        self.assertEqual(production["guaranteed_threshold_amount_wan"], replay["predicted_guaranteed_amount_wan"])
        ladder = {(v["regular_lots"], v["fractional_lots"]): v for v in production["lot_thresholds"]}
        for detail in replay["manual_ladder_errors"]:
            item = ladder.get((detail["regular_lots"], detail["fractional_lots"]))
            if item:
                self.assertEqual(item["threshold_amount_wan"], detail["predicted_threshold_amount_wan"])

    def test_published_target_keeps_actual_mode_and_funding(self):
        with patch.object(context, "_apply_account_pool_prior", side_effect=AssertionError("must preserve actual")):
            result = context.build_prediction({**context._target_from_row(self.target), "ONLINE_VA_SHARES": 80000},
                history_rows=self.history, params=self.params)
        self.assertEqual(result["mode"], "actual")
        self.assertEqual(result["frozen_funds_yi"], .008)

    def test_report_fallback_uses_full_history_not_display_rows(self):
        payload = _payload()
        payload.update(analysis_date="2026-04-09", ipo_info=context._target_from_row(self.target), params=self.params,
                       recent_ipos=[{"SECURITY_CODE": "display_only"}])
        with patch.object(context, "load_history_rows", return_value=self.history), \
                patch.object(context, "build_prediction", wraps=context.build_prediction) as build:
            report_generator._prepare_report_context(payload)
        self.assertEqual(build.call_args.kwargs["as_of_date"], "2026-04-09")
        self.assertNotIn("recent_ipos", build.call_args.kwargs)

    def test_loader_matches_offline_label_merge(self):
        rows, _ = tuning._prepare_rows_with_ladder_labels(tuning._load_history_rows(tuning.DEFAULT_HISTORY_PATH), sync_missing_rows=False)
        self.assertEqual(rows, context.load_history_rows())

    def test_availability_date_reaches_adaptive_calibration(self):
        delayed = {**self.history[0], "manual_label_available_date": "2026-04-08"}
        sample = predictor._historical_sample(context._recent_ipo_from_row(delayed))
        self.assertEqual(sample["sample_date_ordinal"], date(2026, 4, 8).toordinal())


if __name__ == "__main__":
    unittest.main(verbosity=2)
