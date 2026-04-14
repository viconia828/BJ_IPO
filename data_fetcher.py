from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any


BASE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
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
