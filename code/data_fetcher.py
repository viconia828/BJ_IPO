from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any


BASE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EASTMONEY_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
EASTMONEY_QUOTE_FIELDS = "f57,f58,f43,f108,f116,f117,f162,f163,f164,f167,f277"
EASTMONEY_UT = "fa5fd1943c7b386f172d6893dbfba10b"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://data.eastmoney.com/",
}
NUMERIC_FIELDS = {
    "AMPLITUDE",
    "AFTER_ISSUE_PE",
    "APPLY_AMT_UPPER",
    "CHANGE",
    "CHANGE_RATE",
    "CLOSE_PRICE",
    "DEC_SUMFINA",
    "INDUSTRY_PE_NEW",
    "ISSUE_NUM",
    "ISSUE_PRICE",
    "LATELY_PRICE",
    "LD_CLOSE_CHANGE",
    "LD_OPEN_PREMIUM",
    "NETSUMFINA",
    "ONLINE_APPLY_LOWER",
    "ONLINE_APPLY_UPPER",
    "ONLINE_ES_MULTIPLE",
    "ONLINE_ISSUE_LWR",
    "ONLINE_ISSUE_NUM",
    "ONLINE_ISSUE_RATIO",
    "ONLINE_VA_NUM",
    "ONLINE_VA_SHARES",
    "OPEN_CHANGERATE",
    "OPEN_PRICE",
    "PER_SHARES_INCOME",
    "TCHANGE_RATE",
    "TOP_APPLY_MARKETCAP",
    "TOTAL_CHANGE",
    "TOTAL_ISSUE_NUM",
    "TURNOVERRATE",
}


class DataFetcherError(RuntimeError):
    pass


def safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        return 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28
    if month in {4, 6, 9, 11}:
        return 30
    return 31


def _subtract_months(current_date: date, months: int) -> date:
    year = current_date.year
    month = current_date.month - months
    while month <= 0:
        year -= 1
        month += 12
    return date(year, month, min(current_date.day, _days_in_month(year, month)))


def _build_query(params: dict[str, Any]) -> str:
    encoded = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"{BASE_URL}?{encoded}"


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    for key in NUMERIC_FIELDS:
        if key in normalized:
            numeric = safe_float(normalized.get(key))
            normalized[key] = numeric if numeric is not None else normalized.get(key)
    if "SECURITY_CODE" in normalized:
        normalized["SECURITY_CODE"] = str(normalized["SECURITY_CODE"]).strip()
    if "SECURITY_NAME_ABBR" in normalized and normalized["SECURITY_NAME_ABBR"] is not None:
        normalized["SECURITY_NAME_ABBR"] = str(normalized["SECURITY_NAME_ABBR"]).strip()
    return normalized


