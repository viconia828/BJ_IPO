from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
import time
from typing import Any, Callable


ROOT_DIR = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import bse_ipo_valuation
import bse_official_helper
import pdf_parser
import subscription_ladder_labels
import subscription_predictor


DEFAULT_DATASET_PATH = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_OUTPUT_PATH = ROOT_DIR / "data" / "offline_tuning" / "subscription_history_sample.csv"
DEFAULT_LADDER_LABEL_PATH = ROOT_DIR / "data" / "offline_tuning" / "subscription_ladder_labels.csv"
DEFAULT_PDF_DIR = ROOT_DIR / "\u516c\u544a\u6587\u4ef6"

LISTING_ANNOUNCEMENT = "\u4e0a\u5e02\u516c\u544a\u4e66"
ISSUE_ANNOUNCEMENT = "\u53d1\u884c\u516c\u544a"
ISSUE_RESULT_ANNOUNCEMENT = "\u53d1\u884c\u7ed3\u679c\u516c\u544a"

HistoryProgressCallback = Callable[[dict[str, Any]], None]

TRACKED_FIELDS = (
    "APPLY_DATE",
    "ISSUE_RESULT_DATE",
    "BALLOT_NUM_DATE",
    "LISTING_DATE",
    "ISSUE_PRICE",
    "TOTAL_ISSUE_NUM",
    "ONLINE_ISSUE_NUM",
    "TOP_APPLY_MARKETCAP",
    "SUBSCRIPTION_LIMIT_WAN_SHARES",
    "ONLINE_VA_NUM",
    "ONLINE_ALLOCATED_ACCOUNTS",
    "ONLINE_VA_SHARES",
    "ONLINE_ES_MULTIPLE",
    "ONLINE_ISSUE_LWR",
    "FROZEN_FUNDS_YI",
    "FRACTIONAL_THRESHOLD_SHARES",
    "FRACTIONAL_TIME_PRIORITY_REQUIRED",
    "SUBSCRIPTION_AMOUNT_DISTRIBUTION",
)

CSV_COLUMNS = (
    "security_code",
    "security_name_abbr",
    "apply_date",
    "issue_result_date",
    "listing_date",
    "issue_price",
    "total_issue_num_wan",
    "online_issue_shares",
    "online_issue_source",
    "issue_amount_yi",
    "top_apply_amount_wan",
    "top_apply_source",
    "top_apply_shares",
    "online_valid_accounts",
    "online_allocated_accounts",
    "online_valid_shares",
    "online_valid_source",
    "frozen_funds_yi",
    "allocation_rate_pct",
    "subscription_multiple",
    "lock_days",
    "guaranteed_threshold_shares",
    "guaranteed_threshold_amount_wan",
    "guaranteed_threshold_reachable",
    "top_apply_below_guaranteed",
    "top_apply_gap_shares",
    "top_apply_gap_amount_wan",
    "fractional_threshold_shares",
    "fractional_threshold_amount_wan",
    "fractional_threshold_source",
    "fractional_time_priority_required",
    "time_priority_scope",
    "subscription_distribution_json",
    "distribution_bucket_count",
    "allocation_fit_json",
    "allocation_fit_quality",
    "allocation_fit_confidence",
    "allocation_fit_usable_for_tuning",
    "allocation_fit_residual_json",
    "allocation_fit_bucket_count",
    "unallocated_avg_amount_wan",
    "allocation_fit_ready",
    "model_mode",
    "model_ready",
    "guaranteed_label_ready",
    "fractional_label_ready",
    "data_quality",
    "missing_fields",
    "prospectus_pdf_found",
    "issue_pdf_found",
    "result_pdf_found",
    "listing_pdf_found",
    "prospectus_pdf_file",
    "issue_pdf_file",
    "result_pdf_file",
    "listing_pdf_file",
    "parse_errors",
    "download_errors",
)

def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _is_missing(value: Any) -> bool:
    if value in (None, "", "--"):
        return True
    return False


def _clean_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.split(" ", 1)[0].replace("/", "-")


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text != "-0" else "0"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _emit_history_progress(progress_callback: HistoryProgressCallback | None, **event: Any) -> None:
    if progress_callback is None:
        return
    progress_callback(event)


def _document_label(document: str) -> str:
    return {
        "prospectus": "\u62db\u80a1\u8bf4\u660e\u4e66",
        "issue": ISSUE_ANNOUNCEMENT,
        "result": ISSUE_RESULT_ANNOUNCEMENT,
    }.get(document, document)


def _item_code(item: dict[str, Any]) -> str:
    return str(item.get("SECURITY_CODE") or item.get("code") or "").strip()


