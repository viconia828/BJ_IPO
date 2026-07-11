from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import http.client
from http.cookiejar import CookieJar
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable


BASE_URL = "https://www.bse.cn"
EASTMONEY_NOTICE_BASE_URL = "https://xinsanban.eastmoney.com"
EASTMONEY_NOTICE_LIST_URL = f"{EASTMONEY_NOTICE_BASE_URL}/api/gg/list"
DEFAULT_TIMEOUT = 20.0
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
EASTMONEY_HEADERS = {
    "User-Agent": DEFAULT_HEADERS["User-Agent"],
    "Accept": "application/json, text/html, */*",
    "Referer": f"{EASTMONEY_NOTICE_BASE_URL}/Article/NoticeList",
}
JSONP_WRAPPER_PATTERN = re.compile(r"^\s*[^(\s]+\((?P<payload>.*)\)\s*;?\s*$", re.DOTALL)
INVALID_FILE_CHARS = re.compile(r'[<>:"/\\|?*]+')
HTML_MARKERS = (
    "<!doctype html",
    "<html",
    "document.cookie",
    "c3vk",
    "访问验证",
    "开启javascript",
)
PDF_HEADER_MARKER = b"%PDF-"
PDF_EOF_MARKER = b"%%EOF"
PDF_HEADER_SCAN_BYTES = 1024
PDF_EOF_SCAN_BYTES = 4096
PROSPECTUS_STAGE_PRIORITY = {
    "BHG": 0,
    "SYG": 1,
    "SBG": 2,
    "NEW": 3,
}
PROSPECTUS_STAGE_LABELS = {
    "BHG": "注册稿",
    "SYG": "上会稿",
    "SBG": "申报稿",
}
LISTING_ANNOUNCEMENT_KEYWORDS = (
    "向不特定合格投资者公开发行股票并在北京证券交易所上市公告书",
    "北京证券交易所上市公告书",
    "上市公告书",
    "上市公告",
)
ISSUE_ANNOUNCEMENT_KEYWORDS = (
    "向不特定合格投资者公开发行股票并在北京证券交易所上市发行公告",
    "公开发行股票并在北京证券交易所上市发行公告",
    "上市发行公告",
    "发行公告",
)
ISSUE_RESULT_ANNOUNCEMENT_KEYWORDS = (
    "向不特定合格投资者公开发行股票并在北京证券交易所上市发行结果公告",
    "公开发行股票并在北京证券交易所上市发行结果公告",
    "上市发行结果公告",
    "发行结果公告",
    "发行结果",
)
EASTMONEY_PDF_LINK_PATTERN = re.compile(
    r'href="(?P<url>https://pdf\.dfcfw\.com/pdf/[^"]+)"',
    re.IGNORECASE,
)


class BSEOfficialError(RuntimeError):
    pass


class _PDFIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class ListedCompanyProfile:
    post_listing_code: str
    short_name: str
    company_name: str
    listing_date: str


@dataclass(frozen=True)
class BSEProject:
    project_id: int
    pre_listing_code: str
    stock_name: str
    company_name: str
    issue_stage: str
    project_status: str
    update_date: str
    accept_date: str


@dataclass(frozen=True)
class PostListingCodeMapping:
    listed_company: ListedCompanyProfile
    project: BSEProject


@dataclass(frozen=True)
class NewShareIssue:
    issue_id: int
    post_listing_code: str
    pre_listing_code: str
    stock_name: str
    company_name: str
    listing_date: str
    issue_result_date: str


@dataclass(frozen=True)
class DisclosureFile:
    title: str
    publish_date: str
    relative_path: str
    full_url: str
    bucket: str
    bucket_label: str
    document_type: str
    file_ext: str
    detail_url: str = ""
    source: str = "bse"


@dataclass(frozen=True)
class ProspectusResolution:
    mapping: PostListingCodeMapping
    disclosure: DisclosureFile


@dataclass(frozen=True)
class ListingAnnouncementResolution:
    mapping: PostListingCodeMapping
    disclosure: DisclosureFile


@dataclass(frozen=True)
class IssueAnnouncementResolution:
    mapping: PostListingCodeMapping
    disclosure: DisclosureFile


@dataclass(frozen=True)
class IssueResultAnnouncementResolution:
    mapping: PostListingCodeMapping
    disclosure: DisclosureFile


def _normalize_stock_code(code: str) -> str:
    normalized = str(code or "").strip()
    if not re.fullmatch(r"\d{6}", normalized):
        raise BSEOfficialError(f"证券代码格式不正确: {code}")
    return normalized


def _normalize_date_text(raw_value: Any) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    text = text.split(" ", 1)[0].replace("/", "-").replace(".", "-")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return text


def _safe_int(raw_value: Any) -> int:
    try:
        return int(str(raw_value or "").strip())
    except (TypeError, ValueError):
        return 0


def _safe_float(raw_value: Any) -> float | None:
    if raw_value in (None, "", "--"):
        return None
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return None


def _amount_to_wan_shares(raw_value: Any) -> float | None:
    amount = _safe_float(raw_value)
    if amount is None:
        return None
    if amount > 100000:
        return amount / 10000
    return amount


