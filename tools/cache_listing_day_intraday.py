from __future__ import annotations

import argparse
import csv
import io
import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
import sys
from typing import Any, Callable
import xml.etree.ElementTree as ET
import zipfile


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import config_loader
import data_fetcher
import tushare_helper


DEFAULT_OUTPUT_DIR = ROOT_DIR / "首日分时走势"
INTRADAY_CACHE_READY_TIME = time(15, 30)
LOCAL_INTRADAY_FILE_SUFFIXES = {".csv", ".tsv", ".xlsx", ".xls"}
MANUAL_FILE_SOURCE_API = "manual_file"
MANUAL_FILE_PROMPT = (
    "Tushare 和东方财富均未取到可用分钟线；可将该股票首日分钟 Excel/CSV 文件拖入项目根目录后重试，"
    "文件名建议包含股票代码。"
)
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


def _today_cache_block_reason(raw_listing_date: str | date | None, current_datetime: datetime | None = None) -> str:
    now = current_datetime or datetime.now()
    listing_date = date.fromisoformat(_normalize_trade_date(raw_listing_date))
    if listing_date != now.date():
        return ""
    if now.time() >= INTRADAY_CACHE_READY_TIME:
        return ""
    return (
        "上市日为今天，当前仍可能处于盘中或数据未完整阶段；"
        f"为避免写入不完整首日走势，请在 {INTRADAY_CACHE_READY_TIME.strftime('%H:%M')} 后重新运行缓存程序。"
    )


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


def _normalize_lookup_text(raw_value: Any) -> str:
    text = str(raw_value or "").strip().lower()
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"[\s_./\\:：,\-()]+", "", text)


def _find_column(headers: list[str], aliases: list[str], excluded: set[int] | None = None) -> int | None:
    excluded = excluded or set()
    normalized_headers = [_normalize_lookup_text(header) for header in headers]
    normalized_aliases = [_normalize_lookup_text(alias) for alias in aliases]

    for alias in normalized_aliases:
        for index, header in enumerate(normalized_headers):
            if index in excluded:
                continue
            if header == alias:
                return index

    for alias in normalized_aliases:
        for index, header in enumerate(normalized_headers):
            if index in excluded or not alias:
                continue
            if alias in header:
                return index
    return None


def _parse_number(raw_value: Any) -> float | None:
    if raw_value in (None, "", "--", "-", "—"):
        return None
    if isinstance(raw_value, (int, float)):
        return float(raw_value)

    text = str(raw_value).strip()
    if not text or text in {"--", "-", "—"}:
        return None
    text = text.replace(",", "").replace("，", "").replace("%", "")
    multiplier = 1.0
    for suffix, factor in (("亿元", 100_000_000.0), ("亿", 100_000_000.0), ("万元", 10_000.0), ("万", 10_000.0)):
        if text.endswith(suffix):
            multiplier = factor
            text = text[: -len(suffix)]
            break
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _excel_serial_to_datetime(raw_value: Any) -> datetime | None:
    value = _parse_number(raw_value)
    if value is None or value < 20_000 or value > 80_000:
        return None
    day_number = int(value)
    day_fraction = value - day_number
    total_minutes = int(round(day_fraction * 24 * 60))
    return datetime(1899, 12, 30) + timedelta(days=day_number, minutes=total_minutes)


def _parse_time_text(raw_value: Any) -> str:
    if raw_value in (None, ""):
        return ""

    if isinstance(raw_value, (int, float)):
        value = float(raw_value)
        if 0 <= value < 1:
            total_minutes = int(round(value * 24 * 60))
            return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"

    text = str(raw_value or "").strip()
    if not text:
        return ""
    numeric_value = _parse_number(text)
    if numeric_value is not None and 0 <= numeric_value < 1:
        return _parse_time_text(numeric_value)
    match = re.search(r"(\d{1,2}):(\d{2})(?::\d{2})?", text)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    if re.fullmatch(r"\d{3,4}", text):
        return f"{int(text[:-2]):02d}:{text[-2:]}"
    return ""


