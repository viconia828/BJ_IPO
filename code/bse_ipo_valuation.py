from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import re
import sys
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterator

import bse_official_helper
import config_loader
import comparable_data_helper
import data_fetcher
import ipo_data_helper
import note_builder
import pdf_parser
import report_generator
import valuation_engine
from industry_mapping import IndustryMapper


CODE_PATTERN = re.compile(r"^\d{6}$")
ROOT_DIR = Path(__file__).resolve().parents[1]


class RequiredProspectusNotFoundError(RuntimeError):
    """Raised when no usable prospectus is available after official probing."""


@contextmanager
def _progress_heartbeat(
    progress_callback: Callable[[str], None] | None,
    *,
    interval_seconds: float = 30.0,
) -> Iterator[Callable[[str], None] | None]:
    if progress_callback is None or interval_seconds <= 0:
        yield progress_callback
        return

    stop_event = threading.Event()
    start_time = time.monotonic()
    last_progress_time = start_time
    last_progress_lock = threading.Lock()

    def emit_progress(message: str) -> None:
        nonlocal last_progress_time
        with last_progress_lock:
            last_progress_time = time.monotonic()
        progress_callback(message)

    def emit_heartbeat() -> None:
        nonlocal last_progress_time
        while not stop_event.wait(interval_seconds):
            now = time.monotonic()
            with last_progress_lock:
                silence_seconds = now - last_progress_time
                if silence_seconds < interval_seconds:
                    continue
                last_progress_time = now
            elapsed_seconds = int(now - start_time)
            try:
                progress_callback(f"报告生成仍在进行，请稍候（已运行约 {elapsed_seconds} 秒）。")
            except Exception:
                return

    heartbeat_thread = threading.Thread(target=emit_heartbeat, name="report-progress-heartbeat", daemon=True)
    heartbeat_thread.start()
    try:
        yield emit_progress
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=1.0)


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _find_pdf(directory: Path, code: str, suffix: str) -> Path | None:
    candidates = _find_pdf_candidates(directory, code, suffix)
    if candidates:
        return candidates[0]
    return None


def _find_pdf_candidates(directory: Path, code: str, suffix: str) -> list[Path]:
    candidate = directory / f"{code}_{suffix}.pdf"
    if candidate.exists() and bse_official_helper.is_complete_pdf_file(candidate):
        return [candidate]

    aliases = {
        "上市公告书": ["上市公告书", "上市公告"],
        "发行公告": ["上市发行公告", "发行公告"],
        "招股说明书摘要": ["招股说明书摘要", "招股说明书", "招股书摘要", "招股书", "招股意向书摘要", "招股意向书"],
    }
    keywords = aliases.get(suffix, [suffix])
    other_keywords = [item for key, values in aliases.items() if key != suffix for item in values]
    excluded_keywords = {
        "发行公告": ["发行结果", "结果公告", "上市公告书", "招股说明书", "招股意向书"],
    }.get(suffix, [])
    if not directory.exists():
        return []

    pdf_files = sorted(directory.glob("*.pdf"))
    prioritized: list[Path] = []
    fallback: list[Path] = []
    for file_path in pdf_files:
        if not bse_official_helper.is_complete_pdf_file(file_path):
            continue
        stem = file_path.stem
        if code not in stem:
            continue
        if any(keyword in stem for keyword in excluded_keywords):
            continue
        if any(keyword in stem for keyword in keywords):
            prioritized.append(file_path)
        elif not any(keyword in stem for keyword in other_keywords):
            fallback.append(file_path)

    if suffix == "发行公告":
        return prioritized
    return prioritized + fallback


def _pick_prospectus_pdf(directory: Path, code: str, usage: str) -> Path | None:
    candidates = _find_pdf_candidates(directory, code, "招股说明书摘要")
    if not candidates:
        return None

    def rank(file_path: Path) -> tuple[int, int, str]:
        stem = file_path.stem
        is_summary = "摘要" in stem
        if usage == "business":
            return (0 if is_summary else 1, 0 if "招股说明书" in stem else 1, stem)
        return (0 if not is_summary else 1, 0 if "招股说明书" in stem else 1, stem)

    return sorted(candidates, key=rank)[0]