def _network_error_reason(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    if reason:
        return str(reason)
    return str(exc) or exc.__class__.__name__


def _parse_content_length(headers: Any) -> int | None:
    try:
        raw_value = headers.get("Content-Length")
    except AttributeError:
        return None
    if raw_value in (None, ""):
        return None
    try:
        value = int(str(raw_value).strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _validate_pdf_binary(binary: bytes, expected_size: int | None = None) -> None:
    actual_size = len(binary)
    if expected_size is not None and actual_size != expected_size:
        raise _PDFIntegrityError(f"期望 {expected_size} 字节，实际 {actual_size} 字节")
    if PDF_HEADER_MARKER not in binary[:PDF_HEADER_SCAN_BYTES]:
        raise _PDFIntegrityError("未找到 PDF 文件头")
    if PDF_EOF_MARKER not in binary[-PDF_EOF_SCAN_BYTES:]:
        raise _PDFIntegrityError("未找到 PDF 结束标记")


def is_complete_pdf_file(file_path: str | Path) -> bool:
    try:
        path = Path(file_path)
        size = path.stat().st_size
        if size <= 0:
            return False
        with path.open("rb") as file_obj:
            head = file_obj.read(PDF_HEADER_SCAN_BYTES)
            file_obj.seek(max(0, size - PDF_EOF_SCAN_BYTES))
            tail = file_obj.read()
    except OSError:
        return False
    return PDF_HEADER_MARKER in head and PDF_EOF_MARKER in tail


def _date_sort_key(raw_value: str) -> int:
    normalized = _normalize_date_text(raw_value)
    digits = normalized.replace("-", "")
    return int(digits) if digits.isdigit() else 0


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()


def _absolute_url(path_or_url: str) -> str:
    text = str(path_or_url or "").strip()
    if not text:
        return ""
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return urllib.parse.urljoin(BASE_URL, text)


def _strip_prefix(text: str, prefix: str) -> str:
    compact_text = str(text or "").strip()
    return compact_text[len(prefix) :] if compact_text.startswith(prefix) else compact_text


def _sanitize_filename_component(text: str) -> str:
    compact_text = INVALID_FILE_CHARS.sub("_", str(text or "").strip())
    compact_text = re.sub(r"\s+", " ", compact_text).strip(" .")
    return compact_text or "bse_disclosure"


def _normalize_title_text(text: str) -> str:
    compact_text = _compact_text(text)
    compact_text = compact_text.replace("（", "(").replace("）", ")")
    return compact_text


def _listing_announcement_priority(title: str) -> int:
    normalized = _normalize_title_text(title)
    if "向不特定合格投资者公开发行股票并在北京证券交易所上市公告书" in normalized:
        return 0
    if "北京证券交易所上市公告书" in normalized:
        return 1
    if "上市公告书" in normalized:
        return 2
    if "上市公告" in normalized:
        return 3
    return 99


def _issue_announcement_priority(title: str) -> int:
    normalized = _normalize_title_text(title)
    if _listing_announcement_priority(normalized) <= 3:
        return 99
    if "发行结果" in normalized or "结果公告" in normalized:
        return 99
    if "招股说明书" in normalized or "招股意向书" in normalized:
        return 99
    for priority, keyword in enumerate(ISSUE_ANNOUNCEMENT_KEYWORDS):
        if keyword in normalized:
            return priority
    return 99


def _issue_result_announcement_priority(title: str) -> int:
    normalized = _normalize_title_text(title)
    if _listing_announcement_priority(normalized) <= 3:
        return 99
    if "招股说明书" in normalized or "招股意向书" in normalized:
        return 99
    for priority, keyword in enumerate(ISSUE_RESULT_ANNOUNCEMENT_KEYWORDS):
        if keyword in normalized:
            return priority
    return 99


def _normalize_bse_date_value(raw_value: Any) -> str:
    if isinstance(raw_value, dict):
        year = _safe_int(raw_value.get("year"))
        month = _safe_int(raw_value.get("month"))
        day = _safe_int(raw_value.get("date"))
        if year or month or day:
            try:
                return date(year + 1900, month + 1, day).isoformat()
            except ValueError:
                pass
        millis = _safe_int(raw_value.get("time"))
        if millis > 0:
            try:
                return date.fromtimestamp(millis / 1000).isoformat()
            except (OverflowError, OSError, ValueError):
                pass
    return _normalize_date_text(raw_value)


def _prospectus_bucket_from_title(title: str) -> str:
    normalized = _normalize_title_text(title)
    if "娉ㄥ唽绋" in normalized or "注册稿" in normalized:
        return "BHG"
    if "涓婁細绋" in normalized or "上会稿" in normalized:
        return "SYG"
    if "鐢虫姤绋" in normalized or "申报稿" in normalized:
        return "SBG"
    if "鎷涜偂璇存槑涔" in normalized or "招股说明书" in normalized:
        return "NEW"
    if "鎷涜偂鎰忓悜涔" in normalized or "招股意向书" in normalized:
        return "NEW"
    return ""


def _prospectus_kind_from_title(title: str) -> str:
    normalized = _normalize_title_text(title)
    if "鎷涜偂鎰忓悜涔" in normalized or "招股意向书" in normalized:
        return "intent"
    if "鎷涜偂璇存槑涔" in normalized or "招股说明书" in normalized:
        return "prospectus"
    return ""


def _is_prospectus_title(title: str) -> bool:
    return bool(_prospectus_bucket_from_title(title))


def _parse_jsonp_payload(text: str) -> Any:
    compact_text = str(text or "").strip()
    if not compact_text:
        raise BSEOfficialError("北交所官网返回空内容")

    if compact_text[0] in "{[":
        try:
            return json.loads(compact_text)
        except json.JSONDecodeError as exc:
            raise BSEOfficialError("北交所官网返回内容无法解析为 JSON") from exc

    match = JSONP_WRAPPER_PATTERN.match(compact_text)
    if not match:
        raise BSEOfficialError("北交所官网返回内容不是可识别的 JSON/JSONP")

    payload_text = match.group("payload").strip()
    try:
        return json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise BSEOfficialError("北交所官网 JSONP 内容解析失败") from exc


def _looks_like_html(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in HTML_MARKERS)


def _coerce_record_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("content", "data", "rows", "list"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    result = payload.get("result")
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        return _coerce_record_list(result)
    return []


def _coerce_newshare_issue_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    records: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        list_info = item.get("listInfo") or {}
        content = list_info.get("content") or []
        if isinstance(content, list):
            records.extend(entry for entry in content if isinstance(entry, dict))
    return records


class BSEOfficialClient:
    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.timeout = timeout
        self.status_callback = status_callback
        self.cookie_jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))

    def _notify_status(self, message: str) -> None:
        if self.status_callback is not None and message:
            self.status_callback(str(message))

    def _run_with_periodic_status(
        self,
        task: Callable[[], bytes],
        wait_message: str,
        interval_seconds: float = 30.0,
    ) -> bytes:
        result: dict[str, Any] = {}

        def _worker() -> None:
            try:
                result["value"] = task()
            except Exception as exc:  # pragma: no cover - re-raised on caller thread
                result["error"] = exc

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        while thread.is_alive():
            thread.join(interval_seconds)
            if thread.is_alive():
                self._notify_status(wait_message)

        if "error" in result:
            raise result["error"]
        return result["value"]

    def _build_url(self, path_or_url: str) -> str:
        return _absolute_url(path_or_url)

    def _build_request(
        self,
        path_or_url: str,
        params: dict[str, Any] | None = None,
        referer: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> urllib.request.Request:
        request_url = self._build_url(path_or_url)
        if params:
            query = urllib.parse.urlencode(
                {key: value for key, value in params.items() if value not in (None, "")},
                doseq=True,
                quote_via=urllib.parse.quote,
            )
            connector = "&" if "?" in request_url else "?"
            request_url = f"{request_url}{connector}{query}"

        request_headers = dict(DEFAULT_HEADERS)
        if referer:
            request_headers["Referer"] = self._build_url(referer)
        if headers:
            request_headers.update(headers)
        return urllib.request.Request(request_url, headers=request_headers)

    @staticmethod
    def _decode_response(raw_bytes: bytes, charset: str | None = None) -> str:
        tried: list[str] = []
        for encoding in (charset, "utf-8-sig", "utf-8", "gb18030"):
            if not encoding or encoding in tried:
                continue
            tried.append(encoding)
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode("utf-8", errors="replace")

    def _fetch_text_once(
        self,
        path_or_url: str,
        params: dict[str, Any] | None = None,
        referer: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        request = self._build_request(path_or_url, params=params, referer=referer, headers=headers)
        max_attempts = 3
        last_retryable_error: tuple[str, BaseException] | None = None
        for attempt in range(max_attempts):
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    raw_bytes = response.read()
                    charset = response.headers.get_content_charset()
                return self._decode_response(raw_bytes, charset)
            except urllib.error.HTTPError as exc:
                reason = f"HTTP {exc.code}"
                if exc.reason:
                    reason = f"{reason} {exc.reason}"
                raise BSEOfficialError(f"北交所官网请求失败: {reason}") from exc
            except urllib.error.URLError as exc:
                last_retryable_error = ("北交所官网请求失败", exc)
            except TimeoutError as exc:
                last_retryable_error = ("北交所官网请求超时", exc)
            except (http.client.IncompleteRead, OSError) as exc:
                last_retryable_error = ("北交所官网网络请求异常", exc)

            if attempt < max_attempts - 1:
                self._notify_status(f"官网接口响应不稳定，正在重试（{attempt + 2}/{max_attempts}）...")
                time.sleep(0.8 + attempt * 0.7)
                continue

        if last_retryable_error is None:
            raise BSEOfficialError("北交所官网请求失败")
        message, exc = last_retryable_error
        raise BSEOfficialError(f"{message}: {_network_error_reason(exc)}") from exc

    def _download_binary(
        self,
        path_or_url: str,
        referer: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        download_timeout = max(self.timeout, 45.0)
        last_retryable_error: Exception | None = None
        max_attempts = 2
        for attempt in range(max_attempts):
            request = self._build_request(path_or_url, referer=referer, headers=headers)
            try:
                with self.opener.open(request, timeout=download_timeout) as response:
                    chunks: list[bytes] = []
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    binary = b"".join(chunks)
                    _validate_pdf_binary(binary, expected_size=_parse_content_length(response.headers))
                    return binary
            except urllib.error.HTTPError as exc:
                reason = f"HTTP {exc.code}"
                if exc.reason:
                    reason = f"{reason} {exc.reason}"
                raise BSEOfficialError(f"北交所 PDF 下载失败: {reason}") from exc
            except (urllib.error.URLError, TimeoutError, http.client.IncompleteRead, OSError, _PDFIntegrityError) as exc:
                last_retryable_error = exc
                if attempt < max_attempts - 1:
                    self._notify_status(f"官网 PDF 下载较慢，正在重试（{attempt + 2}/{max_attempts}）...")
                    time.sleep(1.0 + attempt)
                    continue

        exc = last_retryable_error
        if exc is None:
            raise BSEOfficialError("北交所 PDF 下载失败: unknown download error")
        self._notify_status("官网直连下载失败，正在切换备用下载方式...")
        try:
            return self._download_binary_via_curl(path_or_url, referer=referer, headers=headers)
        except BSEOfficialError as curl_exc:
            if isinstance(exc, urllib.error.URLError):
                reason = getattr(exc, "reason", exc)
                raise BSEOfficialError(f"北交所 PDF 下载失败: {reason}；curl 兜底失败：{curl_exc}") from exc
            if isinstance(exc, TimeoutError):
                raise BSEOfficialError(f"北交所 PDF 下载超时；curl 兜底失败：{curl_exc}") from exc
            raise BSEOfficialError(f"北交所 PDF 下载中断；curl 兜底失败：{curl_exc}") from exc

    def _download_binary_via_curl(
        self,
        path_or_url: str,
        referer: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        curl_executable = shutil.which("curl.exe") or shutil.which("curl")
        if not curl_executable:
            raise BSEOfficialError("curl is not available")

        request_url = self._build_url(path_or_url)
        request_headers = dict(DEFAULT_HEADERS)
        if headers:
            request_headers.update(headers)

        with tempfile.TemporaryDirectory(prefix="bse_pdf_") as temp_dir:
            output_path = Path(temp_dir) / "download.bin"
            command = [
                curl_executable,
                "-L",
                "--fail",
                "--silent",
                "--show-error",
                request_url,
                "-o",
                str(output_path),
            ]
            if referer:
                command.extend(["-e", self._build_url(referer)])
            for key, value in request_headers.items():
                command.extend(["-H", f"{key}: {value}"])
            try:
                subprocess.run(
                    command,
                    check=True,
                    timeout=max(self.timeout, 300.0),
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                stderr = (exc.stderr or "").strip()
                raise BSEOfficialError(f"curl download failed: {stderr or exc}") from exc
            except subprocess.TimeoutExpired as exc:
                raise BSEOfficialError("curl download timed out") from exc

            if not output_path.exists():
                raise BSEOfficialError("curl download did not create a file")
            binary = output_path.read_bytes()
            try:
                _validate_pdf_binary(binary)
            except _PDFIntegrityError as exc:
                raise BSEOfficialError(f"curl download integrity check failed: {exc}") from exc
            return binary

    def _warm_up(self, urls: Iterable[str]) -> None:
        for item in urls:
            try:
                self._fetch_text_once(item, referer=f"{BASE_URL}/index.html")
            except BSEOfficialError:
                continue

    def _request_payload(
        self,
        path_or_url: str,
        params: dict[str, Any] | None = None,
        referer: str | None = None,
        warmup_urls: Iterable[str] = (),
        headers: dict[str, str] | None = None,
    ) -> Any:
        last_error: BSEOfficialError | None = None
        for attempt in range(2):
            text = self._fetch_text_once(path_or_url, params=params, referer=referer, headers=headers)
            if not _looks_like_html(text):
                try:
                    return _parse_jsonp_payload(text)
                except BSEOfficialError as exc:
                    last_error = exc
            else:
                last_error = BSEOfficialError("北交所官网返回了 HTML 验证页")

            if attempt == 0 and tuple(warmup_urls):
                self._warm_up(warmup_urls)
                continue
            break

        raise last_error or BSEOfficialError("北交所官网请求失败")

    def _request_json(
        self,
        path_or_url: str,
        params: dict[str, Any] | None = None,
        referer: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        text = self._fetch_text_once(path_or_url, params=params, referer=referer, headers=headers)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BSEOfficialError("请求返回内容无法解析为 JSON") from exc
        if not isinstance(payload, dict):
            raise BSEOfficialError("请求返回结构异常，未找到 JSON 对象")
        return payload

    def get_listed_company_detail(self, code: str) -> ListedCompanyProfile:
        normalized_code = _normalize_stock_code(code)
        warmup_page = (
            f"/products/neeq_listed_companies/general_information.html"
            f"?companyCode={normalized_code}&xxfcbj=2&typename=Z"
        )
        payload = self._request_payload(
            "/nqhqController/detailCompany.do",
            params={
                "zqdm": normalized_code,
                "xxfcbj": 2,
            },
            referer=warmup_page,
            warmup_urls=(warmup_page,),
        )
        if not isinstance(payload, dict):
            raise BSEOfficialError(f"北交所公司详情返回结构异常: {normalized_code}")

        baseinfo = payload.get("baseinfo") or {}
        company_name = str(baseinfo.get("name") or "").strip()
        if not company_name:
            raise BSEOfficialError(f"未找到 {normalized_code} 的北交所公司详情")

        return ListedCompanyProfile(
            post_listing_code=normalized_code,
            short_name=str(baseinfo.get("shortname") or "").strip(),
            company_name=company_name,
            listing_date=_normalize_date_text(baseinfo.get("listingDate")),
        )

    def search_projects_by_company_name(
        self,
        company_name: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[BSEProject]:
        normalized_name = str(company_name or "").strip()
        if not normalized_name:
            raise BSEOfficialError("公司全称为空，无法查询北交所审核项目")

        today = date.today()
        payload = self._request_payload(
            "/projectNewsController/infoResult.do",
            params={
                "keyword": normalized_name,
                "isNewThree": 1,
                "sortfield": "updateDate",
                "sorttype": "desc",
                "startTime": _normalize_date_text(start_date) or (today - timedelta(days=365 * 2)).isoformat(),
                "endTime": _normalize_date_text(end_date) or today.isoformat(),
            },
            referer="/audit/project_news.html",
            warmup_urls=("/audit/project_news.html",),
        )
        records = _coerce_record_list(payload)
        projects: list[BSEProject] = []
        for item in records:
            project_id = _safe_int(item.get("id"))
            if not project_id:
                continue
            projects.append(
                BSEProject(
                    project_id=project_id,
                    pre_listing_code=str(item.get("stockCode") or "").strip(),
                    stock_name=str(item.get("stockName") or "").strip(),
                    company_name=str(item.get("companyName") or "").strip(),
                    issue_stage=str(
                        item.get("issueProcessDesc")
                        or item.get("issueProcess")
                        or item.get("stageName")
                        or ""
                    ).strip(),
                    project_status=str(
                        item.get("stateDesc")
                        or item.get("projectStatus")
                        or item.get("statusDesc")
                        or ""
                    ).strip(),
                    update_date=_normalize_date_text(item.get("updateDate")),
                    accept_date=_normalize_date_text(
                        item.get("receiveDate")
                        or item.get("acceptedDate")
                        or item.get("acceptDate")
                    ),
                )
            )

        query_name = _compact_text(normalized_name)
        return sorted(
            projects,
            key=lambda item: (
                0 if _compact_text(item.company_name) == query_name else 1,
                0 if item.pre_listing_code.startswith("8") else 1,
                0 if query_name and query_name in _compact_text(item.company_name) else 1,
                -_date_sort_key(item.update_date),
                -item.project_id,
            ),
        )

    def lookup_best_project_by_company_name(self, company_name: str) -> BSEProject:
        projects = self.search_projects_by_company_name(company_name)
        if not projects:
            raise BSEOfficialError(f"未找到 {company_name} 的北交所审核项目")
        return projects[0]

    def get_project_detail(self, project_id: int | str) -> dict[str, Any]:
        normalized_id = _safe_int(project_id)
        if not normalized_id:
            raise BSEOfficialError(f"项目 ID 无效: {project_id}")

        detail_page = f"/audit/project_news_detail.html?id={normalized_id}"
        payload = self._request_payload(
            "/projectNewsController/infoDetailResult.do",
            params={"id": normalized_id},
            referer=detail_page,
            warmup_urls=(detail_page,),
        )
        if not isinstance(payload, dict):
            raise BSEOfficialError(f"北交所项目详情返回结构异常: {normalized_id}")
        return payload

    def resolve_project_from_post_listing_code(self, code: str) -> PostListingCodeMapping:
        listed_company = self.get_listed_company_detail(code)
        project = self.lookup_best_project_by_company_name(listed_company.company_name)
        return PostListingCodeMapping(
            listed_company=listed_company,
            project=project,
        )

    def map_post_listing_code_to_pre_listing_code(self, code: str) -> str:
        return self.resolve_project_from_post_listing_code(code).project.pre_listing_code

    def search_newshare_issues(
        self,
        company_code: str,
        keyword: str = "",
        state_types: str = "",
    ) -> list[NewShareIssue]:
        normalized_code = _normalize_stock_code(company_code)
        payload = self._request_payload(
            "/newShareController/infoResult.do",
            params={
                "statetypes": state_types,
                "page": 0,
                "companyCode": normalized_code,
                "keyword": keyword,
                "isNewThree": 1,
                "sortfield": "issueResultDate",
                "sorttype": "desc",
                "needFields": [
                    "id",
                    "fxCode",
                    "stockCode",
                    "stockName",
                    "initialIssueAmount",
                    "enquiryType",
                    "issuePrice",
                    "peRatio",
                    "purchaseDate",
                    "issueResultDate",
                    "enterPremiumDate",
                ],
            },
            referer="/newshare/listofissues.html",
            warmup_urls=("/newshare/listofissues.html",),
        )
        records = _coerce_newshare_issue_records(payload)
        issues: list[NewShareIssue] = []
        for item in records:
            issue_id = _safe_int(item.get("id"))
            fx_code = str(item.get("fxCode") or "").strip()
            if not issue_id or not fx_code:
                continue
            issues.append(
                NewShareIssue(
                    issue_id=issue_id,
                    post_listing_code=fx_code,
                    pre_listing_code=str(item.get("stockCode") or "").strip(),
                    stock_name=str(item.get("stockName") or "").strip(),
                    company_name=str(item.get("stockName") or "").strip(),
                    listing_date=_normalize_bse_date_value(item.get("enterPremiumDate")),
                    issue_result_date=_normalize_bse_date_value(item.get("issueResultDate")),
                )
            )
        return sorted(
            issues,
            key=lambda item: (
                0 if item.post_listing_code == normalized_code else 1,
                -_date_sort_key(item.listing_date),
                -item.issue_id,
            ),
        )

    def resolve_newshare_issue_by_post_listing_code(self, code: str) -> NewShareIssue:
        normalized_code = _normalize_stock_code(code)
        issues = self.search_newshare_issues(normalized_code)
        if not issues:
            raise BSEOfficialError(f"newshare issue not found for post-listing code: {normalized_code}")
        return issues[0]

    @staticmethod
    def _mapping_from_newshare_issue(issue: NewShareIssue) -> PostListingCodeMapping:
        listed_company = ListedCompanyProfile(
            post_listing_code=issue.post_listing_code,
            short_name=issue.stock_name or issue.company_name,
            company_name=issue.company_name or issue.stock_name,
            listing_date=issue.listing_date,
        )
        project = BSEProject(
            project_id=issue.issue_id,
            pre_listing_code=issue.pre_listing_code,
            stock_name=issue.stock_name,
            company_name=issue.company_name or issue.stock_name,
            issue_stage="newshare_issue",
            project_status="",
            update_date=issue.listing_date or issue.issue_result_date,
            accept_date="",
        )
        return PostListingCodeMapping(
            listed_company=listed_company,
            project=project,
        )

    def get_newshare_issue_detail(
        self,
        issue_id: int | str,
        page: int = 0,
        page_size: int = 200,
    ) -> dict[str, Any]:
        normalized_id = _safe_int(issue_id)
        if not normalized_id:
            raise BSEOfficialError(f"invalid newshare issue id: {issue_id}")
        detail_page = f"/newshare/listofissues_detail.html?id={normalized_id}"
        payload = self._request_payload(
            "/newShareController/infoDetailResult.do",
            params={
                "id": normalized_id,
                "page": page,
                "pageSize": page_size,
            },
            referer=detail_page,
            warmup_urls=(detail_page,),
        )
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise BSEOfficialError(f"unexpected newshare issue detail payload: {normalized_id}")
        return payload[0]

    def build_newshare_ipo_info_by_post_listing_code(self, code: str) -> dict[str, Any]:
        issue = self.resolve_newshare_issue_by_post_listing_code(code)
        issue_detail = self.get_newshare_issue_detail(issue.issue_id)
        new_share = issue_detail.get("newShare") or {}
        if not isinstance(new_share, dict):
            new_share = {}

        listing_date = issue.listing_date or _normalize_bse_date_value(new_share.get("enterPremiumDate"))
        return {
            "SECURITY_CODE": issue.post_listing_code,
            "SECURITY_NAME_ABBR": issue.stock_name or issue.company_name or issue.post_listing_code,
            "APPLY_DATE": _normalize_bse_date_value(new_share.get("purchaseDate")),
            "LISTING_DATE": listing_date,
            "ISSUE_RESULT_DATE": issue.issue_result_date,
            "ISSUE_PRICE": _safe_float(new_share.get("issuePrice")),
            "AFTER_ISSUE_PE": _safe_float(new_share.get("peRatio")),
            "TOTAL_ISSUE_NUM": _amount_to_wan_shares(new_share.get("initialIssueAmount")),
            "PRE_LISTING_CODE": issue.pre_listing_code,
            "source": "bse_newshare",
        }

    def list_newshare_disclosure_files(
        self,
        issue_detail: dict[str, Any],
        issue: NewShareIssue,
    ) -> list[DisclosureFile]:
        list_info = issue_detail.get("listInfo") or {}
        content = list_info.get("content") or []
        if not isinstance(content, list):
            return []

        detail_url = f"{BASE_URL}/newshare/listofissues_detail.html?id={issue.issue_id}"
        files: list[DisclosureFile] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            relative_path = str(item.get("destFilePath") or "").strip()
            if not relative_path:
                continue
            title = f"{str(item.get('disclosureTitle') or '').strip()}{str(item.get('disclosurePostTitle') or '').strip()}".strip()
            suffix = str(item.get("fileExt") or "").strip()
            if suffix and not suffix.startswith("."):
                suffix = f".{suffix}"
            if not suffix:
                suffix = Path(urllib.parse.urlparse(relative_path).path).suffix or ".pdf"
            bucket = _prospectus_bucket_from_title(title)
            document_type = "disclosure"
            bucket_label = "newshare_detail"
            if _listing_announcement_priority(title) <= 3:
                bucket = "listing_announcement"
                document_type = "listing_announcement"
                bucket_label = "listing_announcement"
            elif _issue_result_announcement_priority(title) <= 3:
                bucket = "issue_result_announcement"
                document_type = "issue_result_announcement"
                bucket_label = "issue_result_announcement"
            elif _issue_announcement_priority(title) <= 3:
                bucket = "issue_announcement"
                document_type = "issue_announcement"
                bucket_label = "issue_announcement"
            elif bucket:
                document_type = "prospectus"
                bucket_label = "newshare_detail"
            files.append(
                DisclosureFile(
                    title=title,
                    publish_date=_normalize_bse_date_value(item.get("publishDate") or item.get("pubDate")),
                    relative_path=relative_path,
                    full_url=_absolute_url(relative_path),
                    bucket=bucket,
                    bucket_label=bucket_label,
                    document_type=document_type,
                    file_ext=suffix,
                    detail_url=detail_url,
                    source="bse_newshare",
                )
            )
        return files

    def list_newshare_prospectus_files(
        self,
        issue_detail: dict[str, Any],
        issue: NewShareIssue,
    ) -> list[DisclosureFile]:
        files = [
            item
            for item in self.list_newshare_disclosure_files(issue_detail, issue)
            if _is_prospectus_title(item.title)
        ]
        return sorted(
            files,
            key=lambda item: (
                PROSPECTUS_STAGE_PRIORITY.get(item.bucket, 99),
                -_date_sort_key(item.publish_date),
                item.title,
            ),
        )

    def list_newshare_listing_announcement_files(
        self,
        issue_detail: dict[str, Any],
        issue: NewShareIssue,
    ) -> list[DisclosureFile]:
        files = [
            item
            for item in self.list_newshare_disclosure_files(issue_detail, issue)
            if _listing_announcement_priority(item.title) <= 3
        ]
        return sorted(
            files,
            key=lambda item: (
                _listing_announcement_priority(item.title),
                -_date_sort_key(item.publish_date),
                item.title,
            ),
        )

    def list_newshare_issue_announcement_files(
        self,
        issue_detail: dict[str, Any],
        issue: NewShareIssue,
    ) -> list[DisclosureFile]:
        files = [
            item
            for item in self.list_newshare_disclosure_files(issue_detail, issue)
            if _issue_announcement_priority(item.title) <= 3
        ]
        return sorted(
            files,
            key=lambda item: (
                _issue_announcement_priority(item.title),
                -_date_sort_key(item.publish_date),
                item.title,
            ),
        )

    def list_newshare_issue_result_announcement_files(
        self,
        issue_detail: dict[str, Any],
        issue: NewShareIssue,
    ) -> list[DisclosureFile]:
        files = [
            item
            for item in self.list_newshare_disclosure_files(issue_detail, issue)
            if _issue_result_announcement_priority(item.title) <= 3
        ]
        return sorted(
            files,
            key=lambda item: (
                _issue_result_announcement_priority(item.title),
                -_date_sort_key(item.publish_date),
                item.title,
            ),
        )

    def list_prospectus_files(
        self,
        project_detail: dict[str, Any] | None = None,
        project_id: int | str | None = None,
    ) -> list[DisclosureFile]:
        detail = project_detail or self.get_project_detail(project_id or 0)
        xxgk_info = detail.get("xxgkInfo") or {}
        gpfxsms = xxgk_info.get("GPFXSMS") or {}
        if not isinstance(gpfxsms, dict):
            return []

        files: list[DisclosureFile] = []
        for bucket, bucket_label in PROSPECTUS_STAGE_LABELS.items():
            items = gpfxsms.get(bucket) or []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                relative_path = str(item.get("destFilePath") or "").strip()
                if not relative_path:
                    continue
                title = str(item.get("fileTitle") or item.get("title") or item.get("fileName") or "").strip()
                if not title:
                    title = f"招股说明书（{bucket_label}）"
                suffix = Path(urllib.parse.urlparse(relative_path).path).suffix or ".pdf"
                files.append(
                    DisclosureFile(
                        title=title,
                        publish_date=_normalize_date_text(item.get("publishDate") or item.get("date")),
                        relative_path=relative_path,
                        full_url=_absolute_url(relative_path),
                        bucket=bucket,
                        bucket_label=bucket_label,
                        document_type="招股说明书",
                        file_ext=suffix,
                        detail_url="",
                        source="bse",
                    )
                )

        return sorted(
            files,
            key=lambda item: (
                PROSPECTUS_STAGE_PRIORITY.get(item.bucket, 99),
                -_date_sort_key(item.publish_date),
                item.title,
            ),
        )

    def pick_best_prospectus_file(
        self,
        files: list[DisclosureFile],
        preferred_kind: str = "",
    ) -> DisclosureFile:
        if not files:
            raise BSEOfficialError("北交所项目详情中未找到招股说明书 PDF")
        normalized_preference = str(preferred_kind or "").strip().lower()
        candidates = list(files)
        if normalized_preference in {"intent", "prospectus"}:
            preferred_files = [
                item for item in candidates if _prospectus_kind_from_title(item.title) == normalized_preference
            ]
            if preferred_files:
                candidates = preferred_files
        return sorted(
            candidates,
            key=lambda item: (
                PROSPECTUS_STAGE_PRIORITY.get(item.bucket, 99),
                -_date_sort_key(item.publish_date),
                item.title,
            ),
        )[0]

    def pick_prospectus_files_by_kind(self, files: list[DisclosureFile]) -> list[DisclosureFile]:
        if not files:
            return []
        selected: list[DisclosureFile] = []
        for kind in ("intent", "prospectus"):
            kind_files = [item for item in files if _prospectus_kind_from_title(item.title) == kind]
            if kind_files:
                selected.append(self.pick_best_prospectus_file(kind_files))
        if selected:
            return selected
        return [self.pick_best_prospectus_file(files)]

    def resolve_prospectus_documents_by_post_listing_code(self, code: str) -> list[ProspectusResolution]:
        newshare_error = ""
        try:
            issue = self.resolve_newshare_issue_by_post_listing_code(code)
            issue_detail = self.get_newshare_issue_detail(issue.issue_id)
            files = self.list_newshare_prospectus_files(issue_detail, issue)
            selected_files = self.pick_prospectus_files_by_kind(files)
            if selected_files:
                mapping = self._mapping_from_newshare_issue(issue)
                return [ProspectusResolution(mapping=mapping, disclosure=item) for item in selected_files]
        except BSEOfficialError as exc:
            newshare_error = str(exc)

        project_error = ""
        try:
            mapping = self.resolve_project_from_post_listing_code(code)
            project_detail = self.get_project_detail(mapping.project.project_id)
            files = self.list_prospectus_files(project_detail=project_detail)
            selected_files = self.pick_prospectus_files_by_kind(files)
            if selected_files:
                return [ProspectusResolution(mapping=mapping, disclosure=item) for item in selected_files]
        except BSEOfficialError as exc:
            project_error = str(exc)

        if newshare_error and project_error:
            raise BSEOfficialError(
                f"newshare path and project path both failed: newshare={newshare_error}; project={project_error}"
            )
        if newshare_error:
            raise BSEOfficialError(newshare_error)
        if project_error:
            raise BSEOfficialError(project_error)
        raise BSEOfficialError("官网未找到招股意向书或招股说明书 PDF")

    def resolve_prospectus_by_post_listing_code(
        self,
        code: str,
        preferred_kind: str = "",
    ) -> ProspectusResolution:
        resolutions = self.resolve_prospectus_documents_by_post_listing_code(code)
        normalized_preference = str(preferred_kind or "").strip().lower()
        if normalized_preference in {"intent", "prospectus"}:
            preferred = [
                item
                for item in resolutions
                if _prospectus_kind_from_title(item.disclosure.title) == normalized_preference
            ]
            if preferred:
                return preferred[0]
        return self.pick_best_prospectus_resolution(resolutions)

    def pick_best_prospectus_resolution(
        self,
        resolutions: list[ProspectusResolution],
    ) -> ProspectusResolution:
        if not resolutions:
            raise BSEOfficialError("北交所项目详情中未找到招股文件 PDF")
        return sorted(
            resolutions,
            key=lambda item: (
                PROSPECTUS_STAGE_PRIORITY.get(item.disclosure.bucket, 99),
                -_date_sort_key(item.disclosure.publish_date),
                item.disclosure.title,
            ),
        )[0]

    @staticmethod
    def _coerce_company_announcement_records(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            records: list[dict[str, Any]] = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                list_info = item.get("listInfo") or {}
                content = list_info.get("content") or []
                if isinstance(content, list):
                    records.extend(entry for entry in content if isinstance(entry, dict))
            return records
        if isinstance(payload, dict):
            list_info = payload.get("listInfo") or {}
            content = list_info.get("content") or []
            if isinstance(content, list):
                return [entry for entry in content if isinstance(entry, dict)]
        return []

    def list_company_announcements(
        self,
        company_code: str,
        keyword: str = "",
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 0,
    ) -> list[DisclosureFile]:
        normalized_code = _normalize_stock_code(company_code)
        today = date.today()
        payload = self._request_payload(
            "/disclosureInfoController/companyAnnouncement.do",
            params={
                "page": page,
                "companyCd": normalized_code,
                "isNewThree": 1,
                "startTime": _normalize_date_text(start_date) or (today - timedelta(days=31)).isoformat(),
                "endTime": _normalize_date_text(end_date) or today.isoformat(),
                "keyword": keyword,
                "xxfcbj": ["2"],
                "needFields": [
                    "companyCd",
                    "companyName",
                    "disclosureTitle",
                    "disclosurePostTitle",
                    "destFilePath",
                    "publishDate",
                    "xxfcbj",
                    "fileExt",
                    "xxzrlx",
                ],
            },
            referer=f"/disclosure/announcement.html?companyCode={normalized_code}&typename=Z&xxfcbj=2",
            warmup_urls=(f"/disclosure/announcement.html?companyCode={normalized_code}&typename=Z&xxfcbj=2",),
        )
        records = self._coerce_company_announcement_records(payload)
        files: list[DisclosureFile] = []
        for item in records:
            relative_path = str(item.get("destFilePath") or "").strip()
            if not relative_path:
                continue
            title = f"{str(item.get('disclosureTitle') or '').strip()}{str(item.get('disclosurePostTitle') or '').strip()}".strip()
            suffix = str(item.get("fileExt") or "").strip()
            if suffix and not suffix.startswith("."):
                suffix = f".{suffix}"
            if not suffix:
                suffix = Path(urllib.parse.urlparse(relative_path).path).suffix or ".pdf"
            files.append(
                DisclosureFile(
                    title=title,
                    publish_date=_normalize_date_text(item.get("publishDate")),
                    relative_path=relative_path,
                    full_url=_absolute_url(relative_path),
                    bucket="listing_announcement",
                    bucket_label="上市公告书",
                    document_type="上市公告书",
                    file_ext=suffix,
                    detail_url="",
                    source="bse",
                )
            )
        return files

    def list_bse_listing_announcement_files(
        self,
        listed_company: ListedCompanyProfile,
    ) -> list[DisclosureFile]:
        listing_date = _normalize_date_text(listed_company.listing_date)
        begin = ""
        end = ""
        if listing_date:
            try:
                listing_dt = date.fromisoformat(listing_date)
                begin = (listing_dt - timedelta(days=20)).isoformat()
                end = (listing_dt + timedelta(days=20)).isoformat()
            except ValueError:
                begin = ""
                end = ""

        candidates: list[DisclosureFile] = []
        for keyword in ("", "上市公告书", "上市公告"):
            try:
                candidates.extend(
                    self.list_company_announcements(
                        listed_company.post_listing_code,
                        keyword=keyword,
                        start_date=begin,
                        end_date=end,
                        page=0,
                    )
                )
            except BSEOfficialError:
                continue

        deduped: dict[str, DisclosureFile] = {}
        for item in candidates:
            if _listing_announcement_priority(item.title) > 3:
                continue
            deduped[item.full_url] = item
        return sorted(
            deduped.values(),
            key=lambda item: (
                _listing_announcement_priority(item.title),
                -_date_sort_key(item.publish_date),
                item.title,
            ),
        )

    def search_eastmoney_notices(
        self,
        security_code: str,
        content: str = "",
        begin_date: str | None = None,
        end_date: str | None = None,
        page_index: int = 1,
    ) -> list[dict[str, Any]]:
        payload = self._request_json(
            EASTMONEY_NOTICE_LIST_URL,
            params={
                "page_index": page_index,
                "type": 0,
                "begin": _normalize_date_text(begin_date),
                "end": _normalize_date_text(end_date),
                "securitycodes": str(security_code or "").strip(),
                "content": content,
                "sortRule": 1,
            },
            referer=f"{EASTMONEY_NOTICE_BASE_URL}/Article/NoticeList",
            headers=EASTMONEY_HEADERS,
        )
        result = payload.get("result") or []
        return [item for item in result if isinstance(item, dict)]

    def _resolve_eastmoney_notice_pdf_url(self, art_code: str) -> str:
        normalized_art_code = str(art_code or "").strip()
        if not normalized_art_code:
            raise BSEOfficialError("东方财富公告缺少 art_code")
        detail_url = f"{EASTMONEY_NOTICE_BASE_URL}/Article/NoticeContent?id={normalized_art_code}"
        try:
            html = self._fetch_text_once(
                detail_url,
                referer=f"{EASTMONEY_NOTICE_BASE_URL}/Article/NoticeList",
                headers=EASTMONEY_HEADERS,
            )
        except BSEOfficialError:
            return f"https://pdf.dfcfw.com/pdf/H2_{normalized_art_code}_1.pdf"
        match = EASTMONEY_PDF_LINK_PATTERN.search(html)
        if match:
            return match.group("url")
        return f"https://pdf.dfcfw.com/pdf/H2_{normalized_art_code}_1.pdf"

    def list_eastmoney_listing_announcement_files(
        self,
        mapping: PostListingCodeMapping,
    ) -> list[DisclosureFile]:
        listing_date = _normalize_date_text(mapping.listed_company.listing_date)
        begin = ""
        end = ""
        if listing_date:
            try:
                listing_dt = date.fromisoformat(listing_date)
                begin = (listing_dt - timedelta(days=20)).isoformat()
                end = (listing_dt + timedelta(days=20)).isoformat()
            except ValueError:
                begin = ""
                end = ""

        candidates: list[dict[str, Any]] = []
        for keyword in ("", "上市公告书", "上市公告"):
            try:
                candidates.extend(
                    self.search_eastmoney_notices(
                        mapping.project.pre_listing_code,
                        content=keyword,
                        begin_date=begin,
                        end_date=end,
                        page_index=1,
                    )
                )
            except BSEOfficialError:
                continue

        files: list[DisclosureFile] = []
        seen_art_codes: set[str] = set()
        for item in candidates:
            art_code = str(item.get("art_code") or "").strip()
            if not art_code or art_code in seen_art_codes:
                continue
            seen_art_codes.add(art_code)
            title = str(item.get("title") or item.get("title_ch") or "").strip()
            if _listing_announcement_priority(title) > 3:
                continue
            notice_page = f"{EASTMONEY_NOTICE_BASE_URL}/Article/NoticeContent?id={art_code}"
            pdf_url = self._resolve_eastmoney_notice_pdf_url(art_code)
            files.append(
                DisclosureFile(
                    title=title,
                    publish_date=_normalize_date_text(item.get("notice_date")),
                    relative_path="",
                    full_url=pdf_url,
                    bucket="listing_announcement",
                    bucket_label="上市公告书",
                    document_type="上市公告书",
                    file_ext=".pdf",
                    detail_url=notice_page,
                    source="eastmoney",
                )
            )

        return sorted(
            files,
            key=lambda item: (
                _listing_announcement_priority(item.title),
                -_date_sort_key(item.publish_date),
                item.title,
            ),
        )

    def pick_best_listing_announcement_file(self, files: list[DisclosureFile]) -> DisclosureFile:
        if not files:
            raise BSEOfficialError("未找到上市公告书 PDF")
        return sorted(
            files,
            key=lambda item: (
                _listing_announcement_priority(item.title),
                -_date_sort_key(item.publish_date),
                item.title,
            ),
        )[0]

    def pick_best_issue_announcement_file(self, files: list[DisclosureFile]) -> DisclosureFile:
        if not files:
            raise BSEOfficialError("未找到发行公告 PDF")
        return sorted(
            files,
            key=lambda item: (
                _issue_announcement_priority(item.title),
                -_date_sort_key(item.publish_date),
                item.title,
            ),
        )[0]

    def pick_best_issue_result_announcement_file(self, files: list[DisclosureFile]) -> DisclosureFile:
        if not files:
            raise BSEOfficialError("未找到发行结果公告 PDF")
        return sorted(
            files,
            key=lambda item: (
                _issue_result_announcement_priority(item.title),
                -_date_sort_key(item.publish_date),
                item.title,
            ),
        )[0]

    def resolve_listing_announcement_from_newshare_by_post_listing_code(
        self,
        code: str,
    ) -> ListingAnnouncementResolution:
        issue = self.resolve_newshare_issue_by_post_listing_code(code)
        mapping = self._mapping_from_newshare_issue(issue)
        issue_detail = self.get_newshare_issue_detail(issue.issue_id)
        issue_files = self.list_newshare_listing_announcement_files(issue_detail, issue)
        if not issue_files:
            raise BSEOfficialError("公开发行一览未找到上市公告书")
        return ListingAnnouncementResolution(
            mapping=mapping,
            disclosure=self.pick_best_listing_announcement_file(issue_files),
        )

    def resolve_issue_announcement_from_newshare_by_post_listing_code(
        self,
        code: str,
    ) -> IssueAnnouncementResolution:
        issue = self.resolve_newshare_issue_by_post_listing_code(code)
        mapping = self._mapping_from_newshare_issue(issue)
        issue_detail = self.get_newshare_issue_detail(issue.issue_id)
        issue_files = self.list_newshare_issue_announcement_files(issue_detail, issue)
        if not issue_files:
            raise BSEOfficialError("公开发行一览未找到发行公告")
        return IssueAnnouncementResolution(
            mapping=mapping,
            disclosure=self.pick_best_issue_announcement_file(issue_files),
        )

    def resolve_issue_result_announcement_from_newshare_by_post_listing_code(
        self,
        code: str,
    ) -> IssueResultAnnouncementResolution:
        issue = self.resolve_newshare_issue_by_post_listing_code(code)
        mapping = self._mapping_from_newshare_issue(issue)
        issue_detail = self.get_newshare_issue_detail(issue.issue_id)
        issue_files = self.list_newshare_issue_result_announcement_files(issue_detail, issue)
        if not issue_files:
            raise BSEOfficialError("公开发行一览未找到发行结果公告")
        return IssueResultAnnouncementResolution(
            mapping=mapping,
            disclosure=self.pick_best_issue_result_announcement_file(issue_files),
        )

    def resolve_issue_announcement_by_post_listing_code(self, code: str) -> IssueAnnouncementResolution:
        return self.resolve_issue_announcement_from_newshare_by_post_listing_code(code)

    def resolve_issue_result_announcement_by_post_listing_code(self, code: str) -> IssueResultAnnouncementResolution:
        return self.resolve_issue_result_announcement_from_newshare_by_post_listing_code(code)

    def resolve_listing_announcement_by_post_listing_code(self, code: str) -> ListingAnnouncementResolution:
        mapping: PostListingCodeMapping | None = None
        newshare_error = ""
        try:
            resolution = self.resolve_listing_announcement_from_newshare_by_post_listing_code(code)
            mapping = resolution.mapping
            return resolution
        except BSEOfficialError as exc:
            newshare_error = str(exc)

        official_error = ""
        official_files: list[DisclosureFile] = []
        try:
            if mapping is None:
                mapping = self.resolve_project_from_post_listing_code(code)
            official_files = self.list_bse_listing_announcement_files(mapping.listed_company)
        except BSEOfficialError as exc:
            official_error = str(exc)
        if mapping is not None and official_files:
            return ListingAnnouncementResolution(
                mapping=mapping,
                disclosure=self.pick_best_listing_announcement_file(official_files),
            )

        if mapping is not None:
            eastmoney_files = self.list_eastmoney_listing_announcement_files(mapping)
            if eastmoney_files:
                return ListingAnnouncementResolution(
                    mapping=mapping,
                    disclosure=self.pick_best_listing_announcement_file(eastmoney_files),
                )

        if newshare_error and official_error:
            raise BSEOfficialError(
                f"newshare path and official listing path both failed: newshare={newshare_error}; official={official_error}"
            )
        if newshare_error:
            raise BSEOfficialError(
                f"公开发行一览路径未命中，官网公告接口与东方财富兜底也未命中。newshare 错误：{newshare_error}"
            )
        if official_error:
            raise BSEOfficialError(f"官网未找到上市公告书，且东方财富兜底也未命中。官网错误：{official_error}")
        raise BSEOfficialError("官网与东方财富均未找到上市公告书")

    def build_prospectus_filename(self, resolution: ProspectusResolution) -> str:
        company = resolution.mapping.listed_company
        disclosure = resolution.disclosure
        title = _sanitize_filename_component(_strip_prefix(disclosure.title, f"{company.short_name}:"))
        suffix = disclosure.file_ext or ".pdf"
        if not suffix.startswith("."):
            suffix = f".{suffix}"
        return f"{company.post_listing_code}_{_sanitize_filename_component(company.short_name)}_{title}{suffix}"

    def build_listing_announcement_filename(self, resolution: ListingAnnouncementResolution) -> str:
        company = resolution.mapping.listed_company
        suffix = resolution.disclosure.file_ext or ".pdf"
        if not suffix.startswith("."):
            suffix = f".{suffix}"
        return f"{company.post_listing_code}_{_sanitize_filename_component(company.short_name)}_上市公告书{suffix}"

    def build_issue_announcement_filename(self, resolution: IssueAnnouncementResolution) -> str:
        company = resolution.mapping.listed_company
        suffix = resolution.disclosure.file_ext or ".pdf"
        if not suffix.startswith("."):
            suffix = f".{suffix}"
        return f"{company.post_listing_code}_{_sanitize_filename_component(company.short_name)}_发行公告{suffix}"

    def build_issue_result_announcement_filename(self, resolution: IssueResultAnnouncementResolution) -> str:
        company = resolution.mapping.listed_company
        suffix = resolution.disclosure.file_ext or ".pdf"
        if not suffix.startswith("."):
            suffix = f".{suffix}"
        return f"{company.post_listing_code}_{_sanitize_filename_component(company.short_name)}_发行结果公告{suffix}"

    def download_disclosure_file(
        self,
        disclosure: DisclosureFile,
        output_path: str | Path,
        overwrite: bool = False,
    ) -> Path:
        target_path = Path(output_path)
        if target_path.exists() and not overwrite:
            if is_complete_pdf_file(target_path):
                return target_path.resolve()
            self._notify_status(f"本地 PDF 不完整，正在重新下载：{target_path.name}")

        referer = "/audit/project_news.html"
        headers: dict[str, str] | None = None
        if disclosure.source == "eastmoney":
            referer = disclosure.detail_url or f"{EASTMONEY_NOTICE_BASE_URL}/Article/NoticeList"
            headers = EASTMONEY_HEADERS
        elif disclosure.source == "bse_newshare":
            referer = disclosure.detail_url or "/newshare/listofissues.html"
        self._notify_status(f"正在下载官网 PDF：{disclosure.title}")
        binary = self._run_with_periodic_status(
            lambda: self._download_binary(disclosure.full_url, referer=referer, headers=headers),
            "官网 PDF 仍在下载，请稍候...",
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _validate_pdf_binary(binary)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target_path.parent,
                prefix=f".{target_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as file_obj:
                temp_path = Path(file_obj.name)
                file_obj.write(binary)
            temp_path.replace(target_path)
        finally:
            if temp_path is not None and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
        return target_path.resolve()

    def download_best_prospectus_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
        preferred_kind: str = "",
    ) -> tuple[ProspectusResolution, Path]:
        resolution = self.resolve_prospectus_by_post_listing_code(code, preferred_kind=preferred_kind)
        document_label = "招股意向书" if _prospectus_kind_from_title(resolution.disclosure.title) == "intent" else "招股说明书"
        self._notify_status(
            f"已定位{document_label}：{resolution.mapping.listed_company.post_listing_code} "
            f"{resolution.mapping.listed_company.short_name} / {resolution.disclosure.title}"
        )
        output_path = Path(output_dir) / self.build_prospectus_filename(resolution)
        downloaded_path = self.download_disclosure_file(
            resolution.disclosure,
            output_path,
            overwrite=overwrite,
        )
        return resolution, downloaded_path

    def download_prospectus_documents_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> list[tuple[ProspectusResolution, Path]]:
        results: list[tuple[ProspectusResolution, Path]] = []
        for resolution in self.resolve_prospectus_documents_by_post_listing_code(code):
            document_label = (
                "招股意向书"
                if _prospectus_kind_from_title(resolution.disclosure.title) == "intent"
                else "招股说明书"
            )
            self._notify_status(
                f"已定位{document_label}：{resolution.mapping.listed_company.post_listing_code} "
                f"{resolution.mapping.listed_company.short_name} / {resolution.disclosure.title}"
            )
            output_path = Path(output_dir) / self.build_prospectus_filename(resolution)
            downloaded_path = self.download_disclosure_file(
                resolution.disclosure,
                output_path,
                overwrite=overwrite,
            )
            results.append((resolution, downloaded_path))
        return results

    def download_listing_announcement_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> tuple[ListingAnnouncementResolution, Path]:
        resolution = self.resolve_listing_announcement_by_post_listing_code(code)
        self._notify_status(
            f"已定位上市公告书：{resolution.mapping.listed_company.post_listing_code} "
            f"{resolution.mapping.listed_company.short_name} / {resolution.disclosure.title}"
        )
        output_path = Path(output_dir) / self.build_listing_announcement_filename(resolution)
        downloaded_path = self.download_disclosure_file(
            resolution.disclosure,
            output_path,
            overwrite=overwrite,
        )
        return resolution, downloaded_path

    def download_issue_announcement_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> tuple[IssueAnnouncementResolution, Path]:
        return self.download_issue_announcement_from_newshare_by_post_listing_code(
            code,
            output_dir,
            overwrite=overwrite,
        )

    def download_issue_result_announcement_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> tuple[IssueResultAnnouncementResolution, Path]:
        return self.download_issue_result_announcement_from_newshare_by_post_listing_code(
            code,
            output_dir,
            overwrite=overwrite,
        )

    def download_issue_announcement_from_newshare_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> tuple[IssueAnnouncementResolution, Path]:
        resolution = self.resolve_issue_announcement_from_newshare_by_post_listing_code(code)
        self._notify_status(
            f"已定位发行公告：{resolution.mapping.listed_company.post_listing_code} "
            f"{resolution.mapping.listed_company.short_name} / {resolution.disclosure.title}"
        )
        output_path = Path(output_dir) / self.build_issue_announcement_filename(resolution)
        downloaded_path = self.download_disclosure_file(
            resolution.disclosure,
            output_path,
            overwrite=overwrite,
        )
        return resolution, downloaded_path

    def download_issue_result_announcement_from_newshare_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> tuple[IssueResultAnnouncementResolution, Path]:
        resolution = self.resolve_issue_result_announcement_from_newshare_by_post_listing_code(code)
        self._notify_status(
            f"已定位发行结果公告：{resolution.mapping.listed_company.post_listing_code} "
            f"{resolution.mapping.listed_company.short_name} / {resolution.disclosure.title}"
        )
        output_path = Path(output_dir) / self.build_issue_result_announcement_filename(resolution)
        downloaded_path = self.download_disclosure_file(
            resolution.disclosure,
            output_path,
            overwrite=overwrite,
        )
        return resolution, downloaded_path

    def download_listing_announcement_from_newshare_by_post_listing_code(
        self,
        code: str,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> tuple[ListingAnnouncementResolution, Path]:
        resolution = self.resolve_listing_announcement_from_newshare_by_post_listing_code(code)
        self._notify_status(
            f"已定位上市公告书：{resolution.mapping.listed_company.post_listing_code} "
            f"{resolution.mapping.listed_company.short_name} / {resolution.disclosure.title}"
        )
        output_path = Path(output_dir) / self.build_listing_announcement_filename(resolution)
        downloaded_path = self.download_disclosure_file(
            resolution.disclosure,
            output_path,
            overwrite=overwrite,
        )
        return resolution, downloaded_path