def _parse_intraday_datetime(raw_value: Any, fallback_date: str, fallback_time: Any = None) -> str:
    time_text = _parse_time_text(fallback_time)

    if isinstance(raw_value, (int, float)):
        value = float(raw_value)
        if value >= 1:
            parsed = _excel_serial_to_datetime(value)
            if parsed:
                return parsed.strftime("%Y/%m/%d %H:%M")
        if 0 <= value < 1 and fallback_date:
            raw_time_text = _parse_time_text(value)
            if raw_time_text:
                return f"{fallback_date.replace('-', '/')} {raw_time_text}"
        if time_text and fallback_date:
            return f"{fallback_date.replace('-', '/')} {time_text}"

    text = str(raw_value or "").strip()
    if not text:
        if time_text and fallback_date:
            return f"{fallback_date.replace('-', '/')} {time_text}"
        return ""

    numeric_value = _parse_number(text)
    if numeric_value is not None:
        if numeric_value >= 1:
            parsed = _excel_serial_to_datetime(numeric_value)
            if parsed:
                return parsed.strftime("%Y/%m/%d %H:%M")
        if 0 <= numeric_value < 1 and fallback_date:
            raw_time_text = _parse_time_text(numeric_value)
            if raw_time_text:
                return f"{fallback_date.replace('-', '/')} {raw_time_text}"
        if time_text and fallback_date:
            return f"{fallback_date.replace('-', '/')} {time_text}"

    normalized_text = (
        text.replace("T", " ")
        .replace("/", "-")
        .replace("年", "-")
        .replace("月", "-")
        .replace("日", " ")
    )
    normalized_text = re.sub(r"\s+", " ", normalized_text).strip()
    if re.fullmatch(r"\d{8}", normalized_text):
        normalized_text = f"{normalized_text[:4]}-{normalized_text[4:6]}-{normalized_text[6:8]}"

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d %H:%M:%S", "%Y%m%d %H:%M"):
        try:
            return datetime.strptime(normalized_text, fmt).strftime("%Y/%m/%d %H:%M")
        except ValueError:
            pass

    parsed_date = _normalize_trade_date(normalized_text)
    if parsed_date and time_text:
        return f"{parsed_date.replace('-', '/')} {time_text}"

    raw_time_text = _parse_time_text(normalized_text)
    if raw_time_text and fallback_date:
        return f"{fallback_date.replace('-', '/')} {raw_time_text}"

    return ""


def _amount_scale_for_header(header: str) -> float:
    text = str(header or "")
    if "亿元" in text:
        return 100_000_000.0
    if "百万" in text:
        return 1_000_000.0
    if "万元" in text:
        return 10_000.0
    if "千元" in text:
        return 1_000.0
    return 1.0


def _volume_scale_for_header(header: str) -> float:
    return 100.0 if "手" in str(header or "") else 1.0


def _scaled_number_or_zero(raw_value: Any, scale: float) -> float:
    value = _parse_number(raw_value)
    if value is None:
        return 0.0
    scaled = round(value * scale, 6)
    if abs(scaled - round(scaled)) < 0.000001:
        return float(round(scaled))
    return scaled


def _cell_at(row: list[Any], index: int | None) -> Any:
    if index is None or index < 0 or index >= len(row):
        return None
    return row[index]


def _cell_code_matches(raw_value: Any, target_code: str) -> bool:
    text = str(raw_value or "").strip().upper()
    if not text:
        return False
    text = text.split(" ", 1)[0]
    if text.endswith(".0"):
        text = text[:-2]
    return _base_code(text) == _base_code(target_code)


def _build_local_column_map(headers: list[str]) -> dict[str, int | None]:
    datetime_index = _find_column(
        headers,
        ["DateTime", "trade_time", "datetime", "日期时间", "交易时间", "日期", "时间"],
    )
    return {
        "code": _find_column(headers, ["代码", "证券代码", "ts_code", "code"]),
        "datetime": datetime_index,
        "time": _find_column(headers, ["时间", "time"], excluded={datetime_index} if datetime_index is not None else set()),
        "open": _find_column(headers, ["open", "开盘价", "开盘"]),
        "high": _find_column(headers, ["high", "最高价", "最高"]),
        "low": _find_column(headers, ["low", "最低价", "最低"]),
        "close": _find_column(headers, ["close", "收盘价", "最新价", "收盘"]),
        "volume": _find_column(headers, ["volume", "vol", "成交量", "成交股数"]),
        "amount": _find_column(headers, ["amount", "成交额", "成交金额"]),
    }