def _load_replay_dataset_items(dataset_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    items = payload.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]

    sample_codes = payload.get("sample_codes") or payload.get("requested_codes") or []
    if not isinstance(sample_codes, list):
        return []
    item_dir = dataset_path.parent / "replay_items"
    loaded: list[dict[str, Any]] = []
    for raw_code in sample_codes:
        code = str(raw_code or "").strip()
        if not code:
            continue
        item_path = item_dir / f"{code}.json"
        if not item_path.exists():
            continue
        item_payload = json.loads(item_path.read_text(encoding="utf-8"))
        item = item_payload.get("item")
        if isinstance(item, dict):
            loaded.append(item)
    return loaded


def _row_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return (
        _clean_date(item.get("LISTING_DATE")) or _clean_date(item.get("APPLY_DATE")),
        str(item.get("SECURITY_CODE") or ""),
    )


def _find_local_pdfs(pdf_dir: Path, code: str) -> dict[str, Path | None]:
    prospectus_pdf = bse_ipo_valuation._pick_prospectus_pdf(pdf_dir, code, "old_shares")
    return {
        "prospectus": prospectus_pdf,
        "issue": bse_ipo_valuation._find_pdf(pdf_dir, code, ISSUE_ANNOUNCEMENT),
        "result": bse_ipo_valuation._find_pdf(pdf_dir, code, ISSUE_RESULT_ANNOUNCEMENT),
        "listing": bse_ipo_valuation._find_pdf(pdf_dir, code, LISTING_ANNOUNCEMENT),
    }


def _download_missing_document(
    code: str,
    pdf_dir: Path,
    document: str,
    *,
    retries: int = 1,
    delay_seconds: float = 0.0,
) -> tuple[Path | None, str]:
    attempts = max(int(retries), 1)
    errors: list[str] = []
    for attempt in range(attempts):
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        try:
            client = bse_official_helper.BSEOfficialClient()
            if document == "issue":
                _, downloaded_path = client.download_issue_announcement_by_post_listing_code(code, pdf_dir, overwrite=False)
            elif document == "result":
                _, downloaded_path = client.download_issue_result_announcement_by_post_listing_code(
                    code,
                    pdf_dir,
                    overwrite=False,
                )
            else:
                return None, f"unsupported document: {document}"
        except bse_official_helper.BSEOfficialError as exc:
            error = str(exc)
            errors.append(f"attempt {attempt + 1}/{attempts}: {error}")
            if attempt < attempts - 1 and ("403" in error or "Forbidden" in error or "限流" in error):
                continue
            if attempt < attempts - 1 and delay_seconds > 0:
                continue
            return None, " | ".join(errors)
        return Path(downloaded_path), ""
    return None, " | ".join(errors)


def _merge_document_fields(
    values: dict[str, Any],
    sources: dict[str, str],
    info: dict[str, Any],
    *,
    default_source: str,
    override: bool = False,
) -> None:
    fields = info.get("fields") if isinstance(info, dict) else {}
    if not isinstance(fields, dict):
        return
    field_sources = info.get("field_sources") if isinstance(info, dict) else {}
    if not isinstance(field_sources, dict):
        field_sources = {}
    for field_name in TRACKED_FIELDS:
        value = fields.get(field_name)
        if _is_missing(value):
            continue
        if not override and not _is_missing(values.get(field_name)):
            continue
        values[field_name] = value
        sources[field_name] = str(field_sources.get(field_name) or default_source)


