from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
import sys
from typing import Any, Callable


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import config_loader
import data_fetcher
import tushare_helper


DEFAULT_OUTPUT_DIR = ROOT_DIR / "首日分时走势"
ProgressCallback = Callable[[str, dict[str, Any]], None]


def _normalize_trade_date(raw_value: str | date | None) -> str:
    if isinstance(raw_value, date):
        return raw_value.isoformat()
    text = str(raw_value or "").strip()
    if not text:
        return date.today().isoformat()
    text = text.split(" ", 1)[0].replace("/", "-")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return date.fromisoformat(text).isoformat()


def _parse_codes(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    seen: set[str] = set()
    normalized_codes: list[str] = []
    for item in raw_value.split(","):
        code = str(item).strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        normalized_codes.append(code)
    return normalized_codes


def _base_code(code: str) -> str:
    normalized_code = str(code or "").strip().upper()
    if "." in normalized_code:
        return normalized_code.split(".", 1)[0]
    return normalized_code


def _prepare_params(params: dict[str, Any] | None) -> dict[str, Any]:
    return dict(params or config_loader.load_params())


def _build_empty_summary(scan_source: str, output_dir: Path) -> dict[str, Any]:
    return {
        "scan_source": scan_source,
        "output_dir": str(output_dir),
        "matched_codes": [],
        "checked_codes": [],
        "cached": [],
        "deferred": [],
        "skipped_existing": [],
        "errors": [],
        "stop_at_existing": None,
        "pending_deferred_before": [],
        "pending_deferred_after": [],
        "matched_count": 0,
        "checked_count": 0,
        "cached_count": 0,
        "deferred_count": 0,
        "skipped_existing_count": 0,
        "error_count": 0,
    }


def _finalize_summary(summary: dict[str, Any]) -> dict[str, Any]:
    summary["matched_count"] = len(summary.get("matched_codes") or [])
    summary["checked_count"] = len(summary.get("checked_codes") or [])
    summary["cached_count"] = len(summary.get("cached") or [])
    summary["deferred_count"] = len(summary.get("deferred") or [])
    summary["skipped_existing_count"] = len(summary.get("skipped_existing") or [])
    summary["error_count"] = len(summary.get("errors") or [])
    return summary


def _emit_progress(progress_callback: ProgressCallback | None, event: str, payload: dict[str, Any]) -> None:
    if progress_callback is None:
        return
    progress_callback(event, dict(payload))


def _deferred_marker_path(strategy_params: dict[str, Any]) -> Path:
    cache_root = Path(str(strategy_params.get("tushare_cache_root") or ROOT_DIR / "data" / "tushare_intraday_db"))
    return cache_root / "deferred_intraday_codes.json"


def _load_deferred_codes(strategy_params: dict[str, Any]) -> list[str]:
    marker_path = _deferred_marker_path(strategy_params)
    if not marker_path.exists():
        return []
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    codes = payload.get("codes") or []
    normalized: list[str] = []
    seen: set[str] = set()
    for code in codes:
        current = str(code or "").strip().upper()
        if not current or current in seen:
            continue
        seen.add(current)
        normalized.append(current)
    return normalized


def _save_deferred_codes(strategy_params: dict[str, Any], codes: list[str]) -> None:
    marker_path = _deferred_marker_path(strategy_params)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"codes": list(codes)}
    marker_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _scan_candidates_by_date(target_date: str, months: int) -> list[dict[str, Any]]:
    scanned = data_fetcher.fetch_recent_ipos(months=months, require_close_price=False)
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in scanned:
        listing_date = _normalize_trade_date(item.get("LISTING_DATE"))
        code = str(item.get("SECURITY_CODE") or "").strip()
        if listing_date != target_date or not code or code in seen:
            continue
        seen.add(code)
        matched.append(item)
    matched.sort(key=lambda row: str(row.get("SECURITY_CODE") or ""))
    return matched


def _scan_latest_candidates(months: int) -> list[dict[str, Any]]:
    scanned = data_fetcher.fetch_recent_ipos(months=months, require_close_price=False)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    today_text = date.today().isoformat()
    for item in scanned:
        listing_date = _normalize_trade_date(item.get("LISTING_DATE"))
        code = str(item.get("SECURITY_CODE") or "").strip()
        if not listing_date or listing_date > today_text or not code or code in seen:
            continue
        seen.add(code)
        candidates.append(item)
    return candidates


