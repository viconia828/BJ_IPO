from __future__ import annotations

from email.message import Message
from io import BytesIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import urllib.response

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
import bse_official_helper as bse


class ScriptedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, responses):
        super().__init__()
        self.responses = list(responses)
        self.requests = []

    def https_open(self, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected repeated network request")
        status, header_values, body = self.responses.pop(0)
        headers = Message()
        for key, value in header_values.items():
            headers[key] = value
        response = urllib.response.addinfourl(BytesIO(body), headers, request.full_url, status)
        response.msg = "OK" if status == 200 else "Found"
        return response


def notice(title, art_code, day="2026-09-14", code="920202"):
    return {
        "title": title, "art_code": art_code, "notice_date": day,
        "codes": [{"stock_code": code, "short_name": "安达股份"}],
    }


class FallbackClient(bse.BSEOfficialClient):
    def __init__(self, records):
        super().__init__(timeout=1)
        self.records = records
        self.feed_calls = 0

    def _request_payload(self, *args, **kwargs):
        raise bse.BSEOfficialError("HTTP 302 循环重定向")

    def _request_json(self, path_or_url, params=None, **kwargs):
        if path_or_url == bse.EASTMONEY_STOCK_NOTICE_DETAIL_URL:
            art_code = params["art_code"]
            return {"data": {
                "art_code": art_code, "security": [{"stock": "920202"}],
                "attach_url": f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf?1789407754000.pdf",
            }}
        if path_or_url != bse.EASTMONEY_STOCK_NOTICE_URL or params["stock_list"] != "920202":
            raise AssertionError("unexpected fallback URL or stock code")
        self.feed_calls += 1
        return {"data": {"list": self.records}}


class RedirectFallbackTests(unittest.TestCase):
    def client_with_transport(self, responses):
        client = bse.BSEOfficialClient(timeout=1)
        transport = ScriptedHTTPSHandler(responses)
        client.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(client.cookie_jar),
            bse._BSEHTTPRedirectHandler(), transport,
        )
        return client, transport

    def test_self_redirect_stops_after_one_request_and_skips_other_official_api(self):
        url = bse.BASE_URL + "/newShareController/infoResult.do"
        client, transport = self.client_with_transport([(302, {"Location": url}, b"")])
        with self.assertRaisesRegex(bse.BSEOfficialError, "HTTP 302.*循环重定向"):
            client._request_payload(url)
        with self.assertRaisesRegex(bse.BSEOfficialError, "循环重定向"):
            client._request_payload("/nqhqController/detailCompany.do")
        self.assertEqual(len(transport.requests), 1)

    def test_normal_redirect_still_works(self):
        client, transport = self.client_with_transport([
            (302, {"Location": "/moved.do"}, b""),
            (200, {}, b'{"ok":true}'),
        ])
        self.assertEqual(client._request_payload("/original.do"), {"ok": True})
        self.assertEqual(len(transport.requests), 2)

    def test_cookie_handshake_self_redirect_still_works(self):
        client, transport = self.client_with_transport([
            (302, {"Location": "/session.do", "Set-Cookie": "session=ready; Path=/"}, b""),
            (200, {}, b'{"ok":true}'),
        ])
        self.assertEqual(client._request_payload("/session.do"), {"ok": True})
        self.assertEqual(transport.requests[1].get_header("Cookie"), "session=ready")

    def test_redirect_cycle_is_bounded(self):
        client, transport = self.client_with_transport([
            (302, {"Location": "/b.do"}, b""),
            (302, {"Location": "/a.do"}, b""),
            (302, {"Location": "/b.do"}, b""),
        ])
        with self.assertRaisesRegex(bse.BSEOfficialError, "循环重定向"):
            client._request_payload("/a.do")
        self.assertLessEqual(len(transport.requests), 3)

    def test_fallback_uses_matching_full_latest_prospectus(self):
        client = FallbackClient([
            notice("安达股份:招股说明书(注册稿)", "AN202609031828965798", "2026-09-03"),
            notice("安达股份:招股说明书摘要", "AN202609151829374200", "2026-09-15"),
            notice("关于招股说明书的回复", "AN202609151829374201", "2026-09-15"),
            notice("其他公司:招股说明书", "AN202609151829374202", "2026-09-15", "920201"),
            notice("安达股份:招股说明书", "AN202609141829374202"),
            notice("安达股份:招股说明书", "bad/url", "2026-09-15"),
            {"title": "安达股份:招股说明书", "art_code": "AN202609151829374204"},
        ])
        result = client.resolve_prospectus_by_post_listing_code("920202")
        self.assertEqual(result.disclosure.full_url, "https://pdf.dfcfw.com/pdf/H2_AN202609141829374202_1.pdf?1789407754000.pdf")
        self.assertEqual(result.disclosure.source, "eastmoney")
        self.assertEqual(result.mapping.listed_company.post_listing_code, "920202")
        self.assertEqual(result.mapping.project.pre_listing_code, "")
        self.assertEqual(client.build_prospectus_filename(result), "920202_安达股份_招股说明书.pdf")

    def test_fallback_supports_announcements_and_reuses_list(self):
        client = FallbackClient([
            notice("安达股份:招股说明书", "AN202609141829374202"),
            notice("安达股份:向不特定合格投资者公开发行股票并在北京证券交易所上市发行公告", "AN202609141829374210"),
            notice("安达股份:发行结果公告", "AN202609211829374210", "2026-09-21"),
            notice("安达股份:北京证券交易所上市公告书", "AN202609241829374210", "2026-09-24"),
            notice("安达股份:关于发行公告的提示性公告", "AN202609251829374210", "2026-09-25"),
        ])
        client.resolve_prospectus_by_post_listing_code("920202")
        issue = client.resolve_issue_announcement_by_post_listing_code("920202")
        self.assertIn("H2_AN202609141829374210_1.pdf?", issue.disclosure.full_url)
        result = client.resolve_issue_result_announcement_by_post_listing_code("920202")
        self.assertEqual(result.disclosure.document_type, "发行结果公告")
        with patch.object(client, "download_disclosure_file", return_value=Path("listing.pdf")) as download:
            listing, _ = client.download_listing_announcement_from_newshare_by_post_listing_code("920202", "unused")
            self.assertEqual(listing.disclosure.document_type, "上市公告书")
            self.assertEqual(download.call_count, 1)
        self.assertEqual(client.feed_calls, 1)

    def test_empty_fallback_keeps_error_and_does_not_invent_document(self):
        client = FallbackClient([])
        with self.assertRaisesRegex(bse.BSEOfficialError, "循环重定向.*备用源未找到 920202"):
            client.resolve_prospectus_by_post_listing_code("920202")

    def test_invalid_fallback_payload_is_not_cached_as_no_documents(self):
        client = bse.BSEOfficialClient()
        with patch.object(client, "_request_json", return_value={"error": "upstream unavailable"}):
            with self.assertRaisesRegex(bse.BSEOfficialError, "结构异常"):
                client._list_stock_notice_documents("920202", "prospectus")
        self.assertNotIn("920202", client._stock_notice_cache)

    def test_pdf_content_is_still_validated(self):
        client, _ = self.client_with_transport([
            (200, {}, b"<html>unavailable</html>"),
            (200, {}, b"%PDF-1.7 truncated"),
        ])
        with patch.object(bse.time, "sleep"), patch.object(client, "_download_binary_via_curl", side_effect=bse.BSEOfficialError("unavailable")):
            with self.assertRaises(bse.BSEOfficialError):
                client._download_binary("https://pdf.dfcfw.com/pdf/test.pdf")

    def test_https_challenge_falls_back_only_for_public_pdf_host(self):
        client, transport = self.client_with_transport([
            (200, {}, b"<script>verification</script>"),
        ])
        url = "https://pdf.dfcfw.com/pdf/H2_AN202609141829374202_1.pdf?1789407754000.pdf"
        expected = b"%PDF-1.7 complete\n%%EOF\n"
        original_open = client.opener.open

        def open_request(request, timeout=0):
            if request.full_url.startswith("http:"):
                self.assertEqual(request.full_url, url.replace("https:", "http:", 1))
                headers = Message()
                headers["Content-Length"] = str(len(expected))
                return urllib.response.addinfourl(BytesIO(expected), headers, request.full_url, 200)
            return original_open(request, timeout=timeout)

        with patch.object(client.opener, "open", side_effect=open_request):
            self.assertEqual(client._download_binary(url), expected)
        self.assertEqual(len(transport.requests), 1)
        for forbidden in (
            "https://www.bse.cn/disclosure/file.pdf",
            "https://pdf.dfcfw.com.example.com/pdf/H2_AN1_1.pdf",
            "https://user:secret@pdf.dfcfw.com/pdf/H2_AN1_1.pdf",
            "https://pdf.dfcfw.com/private/file.pdf",
        ):
            self.assertEqual(client._eastmoney_public_pdf_http_url(forbidden), "")

    def test_attachment_identity_must_match(self):
        client = bse.BSEOfficialClient()
        disclosure = bse.DisclosureFile(
            "安达股份:招股说明书", "2026-09-14", "", "", "NEW", "", "prospectus", ".pdf",
            "https://data.eastmoney.com/notices/detail/920202/AN202609141829374202.html", "eastmoney",
        )
        for data in (
            {"art_code": "AN999"},
            {"art_code": "AN202609141829374202", "security": [{"stock": "920201"}]},
            {"art_code": "AN202609141829374202", "security": [{"stock": "920202"}], "attach_url": "https://example.com/file.pdf"},
        ):
            with patch.object(client, "_request_json", return_value={"data": data}):
                with self.assertRaises(bse.BSEOfficialError):
                    client._resolve_stock_notice_attachment("920202", disclosure)


if __name__ == "__main__":
    unittest.main()