def _build_old_shares_pending_reason(
    listing_pdf: Path | None,
    prospectus_pdf: Path | None,
) -> str:
    listing_found = listing_pdf is not None
    prospectus_found = prospectus_pdf is not None
    if listing_found and prospectus_found:
        return "上市公告书与招股文件均未提取到有效老股数据"
    if listing_found and not prospectus_found:
        return "上市公告书未提取到有效老股数据，且未找到可用招股文件"
    if not listing_found and prospectus_found:
        return "未找到上市公告书，且招股文件未提取到有效老股数据"
    return "未找到上市公告书，且未找到可用招股文件"


def _resolve_old_shares(
    params: dict[str, Any],
    listing_pdf: Path | None,
    prospectus_pdf: Path | None = None,
) -> tuple[float, str, dict[str, Any] | None]:
    raw_value = params.get("old_shares_transfer", "auto")
    if raw_value == "auto":
        extracted_result = None
        selected_label = None
        for file_path, label in ((listing_pdf, "上市公告书"), (prospectus_pdf, "招股文件")):
            if not file_path:
                continue
            extracted_result = pdf_parser.extract_old_shares_result(file_path)
            if extracted_result is not None:
                selected_label = label
                break
        if extracted_result is None:
            pending_reason = _build_old_shares_pending_reason(listing_pdf, prospectus_pdf)
            return 0.0, "待确认（当前按 0 万股计）", {
                "value_wan_shares": None,
                "source_file_type": "待确认",
                "source_rule": "pending",
                "source_anchor": "",
                "raw_snippet": "",
                "confidence": 0.0,
                "unit": "万股",
                "pre_unrestricted_wan_shares": None,
                "listing_pdf_found": listing_pdf is not None,
                "prospectus_pdf_found": prospectus_pdf is not None,
                "selected_source_label": "",
                "fallback_used": False,
                "pending_reason": pending_reason,
            }
        extraction_meta = asdict(extracted_result)
        extraction_meta.update(
            {
                "listing_pdf_found": listing_pdf is not None,
                "prospectus_pdf_found": prospectus_pdf is not None,
                "selected_source_label": selected_label or extracted_result.source_file_type,
                "fallback_used": bool(selected_label == "招股文件" and listing_pdf is not None),
            }
        )
        return (
            extracted_result.value_wan_shares,
            f"{extracted_result.value_wan_shares:.2f} 万股（PDF 提取：{extracted_result.source_file_type}）",
            extraction_meta,
        )

    numeric = float(raw_value)
    return numeric, f"{numeric:.2f} 万股（参数指定）", {
        "value_wan_shares": numeric,
        "source_file_type": "参数指定",
        "source_rule": "manual",
        "source_anchor": "old_shares_transfer",
        "raw_snippet": "",
        "confidence": 1.0,
        "unit": "万股",
        "pre_unrestricted_wan_shares": numeric,
        "listing_pdf_found": listing_pdf is not None,
        "prospectus_pdf_found": prospectus_pdf is not None,
        "selected_source_label": "参数指定",
        "fallback_used": False,
        "pending_reason": "",
    }


def _load_comparable_codes(params: dict[str, Any], prospectus_pdf: Path | None) -> list[str]:
    codes = params.get("comparable_companies") or []
    if codes:
        return list(codes)
    if prospectus_pdf:
        return pdf_parser.extract_comparable_companies(prospectus_pdf)
    return []


ISSUE_DOCUMENT_SUPPLEMENT_FIELDS = (
    "PRICE_WAY",
    "APPLY_DATE",
    "ISSUE_PRICE",
    "AFTER_ISSUE_PE",
    "INDUSTRY_PE_NEW",
    "TOTAL_ISSUE_NUM",
    "TOP_APPLY_MARKETCAP",
    "INDUSTRY",
    "INDUSTRY_CODE",
    "TOTAL_SHARE_CAPITAL_AFTER_ISSUE",
    "SUBSCRIPTION_LIMIT_WAN_SHARES",
)
PROSPECTUS_SUPPLEMENT_FIELDS = ISSUE_DOCUMENT_SUPPLEMENT_FIELDS