def _cache_single_candidate(
    item: dict[str, Any],
    output_path: Path,
    strategy_params: dict[str, Any],
    force: bool = False,
) -> dict[str, Any]:
    code = str(item.get("SECURITY_CODE") or "").strip()
    name = str(item.get("SECURITY_NAME_ABBR") or item.get("SECURITY_NAME") or "").strip()
    listing_date = _normalize_trade_date(item.get("LISTING_DATE"))
    csv_path = output_path / f"{_base_code(code)}.csv"

    if csv_path.exists() and not force:
        return {
            "status": "existing",
            "code": code,
            "name": name,
            "listing_date": listing_date,
            "path": str(csv_path),
        }

    result = tushare_helper.fetch_intraday_bars(code, trade_date=listing_date, params=strategy_params)
    rows = result.get("rows") or []
    fetch_summary = result.get("summary") or {}
    if not rows:
        eastmoney_reason = ""
        try:
            rows = data_fetcher.fetch_intraday_trends(code, trade_date=listing_date)
        except data_fetcher.DataFetcherError as exc:
            eastmoney_reason = str(exc)
        else:
            written_path = tushare_helper.write_intraday_csv(code, rows, output_dir=output_path)
            attempted_apis = list(fetch_summary.get("attempted_apis") or [])
            attempted_apis.append("eastmoney_trends2")
            return {
                "status": "cached",
                "code": code,
                "name": name,
                "listing_date": listing_date,
                "path": str(written_path),
                "rows": len(rows),
                "source_api": "eastmoney_trends2",
                "attempted_apis": attempted_apis,
            }

        if "数据不精确" in eastmoney_reason:
            return {
                "status": "deferred",
                "code": code,
                "name": name,
                "listing_date": listing_date,
                "reason": eastmoney_reason,
                "source_api": "eastmoney_trends2",
                "attempted_apis": list(fetch_summary.get("attempted_apis") or []) + ["eastmoney_trends2"],
            }

        reason = fetch_summary.get("reason") or "未获取到可用分钟线。"
        if eastmoney_reason:
            reason = f"{reason}；东方财富兜底也失败：{eastmoney_reason}"
        return {
            "status": "error",
            "code": code,
            "name": name,
            "listing_date": listing_date,
            "reason": reason,
            "source_api": fetch_summary.get("source_api"),
            "attempted_apis": list(fetch_summary.get("attempted_apis") or []),
        }

    written_path = tushare_helper.write_intraday_csv(code, rows, output_dir=output_path)
    return {
        "status": "cached",
        "code": code,
        "name": name,
        "listing_date": listing_date,
        "path": str(written_path),
        "rows": len(rows),
        "source_api": fetch_summary.get("source_api"),
        "attempted_apis": list(fetch_summary.get("attempted_apis") or []),
    }


