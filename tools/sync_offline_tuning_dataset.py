from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from typing import Any, Callable


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import build_subscription_history
import config_loader
import param_tuning
import subscription_ladder_labels


MANIFEST_SCHEMA = "offline_tuning_sample_manifest_v1"
DEFAULT_PARAMS_PATH = ROOT_DIR / "策略参数.txt"
DEFAULT_DATASET_PATH = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_HISTORY_PATH = ROOT_DIR / "data" / "offline_tuning" / "subscription_history_sample.csv"
DEFAULT_LADDER_LABEL_PATH = ROOT_DIR / "data" / "offline_tuning" / "subscription_ladder_labels.csv"
DEFAULT_MANIFEST_PATH = ROOT_DIR / "data" / "offline_tuning" / "sample_manifest.json"
DEFAULT_REPLAY_ITEM_DIR = ROOT_DIR / "data" / "offline_tuning" / "replay_items"
DEFAULT_RETRY_MARKER_PATH = ROOT_DIR / "data" / "offline_tuning" / "deferred_listing_data_codes.json"
DEFAULT_INTRADAY_DIR = ROOT_DIR / "首日分时走势"


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _date_prefix(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return ""


def _parse_csv_codes(raw_value: str | None) -> list[str] | None:
    if raw_value is None:
        return None
    codes: list[str] = []
    seen: set[str] = set()
    for chunk in raw_value.split(","):
        code = chunk.strip()
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是"}


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


ProgressCallback = Callable[[int, int, dict[str, object]], None]


class _SubscriptionHistoryHeartbeat:
    def __init__(self, interval_seconds: float = 15.0) -> None:
        self._interval_seconds = max(float(interval_seconds), 1.0)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {
            "event": "history_start",
            "index": 0,
            "total": 0,
            "download_total": 0,
            "download_completed": 0,
            "parse_total": 0,
            "parse_completed": 0,
        }

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="subscription-history-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def update(self, event: dict[str, Any]) -> None:
        with self._lock:
            for key, value in event.items():
                self._state[key] = value

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            message = self._format_snapshot()
            if message:
                print(message, flush=True)

    def _format_snapshot(self) -> str:
        with self._lock:
            state = dict(self._state)

        def as_int(key: str) -> int:
            try:
                return int(state.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        event = str(state.get("event") or "")
        action = {
            "history_start": "准备",
            "row_start": "检查",
            "download_start": "下载",
            "download_done": "完成下载",
            "download_error": "下载失败",
            "parse_start": "解析",
            "parse_done": "完成解析",
            "parse_error": "解析失败",
            "parse_skipped": "跳过解析",
            "history_done": "收尾",
        }.get(event, "处理")
        code = str(state.get("code") or "").strip()
        name = str(state.get("name") or "").strip()
        document_label = str(state.get("document_label") or "").strip()
        target_parts = [part for part in (code, name, document_label) if part]
        target = " ".join(target_parts) if target_parts else "申购 history"

        index = as_int("index")
        total = as_int("total")
        download_total = as_int("download_total")
        download_completed = as_int("download_completed")
        parse_total = as_int("parse_total")
        parse_completed = as_int("parse_completed")
        download_remaining = max(download_total - download_completed, 0)
        parse_remaining = max(parse_total - parse_completed, 0)

        sample_text = f"样本 {index}/{total}" if total else "样本准备中"
        if download_total:
            download_text = f"公告下载 {download_completed}/{download_total} 完成，待下载 {download_remaining}"
        else:
            download_text = "公告下载无需处理"
        if parse_total:
            parse_text = f"公告解析 {parse_completed}/{parse_total} 完成，待解析 {parse_remaining}"
        else:
            parse_text = "公告解析暂无待处理"
        return f"[申购 history 心跳] 正在{action} {target}；{sample_text}；{download_text}；{parse_text}。"


def _as_path(value: Any, default: Path) -> Path:
    if value in (None, ""):
        return default
    return Path(value)


def _dedupe_codes(codes: list[str] | tuple[str, ...] | set[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw_code in codes or []:
        code = str(raw_code or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(code)
    return result


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()


def _should_auto_refresh_dataset(
    args: argparse.Namespace,
    dataset_path: Path,
    sample_codes: list[str] | None,
) -> bool:
    if os.environ.get("BSE_TUNING_NO_AUTO_REFRESH") == "1":
        return False
    if bool(getattr(args, "no_auto_refresh_dataset", False)):
        return False
    if str(getattr(args, "mode", "offline") or "offline") not in {"offline", "observe", "auto"}:
        return False
    if sample_codes is not None:
        return True
    return _same_path(dataset_path, Path(param_tuning.DEFAULT_DATASET_PATH))


def _default_dataset_progress(index: int, total: int, spec: dict[str, object]) -> None:
    status = str(spec.get("status") or "")
    code = str(spec.get("code") or "")
    if status in {"built", "upgraded_dataset", "skipped"} or index in {1, total} or index % 10 == 0:
        print(f"[{index}/{total}] replay dataset sync: {status or 'processing'} {code}", flush=True)


def build_and_save_replay_dataset(
    params: dict[str, Any],
    dataset_path: Path,
    *,
    months: int,
    sample_codes: list[str] | None,
    page_size: int,
    use_item_cache: bool = True,
    existing_dataset: dict[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    dataset = param_tuning.build_replay_dataset(
        params,
        months=months,
        sample_codes=sample_codes,
        page_size=page_size,
        use_item_cache=use_item_cache,
        existing_dataset=existing_dataset,
        progress_callback=progress_callback,
    )
    param_tuning.save_replay_dataset(dataset, dataset_path)
    return dataset


def load_or_refresh_replay_dataset(
    args: argparse.Namespace,
    params: dict[str, Any],
    dataset_path: Path,
    sample_codes: list[str] | None = None,
    *,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    months = int(getattr(args, "months", 18) or 18)
    page_size = int(getattr(args, "page_size", 100) or 100)
    mode = str(getattr(args, "mode", "offline") or "offline")

    if bool(getattr(args, "rebuild_dataset", False)):
        print("开始构建历史回放数据集...", flush=True)
        return build_and_save_replay_dataset(
            params,
            dataset_path,
            months=months,
            sample_codes=sample_codes,
            page_size=page_size,
            use_item_cache=False,
            progress_callback=progress_callback,
        )

    if not dataset_path.exists():
        print("开始构建历史回放数据集...", flush=True)
        print("提示：将优先读取 data\\offline_tuning\\replay_items 下的单样本缓存。", flush=True)
        return build_and_save_replay_dataset(
            params,
            dataset_path,
            months=months,
            sample_codes=sample_codes,
            page_size=page_size,
            use_item_cache=True,
            progress_callback=progress_callback,
        )

    dataset = param_tuning.load_replay_dataset(dataset_path)
    if not _should_auto_refresh_dataset(args, dataset_path, sample_codes):
        return dataset

    local_codes = sample_codes if sample_codes is not None else param_tuning.discover_local_sample_codes()
    sync_status = param_tuning.inspect_replay_dataset_sync(
        dataset,
        local_sample_codes=local_codes,
        months=months,
    )
    if not sync_status["needs_refresh"]:
        print(
            "回放数据集已同步本地首日分时走势：CSV {csv_count} 个，可用样本 {sample_count} 个。".format(
                csv_count=len(sync_status.get("local_codes") or []),
                sample_count=dataset.get("available_count", 0),
            ),
            flush=True,
        )
        return dataset

    print("检测到本地首日分时走势与回放数据集不一致，开始自动更新数据集...", flush=True)
    if mode == "auto":
        print(
            "提示：这是自动调参前的数据集同步步骤；如外部数据源不可用，会自动回退到旧回放数据集继续调参。",
            flush=True,
        )
    for reason in sync_status.get("reasons") or []:
        print(f"- {reason}", flush=True)
    try:
        return build_and_save_replay_dataset(
            params,
            dataset_path,
            months=months,
            sample_codes=local_codes,
            page_size=page_size,
            use_item_cache=True,
            existing_dataset=dataset,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        print(f"自动更新回放数据集失败：{exc}", flush=True)
        print("本次将继续使用旧回放数据集；训练集/验证集暂不包含上述新增 CSV。", flush=True)
        print("网络恢复后重新运行调参入口即可再次自动同步；如需强制刷新，可加 --rebuild-dataset。", flush=True)
        if mode == "auto":
            print("继续进入自动调参搜索...", flush=True)
        return dataset


def _empty_retry_marker() -> dict[str, Any]:
    return {
        "pending_codes": [],
        "reasons_by_code": {},
        "last_download_attempt_date_by_code": {},
        "updated_at": "",
    }


def _load_retry_marker(path: Path) -> dict[str, Any]:
    marker = _empty_retry_marker()
    if not path.exists():
        return marker
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return marker
    if isinstance(payload, list):
        marker["pending_codes"] = _dedupe_codes(payload)
        return marker
    if not isinstance(payload, dict):
        return marker
    pending_codes = _dedupe_codes(payload.get("pending_codes") or payload.get("codes") or [])
    reasons_payload = payload.get("reasons_by_code") if isinstance(payload.get("reasons_by_code"), dict) else {}
    attempts_payload = (
        payload.get("last_download_attempt_date_by_code")
        if isinstance(payload.get("last_download_attempt_date_by_code"), dict)
        else {}
    )
    legacy_attempts_payload = (
        payload.get("last_retry_attempt_date_by_code")
        if isinstance(payload.get("last_retry_attempt_date_by_code"), dict)
        else {}
    )
    reasons_by_code = {
        code: _dedupe_codes(reasons_payload.get(code) if isinstance(reasons_payload, dict) else [])
        for code in pending_codes
    }
    attempt_dates = {
        code: _date_prefix(attempts_payload.get(code) or legacy_attempts_payload.get(code))
        for code in pending_codes
    }
    updated_date = _date_prefix(payload.get("updated_at"))
    for code in pending_codes:
        if not attempt_dates.get(code) and "download_errors" in reasons_by_code.get(code, []) and updated_date:
            attempt_dates[code] = updated_date
    marker.update(
        {
            "pending_codes": pending_codes,
            "reasons_by_code": reasons_by_code,
            "last_download_attempt_date_by_code": {code: date for code, date in attempt_dates.items() if date},
            "updated_at": str(payload.get("updated_at") or ""),
        }
    )
    return marker


def _load_retry_codes(path: Path) -> list[str]:
    return list(_load_retry_marker(path).get("pending_codes") or [])


def _same_day_download_cooldown_codes(
    marker: dict[str, Any],
    *,
    current_date: str | None = None,
) -> list[str]:
    today = current_date or _today_text()
    reasons_by_code = marker.get("reasons_by_code") if isinstance(marker.get("reasons_by_code"), dict) else {}
    attempt_dates = (
        marker.get("last_download_attempt_date_by_code")
        if isinstance(marker.get("last_download_attempt_date_by_code"), dict)
        else {}
    )
    cooldown_codes: list[str] = []
    for code in _dedupe_codes(marker.get("pending_codes") or []):
        reasons = _dedupe_codes(reasons_by_code.get(code) or [])
        if "download_errors" not in reasons:
            continue
        if _date_prefix(attempt_dates.get(code)) == today:
            cooldown_codes.append(code)
    return cooldown_codes


def _save_retry_codes(
    path: Path,
    codes: list[str],
    reasons_by_code: dict[str, list[str]],
    *,
    previous_marker: dict[str, Any] | None = None,
    download_attempted_codes: set[str] | list[str] | tuple[str, ...] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    retry_codes = _dedupe_codes(codes)
    previous_attempt_dates = {}
    if isinstance(previous_marker, dict) and isinstance(previous_marker.get("last_download_attempt_date_by_code"), dict):
        previous_attempt_dates = dict(previous_marker.get("last_download_attempt_date_by_code") or {})
    attempted_codes = set(_dedupe_codes(download_attempted_codes or []))
    today = _today_text()
    attempt_dates: dict[str, str] = {}
    for code in retry_codes:
        date = today if code in attempted_codes else _date_prefix(previous_attempt_dates.get(code))
        if date:
            attempt_dates[code] = date
    payload = {
        "schema": "offline_tuning_listing_data_retry_v1",
        "updated_at": _now_text(),
        "pending_codes": retry_codes,
        "reasons_by_code": {code: reasons_by_code.get(code, []) for code in retry_codes},
        "last_download_attempt_date_by_code": attempt_dates,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _history_row_retry_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    data_quality = str(row.get("data_quality") or "").strip()
    missing_fields = str(row.get("missing_fields") or "").strip()
    download_errors = str(row.get("download_errors") or "").strip()
    parse_errors = str(row.get("parse_errors") or "").strip()

    if not _truthy(row.get("issue_pdf_found")):
        reasons.append("missing_issue_announcement")
    if not _truthy(row.get("result_pdf_found")):
        reasons.append("missing_result_announcement")
    if download_errors:
        reasons.append("download_errors")
    if parse_errors:
        reasons.append("parse_errors")
    if data_quality.startswith("needs_"):
        reasons.append(data_quality)
    if missing_fields and not _truthy(row.get("model_ready")):
        reasons.append("missing_fields:" + missing_fields)
    return _dedupe_codes(reasons)


def collect_listing_data_retry_reasons(
    rows_by_code: dict[str, dict[str, Any]],
    *,
    pending_before: list[str] | None = None,
) -> dict[str, list[str]]:
    reasons_by_code: dict[str, list[str]] = {}
    for code, row in rows_by_code.items():
        reasons = _history_row_retry_reasons(row)
        if reasons:
            reasons_by_code[code] = reasons
    for code in _dedupe_codes(pending_before):
        if code not in rows_by_code and code not in reasons_by_code:
            reasons_by_code[code] = ["pending_code_not_in_current_history"]
    return reasons_by_code


def _load_csv_rows_by_code(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        rows = [dict(row) for row in csv.DictReader(file_obj)]
    return {
        str(row.get("security_code") or "").strip(): row
        for row in rows
        if str(row.get("security_code") or "").strip()
    }


def _load_replay_wrappers(item_dir: Path) -> dict[str, dict[str, Any]]:
    wrappers: dict[str, dict[str, Any]] = {}
    if not item_dir.exists():
        return wrappers
    for item_path in sorted(item_dir.glob("*.json")):
        code = item_path.stem.strip()
        if not code:
            continue
        try:
            payload = json.loads(item_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            wrappers[code] = {"code": code, "load_error": "invalid_json"}
            continue
        if isinstance(payload, dict):
            wrappers[code] = payload
    return wrappers


def _discover_intraday_codes(intraday_dir: Path) -> set[str]:
    if not intraday_dir.exists():
        return set()
    return {
        path.stem[:6]
        for path in intraday_dir.glob("*.csv")
        if len(path.stem) >= 6 and path.stem[:6].isdigit()
    }


def _dataset_items_by_code(dataset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("SECURITY_CODE") or "").strip(): item
        for item in dataset.get("items") or []
        if isinstance(item, dict) and str(item.get("SECURITY_CODE") or "").strip()
    }


def _pdf_signature_state(wrapper: dict[str, Any] | None) -> dict[str, Any]:
    pdf_signature = (wrapper or {}).get("pdf_signature")
    if not isinstance(pdf_signature, dict):
        return {"keys": [], "present_count": 0, "missing_count": 0}
    present_count = 0
    missing_count = 0
    for value in pdf_signature.values():
        if value:
            present_count += 1
        else:
            missing_count += 1
    return {
        "keys": sorted(str(key) for key in pdf_signature.keys()),
        "present_count": present_count,
        "missing_count": missing_count,
    }


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _build_sample_entry(
    code: str,
    *,
    in_intraday_csv: bool,
    dataset_item: dict[str, Any] | None,
    replay_wrapper: dict[str, Any] | None,
    history_row: dict[str, Any] | None,
    label_row: dict[str, Any] | None,
    retry_reasons: list[str] | None = None,
    pending_retry_before: bool = False,
) -> dict[str, Any]:
    dataset_item = dataset_item or {}
    history_row = history_row or {}
    label_row = label_row or {}
    replay_wrapper = replay_wrapper or {}
    wrapper_item = replay_wrapper.get("item") if isinstance(replay_wrapper.get("item"), dict) else {}
    replay_item = dataset_item or wrapper_item
    old_shares_meta = replay_item.get("old_shares_meta") if isinstance(replay_item.get("old_shares_meta"), dict) else {}
    comparable_summary = (
        replay_item.get("comparable_summary") if isinstance(replay_item.get("comparable_summary"), dict) else {}
    )
    derived_label = subscription_ladder_labels.derive_fields_from_ladder(label_row) if label_row else {}
    history_quality = build_subscription_history._row_quality_score(history_row) if history_row else 0
    top_apply_amount = _safe_float(
        history_row.get("top_apply_amount_wan")
        or label_row.get("top_apply_amount_wan")
        or replay_item.get("TOP_APPLY_MARKETCAP")
    )
    online_issue_shares = _safe_float(
        history_row.get("online_issue_shares")
        or label_row.get("online_issue_shares")
        or replay_item.get("ONLINE_ISSUE_NUM")
    )
    return {
        "security_code": code,
        "security_name_abbr": _first_non_empty(
            replay_item.get("SECURITY_NAME_ABBR"),
            history_row.get("security_name_abbr"),
            label_row.get("security_name_abbr"),
        ),
        "apply_date": _first_non_empty(
            replay_item.get("APPLY_DATE"),
            history_row.get("apply_date"),
            label_row.get("apply_date"),
        ),
        "listing_date": _first_non_empty(replay_item.get("LISTING_DATE"), history_row.get("listing_date")),
        "in_intraday_csv": bool(in_intraday_csv),
        "in_replay_dataset": bool(dataset_item),
        "has_replay_item": bool(replay_wrapper),
        "in_subscription_history": bool(history_row),
        "in_ladder_labels": bool(label_row),
        "method1_replay_available": bool(replay_item.get("method1_replay_available")),
        "has_intraday_file_in_replay": bool(replay_item.get("has_intraday_file")),
        "old_shares_source": str(old_shares_meta.get("source_file_type") or "").strip(),
        "old_shares_confidence": _safe_float(old_shares_meta.get("confidence")),
        "old_shares_pending_reason": str(old_shares_meta.get("pending_reason") or "").strip(),
        "comparable_code_count": len(replay_item.get("comparable_codes") or []),
        "comparable_returned_count": len(comparable_summary.get("returned_codes") or []),
        "comparable_reason": str(comparable_summary.get("reason") or "").strip(),
        "history_quality_score": history_quality,
        "model_ready": _truthy(history_row.get("model_ready")),
        "allocation_fit_ready": _truthy(history_row.get("allocation_fit_ready")),
        "allocation_fit_usable_for_tuning": _truthy(history_row.get("allocation_fit_usable_for_tuning")),
        "manual_ladder_label_ready": bool(derived_label.get("manual_ladder_label_ready")),
        "manual_ladder_item_count": int(derived_label.get("manual_ladder_item_count") or 0),
        "needs_listing_data_retry": bool(retry_reasons),
        "pending_listing_data_retry_before": bool(pending_retry_before),
        "listing_data_retry_reasons": list(retry_reasons or []),
        "top_apply_amount_wan": top_apply_amount,
        "online_issue_shares": online_issue_shares,
        "replay_item_generated_at": str(replay_wrapper.get("generated_at") or "").strip(),
        "replay_item_cache_version": replay_wrapper.get("cache_version"),
        "replay_pdf_signature": _pdf_signature_state(replay_wrapper),
    }


def build_sample_manifest(
    *,
    dataset: dict[str, Any],
    dataset_path: Path = DEFAULT_DATASET_PATH,
    history_path: Path = DEFAULT_HISTORY_PATH,
    ladder_label_path: Path = DEFAULT_LADDER_LABEL_PATH,
    replay_item_dir: Path = DEFAULT_REPLAY_ITEM_DIR,
    intraday_dir: Path = DEFAULT_INTRADAY_DIR,
    sync_summary: dict[str, Any] | None = None,
    retry_reasons_by_code: dict[str, list[str]] | None = None,
    pending_retry_before_codes: list[str] | None = None,
) -> dict[str, Any]:
    dataset_items = _dataset_items_by_code(dataset)
    replay_wrappers = _load_replay_wrappers(replay_item_dir)
    history_rows = _load_csv_rows_by_code(history_path)
    label_rows = {
        str(row.get("security_code") or "").strip(): row
        for row in subscription_ladder_labels.load_label_rows(ladder_label_path)
        if str(row.get("security_code") or "").strip()
    }
    intraday_codes = _discover_intraday_codes(intraday_dir)
    requested_codes = {
        str(code or "").strip()
        for code in (dataset.get("requested_codes") or dataset.get("sample_codes") or [])
        if str(code or "").strip()
    }
    retry_reasons_by_code = retry_reasons_by_code or {}
    pending_retry_before = set(_dedupe_codes(pending_retry_before_codes))
    all_codes = sorted(
        set(dataset_items)
        | set(replay_wrappers)
        | set(history_rows)
        | set(label_rows)
        | intraday_codes
        | requested_codes
        | set(retry_reasons_by_code)
        | pending_retry_before
    )
    samples = [
        _build_sample_entry(
            code,
            in_intraday_csv=code in intraday_codes,
            dataset_item=dataset_items.get(code),
            replay_wrapper=replay_wrappers.get(code),
            history_row=history_rows.get(code),
            label_row=label_rows.get(code),
            retry_reasons=retry_reasons_by_code.get(code),
            pending_retry_before=code in pending_retry_before,
        )
        for code in all_codes
    ]
    label_only_codes = sorted(set(label_rows) - set(history_rows) - set(dataset_items))
    dataset_only_codes = sorted(set(dataset_items) - set(intraday_codes))
    intraday_only_codes = sorted(intraday_codes - requested_codes)
    retry_codes = sorted(code for code, reasons in retry_reasons_by_code.items() if reasons)
    return {
        "schema": MANIFEST_SCHEMA,
        "generated_at": _now_text(),
        "paths": {
            "dataset": str(dataset_path),
            "replay_items": str(replay_item_dir),
            "history": str(history_path),
            "ladder_labels": str(ladder_label_path),
            "intraday": str(intraday_dir),
        },
        "summary": {
            "intraday_csv_count": len(intraday_codes),
            "replay_item_count": len(replay_wrappers),
            "replay_dataset_count": len(dataset_items),
            "replay_requested_count": len(requested_codes),
            "subscription_history_count": len(history_rows),
            "ladder_label_count": len(label_rows),
            "label_only_count": len(label_only_codes),
            "intraday_only_count": len(intraday_only_codes),
            "dataset_only_count": len(dataset_only_codes),
            "method1_ready_count": sum(1 for item in samples if item.get("method1_replay_available")),
            "model_ready_count": sum(1 for item in samples if item.get("model_ready")),
            "manual_ladder_ready_count": sum(1 for item in samples if item.get("manual_ladder_label_ready")),
            "listing_data_retry_count": len(retry_codes),
        },
        "differences": {
            "label_only_codes": label_only_codes,
            "intraday_only_codes": intraday_only_codes,
            "dataset_only_codes": dataset_only_codes,
            "listing_data_retry_codes": retry_codes,
        },
        "sync_summary": sync_summary or {},
        "samples": samples,
    }


def save_manifest(manifest: dict[str, Any], path: Path = DEFAULT_MANIFEST_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _sync_replay_dataset(
    args: argparse.Namespace,
    params: dict[str, Any],
    *,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    tuning_settings = config_loader.get_tuning_runtime_settings(params)
    dataset_path = _as_path(getattr(args, "dataset_path", None), DEFAULT_DATASET_PATH)
    raw_mode = str(getattr(args, "mode", "offline") or "offline")
    refresh_mode = raw_mode if raw_mode in {"offline", "observe", "auto"} else "offline"
    refresh_args = SimpleNamespace(
        rebuild_dataset=bool(getattr(args, "rebuild_dataset", False)),
        mode=refresh_mode,
        no_auto_refresh_dataset=bool(getattr(args, "no_auto_refresh_dataset", False)),
        months=int(getattr(args, "months", None) or tuning_settings["tuning_replay_months"]),
        page_size=int(getattr(args, "page_size", None) or tuning_settings["tuning_page_size"]),
    )
    sample_codes = _parse_csv_codes(getattr(args, "sample_codes", None))
    return load_or_refresh_replay_dataset(
        refresh_args,
        params,
        dataset_path,
        sample_codes=sample_codes,
        progress_callback=progress_callback,
    )


def sync_offline_tuning_dataset(
    args: argparse.Namespace,
    params: dict[str, Any],
    *,
    progress_callback: ProgressCallback | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    dataset_path = _as_path(getattr(args, "dataset_path", None), DEFAULT_DATASET_PATH)
    history_path = _as_path(getattr(args, "history_path", None), DEFAULT_HISTORY_PATH)
    ladder_label_path = _as_path(getattr(args, "ladder_label_path", None), DEFAULT_LADDER_LABEL_PATH)
    manifest_path = _as_path(getattr(args, "manifest_path", None), dataset_path.parent / "sample_manifest.json")
    replay_item_dir = _as_path(getattr(args, "replay_item_dir", None), dataset_path.parent / "replay_items")
    intraday_dir = _as_path(getattr(args, "intraday_dir", None), DEFAULT_INTRADAY_DIR)
    retry_marker_path = _as_path(
        getattr(args, "retry_marker_path", None),
        dataset_path.parent / "deferred_listing_data_codes.json",
    )

    retry_marker_before = _load_retry_marker(retry_marker_path)
    pending_before = list(retry_marker_before.get("pending_codes") or [])
    same_day_download_cooldown = set(_same_day_download_cooldown_codes(retry_marker_before))
    download_missing = not bool(getattr(args, "no_download_missing_announcements", False))
    if verbose:
        print(
            "选项 2 刷新范围：估值 replay 数据集、申购 history、手工阶梯标签上下文、样本 manifest。",
            flush=True,
        )
        if pending_before:
            if download_missing:
                retry_now_count = len([code for code in pending_before if code not in same_day_download_cooldown])
                print(
                    f"检测到 {len(pending_before)} 只公告/字段待重试样本；"
                    f"{retry_now_count} 只本次会重新尝试下载缺失公告并解析，"
                    f"{len(same_day_download_cooldown)} 只今天已下载失败，本次跳过下载仅解析本地已有公告。",
                    flush=True,
                )
            else:
                print(
                    f"检测到 {len(pending_before)} 只公告/字段待重试样本；本次关闭缺失公告下载，仅解析本地已有公告。",
                    flush=True,
                )

    if verbose:
        print("开始同步估值 replay 数据集...", flush=True)
    dataset = _sync_replay_dataset(
        args,
        params,
        progress_callback=progress_callback,
    )
    item_cache = dataset.get("item_cache") or {}
    if verbose:
        print(
            "估值 replay 样本数：{count}；缓存命中 {hits}，复用旧聚合 {reused}，新建 {misses}，写入 {writes}。".format(
                count=dataset.get("available_count", 0),
                hits=item_cache.get("hits", 0),
                reused=item_cache.get("existing_dataset_reused", 0),
                misses=item_cache.get("misses", 0),
                writes=item_cache.get("writes", 0),
            ),
            flush=True,
        )

    download_retries = max(int(getattr(args, "download_retries", 1) or 1), 1)
    download_delay_seconds = max(float(getattr(args, "download_delay_seconds", 0.0) or 0.0), 0.0)
    parse_prospectus = bool(getattr(args, "parse_prospectus", False))

    if verbose:
        if download_missing:
            print("开始同步申购 history：缺发行公告/发行结果公告时默认下载并解析。", flush=True)
        else:
            print("开始同步申购 history：本次仅解析本地已有公告。", flush=True)
    history_heartbeat = _SubscriptionHistoryHeartbeat(interval_seconds=15.0) if verbose else None
    history_download_attempted_codes: set[str] = set()

    def _history_progress(event: dict[str, Any]) -> None:
        if event.get("event") == "download_start":
            code = str(event.get("code") or "").strip()
            if code:
                history_download_attempted_codes.add(code)
        if history_heartbeat is not None:
            history_heartbeat.update(event)

    if history_heartbeat is not None:
        history_heartbeat.start()
    try:
        history_summary = build_subscription_history.build_subscription_history_table(
            dataset_path=dataset_path,
            output_path=history_path,
            ladder_label_path=ladder_label_path,
            download_missing_issue=download_missing,
            download_missing_result=download_missing,
            download_skip_codes=same_day_download_cooldown if download_missing else set(),
            parse_prospectus=parse_prospectus,
            download_retries=download_retries,
            download_delay_seconds=download_delay_seconds,
            progress_callback=_history_progress,
        )
    finally:
        if history_heartbeat is not None:
            history_heartbeat.stop()
    if verbose:
        print(
            "申购 history 行数：{row_count}；model_ready={model_ready_count}；手工标签行数 {ladder_label_rows}。".format(
                **history_summary
            ),
            flush=True,
        )

    history_rows = _load_csv_rows_by_code(history_path)
    retry_reasons_by_code = collect_listing_data_retry_reasons(
        history_rows,
        pending_before=pending_before,
    )
    retry_codes = sorted(retry_reasons_by_code)
    _save_retry_codes(
        retry_marker_path,
        retry_codes,
        retry_reasons_by_code,
        previous_marker=retry_marker_before,
        download_attempted_codes=history_download_attempted_codes,
    )
    if verbose:
        if retry_codes:
            print(
                f"仍有 {len(retry_codes)} 只样本公告或字段未取齐，已保留待重试标记：{retry_marker_path}",
                flush=True,
            )
        else:
            print("公告和申购 history 字段均已满足当前可解析条件，待重试标记已清空。", flush=True)

    sync_summary = {
        "replay": {
            "available_count": dataset.get("available_count", 0),
            "method1_ready_count": dataset.get("method1_ready_count", 0),
            "item_cache": item_cache,
        },
        "subscription_history": history_summary,
        "listing_data_retry": {
            "marker_path": str(retry_marker_path),
            "pending_before": pending_before,
            "pending_after": retry_codes,
            "pending_after_count": len(retry_codes),
            "download_missing_announcements": download_missing,
            "same_day_download_cooldown_codes": sorted(same_day_download_cooldown),
            "same_day_download_cooldown_count": len(same_day_download_cooldown),
            "download_attempted_codes": sorted(history_download_attempted_codes),
            "download_attempted_count": len(history_download_attempted_codes),
            "download_retries": download_retries,
            "download_delay_seconds": download_delay_seconds,
            "parse_prospectus": parse_prospectus,
        },
    }
    manifest = build_sample_manifest(
        dataset=dataset,
        dataset_path=dataset_path,
        history_path=history_path,
        ladder_label_path=ladder_label_path,
        replay_item_dir=replay_item_dir,
        intraday_dir=intraday_dir,
        sync_summary=sync_summary,
        retry_reasons_by_code=retry_reasons_by_code,
        pending_retry_before_codes=pending_before,
    )
    saved_manifest_path = save_manifest(manifest, manifest_path)
    return {
        "dataset": dataset,
        "history_summary": history_summary,
        "manifest": manifest,
        "manifest_path": saved_manifest_path,
        "retry_marker_path": retry_marker_path,
        "retry_reasons_by_code": retry_reasons_by_code,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步离线调参样本数据集并生成样本 manifest。")
    parser.add_argument("--params-file", type=Path, default=DEFAULT_PARAMS_PATH)
    parser.add_argument("--dataset-path", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--history-path", type=Path, default=DEFAULT_HISTORY_PATH)
    parser.add_argument("--ladder-label-path", type=Path, default=DEFAULT_LADDER_LABEL_PATH)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--replay-item-dir", type=Path, default=DEFAULT_REPLAY_ITEM_DIR)
    parser.add_argument("--intraday-dir", type=Path, default=DEFAULT_INTRADAY_DIR)
    parser.add_argument("--retry-marker-path", type=Path, default=DEFAULT_RETRY_MARKER_PATH)
    parser.add_argument("--rebuild-dataset", action="store_true")
    parser.add_argument("--no-auto-refresh-dataset", action="store_true")
    parser.add_argument("--months", type=int, default=None)
    parser.add_argument("--page-size", type=int, default=None)
    parser.add_argument("--sample-codes", help="限定同步的样本代码，逗号分隔；默认读取本地首日分时 CSV。")
    parser.add_argument(
        "--no-download-missing-announcements",
        action="store_true",
        help="不下载缺失的发行公告/发行结果公告，仅解析本地已有 PDF。",
    )
    parser.add_argument("--download-retries", type=int, default=1)
    parser.add_argument("--download-delay-seconds", type=float, default=0.0)
    parser.add_argument("--parse-prospectus", action="store_true")
    parser.add_argument("--json", action="store_true", help="输出机器可读摘要。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    params = config_loader.load_params(args.params_file)
    result = sync_offline_tuning_dataset(
        args,
        params,
        progress_callback=_default_dataset_progress,
        verbose=True,
    )
    manifest = result["manifest"]
    manifest_path = result["manifest_path"]
    summary = manifest.get("summary") or {}
    print(
        "样本 manifest 已写入：{path}；replay={replay_dataset_count}；history={subscription_history_count}；"
        "labels={ladder_label_count}；label_only={label_only_count}；retry={listing_data_retry_count}。".format(
            path=manifest_path,
            **summary,
        ),
        flush=True,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "manifest_path": str(manifest_path),
                    "retry_marker_path": str(result["retry_marker_path"]),
                    "summary": summary,
                    "retry_codes": sorted(result["retry_reasons_by_code"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