def _seed_base_values(item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for field_name in TRACKED_FIELDS:
        value = item.get(field_name)
        if _is_missing(value):
            continue
        values[field_name] = value
        sources[field_name] = "replay_dataset"
    return values, sources


def _maybe_derive_top_apply(values: dict[str, Any], sources: dict[str, str]) -> None:
    if not _is_missing(values.get("TOP_APPLY_MARKETCAP")):
        return
    limit_wan_shares = _safe_float(values.get("SUBSCRIPTION_LIMIT_WAN_SHARES"))
    issue_price = _safe_float(values.get("ISSUE_PRICE"))
    if not limit_wan_shares or not issue_price:
        return
    values["TOP_APPLY_MARKETCAP"] = limit_wan_shares * issue_price
    sources["TOP_APPLY_MARKETCAP"] = sources.get("SUBSCRIPTION_LIMIT_WAN_SHARES", "derived:subscription_limit")


def _sanitize_implausible_fields(values: dict[str, Any], sources: dict[str, str]) -> list[str]:
    warnings: list[str] = []
    total_issue_wan = _safe_float(values.get("TOTAL_ISSUE_NUM"))
    online_issue = _safe_float(values.get("ONLINE_ISSUE_NUM"))
    if total_issue_wan and online_issue:
        total_issue_shares = total_issue_wan * 10000
        if online_issue < total_issue_shares * 0.2 or online_issue > total_issue_shares * 1.05:
            warnings.append(
                "ONLINE_ISSUE_NUM implausible: "
                f"{_format_value(online_issue)} vs total_issue_shares {_format_value(total_issue_shares)}"
            )
            values.pop("ONLINE_ISSUE_NUM", None)
            sources.pop("ONLINE_ISSUE_NUM", None)
    return warnings


def _fallback_name_from_pdf(code: str, pdfs: dict[str, Path | None]) -> str:
    for file_path in pdfs.values():
        if file_path is None:
            continue
        parts = file_path.stem.split("_")
        if len(parts) >= 3 and parts[0] == code and parts[1].strip():
            return parts[1].strip()
    return ""


def _build_document_parse_plan(
    pdfs: dict[str, Path | None],
    values: dict[str, Any],
    *,
    parse_prospectus: bool = False,
) -> list[tuple[str, Path | None, Any, bool]]:
    parse_plan: list[tuple[str, Path | None, Any, bool]] = []
    if parse_prospectus or any(_is_missing(values.get(field)) for field in ("ISSUE_PRICE", "TOTAL_ISSUE_NUM", "APPLY_DATE")):
        parse_plan.append(("prospectus", pdfs.get("prospectus"), pdf_parser.extract_prospectus_issue_info, False))
    parse_plan.extend(
        (
            ("issue", pdfs.get("issue"), pdf_parser.extract_issue_announcement_info, False),
            ("result", pdfs.get("result"), pdf_parser.extract_issue_result_info, True),
        )
    )
    return parse_plan


def _parse_document_fields(
    pdfs: dict[str, Path | None],
    values: dict[str, Any],
    sources: dict[str, str],
    *,
    parse_prospectus: bool = False,
    progress_callback: HistoryProgressCallback | None = None,
    progress_state: dict[str, int] | None = None,
    code: str = "",
    name: str = "",
    item_index: int = 0,
    item_total: int = 0,
) -> list[str]:
    errors: list[str] = []
    parse_plan = _build_document_parse_plan(pdfs, values, parse_prospectus=parse_prospectus)
    for label, file_path, parser, override in parse_plan:
        if file_path is None:
            continue
        _emit_history_progress(
            progress_callback,
            event="parse_start",
            code=code,
            name=name,
            document=label,
            document_label=_document_label(label),
            file=str(file_path),
            index=item_index,
            total=item_total,
            **(progress_state or {}),
        )
        parse_error = ""
        try:
            info = parser(file_path)
        except Exception as exc:  # pragma: no cover - defensive for malformed local PDFs
            parse_error = str(exc)
            errors.append(f"{label}:{exc}")
        else:
            _merge_document_fields(values, sources, info, default_source=label, override=override)
        if progress_state is not None:
            progress_state["parse_completed"] = int(progress_state.get("parse_completed", 0)) + 1
        _emit_history_progress(
            progress_callback,
            event="parse_done" if not parse_error else "parse_error",
            code=code,
            name=name,
            document=label,
            document_label=_document_label(label),
            file=str(file_path),
            error=parse_error,
            index=item_index,
            total=item_total,
            **(progress_state or {}),
        )
    return errors


def _expected_parse_labels(
    pdfs: dict[str, Path | None],
    values: dict[str, Any],
    *,
    download_missing_issue: bool,
    download_missing_result: bool,
    parse_prospectus: bool,
) -> list[str]:
    expected_pdfs = dict(pdfs)
    if download_missing_issue and expected_pdfs.get("issue") is None:
        expected_pdfs["issue"] = Path("__expected_issue_announcement__.pdf")
    if download_missing_result and expected_pdfs.get("result") is None:
        expected_pdfs["result"] = Path("__expected_issue_result_announcement__.pdf")
    return [
        label
        for label, file_path, _, _ in _build_document_parse_plan(
            expected_pdfs,
            values,
            parse_prospectus=parse_prospectus,
        )
        if file_path is not None
    ]


def _resolve_distribution(values: dict[str, Any]) -> list[dict[str, Any]]:
    distribution = values.get("SUBSCRIPTION_AMOUNT_DISTRIBUTION")
    if isinstance(distribution, list):
        return [item for item in distribution if isinstance(item, dict)]
    return []


def _issue_amount_yi(online_issue_shares: Any, issue_price: Any) -> float | None:
    shares = _safe_float(online_issue_shares)
    price = _safe_float(issue_price)
    if not shares or not price:
        return None
    return shares * price / 100000000


def _resolve_online_issue_shares(values: dict[str, Any]) -> tuple[float | None, str]:
    online_issue = _safe_float(values.get("ONLINE_ISSUE_NUM"))
    if online_issue and online_issue > 0:
        return online_issue, "ONLINE_ISSUE_NUM"
    total_issue_wan = _safe_float(values.get("TOTAL_ISSUE_NUM"))
    if total_issue_wan and total_issue_wan > 0:
        return total_issue_wan * 10000, "TOTAL_ISSUE_NUM fallback"
    return None, ""


def _resolve_top_apply_amount(values: dict[str, Any], issue_price: Any) -> tuple[float | None, str]:
    top_apply = _safe_float(values.get("TOP_APPLY_MARKETCAP"))
    if top_apply and top_apply > 0:
        return top_apply, "TOP_APPLY_MARKETCAP"
    limit_wan_shares = _safe_float(values.get("SUBSCRIPTION_LIMIT_WAN_SHARES"))
    price = _safe_float(issue_price)
    if limit_wan_shares and price:
        return limit_wan_shares * price, "SUBSCRIPTION_LIMIT_WAN_SHARES"
    return None, ""


def _shares_from_amount_wan(amount_wan: Any, issue_price: Any) -> int | None:
    amount = _safe_float(amount_wan)
    price = _safe_float(issue_price)
    if not amount or not price:
        return None
    return int((amount * 10000 // price) // 100 * 100)


def _resolve_valid_subscription_shares(values: dict[str, Any], online_issue_shares: Any) -> tuple[float | None, str]:
    valid_shares = _safe_float(values.get("ONLINE_VA_SHARES"))
    if valid_shares and valid_shares > 0:
        return valid_shares, "ONLINE_VA_SHARES"
    frozen_yi = _safe_float(values.get("FROZEN_FUNDS_YI"))
    issue_price = _safe_float(values.get("ISSUE_PRICE"))
    if frozen_yi and issue_price:
        return frozen_yi * 100000000 / issue_price, "FROZEN_FUNDS_YI"
    lwr_pct = _safe_float(values.get("ONLINE_ISSUE_LWR"))
    online_issue = _safe_float(online_issue_shares)
    if lwr_pct and online_issue:
        return online_issue * 100 / lwr_pct, "ONLINE_ISSUE_LWR"
    multiple = _safe_float(values.get("ONLINE_ES_MULTIPLE"))
    if multiple and online_issue:
        return online_issue * multiple, "ONLINE_ES_MULTIPLE"
    return None, ""


def _resolve_allocated_accounts(values: dict[str, Any]) -> tuple[float | None, str]:
    allocated_accounts = _safe_float(values.get("ONLINE_ALLOCATED_ACCOUNTS"))
    if allocated_accounts and allocated_accounts > 0:
        return allocated_accounts, "ONLINE_ALLOCATED_ACCOUNTS"
    return None, ""


def _model_missing_fields(values: dict[str, Any], prediction: dict[str, Any], online_issue_source: str) -> list[str]:
    missing: list[str] = []
    if not _safe_float(values.get("ISSUE_PRICE")):
        missing.append("ISSUE_PRICE")
    if not _safe_float(values.get("ONLINE_ISSUE_NUM")):
        if "fallback" in online_issue_source:
            missing.append("ONLINE_ISSUE_NUM(actual)")
        else:
            missing.append("ONLINE_ISSUE_NUM")
    if not _safe_float(values.get("ONLINE_VA_SHARES")) and not _safe_float(values.get("FROZEN_FUNDS_YI")):
        missing.append("ONLINE_VA_SHARES/FROZEN_FUNDS_YI")
    if not _safe_float(values.get("ONLINE_VA_NUM")):
        missing.append("ONLINE_VA_NUM")
    if not _safe_float(values.get("ONLINE_ALLOCATED_ACCOUNTS")):
        missing.append("ONLINE_ALLOCATED_ACCOUNTS")
    if not _resolve_distribution(values) and not _safe_float(values.get("FRACTIONAL_THRESHOLD_SHARES")):
        missing.append("SUBSCRIPTION_AMOUNT_DISTRIBUTION/FRACTIONAL_THRESHOLD_SHARES")
    if values.get("FRACTIONAL_TIME_PRIORITY_REQUIRED") in (None, "", "--"):
        missing.append("FRACTIONAL_TIME_PRIORITY_REQUIRED")
    if not prediction.get("available") and prediction.get("reason"):
        missing.append(f"prediction:{prediction.get('reason')}")
    return missing


def _data_quality(
    *,
    model_ready: bool,
    guaranteed_label_ready: bool,
    fractional_label_ready: bool,
    result_pdf_found: bool,
    issue_pdf_found: bool,
) -> str:
    if fractional_label_ready:
        return "ready_fractional"
    if guaranteed_label_ready:
        return "ready_guaranteed"
    if model_ready:
        return "ready_actual_censored"
    if not result_pdf_found:
        return "needs_result_announcement"
    if not issue_pdf_found:
        return "needs_issue_announcement"
    return "needs_manual_review"


def build_subscription_history_rows(
    items: list[dict[str, Any]],
    *,
    pdf_dir: Path = DEFAULT_PDF_DIR,
    download_missing_issue: bool = False,
    download_missing_result: bool = False,
    download_skip_codes: set[str] | list[str] | tuple[str, ...] | None = None,
    parse_prospectus: bool = False,
    download_retries: int = 1,
    download_delay_seconds: float = 0.0,
    progress_callback: HistoryProgressCallback | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    skipped_download_codes = {str(code or "").strip() for code in (download_skip_codes or []) if str(code or "").strip()}
    sorted_items = [item for item in sorted(items, key=_row_sort_key) if _item_code(item)]
    preflight: list[dict[str, Any]] = []
    for item in sorted_items:
        code = _item_code(item)
        allow_download = code not in skipped_download_codes
        values, _ = _seed_base_values(item)
        pdfs = _find_local_pdfs(pdf_dir, code)
        download_documents: list[str] = []
        if allow_download and download_missing_issue and pdfs.get("issue") is None:
            download_documents.append("issue")
        if allow_download and download_missing_result and pdfs.get("result") is None:
            download_documents.append("result")
        expected_parse_labels = _expected_parse_labels(
            pdfs,
            values,
            download_missing_issue=allow_download and download_missing_issue,
            download_missing_result=allow_download and download_missing_result,
            parse_prospectus=parse_prospectus,
        )
        preflight.append(
            {
                "download_documents": download_documents,
                "expected_parse_labels": expected_parse_labels,
            }
        )

    progress_state = {
        "download_total": sum(len(spec["download_documents"]) for spec in preflight),
        "download_completed": 0,
        "parse_total": sum(len(spec["expected_parse_labels"]) for spec in preflight),
        "parse_completed": 0,
    }
    _emit_history_progress(
        progress_callback,
        event="history_start",
        index=0,
        total=len(sorted_items),
        **progress_state,
    )

    for item_index, (item, preflight_spec) in enumerate(zip(sorted_items, preflight), start=1):
        code = _item_code(item)
        allow_download = code not in skipped_download_codes

        values, sources = _seed_base_values(item)
        pdfs = _find_local_pdfs(pdf_dir, code)
        progress_name = str(item.get("SECURITY_NAME_ABBR") or "").strip() or _fallback_name_from_pdf(code, pdfs)
        _emit_history_progress(
            progress_callback,
            event="row_start",
            code=code,
            name=progress_name,
            index=item_index,
            total=len(sorted_items),
            **progress_state,
        )
        download_errors: list[str] = []
        if allow_download and download_missing_issue and pdfs.get("issue") is None:
            _emit_history_progress(
                progress_callback,
                event="download_start",
                code=code,
                name=progress_name,
                document="issue",
                document_label=_document_label("issue"),
                index=item_index,
                total=len(sorted_items),
                **progress_state,
            )
            _, error = _download_missing_document(
                code,
                pdf_dir,
                "issue",
                retries=download_retries,
                delay_seconds=download_delay_seconds,
            )
            progress_state["download_completed"] += 1
            if error:
                download_errors.append(f"issue:{error}")
            pdfs = _find_local_pdfs(pdf_dir, code)
            _emit_history_progress(
                progress_callback,
                event="download_error" if error else "download_done",
                code=code,
                name=progress_name,
                document="issue",
                document_label=_document_label("issue"),
                error=error,
                index=item_index,
                total=len(sorted_items),
                **progress_state,
            )
        if allow_download and download_missing_result and pdfs.get("result") is None:
            _emit_history_progress(
                progress_callback,
                event="download_start",
                code=code,
                name=progress_name,
                document="result",
                document_label=_document_label("result"),
                index=item_index,
                total=len(sorted_items),
                **progress_state,
            )
            _, error = _download_missing_document(
                code,
                pdf_dir,
                "result",
                retries=download_retries,
                delay_seconds=download_delay_seconds,
            )
            progress_state["download_completed"] += 1
            if error:
                download_errors.append(f"result:{error}")
            pdfs = _find_local_pdfs(pdf_dir, code)
            _emit_history_progress(
                progress_callback,
                event="download_error" if error else "download_done",
                code=code,
                name=progress_name,
                document="result",
                document_label=_document_label("result"),
                error=error,
                index=item_index,
                total=len(sorted_items),
                **progress_state,
            )

        for expected_label in preflight_spec["expected_parse_labels"]:
            if pdfs.get(expected_label) is not None:
                continue
            progress_state["parse_completed"] += 1
            _emit_history_progress(
                progress_callback,
                event="parse_skipped",
                code=code,
                name=progress_name,
                document=expected_label,
                document_label=_document_label(expected_label),
                reason="pdf_missing",
                index=item_index,
                total=len(sorted_items),
                **progress_state,
            )

        parse_errors = _parse_document_fields(
            pdfs,
            values,
            sources,
            parse_prospectus=parse_prospectus,
            progress_callback=progress_callback,
            progress_state=progress_state,
            code=code,
            name=progress_name,
            item_index=item_index,
            item_total=len(sorted_items),
        )
        parse_errors.extend(_sanitize_implausible_fields(values, sources))
        _maybe_derive_top_apply(values, sources)

        name = str(item.get("SECURITY_NAME_ABBR") or "").strip() or _fallback_name_from_pdf(code, pdfs)
        if not name:
            name = _fallback_name_from_pdf(code, pdfs)

        prediction = subscription_predictor.build_subscription_prediction(values, recent_ipos=[], params={})
        resolved_online_issue_shares, online_issue_source = _resolve_online_issue_shares(values)
        online_issue_shares = prediction.get("online_issue_shares") or resolved_online_issue_shares
        top_apply_amount_wan, top_apply_source = _resolve_top_apply_amount(values, values.get("ISSUE_PRICE"))
        if prediction.get("top_apply_amount_wan"):
            top_apply_amount_wan = prediction.get("top_apply_amount_wan")
        top_apply_source = sources.get("TOP_APPLY_MARKETCAP", "") or top_apply_source
        top_apply_shares = prediction.get("top_apply_shares") or _shares_from_amount_wan(
            top_apply_amount_wan,
            values.get("ISSUE_PRICE"),
        )
        valid_subscription_shares, valid_subscription_source = _resolve_valid_subscription_shares(values, online_issue_shares)
        if prediction.get("valid_subscription_shares"):
            valid_subscription_shares = prediction.get("valid_subscription_shares")
        valid_subscription_source = (
            sources.get("ONLINE_VA_SHARES", "")
            or sources.get("FROZEN_FUNDS_YI", "")
            or sources.get("ONLINE_ISSUE_LWR", "")
            or sources.get("ONLINE_ES_MULTIPLE", "")
            or valid_subscription_source
        )
        allocated_accounts, allocated_accounts_source = _resolve_allocated_accounts(values)
        if prediction.get("allocated_accounts"):
            allocated_accounts = prediction.get("allocated_accounts")
        allocated_accounts_source = sources.get("ONLINE_ALLOCATED_ACCOUNTS", "") or allocated_accounts_source

        distribution = _resolve_distribution(values)
        allocation_fit = prediction.get("allocation_fit") if isinstance(prediction.get("allocation_fit"), dict) else None
        fractional_source = ""
        if _safe_float(values.get("FRACTIONAL_THRESHOLD_SHARES")):
            fractional_source = sources.get("FRACTIONAL_THRESHOLD_SHARES", "issue_result")
        elif distribution:
            fractional_source = sources.get("SUBSCRIPTION_AMOUNT_DISTRIBUTION", "issue_result:distribution_table")
        elif prediction.get("fractional_threshold_amount_wan"):
            fractional_source = (
                "top_apply_below_guaranteed_all_time_priority"
                if prediction.get("top_apply_below_guaranteed")
                else "model_estimate_without_distribution"
            )

        model_ready = bool(prediction.get("available") and prediction.get("mode") == "actual")
        guaranteed_label_ready = bool(model_ready and prediction.get("guaranteed_threshold_amount_wan") is not None)
        top_apply_time_priority_label = bool(model_ready and prediction.get("top_apply_below_guaranteed"))
        fractional_label_ready = bool(
            model_ready
            and (
                _safe_float(values.get("FRACTIONAL_THRESHOLD_SHARES")) is not None
                or bool(distribution)
                or top_apply_time_priority_label
            )
        )

        missing_fields = _model_missing_fields(values, prediction, online_issue_source)
        row = {
            "security_code": code,
            "security_name_abbr": name,
            "apply_date": _clean_date(values.get("APPLY_DATE")),
            "issue_result_date": _clean_date(values.get("ISSUE_RESULT_DATE") or values.get("BALLOT_NUM_DATE")),
            "listing_date": _clean_date(values.get("LISTING_DATE")),
            "issue_price": _safe_float(values.get("ISSUE_PRICE")),
            "total_issue_num_wan": _safe_float(values.get("TOTAL_ISSUE_NUM")),
            "online_issue_shares": online_issue_shares,
            "online_issue_source": online_issue_source,
            "issue_amount_yi": _issue_amount_yi(online_issue_shares, values.get("ISSUE_PRICE")),
            "top_apply_amount_wan": top_apply_amount_wan,
            "top_apply_source": top_apply_source,
            "top_apply_shares": top_apply_shares,
            "online_valid_accounts": prediction.get("valid_accounts"),
            "online_allocated_accounts": allocated_accounts,
            "online_valid_shares": valid_subscription_shares,
            "online_valid_source": valid_subscription_source,
            "frozen_funds_yi": prediction.get("frozen_funds_yi"),
            "allocation_rate_pct": prediction.get("allocation_rate_pct"),
            "subscription_multiple": prediction.get("subscription_multiple"),
            "lock_days": prediction.get("lock_days"),
            "guaranteed_threshold_shares": prediction.get("guaranteed_threshold_shares"),
            "guaranteed_threshold_amount_wan": prediction.get("guaranteed_threshold_amount_wan"),
            "guaranteed_threshold_reachable": prediction.get("guaranteed_threshold_reachable"),
            "top_apply_below_guaranteed": prediction.get("top_apply_below_guaranteed"),
            "top_apply_gap_shares": prediction.get("top_apply_gap_shares"),
            "top_apply_gap_amount_wan": prediction.get("top_apply_gap_amount_wan"),
            "fractional_threshold_shares": prediction.get("fractional_threshold_shares"),
            "fractional_threshold_amount_wan": prediction.get("fractional_threshold_amount_wan"),
            "fractional_threshold_source": fractional_source,
            "fractional_time_priority_required": prediction.get("fractional_time_priority_required"),
            "time_priority_scope": prediction.get("time_priority_scope"),
            "subscription_distribution_json": distribution,
            "distribution_bucket_count": len(distribution),
            "allocation_fit_json": allocation_fit or {},
            "allocation_fit_quality": (allocation_fit or {}).get("fit_quality", ""),
            "allocation_fit_confidence": (allocation_fit or {}).get("fit_confidence"),
            "allocation_fit_usable_for_tuning": bool(allocation_fit and allocation_fit.get("fit_usable_for_tuning")),
            "allocation_fit_residual_json": (allocation_fit or {}).get("fit_residuals", {}),
            "allocation_fit_bucket_count": len((allocation_fit or {}).get("buckets") or []),
            "unallocated_avg_amount_wan": (allocation_fit or {}).get("unallocated_avg_amount_wan"),
            "allocation_fit_ready": bool(allocation_fit and allocation_fit.get("available")),
            "model_mode": prediction.get("mode", ""),
            "model_ready": model_ready,
            "guaranteed_label_ready": guaranteed_label_ready,
            "fractional_label_ready": fractional_label_ready,
            "data_quality": _data_quality(
                model_ready=model_ready,
                guaranteed_label_ready=guaranteed_label_ready,
                fractional_label_ready=fractional_label_ready,
                result_pdf_found=pdfs.get("result") is not None,
                issue_pdf_found=pdfs.get("issue") is not None,
            ),
            "missing_fields": "|".join(missing_fields),
            "prospectus_pdf_found": pdfs.get("prospectus") is not None,
            "issue_pdf_found": pdfs.get("issue") is not None,
            "result_pdf_found": pdfs.get("result") is not None,
            "listing_pdf_found": pdfs.get("listing") is not None,
            "prospectus_pdf_file": pdfs["prospectus"].name if pdfs.get("prospectus") else "",
            "issue_pdf_file": pdfs["issue"].name if pdfs.get("issue") else "",
            "result_pdf_file": pdfs["result"].name if pdfs.get("result") else "",
            "listing_pdf_file": pdfs["listing"].name if pdfs.get("listing") else "",
            "parse_errors": "|".join(parse_errors),
            "download_errors": "|".join(download_errors),
        }
        rows.append(row)
    _emit_history_progress(
        progress_callback,
        event="history_done",
        index=len(sorted_items),
        total=len(sorted_items),
        **progress_state,
    )
    return rows


def write_subscription_history_csv(rows: list[dict[str, Any]], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _format_value(row.get(column)) for column in CSV_COLUMNS})
    return output_path


def _load_existing_subscription_history_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        return [dict(row) for row in csv.DictReader(file_obj)]


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是"}


def _row_quality_score(row: dict[str, Any]) -> int:
    score = 0
    score += 100 if _truthy(row.get("model_ready")) else 0
    score += 40 if _truthy(row.get("guaranteed_label_ready")) else 0
    score += 40 if _truthy(row.get("fractional_label_ready")) else 0
    score += 25 if _truthy(row.get("result_pdf_found")) else 0
    score += 15 if _truthy(row.get("issue_pdf_found")) else 0
    score += 15 if _truthy(row.get("allocation_fit_usable_for_tuning")) else 0
    for field_name in (
        "online_valid_shares",
        "frozen_funds_yi",
        "allocation_rate_pct",
        "subscription_multiple",
        "guaranteed_threshold_amount_wan",
        "fractional_threshold_amount_wan",
        "allocation_fit_json",
    ):
        if not _is_missing(row.get(field_name)):
            score += 5
    return score


def _merge_existing_history_rows(
    rows: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing_by_code = {
        str(row.get("security_code") or "").strip(): row
        for row in existing_rows
        if str(row.get("security_code") or "").strip()
    }
    merged_rows: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("security_code") or "").strip()
        existing = existing_by_code.get(code)
        if existing and _row_quality_score(existing) > _row_quality_score(row):
            merged_rows.append({column: existing.get(column, "") for column in CSV_COLUMNS})
        else:
            merged_rows.append(row)
    return merged_rows


def build_subscription_history_table(
    *,
    dataset_path: Path = DEFAULT_DATASET_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    pdf_dir: Path = DEFAULT_PDF_DIR,
    download_missing_issue: bool = False,
    download_missing_result: bool = False,
    download_skip_codes: set[str] | list[str] | tuple[str, ...] | None = None,
    parse_prospectus: bool = False,
    download_retries: int = 1,
    download_delay_seconds: float = 0.0,
    ladder_label_path: Path = DEFAULT_LADDER_LABEL_PATH,
    progress_callback: HistoryProgressCallback | None = None,
) -> dict[str, Any]:
    items = _load_replay_dataset_items(dataset_path)
    rows = build_subscription_history_rows(
        items,
        pdf_dir=pdf_dir,
        download_missing_issue=download_missing_issue,
        download_missing_result=download_missing_result,
        download_skip_codes=download_skip_codes,
        parse_prospectus=parse_prospectus,
        download_retries=download_retries,
        download_delay_seconds=download_delay_seconds,
        progress_callback=progress_callback,
    )
    existing_rows = _load_existing_subscription_history_csv(output_path)
    rows = _merge_existing_history_rows(rows, existing_rows)
    write_subscription_history_csv(rows, output_path)
    ladder_summary = subscription_ladder_labels.sync_label_rows(rows, ladder_label_path)
    return {
        "output_path": str(output_path),
        "row_count": len(rows),
        "model_ready_count": sum(1 for row in rows if row.get("model_ready")),
        "fractional_label_ready_count": sum(1 for row in rows if row.get("fractional_label_ready")),
        "result_pdf_count": sum(1 for row in rows if row.get("result_pdf_found")),
        "issue_pdf_count": sum(1 for row in rows if row.get("issue_pdf_found")),
        "ladder_label_path": ladder_summary.get("path"),
        "ladder_label_rows": ladder_summary.get("row_count"),
        "ladder_label_filled": ladder_summary.get("filled_count"),
        "ladder_label_added_codes": ladder_summary.get("added_codes") or [],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the BSE IPO subscription allocation history sample table.")
    parser.add_argument("--dataset-path", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--download-missing-issue", action="store_true")
    parser.add_argument("--download-missing-result", action="store_true")
    parser.add_argument("--download-retries", type=int, default=1)
    parser.add_argument("--download-delay-seconds", type=float, default=0.0)
    parser.add_argument("--parse-prospectus", action="store_true")
    parser.add_argument("--ladder-label-path", type=Path, default=DEFAULT_LADDER_LABEL_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_subscription_history_table(
        dataset_path=args.dataset_path,
        output_path=args.output,
        pdf_dir=args.pdf_dir,
        download_missing_issue=args.download_missing_issue,
        download_missing_result=args.download_missing_result,
        parse_prospectus=args.parse_prospectus,
        download_retries=args.download_retries,
        download_delay_seconds=args.download_delay_seconds,
        ladder_label_path=args.ladder_label_path,
    )
    print(
        "subscription history rows={row_count}, model_ready={model_ready_count}, "
        "fractional_ready={fractional_label_ready_count}, result_pdfs={result_pdf_count}, "
        "issue_pdfs={issue_pdf_count}, ladder_rows={ladder_label_rows}, "
        "ladder_filled={ladder_label_filled}, output={output_path}".format(**summary)
    )
    added_codes = summary.get("ladder_label_added_codes") or []
    if added_codes:
        print("ladder label rows added: " + ", ".join(str(code) for code in added_codes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