def run_cache_job(
    target_date: str | date | None = None,
    months: int = 2,
    codes: list[str] | None = None,
    output_dir: str | Path | None = None,
    force: bool = False,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_trade_date = _normalize_trade_date(target_date)
    output_path = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    output_path.mkdir(parents=True, exist_ok=True)
    strategy_params = _prepare_params(params)
    requested_codes = [str(code).strip() for code in (codes or []) if str(code).strip()]

    summary = _build_empty_summary("manual_codes" if requested_codes else "eastmoney_recent_ipos", output_path)
    summary["trade_date"] = normalized_trade_date

    if requested_codes:
        candidates = [{"SECURITY_CODE": code, "SECURITY_NAME_ABBR": "", "LISTING_DATE": normalized_trade_date} for code in requested_codes]
    else:
        try:
            candidates = _scan_candidates_by_date(normalized_trade_date, months=months)
        except data_fetcher.DataFetcherError as exc:
            summary["errors"].append({"code": "", "name": "", "reason": str(exc), "source_api": "eastmoney_scan"})
            return _finalize_summary(summary)

    summary["matched_codes"] = [str(item.get("SECURITY_CODE") or "").strip() for item in candidates]

    for item in candidates:
        code = str(item.get("SECURITY_CODE") or "").strip()
        if not code:
            continue
        summary["checked_codes"].append(code)
        result = _cache_single_candidate(item, output_path, strategy_params, force=force)
        status = result.pop("status")
        if status == "existing":
            summary["skipped_existing"].append(result)
        elif status == "cached":
            summary["cached"].append(result)
        elif status == "deferred":
            summary["deferred"].append(result)
        else:
            summary["errors"].append(result)

    return _finalize_summary(summary)


def run_latest_missing_cache_job(
    months: int = 18,
    output_dir: str | Path | None = None,
    params: dict[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    output_path = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    output_path.mkdir(parents=True, exist_ok=True)
    strategy_params = _prepare_params(params)
    summary = _build_empty_summary("eastmoney_latest_until_cached", output_path)
    deferred_before = _load_deferred_codes(strategy_params)
    deferred_after: list[str] = []
    summary["pending_deferred_before"] = list(deferred_before)
    _emit_progress(
        progress_callback,
        "start",
        {
            "months": months,
            "output_dir": str(output_path),
            "pending_deferred_before": list(deferred_before),
        },
    )

    try:
        candidates = _scan_latest_candidates(months=months)
    except data_fetcher.DataFetcherError as exc:
        summary["errors"].append({"code": "", "name": "", "reason": str(exc), "source_api": "eastmoney_scan"})
        _emit_progress(progress_callback, "scan_error", {"reason": str(exc)})
        return _finalize_summary(summary)

    summary["matched_codes"] = [str(item.get("SECURITY_CODE") or "").strip() for item in candidates]
    _emit_progress(
        progress_callback,
        "scan_completed",
        {
            "matched_count": len(summary["matched_codes"]),
            "matched_codes": list(summary["matched_codes"]),
        },
    )

    candidate_by_code: dict[str, dict[str, Any]] = {}
    for item in candidates:
        normalized_code = _base_code(str(item.get("SECURITY_CODE") or "")).strip().upper()
        if normalized_code and normalized_code not in candidate_by_code:
            candidate_by_code[normalized_code] = item

    processed_codes: set[str] = set()

    def _handle_candidate(item: dict[str, Any], phase: str, allow_stop_on_existing: bool) -> bool:
        code = str(item.get("SECURITY_CODE") or "").strip()
        if not code:
            return False
        _emit_progress(
            progress_callback,
            "checking",
            {
                "code": code,
                "name": str(item.get("SECURITY_NAME_ABBR") or item.get("SECURITY_NAME") or "").strip(),
                "listing_date": _normalize_trade_date(item.get("LISTING_DATE")),
                "phase": phase,
            },
        )
        summary["checked_codes"].append(code)
        result = _cache_single_candidate(item, output_path, strategy_params, force=False)
        status = result.pop("status")
        current_code = _base_code(str(result.get("code") or code)).strip().upper()
        if current_code:
            processed_codes.add(current_code)
        if status == "existing":
            summary["skipped_existing"].append(result)
            _emit_progress(progress_callback, "existing", result)
            if allow_stop_on_existing:
                summary["stop_at_existing"] = dict(result)
                _emit_progress(progress_callback, "stop_at_existing", result)
                return True
            return False
        if status == "cached":
            summary["cached"].append(result)
            _emit_progress(progress_callback, "cached", result)
            return False
        if status == "deferred":
            summary["deferred"].append(result)
            if current_code and current_code not in deferred_after:
                deferred_after.append(current_code)
            _emit_progress(progress_callback, "deferred", result)
            return False
        error_result = dict(result)
        if current_code:
            if current_code not in deferred_after:
                deferred_after.append(current_code)
            error_result["retry_pending"] = True
        summary["errors"].append(error_result)
        _emit_progress(progress_callback, "error", error_result)
        return False

    for code in deferred_before:
        item = candidate_by_code.get(code)
        if item is None:
            if code not in deferred_after:
                deferred_after.append(code)
            continue
        _handle_candidate(item, phase="retry_pending", allow_stop_on_existing=False)

    for item in candidates:
        normalized_code = _base_code(str(item.get("SECURITY_CODE") or "")).strip().upper()
        if not normalized_code or normalized_code in processed_codes:
            continue
        if _handle_candidate(item, phase="latest_scan", allow_stop_on_existing=True):
            break

    summary["pending_deferred_after"] = list(deferred_after)
    _save_deferred_codes(strategy_params, deferred_after)
    _emit_progress(
        progress_callback,
        "finished",
        {
            "cached_count": len(summary["cached"]),
            "deferred_count": len(summary["deferred"]),
            "error_count": len(summary["errors"]),
            "stop_at_existing": summary.get("stop_at_existing"),
            "pending_deferred_after": list(summary["pending_deferred_after"]),
        },
    )
    return _finalize_summary(summary)


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描上市首日新股并缓存本地分时 CSV。")
    parser.add_argument("--date", dest="trade_date", default=date.today().isoformat(), help="目标上市日期，默认今天，格式 YYYY-MM-DD。")
    parser.add_argument("--months", type=int, default=2, help="从东方财富最近新股列表回看月份数，默认 2。")
    parser.add_argument("--codes", help="手动指定代码，逗号分隔；指定后不再走自动扫描。")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="CSV 输出目录，默认 首日分时走势。")
    parser.add_argument("--force", action="store_true", help="即使本地已存在 CSV 也强制覆盖。")
    parser.add_argument("--latest-until-cached", action="store_true", help="按最新上市顺序往前补缓存，直到遇到本地已有 CSV 为止。")
    args = parser.parse_args()

    if args.latest_until_cached:
        summary = run_latest_missing_cache_job(
            months=max(args.months, 1),
            output_dir=args.output_dir,
        )
    else:
        summary = run_cache_job(
            target_date=args.trade_date,
            months=max(args.months, 1),
            codes=_parse_codes(args.codes),
            output_dir=args.output_dir,
            force=args.force,
        )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