def _value_is_missing(value: Any) -> bool:
    return value in (None, "", "--")


def _apply_issue_document_info(
    ipo_info: dict[str, Any],
    summary: dict[str, Any],
    issue_info: dict[str, Any],
    document_pdf: Path | None = None,
    *,
    source_prefix: str,
    supplement_used_key: str,
    supplemented_fields_key: str,
    info_file_key: str,
    field_sources_key: str,
) -> list[str]:
    fields = dict(issue_info.get("fields") or {}) if isinstance(issue_info, dict) else {}
    if not fields:
        return []

    limit_wan_shares = _safe_float(fields.get("SUBSCRIPTION_LIMIT_WAN_SHARES"))
    issue_price = _safe_float(fields.get("ISSUE_PRICE")) or _safe_float(ipo_info.get("ISSUE_PRICE"))
    if (
        _value_is_missing(ipo_info.get("TOP_APPLY_MARKETCAP"))
        and _value_is_missing(fields.get("TOP_APPLY_MARKETCAP"))
        and limit_wan_shares is not None
        and limit_wan_shares > 0
        and issue_price is not None
        and issue_price > 0
    ):
        fields["TOP_APPLY_MARKETCAP"] = limit_wan_shares * issue_price
        field_sources = dict(issue_info.get("field_sources") or {})
        field_sources["TOP_APPLY_MARKETCAP"] = field_sources.get(
            "SUBSCRIPTION_LIMIT_WAN_SHARES",
            f"{source_prefix}:subscription_limit",
        )
        issue_info = dict(issue_info)
        issue_info["field_sources"] = field_sources

    applied: list[str] = []
    for field_name in ISSUE_DOCUMENT_SUPPLEMENT_FIELDS:
        value = fields.get(field_name)
        if _value_is_missing(value) or not _value_is_missing(ipo_info.get(field_name)):
            continue
        ipo_info[field_name] = value
        applied.append(field_name)

    if not applied:
        return []

    existing_fields = list(summary.get(supplemented_fields_key) or [])
    for field_name in applied:
        if field_name not in existing_fields:
            existing_fields.append(field_name)
    summary[supplement_used_key] = True
    summary[supplemented_fields_key] = existing_fields
    if document_pdf is not None:
        summary[info_file_key] = document_pdf.name

    field_sources = issue_info.get("field_sources") if isinstance(issue_info, dict) else {}
    if isinstance(field_sources, dict):
        source_bucket = dict(summary.get(field_sources_key) or {})
        for field_name in applied:
            if field_name in field_sources:
                source_bucket[field_name] = field_sources[field_name]
        if source_bucket:
            summary[field_sources_key] = source_bucket

    return applied


def _apply_prospectus_issue_info(
    ipo_info: dict[str, Any],
    summary: dict[str, Any],
    issue_info: dict[str, Any],
    prospectus_pdf: Path | None = None,
) -> list[str]:
    return _apply_issue_document_info(
        ipo_info,
        summary,
        issue_info,
        prospectus_pdf,
        source_prefix="prospectus",
        supplement_used_key="prospectus_supplement_used",
        supplemented_fields_key="prospectus_supplemented_fields",
        info_file_key="prospectus_issue_info_file",
        field_sources_key="prospectus_issue_field_sources",
    )


def _apply_issue_announcement_info(
    ipo_info: dict[str, Any],
    summary: dict[str, Any],
    issue_info: dict[str, Any],
    issue_announcement_pdf: Path | None = None,
) -> list[str]:
    return _apply_issue_document_info(
        ipo_info,
        summary,
        issue_info,
        issue_announcement_pdf,
        source_prefix="issue_announcement",
        supplement_used_key="issue_announcement_supplement_used",
        supplemented_fields_key="issue_announcement_supplemented_fields",
        info_file_key="issue_announcement_info_file",
        field_sources_key="issue_announcement_field_sources",
    )


