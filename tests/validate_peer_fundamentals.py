from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
import peer_fundamentals as pf
import ifind_helper as ih
import comparable_data_helper as comparable
import report_source_helper
sys.path.insert(0, str(ROOT / "tools"))
from collect_peer_fundamentals import compare_independent


def mcp(answer, params):
    return {"retrieved_date": "2026-10-12", "result": {"content": [{"type": "text", "text": json.dumps(
        {"code": 1, "data": {"answer": answer, "indicators_params": params}})}]}}


def record(period, revenue, profit, **overrides):
    return {"code": "300272.SZ", "report_period": period, "published_at": "2026-08-28",
        "retrieved_at": "2026-10-12", "statement_scope": "consolidated", "period_basis": "YTD",
        "values": {"revenue": {"value": revenue}, "net_profit_parent": {"value": profit}}, **overrides}


class PeerFundamentalsTests(unittest.TestCase):
    def test_units_and_zero(self):
        self.assertEqual(pf.parse_number("1.2345亿")["value"], 123450000)
        self.assertEqual(pf.parse_number("-42.35万")["value"], -423500)
        self.assertEqual(pf.parse_number("12.5", "万元")["value"], 125000)
        self.assertEqual(pf.parse_number("0")["value"], 0)
        self.assertIsNone(pf.parse_number("--"))
        self.assertIsNone(pf.parse_number("NaN"))

    def test_actual_publication_period_and_statement_scope(self):
        answer = "|证券代码|定期报告实际披露日期|定期报告预计披露日期|\n|---|---|---|\n|300272.SZ|20250826|20260429|\n\n|证券代码|日期|营业收入（单位：元）|单季度.营业收入（单位：元）|\n|---|---|---|---|\n|300272.SZ|20260630|1.2亿|0.7亿|"
        parsed = pf.parse_mcp(mcp(answer, {"定期报告实际披露日期": {"报告期": "20250630"},
            "营业收入": {"报表类型": "合并报表"}}), "test")
        self.assertEqual(parsed["publication_dates"][0]["report_period"], "2025-06-30")
        self.assertEqual(parsed["financials"][0]["values"]["revenue"]["value"], 120000000)
        merged = pf.merge_records(parsed["financials"], parsed["publication_dates"])
        self.assertIsNone(merged[0]["published_at"])
        rejected = pf.parse_mcp(mcp(answer, {"营业收入": {"报表类型": "母公司报表"}}), "test")
        self.assertFalse(rejected["financials"])
        self.assertFalse(rejected["publication_dates"])

    def test_conflicting_parameter_period_rejected(self):
        answer = "|证券代码|日期|营业收入（单位：元）|\n|---|---|---|\n|300272.SZ|20260630|1亿|"
        parsed = pf.parse_mcp(mcp(answer, {"营业收入": {"报表类型": "合并报表", "报告期": "20251231"}}), "test")
        self.assertFalse(parsed["financials"])

    def test_api_echo_checked_before_assignment(self):
        envelope = {"http_status": 200, "request": {"codes": "300272.SZ", "indipara": [
            {"indicator": "ths_revenue_stock", "indiparams": ["20251231", "100"]}]}, "response": {
            "errorcode": 0, "inputParams": {"params": [{"function": "ths_revenue_stock", "params": [
                {"name": "FDREPORT", "value": "20251231"}, {"name": "FL", "value": "100"}]}]},
            "tables": [{"thscode": "300272.SZ", "table": {"ths_revenue_stock": [123.45]}}]}}
        self.assertEqual(pf.parse_api(envelope, "test")[0]["values"]["revenue"]["value"], 123.45)
        envelope["response"]["inputParams"]["params"][0]["params"][0]["value"] = "20260630"
        self.assertFalse(pf.parse_api(envelope, "test"))

    def test_rounding_tolerance_and_conflict(self):
        a = {**record("2025-12-31", 0, 0), "source": "ifind_mcp", "evidence": "mcp", "values": {
            "revenue": {**pf.parse_number("1.2345亿"), "unit": "CNY"}}}
        b = {**a, "source": "ifind_api", "evidence": "api", "values": {
            "revenue": {**pf.parse_number(123452345.67), "unit": "CNY"}}}
        merged = pf.merge_records([a, b], [])[0]
        self.assertFalse(merged["values"]["revenue"]["conflict"])
        self.assertEqual(merged["values"]["revenue"]["source"], "ifind_api")
        self.assertFalse(merged["channel_agreement_is_independent_validation"])
        b["values"]["revenue"]["value"] = 200000000
        self.assertTrue(pf.merge_records([a, b], [])[0]["values"]["revenue"]["conflict"])

    def test_ttm_and_future_collection_date(self):
        rows = [record("2025-12-31", 1000, 100), record("2026-06-30", 600, 60), record("2025-06-30", 400, 40)]
        ttm = pf.derive_ttm(rows, "2026-10-13")
        self.assertEqual(ttm["values"]["revenue"], 1200)
        self.assertEqual(ttm["values"]["net_profit_parent"], 120)
        self.assertEqual(ttm["growth_yoy_pct"]["net_profit_parent"], 50)
        self.assertFalse(pf.derive_ttm(rows, "2026-10-12")["available"])
        self.assertFalse(pf.visible_record(rows[0], "2026-10-13", historical=True))
        rows[-1]["published_at"] = None
        self.assertFalse(pf.derive_ttm(rows, "2026-10-13")["available"])

    def test_restatement_and_conflict_cannot_silently_enter_ttm(self):
        rows = [record("2025-12-31", 1000, 100), record("2026-06-30", 600, 60), record("2025-06-30", 400, 40)]
        rows[1]["values"]["reported_profit_yoy_pct"] = {"value": 25}
        result = pf.derive_ttm(rows, "2026-10-13")
        self.assertFalse(result["available"])
        self.assertEqual(result["basis_warnings"][0]["calculated_yoy_pct"], 50)
        del rows[1]["values"]["reported_profit_yoy_pct"]
        rows[1]["values"]["net_profit_parent"]["conflict"] = True
        self.assertNotIn("net_profit_parent", pf.derive_ttm(rows, "2026-10-13")["values"])

    def test_auth_redaction_and_no_redirect(self):
        value = {"access_token": "secret", "nested": {"note": "server echoed secret", "refresh_token": "refresh"}}
        self.assertNotIn("secret", json.dumps(ih.redact(value, ["secret", "refresh"])))
        self.assertIsNone(ih.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com"))

    def test_cache_and_explicit_refresh_keep_old_evidence(self):
        class FakeClient:
            calls = 0
            def basic_data(self, body):
                self.calls += 1
                return {"http_status": 200, "response": {"errorcode": 0, "tables": []}}
        client = FakeClient()
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            folder = Path(directory)
            first = ih.fetch_financials(["300272.SZ"], ["20251231"], folder, client=client)
            before = Path(first[0]["evidence_path"]).read_bytes()
            second = ih.fetch_financials(["300272.SZ"], ["20251231"], folder, client=client)
            self.assertTrue(second[0]["cache_hit"])
            self.assertEqual(client.calls, 1)
            ih.fetch_financials(["300272.SZ"], ["20251231"], folder, client=client, refresh=True)
            self.assertEqual(len(list(folder.glob("*.json"))), 2)
            self.assertEqual(Path(first[0]["evidence_path"]).read_bytes(), before)

    def test_attach_preserves_prices_and_rejects_future_profiles(self):
        original = [{"code": "300272.SZ", "pe_ttm": 123, "close": 6}]
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            root = Path(directory)
            folder = root / "data/peer_fundamentals"
            folder.mkdir(parents=True)
            (folder / "catalog.json").write_text(json.dumps({"companies": {"300272.SZ": {
                "profiles": [{"business": "water", "retrieved_at": "2026-10-12"}],
                "financials": [record("2025-12-31", 1000, 100)]}}}), "utf-8")
            old, summary = pf.attach_cached_fundamentals(original, root, "2026-10-09")
            self.assertEqual(old, original)
            self.assertFalse(summary["available"])
            new, summary = pf.attach_cached_fundamentals(original, root, "2026-10-13")
            self.assertEqual(new[0]["pe_ttm"], 123)
            self.assertEqual(original, [{"code": "300272.SZ", "pe_ttm": 123, "close": 6}])
            self.assertEqual(summary["attached"], 1)

    def test_provider_integration_preserves_valuation_values(self):
        original = {"items": [{"code": "300272.SZ", "pe_ttm": 123, "close": 6}], "summary": {"provider": "tushare"}}
        with patch.object(comparable.tushare_helper, "get_comparable_valuations", return_value=deepcopy(original)):
            result = comparable.get_comparable_valuations(["300272.SZ", "872643.NQ"], {"comparable_data_source": "tushare", "fundamentals_as_of": "2026-10-13"})
        self.assertEqual(result["items"][0]["pe_ttm"], 123)
        self.assertEqual(result["items"][0]["close"], 6)
        self.assertEqual([c["code"] for c in result["fundamental_candidates"]], ["300272.SZ", "872643.NQ"])
        self.assertEqual(result["summary"]["fundamentals"]["missing_quote_codes"], ["872643.NQ"])
        text = report_source_helper.build_comparable_source_text({"comparable_summary": result["summary"]})
        self.assertIn("未用于定价", text)

    def test_independent_date_difference_and_financial_conflict(self):
        financial = record("2025-12-31", 1000, 100)
        for v in financial["values"].values():
            v.update({"rounding_tolerance": .005, "evidence": "ifind", "conflict": False})
        financial["conflicts"] = []
        checks = [{"code": "300272.SZ", "period": "20251231", "evidence": "tushare",
                   "row": {"f_ann_date": "20261014", "revenue": 1100, "n_income_attr_p": 100}}]
        compared = compare_independent([financial], checks)
        self.assertEqual(sum(not c["consistent"] for c in compared), 1)
        self.assertTrue(financial["values"]["revenue"]["conflict"])
        self.assertTrue(financial["publication_date_needs_review"])
        self.assertFalse(pf.visible_record(financial, "2026-10-13"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
