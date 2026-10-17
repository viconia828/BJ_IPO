from __future__ import annotations

import math
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
import sentiment_tide as tide
import valuation_engine as ve
import config_loader
import param_tuning
import report_generator


def row(day, gain, code=None):
    return {"SECURITY_CODE": code or f"920{day:03d}", "LISTING_DATE": f"2026-10-{day:02d}",
            "ISSUE_PRICE": 10., "LD_AVERAGE_CHANGE": gain, "AVERAGE_PRICE": 10 * (1 + gain / 100),
            "AFTER_ISSUE_PE": 15., "INDUSTRY_PE_NEW": 30., "float_shares": 1000., "old_shares": 0.,
            "industry_primary": "装备", "industry_secondary": "机械"}


def apply(history, **params):
    return ve.apply_local_center_overlay(
        {"available": True, "target_price": 30., "base_target_price": 20.},
        issue_price=10., issue_pe=15., industry_pe=30., float_shares=1000., old_shares=0.,
        industry={"primary": "装备", "secondary": "机械"}, recent_ipos=history,
        params={"sentiment_tide_enabled": True, "recent_days": 30,
                "local_center_overlay_enabled": True, "local_center_smooth_warmup": True,
                "local_center_alpha": .75, "local_center_min_history": 5, **params},
        target_code="920999", target_listing_date="2026-10-17")


class TideTests(unittest.TestCase):
    def test_shock_only_affects_next_listing(self):
        history = [row(1, 500)]
        self.assertEqual(tide.classify(history, date(2026, 10, 1))["tier"], "medium")
        self.assertEqual(tide.classify(history, date(2026, 10, 2))["tier"], "high")

    def test_break_after_mosen_and_future_excluded(self):
        history = [row(1, 600), row(2, 400), row(15, 92)]
        before = tide.classify(history, date(2026, 10, 15))
        after = tide.classify(history, date(2026, 10, 16))
        self.assertEqual(before["tier"], "high")
        self.assertEqual(after["tier"], "low")
        self.assertEqual(after, tide.classify(history + [row(16, 3000), row(20, 9000)], date(2026, 10, 16)))

    def test_wave_lasts_only_few_batches(self):
        history = [row(1, 600), row(2, 400), row(3, 400), row(4, 400)]
        self.assertEqual(tide.classify(history, date(2026, 10, 4))["tier"], "high")
        self.assertEqual(tide.classify(history, date(2026, 10, 5))["events"][-1]["reason"], "wave_age")
        history += [row(5, 900)]
        self.assertEqual(tide.classify(history, date(2026, 10, 6))["tier"], "high")

    def test_same_day_order_and_duplicate_invariance(self):
        history = [row(1, 600), row(2, 100), row(2, 500, "920888"), row(3, 350)]
        expected = tide.classify(history, date(2026, 10, 6))
        self.assertEqual(len(expected["events"]), 3)
        self.assertEqual(expected, tide.classify(list(reversed(history)) + [history[0]], date(2026, 10, 6)))
        conflict = {**history[0], "LD_AVERAGE_CHANGE": 100}
        self.assertEqual(tide.classify(history + [conflict], date(2026, 10, 6))["sample_count"], 3)

    def test_missing_stale_and_target_outcomes_excluded(self):
        history = [row(1, float("nan")), row(2, float("inf")), row(3, 500, "target"),
                   {**row(4, 800), "LISTING_DATE": "2026-08-01"}]
        result = tide.classify(history, date(2026, 10, 17), target_code="target")
        self.assertEqual(result["sample_count"], 0)

    def test_only_positive_premium_is_scaled(self):
        state = {"tier": "low"}
        for raw, expected in ((40, 30), (15, 15), (20, 20)):
            final = tide.apply({"available": True, "base_target_price": 20, "target_price": raw}, state, {})
            self.assertEqual(final["target_price"], expected)
            self.assertAlmostEqual(final["range_low"], expected * .9)
        unavailable = {"available": False}
        self.assertEqual(tide.apply(unavailable, state, {}), unavailable)
        high = tide.apply({"available": True, "base_target_price": 20, "target_price": 40}, {"tier": "high"}, {})
        self.assertAlmostEqual(high["target_price"], 42.)

    def test_ramp_does_not_switch_on_at_five(self):
        # Identical returns make the regression prediction invariant as n grows.
        for n in range(1, 7):
            final = apply([row(day, 100) for day in range(1, n + 1)], sentiment_tide_enabled=False)
            alpha = .75 * min(n / 5, 1)
            self.assertAlmostEqual(final["local_center_alpha"], alpha)
            self.assertAlmostEqual(final["target_price"], 30 * (1 - alpha) + 20 * alpha)
        original = apply([row(day, 100) for day in range(1, 5)],
                         sentiment_tide_enabled=False, local_center_smooth_warmup=False)
        self.assertEqual(original["target_price"], 30)

    def test_same_live_and_replay_recent_pool(self):
        history = [row(i, 100 + i * 35) for i in range(1, 7)]
        old = {**row(1, 900), "LISTING_DATE": "2026-01-01"}
        self.assertEqual(apply(history), apply(history + [old, row(17, 9000), row(19, 8000)]))
        # Tide still gates method-three premium when local regression is disabled.
        final = apply([row(15, 90)], local_center_overlay_enabled=False)
        self.assertEqual(final["target_price"], 25)

    def test_replay_never_uses_target_or_future_labels(self):
        params = config_loader.load_params()
        target = {**row(15, 100), "comparable_data": [{"code": "600001", "pe_ttm": 30}]}
        history = [row(i, 350 + i * 20) for i in range(1, 7)]
        predict = lambda item, pool: param_tuning._evaluate_replay_prediction(item, params, pool, param_tuning.COMPOSITE_EVALUATION_SCOPE)[0]
        before = predict(target, history)
        polluted = {**target, "AVERAGE_PRICE": 10000, "LD_AVERAGE_CHANGE": 100000}
        after = predict(polluted, history + [polluted, row(16, 9999)])
        self.assertEqual(before, after)

    def test_configuration_and_tuning_contract(self):
        params = config_loader.load_params()
        self.assertEqual(tide.factors(params), {"low": .5, "medium": .75, "high": 1.1})
        for bad in (float("nan"), -1, 2):
            with self.assertRaises(ValueError):
                tide.factors({"sentiment_tide_low_factor": bad})
        with self.assertRaises(ValueError):
            tide.factors({"sentiment_tide_low_factor": .9})
        groups = param_tuning.build_auto_tune_candidate_groups(params, {"items": [], "evaluation_scope": param_tuning.COMPOSITE_EVALUATION_SCOPE})
        windows = [v["recent_days"] for _, vs in groups for v in vs if "recent_days" in v]
        self.assertEqual(windows, [30])
        self.assertFalse(any(k.startswith("sentiment_tide_") for _, vs in groups for v in vs for k in v))

    def test_report_explains_final_gate(self):
        final = apply([row(15, 90)], local_center_overlay_enabled=False)
        lines = report_generator._build_composite_lines({"params": {},
            "method1": {"available": True, "target_price": 20}, "method2": {},
            "method3": {}, "final": final})
        self.assertIn("低档", "\n".join(lines))
        self.assertIn("25.00", lines[-2])


if __name__ == "__main__":
    unittest.main()