def _request_data(params: dict[str, Any]) -> list[dict[str, Any]]:
    request = urllib.request.Request(_build_query(params), headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise DataFetcherError(f"东方财富请求失败: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise DataFetcherError("东方财富返回内容无法解析为 JSON") from exc

    result = payload.get("result") or {}
    data = result.get("data") or []
    if not isinstance(data, list):
        raise DataFetcherError("东方财富返回结构异常，未找到 result.data")
    return [_normalize_record(item) for item in data]


def _request_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    request_url = f"{url}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"
    request = urllib.request.Request(request_url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise DataFetcherError(f"东方财富请求失败: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise DataFetcherError("东方财富返回内容无法解析为 JSON") from exc

    if not isinstance(payload, dict):
        raise DataFetcherError("东方财富返回结构异常，未找到 JSON 对象")
    return payload


def build_eastmoney_secid(code: str) -> str:
    normalized = str(code).strip().upper()
    if "." in normalized:
        base_code, market = normalized.split(".", 1)
    else:
        base_code, market = normalized, ""

    if market == "SH" or base_code.startswith(("600", "601", "603", "605", "688")):
        return f"1.{base_code}"
    if market in {"SZ", "BJ", "NQ"} or base_code.startswith(("000", "001", "002", "003", "300", "301", "920", "830", "831", "832", "833", "834", "835", "836", "837", "838")):
        return f"0.{base_code}"
    raise DataFetcherError(f"无法识别证券代码市场: {code}")


def fetch_equity_snapshot(code: str) -> dict[str, Any]:
    normalized_code = str(code).strip().upper()
    payload = _request_json(
        EASTMONEY_QUOTE_URL,
        {
            "secid": build_eastmoney_secid(normalized_code),
            "fields": EASTMONEY_QUOTE_FIELDS,
            "ut": EASTMONEY_UT,
            "invt": 2,
            "fltt": 2,
        },
    )
    data = payload.get("data") or {}
    if not isinstance(data, dict) or not data:
        raise DataFetcherError(f"东方财富未返回 {normalized_code} 的快照数据")

    close_raw = safe_float(data.get("f43"))
    eps_ttm_raw = safe_float(data.get("f108"))
    total_cap_raw = safe_float(data.get("f116"))
    float_cap_raw = safe_float(data.get("f117"))
    pe_ttm_direct_raw = safe_float(data.get("f164"))
    pe_alt_raw = safe_float(data.get("f162"))
    pb_raw = safe_float(data.get("f167"))
    total_shares_raw = safe_float(data.get("f277"))

    close_value = None
    if close_raw is not None:
        close_value = close_raw / 100 if abs(close_raw) >= 1000 else close_raw
    pe_ttm_value = pe_ttm_direct_raw
    if pe_ttm_value is None and close_value is not None and eps_ttm_raw not in (None, 0):
        pe_ttm_value = close_value / eps_ttm_raw

    return {
        "code": normalized_code,
        "name": str(data.get("f58", "") or "").strip() or normalized_code,
        "close": close_value,
        "pe_ttm": pe_ttm_value,
        "eps_ttm": eps_ttm_raw,
        "pb_lf": pb_raw,
        "mkt_cap": (total_cap_raw / 100000000) if total_cap_raw is not None else None,
        "float_mkt_cap": (float_cap_raw / 100000000) if float_cap_raw is not None else None,
        "total_shares": total_shares_raw,
        "trade_date": date.today().isoformat(),
        "price_basis": "quote_last",
        "source": "eastmoney_api",
        "raw_fields": {
            "f43": close_raw,
            "f108": eps_ttm_raw,
            "f116": total_cap_raw,
            "f117": float_cap_raw,
            "f162": pe_alt_raw,
            "f163": safe_float(data.get("f163")),
            "f164": pe_ttm_direct_raw,
            "f167": pb_raw,
            "f277": total_shares_raw,
        },
    }


def fetch_ipo_info(code: str) -> dict[str, Any]:
    params = {
        "reportName": "RPTA_APP_IPOAPPLY",
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
        "pageSize": 1,
        "pageNumber": 1,
        "filter": f'(SECURITY_CODE="{code}")',
    }
    data = _request_data(params)
    if not data:
        raise DataFetcherError(f"未查询到新股代码 {code} 的信息")
    return data[0]


def fetch_recent_ipos(months: int = 3, page_size: int = 50) -> list[dict[str, Any]]:
    cutoff = _subtract_months(date.today(), months).isoformat()
    page_number = 1
    records: list[dict[str, Any]] = []

    while True:
        params = {
            "reportName": "RPTA_APP_IPOAPPLY",
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
            "pageSize": page_size,
            "pageNumber": page_number,
            "sortColumns": "LISTING_DATE",
            "sortTypes": "-1",
            "filter": f'(MARKET_TYPE="北交所")(LISTING_DATE>=\'{cutoff}\')',
        }
        page_data = _request_data(params)
        if not page_data:
            break

        for item in page_data:
            if item.get("LISTING_DATE") and safe_float(item.get("CLOSE_PRICE")) is not None:
                records.append(item)

        if len(page_data) < page_size:
            break

        page_number += 1
        time.sleep(0.3)

    return records
