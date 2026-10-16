from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime
import http.client
import io
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "tools"))
sys.path.insert(0, str(ROOT_DIR / "code"))

import add_new_ipo_intraday_cache as cli
import cache_listing_day_intraday as cache
import tushare_helper
from local_file_db import LocalFileDB


class IntradayNetworkLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_parent = ROOT_DIR / "data" / "temp_validation"
        temp_parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="intraday_network_", dir=temp_parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.params = {
            "tushare_cache_root": str(self.root / "cache"),
            "tushare_token_env": "INTRADAY_TEST_TOKEN",
            "tushare_daily_request_quota": 9999,
        }
        token_patch = patch.dict(os.environ, {"INTRADAY_TEST_TOKEN": "test-token"})
        token_patch.start()
        self.addCleanup(token_patch.stop)
        self.today = date.today().isoformat()
        self.item = {"SECURITY_CODE": "920196", "SECURITY_NAME_ABBR": "南方乳业", "LISTING_DATE": self.today}

    def test_transport_errors_during_open_and_read_are_recorded(self) -> None:
        settings = tushare_helper._build_settings(self.params)
        db = LocalFileDB(settings["cache_root"])
        failures = [
            TimeoutError("The read operation timed out"),
            ssl.SSLError("fixture bad record mac"),
            ConnectionResetError("fixture reset"),
            http.client.IncompleteRead(b"partial", 100),
        ]
        for phase in ("open", "read"):
            for failure in failures:
                with self.subTest(phase=phase, error=type(failure).__name__):
                    with patch.object(tushare_helper.urllib.request, "urlopen") as urlopen:
                        if phase == "open":
                            urlopen.side_effect = failure
                        else:
                            urlopen.return_value.__enter__.return_value.read.side_effect = failure
                        rows, reason = tushare_helper._call_tushare_api(
                            "stk_mins", {"ts_code": "920196.BJ"}, "close", settings, db,
                        )
                    self.assertEqual(rows, [])
                    self.assertIn(type(failure).__name__, reason)
                    event = db.load_request_log()["events"][-1]
                    self.assertEqual(event["request_kind"], "stk_mins")
                    self.assertEqual(event["error_code"], "network_error")
                    self.assertEqual(event["error_type"], type(failure).__name__)
                    self.assertNotIn("test-token", json.dumps(event))

    def test_api_failure_message_is_recorded(self) -> None:
        settings = tushare_helper._build_settings(self.params)
        db = LocalFileDB(settings["cache_root"])
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"code": 40203, "msg": "fixture permission denied", "data": None}
        ).encode()
        with patch.object(tushare_helper.urllib.request, "urlopen", return_value=response):
            rows, reason = tushare_helper._call_tushare_api(
                "rt_min_daily", {"ts_code": "920196.BJ"}, "close", settings, db,
            )
        self.assertEqual(rows, [])
        self.assertIn("fixture permission denied", reason)
        self.assertEqual(db.load_request_log()["events"][-1]["error_message"], "fixture permission denied")

    def test_network_error_reaches_eastmoney_fallback(self) -> None:
        events = []
        rows = [{
            "DateTime": f"{self.today.replace('-', '/')} 15:00", "open": 10, "high": 10,
            "low": 10, "close": 10, "volume": 100, "amount": 1000,
        }]
        with patch.object(tushare_helper.urllib.request, "urlopen", side_effect=TimeoutError("read timed out")), \
             patch.object(cache.data_fetcher, "fetch_intraday_trends", return_value=rows) as fallback:
            result = cache._cache_single_candidate(
                self.item, self.root / "csv", self.params,
                current_datetime=datetime.combine(date.today(), cache.INTRADAY_CACHE_READY_TIME),
                progress_callback=lambda event, payload: events.append((event, payload)),
            )
        fallback.assert_called_once_with("920196", trade_date=self.today)
        self.assertEqual(result["status"], "cached")
        self.assertEqual(result["source_api"], "eastmoney_trends2")
        self.assertTrue(Path(result["path"]).exists())
        self.assertIn("TimeoutError", events[0][1]["reason"])

    def test_all_sources_failed_keeps_retry_marker(self) -> None:
        self.params["manual_intraday_file_root"] = str(self.root)
        with patch.object(cache, "_scan_latest_candidates", return_value=[self.item]), \
             patch.object(tushare_helper.urllib.request, "urlopen", side_effect=ssl.SSLError("fixture EOF")), \
             patch.object(cache.data_fetcher, "fetch_intraday_trends", side_effect=cache.data_fetcher.DataFetcherError("fixture offline")):
            summary = cache.run_latest_missing_cache_job(
                output_dir=self.root / "csv", params=self.params,
                current_datetime=datetime.combine(date.today(), cache.INTRADAY_CACHE_READY_TIME),
            )
        self.assertEqual(summary["error_count"], 1)
        self.assertEqual(summary["pending_deferred_after"], ["920196"])
        self.assertEqual(cache._load_deferred_codes(self.params), ["920196"])
        self.assertFalse((self.root / "csv" / "920196.csv").exists())

    def test_cli_preserves_progress_and_traceback_after_failure(self) -> None:
        def fail_job(**kwargs):
            kwargs["progress_callback"]("checking", {"code": "920196", "name": "南方乳业"})
            raise ValueError("fixture unexpected failure")

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(cli, "DEFAULT_LOG_DIR", self.root / "logs"), \
             patch.object(cli, "_build_runtime_params", return_value=self.params), \
             patch.object(cli.cache_listing_day_intraday, "run_latest_missing_cache_job", side_effect=fail_job), \
             patch.object(sys, "argv", ["add_new_ipo_intraday_cache.py"]), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = cli.main()
        self.assertEqual(exit_code, 1)
        logs = list((self.root / "logs").glob("refresh_*.log"))
        self.assertEqual(len(logs), 1)
        content = logs[0].read_text(encoding="utf-8")
        self.assertIn("920196", content)
        self.assertIn("南方乳业", content)
        self.assertIn("Traceback (most recent call last)", content)
        self.assertIn("ValueError: fixture unexpected failure", content)
        self.assertIn(str(logs[0]), stderr.getvalue())

    def test_cli_records_summary_and_preserves_exit_status(self) -> None:
        for index, (summary, expected) in enumerate([
            ({"cached_count": 1, "error_count": 0}, 0),
            ({"cached_count": 0, "error_count": 1}, 1),
        ]):
            log_dir = self.root / f"logs_{index}"
            with self.subTest(summary=summary), \
                 patch.object(cli, "DEFAULT_LOG_DIR", log_dir), \
                 patch.object(cli, "_build_runtime_params", return_value=self.params), \
                 patch.object(cli.cache_listing_day_intraday, "run_latest_missing_cache_job", return_value=summary), \
                 patch.object(sys, "argv", ["add_new_ipo_intraday_cache.py"]), redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(), expected)
            self.assertIn("summary ", next(log_dir.glob("*.log")).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