def _parse_local_intraday_matrix(
    matrix: list[list[Any]],
    code: str,
    trade_date: str,
    file_path: Path,
) -> tuple[list[dict[str, Any]], str]:
    filename_matches_code = _base_code(code) in file_path.stem.upper()
    header_index: int | None = None
    column_map: dict[str, int | None] = {}
    headers: list[str] = []

    for index, row in enumerate(matrix[:30]):
        current_headers = [str(value or "").strip() for value in row]
        current_map = _build_local_column_map(current_headers)
        required_count = sum(current_map.get(key) is not None for key in ("datetime", "open", "high", "low", "close"))
        if required_count >= 5:
            header_index = index
            column_map = current_map
            headers = current_headers
            break

    if header_index is None:
        return [], "未识别到分钟线表头"

    rows_by_time: dict[str, dict[str, Any]] = {}
    trade_date_prefix = trade_date.replace("-", "/")
    amount_scale = _amount_scale_for_header(str(_cell_at(headers, column_map.get("amount")) or ""))
    volume_scale = _volume_scale_for_header(str(_cell_at(headers, column_map.get("volume")) or ""))
    skipped_code_mismatch = 0
    skipped_bad_price = 0

    for row in matrix[header_index + 1 :]:
        if not any(str(value or "").strip() for value in row):
            continue

        code_index = column_map.get("code")
        if code_index is not None:
            if not _cell_code_matches(_cell_at(row, code_index), code):
                skipped_code_mismatch += 1
                continue
        elif not filename_matches_code:
            skipped_code_mismatch += 1
            continue

        dt = _parse_intraday_datetime(
            _cell_at(row, column_map.get("datetime")),
            fallback_date=trade_date,
            fallback_time=_cell_at(row, column_map.get("time")),
        )
        if not dt or not dt.startswith(trade_date_prefix):
            continue

        open_price = _parse_number(_cell_at(row, column_map.get("open")))
        high_price = _parse_number(_cell_at(row, column_map.get("high")))
        low_price = _parse_number(_cell_at(row, column_map.get("low")))
        close_price = _parse_number(_cell_at(row, column_map.get("close")))
        if None in {open_price, high_price, low_price, close_price} or min(open_price, high_price, low_price, close_price) <= 0:
            skipped_bad_price += 1
            continue

        rows_by_time[dt] = {
            "DateTime": dt,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": _scaled_number_or_zero(_cell_at(row, column_map.get("volume")), volume_scale),
            "amount": _scaled_number_or_zero(_cell_at(row, column_map.get("amount")), amount_scale),
        }

    rows = [rows_by_time[key] for key in sorted(rows_by_time)]
    if rows:
        return rows, ""
    if skipped_code_mismatch:
        return [], f"文件中的代码与 {code} 不匹配"
    if skipped_bad_price:
        return [], "文件中价格字段缺失或存在非正数"
    return [], f"未解析到 {trade_date} 的分钟线"


def _column_index_from_cell_ref(cell_ref: str) -> int:
    match = re.match(r"([A-Z]+)", str(cell_ref or "").upper())
    if not match:
        return 0
    index = 0
    for char in match.group(1):
        index = index * 26 + ord(char) - ord("A") + 1
    return index - 1


def _coerce_xlsx_cell_value(text: str) -> Any:
    stripped = str(text or "").strip()
    if not stripped:
        return ""
    if re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
        number = float(stripped)
        return int(number) if number.is_integer() else number
    return stripped


