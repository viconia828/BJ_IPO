from __future__ import annotations

from pathlib import Path
import shutil
import sys
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import bse_official_helper


TEMP_DIR = ROOT_DIR / "tests" / "_tmp" / "bse_official_helper"


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


class FakeBSEOfficialClient(bse_official_helper.BSEOfficialClient):
    def __init__(self, official_listing_available: bool = True, status_callback=None) -> None:
        super().__init__(timeout=1, status_callback=status_callback)
        self.official_listing_available = official_listing_available
        self.download_requests: list[tuple[str, str | None]] = []

    def _request_payload(
        self,
        path_or_url: str,
        params: dict[str, object] | None = None,
        referer: str | None = None,
        warmup_urls: tuple[str, ...] | list[str] = (),
        headers: dict[str, str] | None = None,
    ) -> object:
        _ = (referer, warmup_urls, headers)
        if path_or_url == "/nqhqController/detailCompany.do":
            return {
                "baseinfo": {
                    "stockCode": "920177",
                    "shortname": "恒道科技",
                    "name": "浙江恒道科技股份有限公司",
                    "listingDate": "20260416",
                }
            }

        if path_or_url == "/projectNewsController/infoResult.do":
            keyword = str((params or {}).get("keyword") or "")
            if keyword != "浙江恒道科技股份有限公司":
                raise AssertionError(f"unexpected keyword: {keyword}")
            return {
                "content": [
                    {
                        "id": 615,
                        "stockCode": "874202",
                        "stockName": "恒道科技",
                        "companyName": "浙江恒道科技股份有限公司",
                        "updateDate": "2026-03-20",
                    },
                    {
                        "id": 618,
                        "stockCode": "874202",
                        "stockName": "恒道科技",
                        "companyName": "浙江恒道科技股份有限公司",
                        "updateDate": "2026-03-30",
                    },
                    {
                        "id": 700,
                        "stockCode": "874299",
                        "stockName": "恒道科技",
                        "companyName": "其他公司",
                        "updateDate": "2026-04-10",
                    },
                ]
            }

        if path_or_url == "/projectNewsController/infoDetailResult.do":
            project_id = int((params or {}).get("id") or 0)
            if project_id != 618:
                raise AssertionError(f"unexpected project id: {project_id}")
            return {
                "projectNews": {
                    "id": 618,
                    "stockCode": "874202",
                },
                "xxgkInfo": {
                    "GPFXSMS": {
                        "SBG": [
                            {
                                "fileTitle": "恒道科技:招股说明书(申报稿)",
                                "publishDate": "2025-06-13",
                                "destFilePath": "/disclosure/2025/2025-06-13/1749807495_897032.pdf",
                            }
                        ],
                        "SYG": [
                            {
                                "fileTitle": "恒道科技:招股说明书(上会稿)",
                                "publishDate": "2026-01-21",
                                "destFilePath": "/disclosure/2026/2026-01-21/1768996572_291951.pdf",
                            }
                        ],
                        "BHG": [
                            {
                                "fileTitle": "恒道科技:招股说明书(注册稿)",
                                "publishDate": "2026-03-30",
                                "destFilePath": "/disclosure/2026/2026-03-30/1774855822_070697.pdf",
                            }
                        ],
                    }
                },
            }

        if path_or_url == "/disclosureInfoController/companyAnnouncement.do":
            if not self.official_listing_available:
                raise bse_official_helper.BSEOfficialError("mock official listing unavailable")
            return [
                {
                    "listInfo": {
                        "content": [
                            {
                                "disclosureTitle": "恒道科技：",
                                "disclosurePostTitle": "北京证券交易所上市公告书",
                                "destFilePath": "/disclosure/2026/2026-04-02/1788200000_000001.pdf",
                                "publishDate": "2026-04-02",
                                "fileExt": "pdf",
                            }
                        ]
                    }
                }
            ]

        if path_or_url == "/newShareController/infoResult.do":
            return [{"listInfo": {"content": [], "totalElements": 0}}]

        raise AssertionError(f"unexpected path: {path_or_url}")

    def _request_json(
        self,
        path_or_url: str,
        params: dict[str, Any] | None = None,
        referer: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        _ = (referer, headers)
        if path_or_url != bse_official_helper.EASTMONEY_NOTICE_LIST_URL:
            raise AssertionError(f"unexpected json path: {path_or_url}")
        security_code = str((params or {}).get("securitycodes") or "")
        if security_code != "874202":
            return {"result": []}
        return {
            "result": [
                {
                    "art_code": "AN202604021820985987",
                    "title": "恒道科技:北京证券交易所上市公告书",
                    "notice_date": "2026-04-02",
                }
            ]
        }

    def _fetch_text_once(
        self,
        path_or_url: str,
        params: dict[str, Any] | None = None,
        referer: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        _ = (params, referer, headers)
        if str(path_or_url).startswith("https://xinsanban.eastmoney.com/Article/NoticeContent?id="):
            return (
                '<html><body><a href="https://pdf.dfcfw.com/pdf/'
                'H2_AN202604021820985987_1.pdf">查看PDF原文</a></body></html>'
            )
        return super()._fetch_text_once(path_or_url, params=params, referer=referer, headers=headers)

    def _download_binary(
        self,
        path_or_url: str,
        referer: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        _ = headers
        self.download_requests.append((str(path_or_url), referer))
        return b"%PDF-1.7 fake disclosure"


def _run_parse_jsonp_case(failures: list[str]) -> None:
    payload = bse_official_helper._parse_jsonp_payload('null({"stockCode":"920177","ok":true});')
    _assert(isinstance(payload, dict), "parse_jsonp: expected dict payload", failures)
    _assert(payload.get("stockCode") == "920177", "parse_jsonp: stockCode mismatch", failures)
    _assert(payload.get("ok") is True, "parse_jsonp: boolean mismatch", failures)
    print("OK parse_jsonp: JSONP wrapper parsed successfully")


def _run_mapping_case(failures: list[str]) -> None:
    client = FakeBSEOfficialClient()
    mapping = client.resolve_project_from_post_listing_code("920177")
    _assert(mapping.listed_company.company_name == "浙江恒道科技股份有限公司", "mapping: company name mismatch", failures)
    _assert(mapping.listed_company.listing_date == "2026-04-16", "mapping: listing date mismatch", failures)
    _assert(mapping.project.pre_listing_code == "874202", "mapping: pre-listing code mismatch", failures)
    _assert(mapping.project.project_id == 618, "mapping: project id mismatch", failures)
    print("OK mapping: 920177 resolved to official 874202 project")


def _run_prospectus_resolution_case(failures: list[str]) -> None:
    client = FakeBSEOfficialClient()
    resolution = client.resolve_prospectus_by_post_listing_code("920177")
    _assert(resolution.disclosure.bucket == "BHG", "prospectus: expected BHG to be preferred", failures)
    _assert(
        resolution.disclosure.full_url == "https://www.bse.cn/disclosure/2026/2026-03-30/1774855822_070697.pdf",
        "prospectus: full url mismatch",
        failures,
    )
    _assert(
        client.build_prospectus_filename(resolution) == "920177_恒道科技_招股说明书(注册稿).pdf",
        "prospectus: output filename mismatch",
        failures,
    )
    print("OK prospectus: picked BHG prospectus and built stable filename")


def _run_prospectus_download_case(failures: list[str]) -> None:
    messages: list[str] = []
    client = FakeBSEOfficialClient(status_callback=messages.append)
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    resolution, output_path = client.download_best_prospectus_by_post_listing_code("920177", TEMP_DIR, overwrite=True)
    file_bytes = output_path.read_bytes()

    _assert(output_path.exists(), "download: expected output file to exist", failures)
    _assert(output_path.name == "920177_恒道科技_招股说明书(注册稿).pdf", "download: output name mismatch", failures)
    _assert(file_bytes.startswith(b"%PDF-1.7"), "download: pdf bytes mismatch", failures)
    _assert(
        client.download_requests == [("https://www.bse.cn/disclosure/2026/2026-03-30/1774855822_070697.pdf", "/audit/project_news.html")],
        "download: download url mismatch",
        failures,
    )
    _assert(
        resolution.mapping.project.pre_listing_code == "874202",
        "download: mapping info missing from resolution",
        failures,
    )
    _assert(
        any("已定位招股说明书" in item for item in messages),
        "download: missing resolve progress message",
        failures,
    )
    _assert(
        any("正在下载官网 PDF" in item for item in messages),
        "download: missing download progress message",
        failures,
    )
    print("OK download: wrote official prospectus pdf to local path")


def _run_official_listing_case(failures: list[str]) -> None:
    client = FakeBSEOfficialClient(official_listing_available=True)
    resolution = client.resolve_listing_announcement_by_post_listing_code("920177")
    _assert(resolution.disclosure.source == "bse", "listing official: expected official source", failures)
    _assert(
        resolution.disclosure.full_url == "https://www.bse.cn/disclosure/2026/2026-04-02/1788200000_000001.pdf",
        "listing official: full url mismatch",
        failures,
    )
    _assert(
        client.build_listing_announcement_filename(resolution) == "920177_恒道科技_上市公告书.pdf",
        "listing official: filename mismatch",
        failures,
    )
    print("OK listing official: resolved listing announcement from BSE disclosure feed")


def _run_eastmoney_listing_fallback_case(failures: list[str]) -> None:
    client = FakeBSEOfficialClient(official_listing_available=False)
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    resolution, output_path = client.download_listing_announcement_by_post_listing_code(
        "920177",
        TEMP_DIR,
        overwrite=True,
    )
    file_bytes = output_path.read_bytes()

    _assert(resolution.disclosure.source == "eastmoney", "listing fallback: expected eastmoney source", failures)
    _assert(
        resolution.disclosure.detail_url == "https://xinsanban.eastmoney.com/Article/NoticeContent?id=AN202604021820985987",
        "listing fallback: detail url mismatch",
        failures,
    )
    _assert(
        output_path.name == "920177_恒道科技_上市公告书.pdf",
        "listing fallback: output filename mismatch",
        failures,
    )
    _assert(file_bytes.startswith(b"%PDF-1.7"), "listing fallback: pdf bytes mismatch", failures)
    _assert(
        client.download_requests == [
            (
                "https://pdf.dfcfw.com/pdf/H2_AN202604021820985987_1.pdf",
                "https://xinsanban.eastmoney.com/Article/NoticeContent?id=AN202604021820985987",
            )
        ],
        "listing fallback: download request mismatch",
        failures,
    )
    print("OK listing fallback: resolved and downloaded Eastmoney listing announcement pdf")


def main() -> int:
    failures: list[str] = []
    _run_parse_jsonp_case(failures)
    _run_mapping_case(failures)
    _run_prospectus_resolution_case(failures)
    _run_prospectus_download_case(failures)
    _run_official_listing_case(failures)
    _run_eastmoney_listing_fallback_case(failures)

    if failures:
        print("\nBSE official helper validation failed:")
        for item in failures:
            print(f"- {item}")
        return 1

    print("\nBSE official helper validation passed: 6 cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
