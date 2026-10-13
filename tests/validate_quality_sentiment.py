from __future__ import annotations
from copy import deepcopy
from datetime import date, timedelta
import json
import hashlib
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT / "tests"))
import quality_sentiment as qs
import quality_sentiment_data as data
import valuation_engine
import report_generator
from validate_report_dual_valuation_ranges import _payload

PARAMS = {"price_range_width": .1, "bse_discount_factor": .75, "quality_sentiment_mode": "shadow"}


def issuer(code, listing, fundamental=10, actual=30):
    return {"SECURITY_CODE": code, "SECURITY_NAME_ABBR": code, "LISTING_DATE": str(listing),
            "ISSUE_PRICE": 10, "AFTER_ISSUE_PE": 10, "AVERAGE_PRICE": actual, "CLOSE_PRICE": actual,
            "industry_secondary": "汽车零部件", "fundamental_review": {
                "center": fundamental, "low": fundamental * .9, "high": fundamental * 1.1,
                "available_date": str(listing - timedelta(days=1)), "source": "test_prelisting_evidence",
                "excludes_ipo_sentiment": True}}


class QualitySentimentTests(unittest.TestCase):
    def setUp(self):
        self.history = [issuer(f"910{i:03}", date(2026, 1, 1) + timedelta(days=i * 3)) for i in range(12)]
        self.target = issuer("920999", date(2026, 2, 15))

    def evaluate(self, target=None, history=None):
        return qs.evaluate(target or self.target, self.history if history is None else history, PARAMS, as_of="2026-02-15")

    def test_future_target_and_same_day_outcomes_do_not_leak(self):
        baseline = self.evaluate()
        future = issuer("930001", date(2026, 2, 16), actual=99999)
        same_day = issuer("930002", date(2026, 2, 15), actual=99999)
        target = {**self.target, "AVERAGE_PRICE": 999999}
        other = self.evaluate(target, self.history + [target, future, same_day])
        self.assertEqual(baseline, other)

    def test_company_basis_changes_price_without_raw_return_ranking(self):
        history = deepcopy(self.history)
        history[-2] = issuer("910010", date(2026, 1, 31), fundamental=10, actual=37.1)
        history[-1] = issuer("910011", date(2026, 2, 3), fundamental=50, actual=57.1)
        result = self.evaluate(history=history)
        anda, yute = result["recent_details"][-2:]
        self.assertGreater(yute["actual"], anda["actual"])
        self.assertLess(yute["premium_pct"], anda["premium_pct"])
        double = deepcopy(self.target)
        for key in ("center", "low", "high"):
            double["fundamental_review"][key] *= 2
        self.assertGreater(self.evaluate(double)["sentiment"]["center"], self.evaluate()["sentiment"]["center"])

    def test_positive_premium_can_miss_archived_expectation(self):
        history = deepcopy(self.history)
        history[-1]["sentiment_snapshot"] = {"origin": "archived", "source": "immutable.json", "as_of": "2026-02-02", "center": 50}
        detail = self.evaluate(history=history)["recent_details"][-1]
        self.assertGreater(detail["premium_pct"], 0)
        self.assertLess(detail["expectation_error"], 0)
        self.assertEqual(detail["expectation_origin"], "archived")

    def test_post_dates_filtered_individually_and_aliases_masked(self):
        item = {**self.history[-1], "NEXT_TRADE_DATE": "2026-02-04", "NEXT_DAY_CLOSE": 24,
                "LD_AVERAGE_CHANGE": 200,
                "THIRD_TRADE_DATE": "2026-02-16", "THIRD_DAY_CLOSE": 3,
                "NEXT_DAY_FROM_LISTING_CLOSE_PCT": -20, "THIRD_DAY_FROM_LISTING_CLOSE_PCT": -90,
                "POST_LISTING_PROFIT_EFFECT_PCT": -55, "D3_CLOSE_CHANGE": -99}
        masked = qs.mask_post_listing(item, "2026-02-15")
        self.assertAlmostEqual(masked["POST_LISTING_PROFIT_EFFECT_PCT"], -20)
        self.assertIsNone(masked["THIRD_DAY_CLOSE"])
        self.assertIsNone(masked["D3_CLOSE_CHANGE"])
        with_date = valuation_engine.method3_recent_sentiment(10, [item], {"recent_days": 90}, "T", "2026-02-15")
        self.assertAlmostEqual(with_date["post_listing_factor_pct"], -20)
        item.pop("NEXT_TRADE_DATE")
        self.assertIsNone(qs.mask_post_listing(item, "2026-02-15")["POST_LISTING_PROFIT_EFFECT_PCT"])
        result = valuation_engine.method3_recent_sentiment(10, [item], {"recent_days": 90}, "T", "2026-02-15")
        self.assertIsNone(result["post_listing_factor_pct"])

    def test_anchor_requires_dated_distinct_peers_and_excludes_legacy_factors(self):
        target = {"ISSUE_PRICE": 10, "AFTER_ISSUE_PE": 10, "comparable_data": [
            {"code": "A", "pe_ttm": 20, "trade_date": "20260210"},
            {"code": "A", "pe_ttm": 20, "trade_date": "20260210"},
            {"code": "B", "pe_ttm": 9999, "trade_date": "20260215"}]}
        self.assertFalse(qs.build_anchor(target, "2026-02-15", PARAMS)["available"])
        target["comparable_data"][-1] = {"code": "B", "pe_ttm": 20, "trade_date": "20260210"}
        anchor = qs.build_anchor(target, "2026-02-15", {**PARAMS, "small_cap_premium": 99, "sentiment_premium_cap_pct": 999})
        self.assertEqual(anchor["center"], 15)

    def test_missing_anchor_and_target_failure_do_not_use_legacy_price(self):
        target = {**self.target, "fundamental_review": {}}
        result = self.evaluate(target)
        self.assertFalse(result["available"])
        self.assertEqual(result["state"]["code"], "uncertain")
        output = qs.attach({"available": True, "target_price": 999}, {**result, "mode": "active"})
        self.assertFalse(output["available"])
        self.assertIsNone(output["target_price"])

    def test_same_day_order_does_not_change_forecasts(self):
        history = deepcopy(self.history)
        history[-1]["LISTING_DATE"] = history[-2]["LISTING_DATE"]
        self.assertEqual(self.evaluate(history=history), self.evaluate(history=list(reversed(history))))

    def test_application_fallback_without_trading_is_not_completed_history(self):
        unlisted = {**issuer("930009", date(2026, 2, 14)), "AVERAGE_PRICE": None, "CLOSE_PRICE": None,
                    "listing_date_source": "apply_date_fallback"}
        self.assertEqual(self.evaluate(), self.evaluate(history=self.history + [unlisted]))

    def test_no_new_observations_do_not_create_cooling(self):
        result = self.evaluate()
        later = qs.evaluate(self.target, self.history, PARAMS, as_of="2026-03-01")
        self.assertAlmostEqual(result["sentiment"]["center"], later["sentiment"]["center"])
        self.assertEqual(later["state"]["code"], "stale")

    def test_weak_aftermarket_is_visible_with_missing_fundamentals(self):
        history = deepcopy(self.history)
        for item in history[-3:]:
            item["fundamental_review"] = {}
            item["NEXT_TRADE_DATE"] = str(qs.day(item["LISTING_DATE"]) + timedelta(days=1))
            item["NEXT_DAY_CLOSE"] = 20
        result = self.evaluate(history=history)
        self.assertEqual(result["state"]["code"], "weak_aftermarket")
        self.assertNotEqual(result["state"]["code"], "cooling_confirmed")

    def test_report_and_shadow_price_ownership(self):
        result = self.evaluate()
        payload = _payload()
        old = deepcopy(payload["final"])
        payload["final"] = qs.attach(old, result)
        self.assertEqual(payload["final"]["target_price"], old["target_price"])
        markdown = report_generator.build_report_markdown(payload)
        html = report_generator._build_report_html(report_generator._prepare_report_context(payload))
        for content in (markdown, html):
            self.assertIn("个股资质与情绪观察", content)
            self.assertIn("历史模拟", content)
            self.assertIn("旧版三方法中枢，含情绪", content)
            observation = content.split("个股资质与情绪观察", 1)[1].split("五、关注提示", 1)[0]
            self.assertIn("PE基础区间", observation)
            self.assertNotIn("数据截断", observation)
            self.assertNotIn("预期来源", observation)
            self.assertNotIn("<ul>", observation)
            self.assertNotIn("\n- ", observation)
            headings = ["综合估值", "个股资质与情绪观察", "五、关注提示", "近期北交所新股首日表现一览"]
            positions = [content.index(heading) for heading in headings]
            self.assertEqual(positions, sorted(positions))
        overview = report_generator.build_report_overview_text(payload)
        self.assertIn("正式综合区间（元） 20.25 - 24.75", overview)
        for removed in ("原综合参考区间", "旧版情绪区间", "情绪观察"):
            self.assertNotIn(removed, overview)
        active = {**result, "mode": "active"}
        payload["final"] = qs.attach(old, active)
        ranges = report_generator._build_user_valuation_ranges(payload["final"], 10, .1)
        self.assertEqual(ranges["baseline_center"], result["anchor"]["center"])
        self.assertEqual(payload["final"]["target_price"], result["sentiment"]["center"])

    def test_forward_snapshots_are_immutable_and_not_backfilled(self):
        tmp_root = ROOT / "tests/_tmp"
        tmp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_root) as temp:
            root = Path(temp)
            target = {**self.target, "LISTING_DATE": "2099-01-01"}
            result = self.evaluate()
            one = data.save_forward_snapshot(target, result, root)
            before = Path(one["path"]).read_bytes()
            self.assertEqual(one["sha256"], hashlib.sha256(before).hexdigest())
            two = data.save_forward_snapshot(target, result, root)
            self.assertNotEqual(one["path"], two["path"])
            self.assertEqual(before, Path(one["path"]).read_bytes())
            self.assertFalse(data.save_forward_snapshot(self.target, result, root)["saved"])


if __name__ == "__main__":
    unittest.main()
