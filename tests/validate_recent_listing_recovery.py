from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
import ipo_data_helper
import report_generator
import report_source_helper
import tushare_ipo_helper as ts
import valuation_engine


class RecentListingRecoveryTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "tests/_tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tests/_tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "首日分时走势").mkdir()
        (self.root / "data/offline_tuning").mkdir(parents=True)
        self.rows = [
            {"SECURITY_CODE": "930001", "SECURITY_NAME_ABBR": "已上市甲", "ISSUE_PRICE": 10,
             "APPLY_DATE": "2026-09-23", "LISTING_DATE": "2026-10-15", "AVERAGE_PRICE": 999,
             "ONLINE_ISSUE_NUM": 1000000, "TOP_APPLY_MARKETCAP": 500, "old_shares": 0},
            {"SECURITY_CODE": "930002", "SECURITY_NAME_ABBR": "已上市乙", "ISSUE_PRICE": 10,
             "APPLY_DATE": "2026-09-28", "LISTING_DATE": "2026-09-28", "listing_date_source": "apply_date_fallback"},
            {"SECURITY_CODE": "930003", "SECURITY_NAME_ABBR": "未上市", "ISSUE_PRICE": 10},
        ]
        (self.root / "data/offline_tuning/replay_dataset.json").write_text(
            json.dumps({"items": self.rows}), encoding="utf-8")
        self.write_tape("930001", "2026-10-15")
        self.write_tape("930002", "2026-10-16")

    def write_tape(self, code, day, end="15:00"):
        (self.root / "首日分时走势" / f"{code}.csv").write_text(
            "DateTime,open,high,low,close,volume,amount\n"
            f"{day} 09:30,20,20,20,20,100,2000\n"
            f"{day} {end},20,20,20,20,100,2000\n", encoding="utf-8")

    def recover(self, records, **kwargs):
        return ipo_data_helper.supplement_local_recent_listings(
            records, 30, root=self.root, as_of_date=date(2026, 10, 17), **kwargs)

    def test_missing_provider_rows_join_report_and_sentiment_from_completed_tapes(self):
        recovered, summary = self.recover([], target_code="930009", target_apply_date="2026-10-20")
        self.assertEqual({r["SECURITY_CODE"] for r in recovered}, {"930001", "930002"})
        self.assertEqual(set(summary["appended_codes"]), {"930001", "930002"})
        self.assertTrue(all(r["AVERAGE_PRICE"] == 20 and r["LD_AVERAGE_CHANGE"] == 100 for r in recovered))
        self.assertEqual(recovered[0]["TOP_APPLY_MARKETCAP"], 500)
        self.assertEqual(recovered[0]["ONLINE_ISSUE_NUM"], 1000000)
        self.assertEqual(recovered[0]["old_shares"], 0)
        displayed = report_generator._filter_recent_items_by_days(recovered, 30, "2026-10-17")
        self.assertEqual([r["SECURITY_CODE"] for r in displayed], ["930002", "930001"])
        sentiment = valuation_engine.method3_recent_sentiment(10, recovered, {"recent_days": 30},
                                                              "930009", "2026-10-20")
        self.assertEqual(set(sentiment["sample_codes"]), {"930001", "930002"})
        self.assertTrue(all(item.get("sha256") for item in summary["audit"]))
        source = report_source_helper.build_ipo_source_text({"ipo_data_summary": {"local_listing_overlay": summary}})
        self.assertIn("本地完整首日分时（补充2只近期样本）", source)

    def test_placeholder_replaced_existing_genuine_prices_preserved_no_duplicates(self):
        records = [deepcopy(self.rows[1]), {**self.rows[0], "AVERAGE_PRICE": 21, "CLOSE_PRICE": 22}]
        records[0]["LISTING_DATE_SOURCE"] = "apply_date_fallback"
        original = deepcopy(records)
        recovered, summary = self.recover(records)
        self.assertEqual(records, original)
        self.assertEqual(len(recovered), 2)
        self.assertEqual(recovered[0]["LISTING_DATE"], "2026-10-16")
        self.assertEqual(recovered[0]["LISTING_DATE_SOURCE"], "dated_listing_intraday_csv")
        self.assertEqual(recovered[0]["listing_date_source"], "dated_listing_intraday_csv")
        self.assertEqual(recovered[1]["AVERAGE_PRICE"], 21)
        self.assertEqual(recovered[1]["CLOSE_PRICE"], 22)
        self.assertEqual(summary["appended_codes"], [])

    def test_future_target_same_day_incomplete_and_old_tapes_are_excluded(self):
        rows, _ = self.recover([], target_apply_date="2026-10-16")
        self.assertEqual([r["SECURITY_CODE"] for r in rows], ["930001"])
        rows, _ = self.recover([], target_code="930001", target_apply_date="2026-10-16")
        self.assertEqual(rows, [])
        self.write_tape("930001", "2026-09-01")
        self.write_tape("930002", "2026-10-18")
        self.write_tape("930003", "2026-10-16", end="11:30")
        self.assertEqual(self.recover([])[0], [])

    def test_conflicting_real_date_is_audited_not_replaced(self):
        row = {**self.rows[0], "LISTING_DATE": "2026-10-14"}
        recovered, summary = self.recover([row], target_code="930002")
        self.assertEqual(recovered, [row])
        self.assertEqual(summary["audit"][0]["reason"], "listing_date_conflict")

    def test_pending_calendar_refresh_reaches_subscription_before_short_backfill(self):
        db = ts.LocalFileDB(self.root / "tushare")
        snapshot = {"start_date": "20260901", "end_date": "20261016",
                    "updated_at": (datetime.now() - timedelta(days=2)).isoformat(),
                    "rows": [{"ts_code": "930001.BJ", "ipo_date": "20260923", "issue_date": "", "price": 10}]}
        db.save_variable_record(ts.NEW_SHARE_RANGE_CACHE_CODE, {ts.NEW_SHARE_RANGE_FIELD_KEY: snapshot},
                                trade_date="20261016", source="fixture")
        settings = {"variable_ttl": timedelta(days=1), "recent_trade_days": 12}
        with patch.object(ts, "_fetch_new_share_range_api", return_value=[]) as fetch:
            ts._get_recent_new_share_rows("20260907", "20261017", settings, db, {})
        self.assertEqual(fetch.call_args.args[:2], ("20260923", "20261017"))


if __name__ == "__main__":
    unittest.main()
