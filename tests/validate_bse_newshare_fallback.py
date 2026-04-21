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


TEMP_DIR = ROOT_DIR / "tests" / "_tmp" / "bse_newshare_fallback"


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _structured_date(year: int, month: int, day: int) -> dict[str, int]:
    return {
        "year": year - 1900,
        "month": month - 1,
        "date": day,
        "time": 0,
    }


class FakeNewShareFallbackClient(bse_official_helper.BSEOfficialClient):
    def __init__(self) -> None:
        super().__init__(timeout=1)
        self.download_requests: list[tuple[str, str | None]] = []
        self.detail_company_requested = False
        self.project_search_requested = False
        self.project_detail_requested = False

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
            self.detail_company_requested = True
            return {
                "baseinfo": {
                    "stockCode": "920028",
                    "shortname": "新恒泰",
                    "name": "浙江新恒泰新材料股份有限公司",
                    "listingDate": "20260318",
                }
            }

        if path_or_url == "/newShareController/infoResult.do":
            company_code = str((params or {}).get("companyCode") or "")
            if company_code != "920028":
                return [{"listInfo": {"content": [], "totalElements": 0}}]
            return [
                {
                    "listInfo": {
                        "content": [
                            {
                                "id": 304,
                                "fxCode": "920028",
                                "stockCode": "874502",
                                "stockName": "新恒泰",
                                "issueResultDate": _structured_date(2026, 3, 14),
                                "enterPremiumDate": _structured_date(2026, 3, 18),
                            }
                        ],
                        "totalElements": 1,
                    }
                }
            ]

        if path_or_url == "/newShareController/infoDetailResult.do":
            issue_id = int((params or {}).get("id") or 0)
            if issue_id != 304:
                raise AssertionError(f"unexpected issue id: {issue_id}")
            return [
                {
                    "newShare": {
                        "id": 304,
                        "fxCode": "920028",
                        "stockCode": "874502",
                        "stockName": "新恒泰",
                        "issueResultDate": _structured_date(2026, 3, 14),
                        "enterPremiumDate": _structured_date(2026, 3, 18),
                    },
                    "listInfo": {
                        "content": [
                            {
                                "companyCd": "920028",
                                "companyName": "新恒泰",
                                "disclosureTitle": "新恒泰:招股说明书",
                                "disclosurePostTitle": "",
                                "destFilePath": "/disclosure/2026/2026-03-09/1773049701_206613.pdf",
                                "publishDate": "2026-03-09",
                                "fileExt": "pdf",
                            },
                            {
                                "companyCd": "920028",
                                "companyName": "新恒泰",
                                "disclosureTitle": "新恒泰:向不特定合格投资者公开发行股票并在北京证券交易所上市公告书",
                                "disclosurePostTitle": "",
                                "destFilePath": "/disclosure/2026/2026-03-18/1773829213_519645.pdf",
                                "publishDate": "2026-03-18",
                                "fileExt": "pdf",
                            },
                        ]
                    },
                }
            ]

        if path_or_url == "/projectNewsController/infoResult.do":
            self.project_search_requested = True
            return {
                "content": [
                    {
                        "id": 88,
                        "stockCode": "874502",
                        "stockName": "新恒泰",
                        "companyName": "浙江新恒泰新材料股份有限公司",
                        "updateDate": "2026-03-10",
                    }
                ]
            }

        if path_or_url == "/projectNewsController/infoDetailResult.do":
            self.project_detail_requested = True
            return {
                "projectNews": {
                    "id": 88,
                    "stockCode": "874502",
                },
                "xxgkInfo": {
                    "GPFXSMS": {
                        "BHG": [
                            {
                                "fileTitle": "新恒泰:招股说明书(注册稿)",
                                "publishDate": "2026-03-10",
                                "destFilePath": "/disclosure/2026/2026-03-10/1773100000_000001.pdf",
                            }
                        ]
                    }
                },
            }

        if path_or_url == "/disclosureInfoController/companyAnnouncement.do":
            return [{"listInfo": {"content": [], "totalElements": 0}}]

        raise AssertionError(f"unexpected path: {path_or_url}")

    def _request_json(
        self,
        path_or_url: str,
        params: dict[str, Any] | None = None,
        referer: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        _ = (path_or_url, params, referer, headers)
        return {"result": []}

    def _download_binary(
        self,
        path_or_url: str,
        referer: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        _ = headers
        self.download_requests.append((str(path_or_url), referer))
        return b"%PDF-1.7 fake newshare disclosure"


def _run_issue_mapping_case(failures: list[str]) -> None:
    client = FakeNewShareFallbackClient()
    issue = client.resolve_newshare_issue_by_post_listing_code("920028")
    _assert(issue.issue_id == 304, "newshare mapping: issue id mismatch", failures)
    _assert(issue.pre_listing_code == "874502", "newshare mapping: pre-listing code mismatch", failures)
    _assert(issue.stock_name == "新恒泰", "newshare mapping: stock name mismatch", failures)
    print("OK newshare issue: resolved 920028 to official issue detail entry")


def _run_prospectus_case(failures: list[str]) -> None:
    client = FakeNewShareFallbackClient()
    resolution = client.resolve_prospectus_by_post_listing_code("920028")
    _assert(resolution.disclosure.source == "bse_newshare", "newshare prospectus: source mismatch", failures)
    _assert(
        resolution.disclosure.full_url == "https://www.bse.cn/disclosure/2026/2026-03-09/1773049701_206613.pdf",
        "newshare prospectus: full url mismatch",
        failures,
    )
    _assert(
        resolution.mapping.project.pre_listing_code == "874502",
        "newshare prospectus: mapping pre-listing code mismatch",
        failures,
    )
    _assert(
        client.build_prospectus_filename(resolution) == "920028_新恒泰_招股说明书.pdf",
        "newshare prospectus: filename mismatch",
        failures,
    )
    print("OK newshare prospectus: resolved via issue detail route")


def _run_prospectus_priority_case(failures: list[str]) -> None:
    client = FakeNewShareFallbackClient()
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    resolution, output_path = client.download_best_prospectus_by_post_listing_code(
        "920028",
        TEMP_DIR,
        overwrite=True,
    )

    _assert(output_path.exists(), "newshare prospectus priority: expected output file", failures)
    _assert(resolution.disclosure.source == "bse_newshare", "newshare prospectus priority: source mismatch", failures)
    _assert(not client.detail_company_requested, "newshare prospectus priority: old company detail should not run", failures)
    _assert(not client.project_search_requested, "newshare prospectus priority: old project search should not run", failures)
    _assert(not client.project_detail_requested, "newshare prospectus priority: old project detail should not run", failures)
    _assert(
        client.download_requests == [
            (
                "https://www.bse.cn/disclosure/2026/2026-03-09/1773049701_206613.pdf",
                "https://www.bse.cn/newshare/listofissues_detail.html?id=304",
            )
        ],
        "newshare prospectus priority: download request mismatch",
        failures,
    )
    print("OK newshare prospectus priority: preferred issue-detail disclosures over legacy project path")


def _run_listing_download_case(failures: list[str]) -> None:
    client = FakeNewShareFallbackClient()
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    resolution, output_path = client.download_listing_announcement_by_post_listing_code(
        "920028",
        TEMP_DIR,
        overwrite=True,
    )
    file_bytes = output_path.read_bytes()

    _assert(output_path.exists(), "newshare listing: expected output file", failures)
    _assert(output_path.name == "920028_新恒泰_上市公告书.pdf", "newshare listing: filename mismatch", failures)
    _assert(resolution.disclosure.source == "bse_newshare", "newshare listing: source mismatch", failures)
    _assert(file_bytes.startswith(b"%PDF-1.7"), "newshare listing: pdf bytes mismatch", failures)
    _assert(
        client.download_requests == [
            (
                "https://www.bse.cn/disclosure/2026/2026-03-18/1773829213_519645.pdf",
                "https://www.bse.cn/newshare/listofissues_detail.html?id=304",
            )
        ],
        "newshare listing: download request mismatch",
        failures,
    )
    print("OK newshare listing: downloaded official listing announcement from issue detail page")


def main() -> int:
    failures: list[str] = []
    _run_issue_mapping_case(failures)
    _run_prospectus_case(failures)
    _run_prospectus_priority_case(failures)
    _run_listing_download_case(failures)

    if failures:
        print("\nBSE newshare fallback validation failed:")
        for item in failures:
            print(f"- {item}")
        return 1

    print("\nBSE newshare fallback validation passed: 4 cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