def _calc_change_pct(issue_price: float | None, target_price: float | None) -> float | None:
    if not issue_price or not target_price:
        return None
    return (target_price / issue_price - 1) * 100


def _ensure_local_prospectus_pdf(
    directory: Path,
    code: str,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[Path | None, str]:
    existing = _pick_prospectus_pdf(directory, code, "old_shares")
    if existing is not None:
        return existing, ""

    if progress_callback is not None:
        progress_callback("招股说明书下载中，请稍候。")

    try:
        client = bse_official_helper.BSEOfficialClient(status_callback=progress_callback)
        _, downloaded_path = client.download_best_prospectus_by_post_listing_code(
            code,
            directory,
            overwrite=False,
        )
    except bse_official_helper.BSEOfficialError as exc:
        return None, str(exc)

    refreshed = _pick_prospectus_pdf(directory, code, "old_shares")
    if refreshed is not None:
        return refreshed, ""
    if downloaded_path.exists():
        return None, f"招股说明书已下载到本地，但未识别为可用文件：{downloaded_path.name}"
    return None, "招股说明书下载后仍未在本地找到可用文件"


def _ensure_local_official_documents(
    directory: Path,
    code: str,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[Path | None, str, Path | None, str, Path | None, str]:
    existing_prospectus = _pick_prospectus_pdf(directory, code, "old_shares")
    existing_issue_announcement = _find_pdf(directory, code, "发行公告")
    existing_listing = _find_pdf(directory, code, "上市公告书")
    if existing_prospectus is not None and existing_issue_announcement is not None and existing_listing is not None:
        return existing_prospectus, "", existing_issue_announcement, "", existing_listing, ""

    if progress_callback is not None:
        pending_labels: list[str] = []
        if existing_prospectus is None:
            pending_labels.append("招股说明书")
        if existing_issue_announcement is None:
            pending_labels.append("发行公告")
        if existing_listing is None:
            pending_labels.append("上市公告书")
        pending_text = "/".join(pending_labels)
        if len(pending_labels) == 1 and pending_labels[0] == "招股说明书":
            progress_callback("招股说明书下载中，请稍候。")
        else:
            progress_callback(f"{pending_text}探测中，请稍候。")

    client = bse_official_helper.BSEOfficialClient(status_callback=progress_callback)
    prospectus_download_error = ""
    issue_announcement_download_error = ""
    listing_download_error = ""
    downloaded_path: Path | None = None

    if existing_prospectus is None:
        try:
            _, downloaded_path = client.download_best_prospectus_by_post_listing_code(
                code,
                directory,
                overwrite=False,
            )
        except bse_official_helper.BSEOfficialError as exc:
            prospectus_download_error = str(exc)

    if existing_issue_announcement is None:
        try:
            _, existing_issue_announcement = client.download_issue_announcement_from_newshare_by_post_listing_code(
                code,
                directory,
                overwrite=False,
            )
        except bse_official_helper.BSEOfficialError as exc:
            issue_announcement_download_error = str(exc)

    if existing_listing is None:
        try:
            _, existing_listing = client.download_listing_announcement_from_newshare_by_post_listing_code(
                code,
                directory,
                overwrite=False,
            )
        except bse_official_helper.BSEOfficialError as exc:
            listing_download_error = str(exc)

    refreshed_prospectus = _pick_prospectus_pdf(directory, code, "old_shares")
    refreshed_issue_announcement = _find_pdf(directory, code, "发行公告")
    refreshed_listing = _find_pdf(directory, code, "上市公告书")
    if refreshed_prospectus is not None:
        return (
            refreshed_prospectus,
            "",
            refreshed_issue_announcement or existing_issue_announcement,
            issue_announcement_download_error,
            refreshed_listing or existing_listing,
            listing_download_error,
        )
    if existing_prospectus is not None:
        return (
            existing_prospectus,
            "",
            refreshed_issue_announcement or existing_issue_announcement,
            issue_announcement_download_error,
            refreshed_listing or existing_listing,
            listing_download_error,
        )
    if not prospectus_download_error and downloaded_path is not None and downloaded_path.exists():
        prospectus_download_error = f"招股说明书已下载到本地，但未识别为可用文件：{downloaded_path.name}"
    if not prospectus_download_error:
        prospectus_download_error = "招股说明书下载后仍未在本地找到可用文件"
    return (
        None,
        prospectus_download_error,
        refreshed_issue_announcement or existing_issue_announcement,
        issue_announcement_download_error,
        refreshed_listing or existing_listing,
        listing_download_error,
    )


def build_analysis_data(
    code: str,
    params: dict[str, Any] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    ipo_data_bundle: dict[str, Any]
    ipo_info: dict[str, Any]
    ipo_data_summary: dict[str, Any]
    params = dict(params) if params is not None else config_loader.load_params(ROOT_DIR / "策略参数.txt")
    mapper = IndustryMapper(params)

    recent_days = config_loader.resolve_recent_days(params)
    recent_months_compat = max((recent_days + 29) // 30, 1)
    ipo_data_bundle = ipo_data_helper.prepare_ipo_data(code, recent_months_compat, params)
    ipo_info = ipo_data_bundle.get("ipo_info") or {}
    ipo_data_summary = ipo_data_bundle.get("summary") or {}

    pdf_dir = ROOT_DIR / "公告文件"
    listing_pdf = _find_pdf(pdf_dir, code, "上市公告书")
    issue_announcement_pdf = _find_pdf(pdf_dir, code, "发行公告")
    old_shares_fallback_pdf = _pick_prospectus_pdf(pdf_dir, code, "old_shares")
    comparable_pdf = _pick_prospectus_pdf(pdf_dir, code, "comparables")
    business_pdf = _pick_prospectus_pdf(pdf_dir, code, "business")
    prospectus_download_error = ""
    issue_announcement_download_error = ""
    listing_download_error = ""
    prospectus_issue_parse_error = ""
    issue_announcement_parse_error = ""
    prospectus_available = any((old_shares_fallback_pdf, comparable_pdf, business_pdf))

    if not prospectus_available or issue_announcement_pdf is None or listing_pdf is None:
        (
            old_shares_fallback_pdf,
            prospectus_download_error,
            issue_announcement_pdf,
            issue_announcement_download_error,
            listing_pdf,
            listing_download_error,
        ) = _ensure_local_official_documents(
            pdf_dir,
            code,
            progress_callback=progress_callback,
        )
        old_shares_fallback_pdf = _pick_prospectus_pdf(pdf_dir, code, "old_shares")
        issue_announcement_pdf = _find_pdf(pdf_dir, code, "发行公告") or issue_announcement_pdf
        listing_pdf = _find_pdf(pdf_dir, code, "上市公告书") or listing_pdf
        comparable_pdf = _pick_prospectus_pdf(pdf_dir, code, "comparables")
        business_pdf = _pick_prospectus_pdf(pdf_dir, code, "business")
        prospectus_available = any((old_shares_fallback_pdf, comparable_pdf, business_pdf))

        if not prospectus_available:
            detail = prospectus_download_error or "官网探测结束后仍未找到可用招股说明书"
            raise RequiredProspectusNotFoundError(f"未取到招股说明书，生成报告失败：{detail}")

        if listing_pdf is None and progress_callback is not None:
            message = "上市公告书未下载，可手动补充；本次将按无上市公告书方式继续生成估值报告。"
            if listing_download_error:
                message = f"{message} 原因：{listing_download_error}"
            progress_callback(message)

    prospectus_issue_pdf = old_shares_fallback_pdf or comparable_pdf or business_pdf
    if prospectus_issue_pdf:
        try:
            prospectus_issue_info = pdf_parser.extract_prospectus_issue_info(prospectus_issue_pdf)
            _apply_prospectus_issue_info(ipo_info, ipo_data_summary, prospectus_issue_info, prospectus_issue_pdf)
        except Exception as exc:
            prospectus_issue_parse_error = str(exc)
            ipo_data_summary["prospectus_issue_parse_error"] = prospectus_issue_parse_error

    if issue_announcement_pdf:
        try:
            issue_announcement_info = pdf_parser.extract_issue_announcement_info(issue_announcement_pdf)
            _apply_issue_announcement_info(ipo_info, ipo_data_summary, issue_announcement_info, issue_announcement_pdf)
        except Exception as exc:
            issue_announcement_parse_error = str(exc)
            ipo_data_summary["issue_announcement_parse_error"] = issue_announcement_parse_error

    industry = mapper.resolve_stock_industry(code, ipo_info)

    old_shares, old_shares_desc, old_shares_meta = _resolve_old_shares(params, listing_pdf, old_shares_fallback_pdf)
    total_issue_num = _safe_float(ipo_info.get("TOTAL_ISSUE_NUM")) or 0.0
    float_shares = total_issue_num + old_shares

    comparable_codes = _load_comparable_codes(params, comparable_pdf)
    comparable_data = []
    comparable_summary = {
        "provider": str(params.get("comparable_data_source", "wind")),
        "channel": str(params.get("wind_channel", "disabled")),
        "requested_codes": list(comparable_codes),
        "returned_codes": [],
        "fixed_cache_hits": [],
        "variable_cache_hits": [],
        "api_fetched_fixed": [],
        "api_fetched_variable": [],
        "stale_variable_used": [],
        "skipped_due_quota": [],
        "skipped_unsupported": [],
        "api_calls": 0,
        "quota_limit": int(params.get("wind_daily_request_quota", 20)),
        "quota_used_today": 0,
        "quota_remaining": int(params.get("wind_daily_request_quota", 20)),
        "local_computed_codes": [],
        "eastmoney_api_calls": 0,
        "eastmoney_fetched": [],
        "eastmoney_cache_hits": [],
        "eastmoney_fallback_used": [],
        "cross_validated_codes": [],
        "cross_validation_warnings": [],
        "reason": "",
    }
    if comparable_codes:
        comparable_result = comparable_data_helper.get_comparable_valuations(comparable_codes, params)
        comparable_data = comparable_result.get("items") or []
        comparable_summary = comparable_result.get("summary") or comparable_summary

    company_description = (
        pdf_parser.extract_business_desc(business_pdf)
        if business_pdf
        else str(ipo_info.get("MAIN_BUSINESS", "") or "")
    )
    if not company_description:
        company_description = str(ipo_info.get("MAIN_BUSINESS", "") or "")

    recent_ipos = mapper.enrich_recent_ipos(ipo_data_bundle.get("recent_ipos") or [])
    recent_ipos = [item for item in recent_ipos if item.get("SECURITY_CODE") != code]

    issue_price = _safe_float(ipo_info.get("ISSUE_PRICE"))
    issue_pe = _safe_float(ipo_info.get("AFTER_ISSUE_PE"))
    industry_pe = _safe_float(ipo_info.get("INDUSTRY_PE_NEW"))

    method1 = valuation_engine.method1_comparable(issue_price, issue_pe, comparable_data, params)
    method2 = valuation_engine.method2_industry_momentum(
        issue_price=issue_price,
        issue_pe=issue_pe,
        industry_pe=industry_pe,
        float_shares=float_shares,
        industry={"primary": industry.primary, "secondary": industry.secondary, "display_name": industry.display_name},
        recent_ipos=recent_ipos,
        params=params,
        target_code=code,
        target_listing_date=ipo_info.get("LISTING_DATE"),
    )
    final = valuation_engine.composite_valuation(method1, method2, params)

    final_change_pct = _calc_change_pct(issue_price, final.get("target_price"))
    range_change_low = _calc_change_pct(issue_price, final.get("range_low"))
    range_change_high = _calc_change_pct(issue_price, final.get("range_high"))

    notes = note_builder.generate_notes(
        {
            "ipo_info": ipo_info,
            "float_shares": float_shares,
            "industry": {"primary": industry.primary, "secondary": industry.secondary},
            "method1": method1,
            "method2": method2,
            "old_shares_desc": old_shares_desc,
            "old_shares_meta": old_shares_meta,
            "comparable_codes": comparable_codes,
            "comparable_summary": comparable_summary,
            "wind_summary": comparable_summary,
            "ipo_data_summary": ipo_data_summary,
        },
        params,
    )

    payload = {
        "analysis_date": date.today().isoformat(),
        "params": params,
        "ipo_info": ipo_info,
        "industry": {
            "primary": industry.primary,
            "secondary": industry.secondary,
            "source": industry.source,
            "display_name": industry.display_name,
        },
        "float_shares": float_shares,
        "old_shares_desc": old_shares_desc,
        "old_shares_meta": old_shares_meta,
        "company_description": company_description,
        "prospectus_download_error": prospectus_download_error,
        "issue_announcement_download_error": issue_announcement_download_error,
        "listing_download_error": listing_download_error,
        "issue_announcement_pdf_found": issue_announcement_pdf is not None,
        "listing_pdf_found": listing_pdf is not None,
        "prospectus_issue_parse_error": prospectus_issue_parse_error,
        "issue_announcement_parse_error": issue_announcement_parse_error,
        "comparable_codes": comparable_codes,
        "comparable_data": comparable_data,
        "comparable_summary": comparable_summary,
        "wind_summary": comparable_summary,
        "ipo_data_summary": ipo_data_summary,
        "recent_ipos": recent_ipos,
        "method1": method1,
        "method2": method2,
        "final": final,
        "final_change_pct": final_change_pct,
        "range_change_low": range_change_low,
        "range_change_high": range_change_high,
        "notes": notes,
    }

    return payload


def run(
    code: str,
    params: dict[str, Any] | None = None,
    output_dir: str | Path | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> str:
    with _progress_heartbeat(progress_callback) as heartbeat_progress_callback:
        payload = build_analysis_data(code, params=params, progress_callback=heartbeat_progress_callback)
        target_output_dir = str(Path(output_dir)) if output_dir is not None else str(ROOT_DIR / "输出")
        return report_generator.generate_report(payload, target_output_dir)


def _prompt_code_interactively() -> str | None:
    while True:
        code = input("请输入 6 位新股代码（例如 920177，直接回车可退出）：").strip()
        if not code:
            print("已取消，本次未生成报告。")
            return None
        if CODE_PATTERN.fullmatch(code):
            return code
        print("输入有误，请输入 6 位数字代码。")


def _normalize_code(code: str) -> str:
    cleaned = str(code or "").strip()
    if not CODE_PATTERN.fullmatch(cleaned):
        raise ValueError("请输入 6 位数字代码，例如 920177。")
    return cleaned


def main() -> int:
    interactive = len(sys.argv) <= 1
    if interactive:
        code = _prompt_code_interactively()
        if code is None:
            return 0
    else:
        code = sys.argv[1].strip()

    try:
        code = _normalize_code(code)
        if interactive:
            print(f"已收到代码 {code}，正在生成报告，请稍候。这一步会读取公告文件并整理估值数据，通常需要几十秒。")
        output_path = run(code, progress_callback=print)
    except FileNotFoundError as exc:
        print(f"文件缺失：{exc}")
        return 1
    except data_fetcher.DataFetcherError as exc:
        print(f"数据获取失败：{exc}")
        return 1
    except RequiredProspectusNotFoundError as exc:
        print(str(exc))
        return 1
    except ValueError as exc:
        print(f"参数或输入错误：{exc}")
        return 1
    except Exception as exc:
        print(f"运行失败：{exc}")
        return 1

    report_path = Path(output_path).resolve()
    overview_path = report_path.with_name(f"{report_path.stem}_一览.txt")
    print(f"报告已生成：{report_path}")
    if overview_path.exists():
        print(f"一览已生成：{overview_path}")
    print(f"报告所在目录：{report_path.parent}")
    if interactive:
        print("可直接打开上面的完整路径，或到“输出”文件夹中查看生成的 PDF 报告。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