def _read_xlsx_matrices(path: Path) -> list[list[list[Any]]]:
    main_ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    package_rel_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"

    with zipfile.ZipFile(path) as archive:
        archive_names = set(archive.namelist())
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive_names:
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in shared_root.findall(f"{main_ns}si"):
                shared_strings.append("".join(text.text or "" for text in item.iter(f"{main_ns}t")))

        rel_targets: dict[str, str] = {}
        if "xl/_rels/workbook.xml.rels" in archive_names:
            rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            for rel in rel_root.findall(f"{package_rel_ns}Relationship"):
                rel_id = str(rel.attrib.get("Id") or "")
                target = str(rel.attrib.get("Target") or "")
                if target.startswith("/"):
                    target = target.lstrip("/")
                elif not target.startswith("xl/"):
                    target = f"xl/{target}"
                if rel_id and target:
                    rel_targets[rel_id] = target

        sheet_paths: list[str] = []
        if "xl/workbook.xml" in archive_names:
            workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
            for sheet in workbook_root.findall(f"{main_ns}sheets/{main_ns}sheet"):
                rel_id = str(sheet.attrib.get(f"{rel_ns}id") or "")
                target = rel_targets.get(rel_id)
                if target and target in archive_names:
                    sheet_paths.append(target)
        if not sheet_paths:
            sheet_paths = sorted(name for name in archive_names if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))

        matrices: list[list[list[Any]]] = []
        for sheet_path in sheet_paths:
            sheet_root = ET.fromstring(archive.read(sheet_path))
            matrix: list[list[Any]] = []
            for row_node in sheet_root.findall(f"{main_ns}sheetData/{main_ns}row"):
                cells: dict[int, Any] = {}
                for cell in row_node.findall(f"{main_ns}c"):
                    cell_type = cell.attrib.get("t")
                    value_node = cell.find(f"{main_ns}v")
                    value_text = "" if value_node is None or value_node.text is None else value_node.text
                    if cell_type == "s":
                        value = shared_strings[int(value_text)] if value_text.isdigit() and int(value_text) < len(shared_strings) else ""
                    elif cell_type == "inlineStr":
                        value = "".join(text.text or "" for text in cell.iter(f"{main_ns}t"))
                    else:
                        value = _coerce_xlsx_cell_value(value_text)
                    cells[_column_index_from_cell_ref(str(cell.attrib.get("r") or ""))] = value
                if cells:
                    max_index = max(cells)
                    matrix.append([cells.get(index, "") for index in range(max_index + 1)])
            if matrix:
                matrices.append(matrix)
        return matrices


def _read_delimited_matrix(path: Path) -> list[list[Any]]:
    errors: list[str] = []
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            text = path.read_text(encoding=encoding)
        except UnicodeError as exc:
            errors.append(f"{encoding}: {exc}")
            continue
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;")
        except csv.Error:
            dialect = csv.excel_tab if delimiter == "\t" else csv.excel
        return [list(row) for row in csv.reader(io.StringIO(text), dialect)]
    raise ValueError("CSV 编码无法识别：" + "；".join(errors))


def _read_local_intraday_file(path: Path, code: str, trade_date: str) -> tuple[list[dict[str, Any]], str]:
    suffix = path.suffix.lower()
    if suffix == ".xls":
        return [], "暂不支持旧版 .xls，请另存为 .xlsx 或 .csv 后重试"
    matrices = _read_xlsx_matrices(path) if suffix == ".xlsx" else [_read_delimited_matrix(path)]
    reasons: list[str] = []
    for matrix in matrices:
        rows, reason = _parse_local_intraday_matrix(matrix, code=code, trade_date=trade_date, file_path=path)
        if rows:
            return rows, ""
        if reason:
            reasons.append(reason)
    return [], "；".join(reasons) or "未解析到可用分钟线"


def _local_intraday_file_candidates(code: str, search_dir: Path) -> list[Path]:
    base_code = _base_code(code).upper()
    ts_code = f"{base_code}.BJ" if base_code.startswith("920") else base_code
    candidates = [
        path
        for path in search_dir.iterdir()
        if path.is_file() and path.suffix.lower() in LOCAL_INTRADAY_FILE_SUFFIXES
    ]
    candidates.sort(
        key=lambda path: (
            0 if base_code in path.stem.upper() or ts_code in path.stem.upper() else 1,
            -path.stat().st_mtime,
            path.name,
        )
    )
    return candidates


def _fetch_local_intraday_file(code: str, trade_date: str, search_dir: Path | None = None) -> dict[str, Any]:
    search_path = search_dir or ROOT_DIR
    candidates = _local_intraday_file_candidates(code, search_path)
    attempted_files: list[str] = []
    failure_reasons: list[str] = []

    for path in candidates:
        attempted_files.append(path.name)
        try:
            rows, reason = _read_local_intraday_file(path, code=code, trade_date=trade_date)
        except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
            rows = []
            reason = str(exc)
        if rows:
            return {
                "rows": rows,
                "summary": {
                    "source_api": MANUAL_FILE_SOURCE_API,
                    "source_path": str(path),
                    "attempted_files": attempted_files,
                    "reason": "",
                },
            }
        failure_reasons.append(f"{path.name}: {reason}")

    reason = MANUAL_FILE_PROMPT
    if failure_reasons:
        reason = "已检查项目根目录文件但未能解析：" + "；".join(failure_reasons[:5]) + "；" + MANUAL_FILE_PROMPT
    return {
        "rows": [],
        "summary": {
            "source_api": MANUAL_FILE_SOURCE_API,
            "source_path": "",
            "attempted_files": attempted_files,
            "reason": reason,
        },
    }


