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

import bse_ipo_valuation
import comparable_data_helper
import config_loader
import ipo_data_helper
import pdf_parser


TEMP_ROOT = ROOT_DIR / "tests" / "_tmp" / "prospectus_autodownload"
TEMP_PDF_DIR = TEMP_ROOT / "公告文件"


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _fake_prepare_ipo_data(
    code: str,
    months: int,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ = (months, params)
    return {
        "ipo_info": {
            "SECURITY_CODE": code,
            "SECURITY_NAME_ABBR": "测试公司",
            "APPLY_DATE": "2026-04-10",
            "LISTING_DATE": "2026-04-21",
            "PRICE_WAY": "直接定价",
            "TOTAL_ISSUE_NUM": 1000.0,
            "TOP_APPLY_MARKETCAP": 1200.0,
            "ONLINE_VA_NUM": 100000.0,
            "ONLINE_ISSUE_LWR": 0.05,
            "ISSUE_PRICE": 10.0,
            "AFTER_ISSUE_PE": 18.0,
            "INDUSTRY_PE_NEW": 25.0,
            "SW_INDUSTRY": "通用设备",
            "MAIN_BUSINESS": "默认主营业务",
        },
        "recent_ipos": [],
        "summary": {
            "provider": "mock",
            "recent_sample_count": 0,
            "supplemented_fields": [],
            "reason": "",
        },
    }


def _fake_get_comparable_valuations(
    codes: list[str],
    channel: str = "disabled",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ = (channel, params)
    items = [
        {
            "code": code,
            "name": "可比公司",
            "close": 20.0,
            "pe_ttm": 30.0,
            "pb_lf": 3.0,
            "mkt_cap": 100.0,
            "trade_date": "2026-04-21",
            "source": "mock",
            "close_source": "mock",
            "pe_source": "mock",
            "pb_source": "mock",
            "mkt_cap_source": "mock",
            "data_sources": ["mock"],
            "cross_validation": {},
            "is_stale": False,
        }
        for code in codes
    ]
    return {
        "items": items,
        "summary": {
            "provider": "mock",
            "channel": "disabled",
            "requested_codes": list(codes),
            "returned_codes": list(codes),
            "fixed_cache_hits": [],
            "variable_cache_hits": [],
            "api_fetched_fixed": [],
            "api_fetched_variable": [],
            "stale_variable_used": [],
            "skipped_due_quota": [],
            "skipped_unsupported": [],
            "api_calls": 0,
            "quota_limit": 0,
            "quota_used_today": 0,
            "quota_remaining": 0,
            "local_computed_codes": [],
            "eastmoney_api_calls": 0,
            "eastmoney_fetched": [],
            "eastmoney_cache_hits": [],
            "eastmoney_fallback_used": [],
            "cross_validated_codes": [],
            "cross_validation_warnings": [],
            "reason": "",
        },
    }


def _fake_extract_old_shares_result(file_path: str | Path) -> pdf_parser.OldSharesExtractionResult | None:
    _ = file_path
    return None


def _fake_extract_comparable_companies(file_path: str | Path) -> list[str]:
    return ["300001.SZ"] if Path(file_path).exists() else []


def _fake_extract_business_desc(file_path: str | Path) -> str:
    return "来自招股书的主营业务" if Path(file_path).exists() else ""


class FakeSuccessClient:
    prospectus_calls = 0
    issue_calls = 0
    listing_calls = 0

    @classmethod
    def reset_counters(cls) -> None:
        cls.prospectus_calls = 0
        cls.issue_calls = 0
        cls.listing_calls = 0

    def __init__(self, timeout: float = 20.0, status_callback=None) -> None:
        _ = timeout
        self.status_callback = status_callback

    def download_best_prospectus_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> tuple[None, Path]:
        _ = overwrite
        type(self).prospectus_calls += 1
        if self.status_callback is not None:
            self.status_callback(f"resolved prospectus: {code}")
            self.status_callback("downloading official pdf")
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{code}_测试公司_招股说明书.pdf"
        target_path.write_bytes(b"%PDF-1.7 fake prospectus\n%%EOF\n")
        return None, target_path

    def download_issue_announcement_from_newshare_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> tuple[None, Path]:
        _ = overwrite
        type(self).issue_calls += 1
        if self.status_callback is not None:
            self.status_callback(f"resolved issue announcement: {code}")
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{code}_发行公告.pdf"
        target_path.write_bytes(b"%PDF-1.7 fake issue announcement\n%%EOF\n")
        return None, target_path

    def download_listing_announcement_from_newshare_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> tuple[None, Path]:
        _ = overwrite
        type(self).listing_calls += 1
        if self.status_callback is not None:
            self.status_callback(f"resolved listing: {code}")
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{code}_上市公告书.pdf"
        target_path.write_bytes(b"%PDF-1.7 fake listing announcement\n%%EOF\n")
        return None, target_path


class FakeListingFailureClient:
    prospectus_calls = 0
    issue_calls = 0
    listing_calls = 0

    @classmethod
    def reset_counters(cls) -> None:
        cls.prospectus_calls = 0
        cls.issue_calls = 0
        cls.listing_calls = 0

    def __init__(self, timeout: float = 20.0, status_callback=None) -> None:
        _ = timeout
        self.status_callback = status_callback

    def download_best_prospectus_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> tuple[None, Path]:
        _ = overwrite
        type(self).prospectus_calls += 1
        if self.status_callback is not None:
            self.status_callback(f"resolved prospectus: {code}")
            self.status_callback("downloading official pdf")
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{code}_测试公司_招股说明书.pdf"
        target_path.write_bytes(b"%PDF-1.7 fake prospectus\n%%EOF\n")
        return None, target_path

    def download_issue_announcement_from_newshare_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> tuple[None, Path]:
        _ = overwrite
        type(self).issue_calls += 1
        if self.status_callback is not None:
            self.status_callback(f"resolved issue announcement: {code}")
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{code}_发行公告.pdf"
        target_path.write_bytes(b"%PDF-1.7 fake issue announcement\n%%EOF\n")
        return None, target_path

    def download_listing_announcement_from_newshare_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> tuple[None, Path]:
        _ = (code, output_dir, overwrite)
        type(self).listing_calls += 1
        if self.status_callback is not None:
            self.status_callback(f"resolved listing: {code}")
        raise bse_ipo_valuation.bse_official_helper.BSEOfficialError("模拟未找到上市公告书")


class FakeFailureClient:
    prospectus_calls = 0
    issue_calls = 0
    listing_calls = 0

    @classmethod
    def reset_counters(cls) -> None:
        cls.prospectus_calls = 0
        cls.issue_calls = 0
        cls.listing_calls = 0

    def __init__(self, timeout: float = 20.0, status_callback=None) -> None:
        _ = timeout
        self.status_callback = status_callback

    def download_best_prospectus_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> tuple[None, Path]:
        _ = (code, output_dir, overwrite)
        type(self).prospectus_calls += 1
        if self.status_callback is not None:
            self.status_callback(f"resolved prospectus: {code}")
            self.status_callback("downloading official pdf")
        raise bse_ipo_valuation.bse_official_helper.BSEOfficialError("模拟下载失败")

    def download_issue_announcement_from_newshare_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> tuple[None, Path]:
        _ = (code, output_dir, overwrite)
        type(self).issue_calls += 1
        if self.status_callback is not None:
            self.status_callback(f"resolved issue announcement: {code}")
        raise bse_ipo_valuation.bse_official_helper.BSEOfficialError("模拟未找到发行公告")

    def download_listing_announcement_from_newshare_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> tuple[None, Path]:
        _ = (code, output_dir, overwrite)
        type(self).listing_calls += 1
        if self.status_callback is not None:
            self.status_callback(f"resolved listing: {code}")
        raise bse_ipo_valuation.bse_official_helper.BSEOfficialError("模拟未找到上市公告书")


def _prepare_local_prospectus(code: str) -> None:
    TEMP_PDF_DIR.mkdir(parents=True, exist_ok=True)
    (TEMP_PDF_DIR / f"{code}_测试公司_招股说明书.pdf").write_bytes(b"%PDF-1.7 local prospectus\n%%EOF\n")


def _prepare_local_issue_announcement(code: str) -> None:
    TEMP_PDF_DIR.mkdir(parents=True, exist_ok=True)
    (TEMP_PDF_DIR / f"{code}_发行公告.pdf").write_bytes(b"%PDF-1.7 local issue announcement\n%%EOF\n")


def _run_case(
    client_cls: type,
    failures: list[str],
    *,
    local_prospectus: bool,
    expected_start_message: str,
    should_raise: bool,
    expect_listing_warning: bool,
    expect_prospectus_probe: bool,
    expect_issue_probe: bool,
    expect_listing_probe: bool,
    expected_pdf_count: int,
    expected_issue_error: str,
    expected_listing_error: str,
) -> None:
    if TEMP_ROOT.exists():
        shutil.rmtree(TEMP_ROOT)
    TEMP_PDF_DIR.mkdir(parents=True, exist_ok=True)
    if local_prospectus:
        _prepare_local_prospectus("920177")

    params = config_loader.load_params(ROOT_DIR / "策略参数.txt")
    messages: list[str] = []
    payload: dict[str, Any] | None = None
    raised_error = ""

    original_root = bse_ipo_valuation.ROOT_DIR
    original_prepare = ipo_data_helper.prepare_ipo_data
    original_comparables = comparable_data_helper.get_comparable_valuations
    original_extract_old_shares = pdf_parser.extract_old_shares_result
    original_extract_comparables = pdf_parser.extract_comparable_companies
    original_extract_business = pdf_parser.extract_business_desc
    original_client = bse_ipo_valuation.bse_official_helper.BSEOfficialClient

    bse_ipo_valuation.ROOT_DIR = TEMP_ROOT
    ipo_data_helper.prepare_ipo_data = _fake_prepare_ipo_data
    comparable_data_helper.get_comparable_valuations = _fake_get_comparable_valuations
    pdf_parser.extract_old_shares_result = _fake_extract_old_shares_result
    pdf_parser.extract_comparable_companies = _fake_extract_comparable_companies
    pdf_parser.extract_business_desc = _fake_extract_business_desc
    bse_ipo_valuation.bse_official_helper.BSEOfficialClient = client_cls
    client_cls.reset_counters()

    try:
        try:
            payload = bse_ipo_valuation.build_analysis_data(
                "920177",
                params=params,
                progress_callback=messages.append,
            )
        except bse_ipo_valuation.RequiredProspectusNotFoundError as exc:
            raised_error = str(exc)
    finally:
        bse_ipo_valuation.ROOT_DIR = original_root
        ipo_data_helper.prepare_ipo_data = original_prepare
        comparable_data_helper.get_comparable_valuations = original_comparables
        pdf_parser.extract_old_shares_result = original_extract_old_shares
        pdf_parser.extract_comparable_companies = original_extract_comparables
        pdf_parser.extract_business_desc = original_extract_business
        bse_ipo_valuation.bse_official_helper.BSEOfficialClient = original_client

    _assert(messages[:1] == [expected_start_message], "autodownload: missing start message", failures)
    _assert(
        client_cls.prospectus_calls == (1 if expect_prospectus_probe else 0),
        f"autodownload: unexpected prospectus probe count {client_cls.prospectus_calls}",
        failures,
    )
    _assert(
        client_cls.listing_calls == (1 if expect_listing_probe else 0),
        f"autodownload: unexpected listing probe count {client_cls.listing_calls}",
        failures,
    )
    _assert(
        client_cls.issue_calls == (1 if expect_issue_probe else 0),
        f"autodownload: unexpected issue announcement probe count {client_cls.issue_calls}",
        failures,
    )
    _assert(
        any("resolved issue announcement" in item for item in messages) == expect_issue_probe,
        "autodownload: issue announcement probe progress mismatch",
        failures,
    )
    _assert(
        any("resolved listing" in item for item in messages) == expect_listing_probe,
        "autodownload: listing probe progress mismatch",
        failures,
    )

    if should_raise:
        _assert(
            any("resolved prospectus" in item for item in messages) == expect_prospectus_probe,
            "autodownload failure: prospectus progress mismatch",
            failures,
        )
        _assert(
            raised_error == "未取到招股说明书，生成报告失败：模拟下载失败",
            "autodownload failure: error text mismatch",
            failures,
        )
        _assert(payload is None, "autodownload failure: payload should not be returned", failures)
        _assert(len(list(TEMP_PDF_DIR.glob("*.pdf"))) == expected_pdf_count, "autodownload failure: unexpected pdf count", failures)
        print("OK prospectus autodownload failure: missing prospectus now aborts report generation")
        return

    _assert(
        any("resolved prospectus" in item for item in messages) == expect_prospectus_probe,
        "autodownload success: prospectus progress mismatch",
        failures,
    )
    _assert(
        any("downloading official pdf" in item for item in messages) == expect_prospectus_probe,
        "autodownload success: download progress mismatch",
        failures,
    )
    _assert(
        payload is not None,
        "autodownload success: payload should be returned",
        failures,
    )
    if payload is None:
        return
    _assert(payload.get("prospectus_download_error") == "", "autodownload success: unexpected prospectus error", failures)
    _assert(
        payload.get("issue_announcement_download_error") == expected_issue_error,
        "autodownload success: issue announcement error text mismatch",
        failures,
    )
    _assert(payload.get("listing_download_error") == expected_listing_error, "autodownload success: listing error text mismatch", failures)
    _assert(
        payload.get("issue_announcement_pdf_found") == (expected_issue_error == ""),
        "autodownload success: issue announcement found flag mismatch",
        failures,
    )
    _assert(payload.get("comparable_codes") == ["300001.SZ"], "autodownload success: comparable codes mismatch", failures)
    _assert(payload.get("company_description") == "来自招股书的主营业务", "autodownload success: business description mismatch", failures)
    _assert(
        any("上市公告书未下载，可手动补充" in item for item in messages) == expect_listing_warning,
        "autodownload success: listing warning mismatch",
        failures,
    )
    _assert(len(list(TEMP_PDF_DIR.glob("*.pdf"))) == expected_pdf_count, "autodownload success: unexpected pdf count", failures)
    if local_prospectus:
        print("OK prospectus autodownload: local prospectus still triggers listing probe when listing is missing")
    elif expected_listing_error:
        print("OK prospectus autodownload: prospectus is enough for report generation even without listing announcement")
    else:
        print("OK prospectus autodownload: missing local files were downloaded and reused")


def _run_issue_fallback_after_prospectus_parse_failure_case(failures: list[str]) -> None:
    if TEMP_ROOT.exists():
        shutil.rmtree(TEMP_ROOT)
    TEMP_PDF_DIR.mkdir(parents=True, exist_ok=True)
    _prepare_local_prospectus("920177")
    _prepare_local_issue_announcement("920177")

    def fake_prepare_missing_fields(
        code: str,
        months: int,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        bundle = _fake_prepare_ipo_data(code, months, params)
        ipo_info = bundle["ipo_info"]
        for field_name in ("APPLY_DATE", "ISSUE_PRICE", "AFTER_ISSUE_PE", "TOP_APPLY_MARKETCAP"):
            ipo_info[field_name] = None
        return bundle

    def fake_prospectus_parse_failure(file_path: str | Path) -> dict[str, Any]:
        _ = file_path
        raise RuntimeError("mock prospectus parse failure")

    def fake_issue_announcement_info(file_path: str | Path) -> dict[str, Any]:
        _ = file_path
        return {
            "fields": {
                "APPLY_DATE": "2026-06-01",
                "ISSUE_PRICE": 9.65,
                "AFTER_ISSUE_PE": 14.93,
                "TOP_APPLY_MARKETCAP": 969.25,
            },
            "field_sources": {
                "APPLY_DATE": "issue_announcement:test",
                "ISSUE_PRICE": "issue_announcement:test",
                "AFTER_ISSUE_PE": "issue_announcement:test",
                "TOP_APPLY_MARKETCAP": "issue_announcement:test",
            },
            "raw_snippets": {},
        }

    params = config_loader.load_params(ROOT_DIR / "策略参数.txt")
    messages: list[str] = []
    original_root = bse_ipo_valuation.ROOT_DIR
    original_prepare = ipo_data_helper.prepare_ipo_data
    original_comparables = comparable_data_helper.get_comparable_valuations
    original_extract_old_shares = pdf_parser.extract_old_shares_result
    original_extract_comparables = pdf_parser.extract_comparable_companies
    original_extract_business = pdf_parser.extract_business_desc
    original_extract_prospectus_issue = pdf_parser.extract_prospectus_issue_info
    original_extract_issue_announcement = pdf_parser.extract_issue_announcement_info
    original_client = bse_ipo_valuation.bse_official_helper.BSEOfficialClient

    bse_ipo_valuation.ROOT_DIR = TEMP_ROOT
    ipo_data_helper.prepare_ipo_data = fake_prepare_missing_fields
    comparable_data_helper.get_comparable_valuations = _fake_get_comparable_valuations
    pdf_parser.extract_old_shares_result = _fake_extract_old_shares_result
    pdf_parser.extract_comparable_companies = _fake_extract_comparable_companies
    pdf_parser.extract_business_desc = _fake_extract_business_desc
    pdf_parser.extract_prospectus_issue_info = fake_prospectus_parse_failure
    pdf_parser.extract_issue_announcement_info = fake_issue_announcement_info
    bse_ipo_valuation.bse_official_helper.BSEOfficialClient = FakeListingFailureClient
    FakeListingFailureClient.reset_counters()

    try:
        payload = bse_ipo_valuation.build_analysis_data(
            "920177",
            params=params,
            progress_callback=messages.append,
        )
    finally:
        bse_ipo_valuation.ROOT_DIR = original_root
        ipo_data_helper.prepare_ipo_data = original_prepare
        comparable_data_helper.get_comparable_valuations = original_comparables
        pdf_parser.extract_old_shares_result = original_extract_old_shares
        pdf_parser.extract_comparable_companies = original_extract_comparables
        pdf_parser.extract_business_desc = original_extract_business
        pdf_parser.extract_prospectus_issue_info = original_extract_prospectus_issue
        pdf_parser.extract_issue_announcement_info = original_extract_issue_announcement
        bse_ipo_valuation.bse_official_helper.BSEOfficialClient = original_client

    ipo_info = payload.get("ipo_info") or {}
    summary = payload.get("ipo_data_summary") or {}
    _assert(ipo_info.get("APPLY_DATE") == "2026-06-01", "issue fallback: apply date mismatch", failures)
    _assert(ipo_info.get("ISSUE_PRICE") == 9.65, "issue fallback: issue price mismatch", failures)
    _assert(ipo_info.get("TOP_APPLY_MARKETCAP") == 969.25, "issue fallback: top apply mismatch", failures)
    _assert(
        summary.get("prospectus_issue_parse_error") == "mock prospectus parse failure",
        "issue fallback: prospectus parse error not recorded",
        failures,
    )
    _assert(
        set(summary.get("issue_announcement_supplemented_fields") or []) >= {"APPLY_DATE", "ISSUE_PRICE", "TOP_APPLY_MARKETCAP"},
        "issue fallback: supplemented fields missing",
        failures,
    )
    _assert(FakeListingFailureClient.issue_calls == 0, "issue fallback: local issue announcement should not download", failures)
    _assert(FakeListingFailureClient.listing_calls == 1, "issue fallback: listing probe count mismatch", failures)
    print("OK issue announcement fallback: prospectus parse failure does not block issue fields")


def main() -> int:
    failures: list[str] = []
    _run_case(
        FakeSuccessClient,
        failures,
        local_prospectus=False,
        expected_start_message="招股说明书/发行公告/上市公告书探测中，请稍候。",
        should_raise=False,
        expect_listing_warning=False,
        expect_prospectus_probe=True,
        expect_issue_probe=True,
        expect_listing_probe=True,
        expected_pdf_count=3,
        expected_issue_error="",
        expected_listing_error="",
    )
    _run_case(
        FakeListingFailureClient,
        failures,
        local_prospectus=False,
        expected_start_message="招股说明书/发行公告/上市公告书探测中，请稍候。",
        should_raise=False,
        expect_listing_warning=True,
        expect_prospectus_probe=True,
        expect_issue_probe=True,
        expect_listing_probe=True,
        expected_pdf_count=2,
        expected_issue_error="",
        expected_listing_error="模拟未找到上市公告书",
    )
    _run_case(
        FakeListingFailureClient,
        failures,
        local_prospectus=True,
        expected_start_message="发行公告/上市公告书探测中，请稍候。",
        should_raise=False,
        expect_listing_warning=True,
        expect_prospectus_probe=False,
        expect_issue_probe=True,
        expect_listing_probe=True,
        expected_pdf_count=2,
        expected_issue_error="",
        expected_listing_error="模拟未找到上市公告书",
    )
    _run_case(
        FakeFailureClient,
        failures,
        local_prospectus=False,
        expected_start_message="招股说明书/发行公告/上市公告书探测中，请稍候。",
        should_raise=True,
        expect_listing_warning=False,
        expect_prospectus_probe=True,
        expect_issue_probe=True,
        expect_listing_probe=True,
        expected_pdf_count=0,
        expected_issue_error="模拟未找到发行公告",
        expected_listing_error="模拟未找到上市公告书",
    )
    _run_issue_fallback_after_prospectus_parse_failure_case(failures)

    if failures:
        print("\nProspectus autodownload validation failed:")
        for item in failures:
            print(f"- {item}")
        return 1

    print("\nProspectus autodownload validation passed: 5 cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