def _cache_single_candidate(
    item: dict[str, Any],
    output_path: Path,
    strategy_params: dict[str, Any],
    force: bool = False,
    current_datetime: datetime | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    code = str(item.get("SECURITY_CODE") or "").strip()
    name = str(item.get("SECURITY_NAME_ABBR") or item.get("SECURITY_NAME") or "").strip()
    listing_date = _normalize_trade_date(item.get("LISTING_DATE"))
    csv_path = output_path / f"{_base_code(code)}.csv"

    block_reason = _today_cache_block_reason(listing_date, current_datetime=current_datetime)
    if block_reason:
        return {
            "status": "deferred",
            "code": code,
            "name": name,
            "listing_date": listing_date,
            "reason": block_reason,
            "source_api": "intraday_guard",
            "attempted_apis": [],
        }

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
        attempted_apis = list(fetch_summary.get("attempted_apis") or [])
        tushare_reason = str(fetch_summary.get("reason") or "Tushare 未返回可用分钟线。")
        source_failures = [
            {
                "source": "Tushare",
                "source_api": fetch_summary.get("source_api") or "tushare",
                "reason": tushare_reason,
            }
        ]
        _emit_progress(
            progress_callback,
            "source_failed",
            {
                "code": code,
                "name": name,
                "listing_date": listing_date,
                "source": "Tushare",
                "source_api": fetch_summary.get("source_api") or "tushare",
                "reason": tushare_reason,
            },
        )

        eastmoney_reason = ""
        try:
            rows = data_fetcher.fetch_intraday_trends(code, trade_date=listing_date)
        except data_fetcher.DataFetcherError as exc:
            eastmoney_reason = str(exc)
        else:
            written_path = tushare_helper.write_intraday_csv(code, rows, output_dir=output_path)
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
                "source_failures": source_failures,
            }

        source_failures.append(
            {
                "source": "东方财富",
                "source_api": "eastmoney_trends2",
                "reason": eastmoney_reason,
            }
        )
        _emit_progress(
            progress_callback,
            "source_failed",
            {
                "code": code,
                "name": name,
                "listing_date": listing_date,
                "source": "东方财富",
                "source_api": "eastmoney_trends2",
                "reason": eastmoney_reason,
            },
        )

        local_search_dir = Path(str(strategy_params.get("manual_intraday_file_root") or ROOT_DIR))
        local_result = _fetch_local_intraday_file(code, listing_date, search_dir=local_search_dir)
        local_rows = local_result.get("rows") or []
        local_summary = local_result.get("summary") or {}
        attempted_apis.extend(["eastmoney_trends2", MANUAL_FILE_SOURCE_API])
        if local_rows:
            written_path = tushare_helper.write_intraday_csv(code, local_rows, output_dir=output_path)
            return {
                "status": "cached",
                "code": code,
                "name": name,
                "listing_date": listing_date,
                "path": str(written_path),
                "rows": len(local_rows),
                "source_api": MANUAL_FILE_SOURCE_API,
                "source_path": local_summary.get("source_path"),
                "attempted_apis": attempted_apis,
                "attempted_files": list(local_summary.get("attempted_files") or []),
                "source_failures": source_failures,
            }

        manual_reason = str(local_summary.get("reason") or MANUAL_FILE_PROMPT)
        reason = f"Tushare 取数失败：{tushare_reason}；东方财富取数失败：{eastmoney_reason}；{manual_reason}"
        status = "deferred" if "数据不精确" in eastmoney_reason else "error"
        return {
            "status": status,
            "code": code,
            "name": name,
            "listing_date": listing_date,
            "reason": reason,
            "source_api": MANUAL_FILE_SOURCE_API,
            "attempted_apis": attempted_apis,
            "attempted_files": list(local_summary.get("attempted_files") or []),
            "source_failures": source_failures,
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
    current_datetime: datetime | None = None,
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
        result = _cache_single_candidate(item, output_path, strategy_params, force=force, current_datetime=current_datetime)
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
    current_datetime: datetime | None = None,
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
        result = _cache_single_candidate(
            item,
            output_path,
            strategy_params,
            force=False,
            current_datetime=current_datetime,
            progress_callback=progress_callback,
        )
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
