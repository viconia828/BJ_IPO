from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import listing_average_price_helper


DEFAULT_CORPUS_INDEX = ROOT_DIR / "data" / "xueqiu_corpus" / "index.json"
DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_SCAN_REPORT = ROOT_DIR / "调参" / "valuation_hit_rate_scan_202603plus_20260710_001437.json"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"


LOCAL_SAMPLE_ALIASES = {
    "爱伦医疗": ["爱舍伦"],
    "欧伦电气": ["欧伦电器"],
    "永励精密": ["永励", "永励精工"],
    "科莱瑞迪": ["科莱瑞迪医疗"],
}


RANGE_PATTERNS = [
    (
        "first_day_price_range_flexible",
        re.compile(
            r"(?:上市首日定价区间|上市首日股价区间|上市首日价格区间|首日股价区间|首日价格区间|首日定价区间)"
            r"[^0-9]{0,16}"
            r"(?P<low>\d+(?:\.\d+)?)元?"
            r"(?:-|~|～|至|到|,|，|、|—|－)+"
            r"(?P<high>\d+(?:\.\d+)?)元?"
        ),
    ),
    (
        "first_day_price_range",
        re.compile(
            r"(?:对应上市首日价格|对应上市首日股价|上市首日价格|首日价格区间为|"
            r"首日价格区间|首日预判股价|预判股价|首日股价|对应价格区间|对应股价)"
            r"[：:为约大概]*"
            r"(?P<low>\d+(?:\.\d+)?)元?"
            r"(?:-|~|～|至|到|,|，|、|—|－)+"
            r"(?P<high>\d+(?:\.\d+)?)元?"
        ),
    ),
    (
        "market_cap_price_range",
        re.compile(
            r"价格区间[：:为约大概]*"
            r"(?P<low>\d+(?:\.\d+)?)元?"
            r"(?:-|~|～|至|到|,|，|、|—|－)+"
            r"(?P<high>\d+(?:\.\d+)?)元?"
        ),
    ),
]

SINGLE_PRICE_PATTERNS = [
    (
        "base_formula_price",
        re.compile(
            r"首日价格为\s*\d+(?:\.\d+)?\s*\*[^=]{1,32}="
            r"(?P<price>\d+(?:\.\d+)?)元"
        ),
    ),
    (
        "lower_bound_price",
        re.compile(r"(?:不低于|不会低于|至少|保底|底部价格)[：:为约大概]*(?P<price>\d+(?:\.\d+)?)元"),
    ),
    (
        "target_single_price",
        re.compile(
            r"(?:可能达到|有望达到|预计达到|目标价|中枢价格|中枢价|首日中枢价格|首日目标价)"
            r"[：:为约大概]*(?P<price>\d+(?:\.\d+)?)元"
        ),
    ),
]

TARGET_PE_PATTERNS = [
    (
        "target_pe_range",
        re.compile(
            r"(?:给予|确定|对应|合理估值|基本面合理估值|上市首日定价区间|定价区间|估值区间|首日估值|PE)"
            r"[^。\n]{0,24}?"
            r"(?P<low>\d+(?:\.\d+)?)\s*[-~～至到]\s*(?P<high>\d+(?:\.\d+)?)\s*(?:倍|X|x|PE)"
        ),
    ),
    (
        "generic_pe_range",
        re.compile(
            r"(?P<low>\d+(?:\.\d+)?)\s*[-~～至到]\s*(?P<high>\d+(?:\.\d+)?)\s*"
            r"(?:倍|X|x)(?:PE|动态PE|静态PE|估值|的合理估值区间|的合理估值)?"
        ),
    ),
    (
        "target_pe",
        re.compile(
            r"(?:暂给予|给予|参考|我认为|可以达到|达到)[^。\n]{0,20}?"
            r"(?<![-~～至到\d])(?P<pe>\d+(?:\.\d+)?)(?!\s*[-~～至到]\s*\d)\s*(?:倍|X|x)(?:PE|估值|相对合理)?"
        ),
    ),
]


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp)
        except (OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("T", " ").replace("Z", "")
    text = re.sub(r"\.\d+$", "", text)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _date_text(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _compact_text(text: str) -> str:
    text = (text or "").replace("\u00a0", "")
    text = text.replace("－", "-").replace("—", "-").replace("～", "-")
    return re.sub(r"\s+", "", text)


def _clean_title_name(title: str) -> str:
    title = re.sub(r"\$[^$]+\$", "", title or "")
    title = re.sub(r"(北交新股|新股|上市估值分析|上市估值|上市首日价格分析|首日价格分析|上市前瞻|上市首日股价预判|上市股价预判|股价预判)", "", title)
    title = re.sub(r"\d+月\d+日.*$", "", title)
    title = title.strip(" ，,：:-")
    return title


def _aliases_for_item(item: dict[str, Any]) -> list[str]:
    name = str(item.get("SECURITY_NAME_ABBR") or "").strip()
    aliases = [name]
    aliases.extend(LOCAL_SAMPLE_ALIASES.get(name, []))
    if name.endswith("电气"):
        aliases.append(name[:-2] + "电器")
    if name.endswith("电器"):
        aliases.append(name[:-2] + "电气")
    return [alias for alias in dict.fromkeys(aliases) if alias]


def _load_dataset(path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    payload = _read_json(path)
    items = list(payload.get("items") or [])
    by_code = {str(item.get("SECURITY_CODE")): item for item in items if item.get("SECURITY_CODE")}
    return items, by_code


def _first_intraday_date(code: str, intraday_dir: Path) -> datetime | None:
    csv_path = intraday_dir / f"{code}.csv"
    if not csv_path.exists():
        return None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with csv_path.open("r", encoding=encoding) as handle:
                header = handle.readline()
                if not header:
                    continue
                first = handle.readline()
                if not first:
                    continue
                first_col = first.split(",", 1)[0].strip()
                return _parse_dt(first_col)
        except UnicodeDecodeError:
            continue
        except OSError:
            return None
    return None


def _resolve_listing_date(item: dict[str, Any], intraday_dir: Path) -> tuple[datetime | None, str]:
    code = str(item.get("SECURITY_CODE") or "").strip()
    dataset_dt = _parse_dt(item.get("LISTING_DATE"))
    csv_dt = _first_intraday_date(code, intraday_dir)
    if csv_dt and dataset_dt:
        # The replay cache can lag or carry a provisional listing date for the newest names.
        if abs((csv_dt.date() - dataset_dt.date()).days) > 2:
            return csv_dt, "intraday_csv_date"
    if dataset_dt:
        return dataset_dt, "replay_dataset"
    if csv_dt:
        return csv_dt, "intraday_csv_date"
    return None, ""


def _resolve_average_price(item: dict[str, Any], intraday_dir: Path) -> tuple[float | None, str, str]:
    average_price = _safe_float(item.get("AVERAGE_PRICE"))
    if average_price is not None and average_price > 0:
        return average_price, str(item.get("average_price_source") or "replay_dataset").strip(), ""
    code = str(item.get("SECURITY_CODE") or "").strip()
    csv_result = listing_average_price_helper.calc_intraday_csv_average_price(code, intraday_dir=intraday_dir)
    average_price = _safe_float(csv_result.get("average_price"))
    if average_price is not None and average_price > 0:
        return average_price, "intraday_csv", str(csv_result.get("reason") or "")
    return None, str(csv_result.get("source") or ""), str(csv_result.get("reason") or "未取得首日成交均价")


def _normalize_code(code: str) -> str | None:
    text = str(code or "").strip().upper()
    match = re.search(r"920\d{3}", text)
    return match.group(0) if match else None


def _article_file_path(article: dict[str, Any]) -> Path | None:
    value = article.get("file")
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _load_articles(index_path: Path) -> list[dict[str, Any]]:
    payload = _read_json(index_path)
    loaded: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in payload.get("articles") or []:
        key = (str(item.get("user_id") or ""), str(item.get("status_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        path = _article_file_path(item)
        article = dict(item)
        if path and path.exists():
            try:
                article.update(_read_json(path))
            except (OSError, json.JSONDecodeError):
                article["load_error"] = str(path)
        loaded.append(article)
    return loaded


def _find_local_matches(article: dict[str, Any], dataset_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    title = str(article.get("title") or "")
    page_title = str(article.get("page_title") or "")
    title_haystack = _compact_text(title)
    page_title_haystack = _compact_text(page_title)
    codes: set[str] = set()
    for mention in article.get("stock_mentions") or []:
        if isinstance(mention, dict):
            code = _normalize_code(str(mention.get("code") or ""))
            if code:
                codes.add(code)
    for code in re.findall(r"BJ?(920\d{3})", page_title_haystack, flags=re.IGNORECASE):
        codes.add(code)

    matches: list[dict[str, Any]] = []
    for item in dataset_items:
        code = str(item.get("SECURITY_CODE") or "").strip()
        aliases = _aliases_for_item(item)
        positions: list[int] = []
        if code in codes:
            positions.append(0)
        for alias in aliases:
            idx = title_haystack.find(alias)
            if idx >= 0:
                positions.append(idx)
        if positions:
            matches.append(
                {
                    "code": code,
                    "name": str(item.get("SECURITY_NAME_ABBR") or "").strip(),
                    "aliases": aliases,
                    "first_pos": min(positions),
                }
            )
    matches.sort(key=lambda row: row["first_pos"])
    return matches


def _all_alias_positions(text: str, matches: list[dict[str, Any]]) -> list[tuple[int, str]]:
    positions: list[tuple[int, str]] = []
    for match in matches:
        code = match["code"]
        for alias in match["aliases"]:
            for found in re.finditer(re.escape(alias), text):
                positions.append((found.start(), code))
    positions.sort(key=lambda item: item[0])
    deduped: list[tuple[int, str]] = []
    for pos, code in positions:
        if deduped and deduped[-1] == (pos, code):
            continue
        deduped.append((pos, code))
    return deduped


def _candidate_windows(article: dict[str, Any], match: dict[str, Any], all_matches: list[dict[str, Any]]) -> list[str]:
    title = _compact_text(str(article.get("title") or ""))
    text = _compact_text(str(article.get("text") or ""))
    if len(all_matches) <= 1:
        return [f"{title}{text}"]

    positions = _all_alias_positions(text, all_matches)
    windows: list[str] = []
    code = match["code"]
    for idx, (pos, pos_code) in enumerate(positions):
        if pos_code != code:
            continue
        next_positions = [next_pos for next_pos, next_code in positions[idx + 1 :] if next_code != code and next_pos > pos + 8]
        end = next_positions[0] if next_positions else min(len(text), pos + 900)
        windows.append(text[pos:end])
        windows.append(text[pos : min(len(text), pos + 1200)])
    unique_windows = []
    for window in windows:
        if window and window not in unique_windows:
            unique_windows.append(window)
    return unique_windows


def _extract_price_ranges(text: str) -> list[dict[str, Any]]:
    compact = _compact_text(text)
    ranges: list[dict[str, Any]] = []
    for kind, pattern in RANGE_PATTERNS:
        for match in pattern.finditer(compact):
            low = _safe_float(match.group("low"))
            high = _safe_float(match.group("high"))
            if low is None or high is None:
                continue
            if low <= 0 or high <= 0:
                continue
            if low > high:
                low, high = high, low
            if high / low > 15:
                continue
            ranges.append(
                {
                    "low": low,
                    "high": high,
                    "mid": (low + high) / 2,
                    "kind": kind,
                    "text": match.group(0),
                    "span": list(match.span()),
                }
            )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[float, float, str]] = set()
    for item in ranges:
        key = (round(item["low"], 4), round(item["high"], 4), item["kind"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _extract_single_prices(text: str) -> list[dict[str, Any]]:
    compact = _compact_text(text)
    prices: list[dict[str, Any]] = []
    for kind, pattern in SINGLE_PRICE_PATTERNS:
        for match in pattern.finditer(compact):
            price = _safe_float(match.group("price"))
            if price is None or price <= 0:
                continue
            prices.append({"price": price, "kind": kind, "text": match.group(0), "span": list(match.span())})
    return prices


def _extract_target_pe(text: str) -> list[dict[str, Any]]:
    compact = _compact_text(text)
    result: list[dict[str, Any]] = []
    for kind, pattern in TARGET_PE_PATTERNS:
        for match in pattern.finditer(compact):
            if "pe" in match.groupdict():
                pe = _safe_float(match.group("pe"))
                if pe:
                    result.append({"kind": kind, "pe": pe, "text": match.group(0), "span": list(match.span())})
            else:
                low = _safe_float(match.group("low"))
                high = _safe_float(match.group("high"))
                if low and high:
                    result.append(
                        {
                            "kind": kind,
                            "low": min(low, high),
                            "high": max(low, high),
                            "text": match.group(0),
                            "span": list(match.span()),
                        }
                    )
    return result


def _dedupe_ranges(ranges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[float, float, str]] = set()
    for item in ranges:
        key = (round(float(item["low"]), 4), round(float(item["high"]), 4), str(item.get("kind") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _single_prices_to_ranges(single_prices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    for item in single_prices:
        price = _safe_float(item.get("price"))
        if price is None or price <= 0:
            continue
        kind = str(item.get("kind") or "")
        if kind == "lower_bound_price":
            low = price
            high = price * 1.2
            range_kind = "lower_bound_price_capped20_range"
        else:
            low = price * 0.9
            high = price * 1.1
            range_kind = f"{kind or 'single_price'}_fixed10_range"
        ranges.append(
            {
                "low": low,
                "high": high,
                "mid": (low + high) / 2,
                "kind": range_kind,
                "text": str(item.get("text") or ""),
                "span": item.get("span") or [],
                "derived_from": item,
            }
        )
    return _dedupe_ranges(ranges)


def _pe_mentions_to_price_ranges(target_pe: list[dict[str, Any]], dataset_item: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not dataset_item:
        return []
    issue_price = _safe_float(dataset_item.get("ISSUE_PRICE"))
    after_issue_pe = _safe_float(dataset_item.get("AFTER_ISSUE_PE"))
    if issue_price is None or after_issue_pe is None or issue_price <= 0 or after_issue_pe <= 0:
        return []
    eps = issue_price / after_issue_pe
    ranges: list[dict[str, Any]] = []
    for item in target_pe:
        kind = str(item.get("kind") or "target_pe")
        if "low" in item and "high" in item:
            pe_low = _safe_float(item.get("low"))
            pe_high = _safe_float(item.get("high"))
            if pe_low is None or pe_high is None:
                continue
            if pe_low <= 0 or pe_high <= 0 or pe_high / max(pe_low, 1e-9) > 8:
                continue
            low = eps * min(pe_low, pe_high)
            high = eps * max(pe_low, pe_high)
            range_kind = f"{kind}_implied_price"
        else:
            pe = _safe_float(item.get("pe"))
            if pe is None or pe <= 0:
                continue
            center = eps * pe
            low = center * 0.9
            high = center * 1.1
            range_kind = f"{kind}_fixed10_implied_price"
        if low <= 0 or high <= 0 or high / max(low, 1e-9) > 15:
            continue
        ranges.append(
            {
                "low": low,
                "high": high,
                "mid": (low + high) / 2,
                "kind": range_kind,
                "text": str(item.get("text") or ""),
                "span": item.get("span") or [],
                "derived_from": item,
                "implied_eps": eps,
            }
        )
    return _dedupe_ranges(ranges)


def _range_prefix_has_other_match(
    window: str,
    range_item: dict[str, Any],
    match: dict[str, Any],
    all_matches: list[dict[str, Any]],
) -> bool:
    if len(all_matches) <= 1:
        return False
    span = range_item.get("span")
    if not isinstance(span, list) or not span:
        return False
    range_start = int(span[0])
    prefix = window[:range_start]
    last_target_pos = -1
    for alias in match["aliases"]:
        if not alias:
            continue
        pos = prefix.rfind(alias)
        if pos > last_target_pos:
            last_target_pos = pos
    last_other_pos = -1
    for other in all_matches:
        if other["code"] == match["code"]:
            continue
        for alias in other["aliases"]:
            if not alias:
                continue
            pos = prefix.rfind(alias)
            if pos > last_other_pos:
                last_other_pos = pos
    return last_other_pos > last_target_pos


def _select_range_for_match(
    article: dict[str, Any],
    match: dict[str, Any],
    all_matches: list[dict[str, Any]],
    dataset_item: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    best_range: dict[str, Any] | None = None
    fallback_range: dict[str, Any] | None = None
    all_ranges: list[dict[str, Any]] = []
    all_single_prices: list[dict[str, Any]] = []
    for window in _candidate_windows(article, match, all_matches):
        ranges = _extract_price_ranges(window)
        single_prices = _extract_single_prices(window)
        synthetic_single_ranges = _single_prices_to_ranges(single_prices)
        synthetic_pe_ranges = _pe_mentions_to_price_ranges(_extract_target_pe(window), dataset_item)
        synthetic_ranges = synthetic_single_ranges + synthetic_pe_ranges
        candidate_ranges = ranges + synthetic_ranges
        all_ranges.extend(candidate_ranges)
        all_single_prices.extend(single_prices)
        for range_item in candidate_ranges:
            if _range_prefix_has_other_match(window, range_item, match, all_matches):
                continue
            selected = dict(range_item)
            selected["window"] = window[:260]
            if range_item in ranges:
                best_range = selected
                break
            if fallback_range is None:
                fallback_range = selected
        if best_range is not None:
            break

    if best_range is None and fallback_range is not None:
        best_range = fallback_range

    if best_range is None and len(all_matches) == 1:
        extracted = article.get("extracted") if isinstance(article.get("extracted"), dict) else {}
        raw_range = extracted.get("price_range") if isinstance(extracted, dict) else None
        if isinstance(raw_range, dict):
            low = _safe_float(raw_range.get("low"))
            high = _safe_float(raw_range.get("high"))
            if low and high:
                best_range = {
                    "low": min(low, high),
                    "high": max(low, high),
                    "mid": (low + high) / 2,
                    "kind": "cached_extracted_price_range",
                    "text": str(raw_range.get("text") or ""),
                    "window": "",
                }

    return best_range, all_ranges, all_single_prices


def _load_scan_context(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = _read_json(path)
    sample_codes = set(payload.get("sample_codes") or [])
    baseline_miss = set((payload.get("baseline") or {}).get("miss_codes") or [])
    baseline_hit = sample_codes - baseline_miss
    top_candidates = payload.get("top_candidates") or []
    best = top_candidates[0] if top_candidates else {}
    best_miss = set((best.get("exact_score") or {}).get("miss_codes") or best.get("miss_codes") or [])
    best_hit = sample_codes - best_miss if best_miss else set()
    return {
        "path": str(path),
        "sample_codes": sorted(sample_codes),
        "baseline_hit_codes": sorted(baseline_hit),
        "best_hit_codes": sorted(best_hit),
        "baseline_score": (payload.get("baseline") or {}).get("exact_score") or {},
        "best_score": best.get("exact_score") or best.get("rough_score") or {},
    }


def _calc_change_pct(issue_price: Any, price: Any) -> float | None:
    issue = _safe_float(issue_price)
    target = _safe_float(price)
    if not issue or target is None:
        return None
    return (target / issue - 1) * 100


def _fmt_num(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:.{digits}f}"


def _fmt_pct(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:.1f}%"


def _summarize(rows: list[dict[str, Any]], scan_context: dict[str, Any]) -> dict[str, Any]:
    evaluated = [row for row in rows if row.get("evaluation_status") == "evaluated"]
    hits = [row for row in evaluated if row.get("hit")]
    unique_codes = sorted({row["code"] for row in evaluated})
    unique_hit_codes = sorted({row["code"] for row in hits})
    scan_sample_codes = set(scan_context.get("sample_codes") or [])
    baseline_hit_codes = set(scan_context.get("baseline_hit_codes") or [])
    best_hit_codes = set(scan_context.get("best_hit_codes") or [])
    comparable_codes = [code for code in unique_codes if code in scan_sample_codes]
    by_author: dict[str, dict[str, Any]] = {}
    for author, author_rows in _group_by(evaluated, "author_name").items():
        author_hits = [row for row in author_rows if row.get("hit")]
        by_author[author] = {
            "evaluated_forecast_count": len(author_rows),
            "hit_count": len(author_hits),
            "hit_rate": len(author_hits) / len(author_rows) if author_rows else None,
            "unique_code_count": len({row["code"] for row in author_rows}),
            "unique_hit_code_count": len({row["code"] for row in author_hits}),
        }

    return {
        "forecast_row_count": len(rows),
        "evaluated_forecast_count": len(evaluated),
        "hit_count": len(hits),
        "hit_rate": len(hits) / len(evaluated) if evaluated else None,
        "unique_evaluated_code_count": len(unique_codes),
        "unique_hit_code_count": len(unique_hit_codes),
        "unique_hit_rate": len(unique_hit_codes) / len(unique_codes) if unique_codes else None,
        "unique_codes": unique_codes,
        "unique_hit_codes": unique_hit_codes,
        "excluded_count": len(rows) - len(evaluated),
        "baseline_same_code": {
            "comparable_code_count": len(comparable_codes),
            "hit_count": len([code for code in comparable_codes if code in baseline_hit_codes]),
            "hit_rate": (
                len([code for code in comparable_codes if code in baseline_hit_codes]) / len(comparable_codes)
                if comparable_codes
                else None
            ),
        },
        "best_scan_same_code": {
            "comparable_code_count": len(comparable_codes),
            "hit_count": len([code for code in comparable_codes if code in best_hit_codes]),
            "hit_rate": (
                len([code for code in comparable_codes if code in best_hit_codes]) / len(comparable_codes)
                if comparable_codes
                else None
            ),
        },
        "by_author": by_author,
    }


def _group_by(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "")].append(row)
    return dict(grouped)


def _build_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# 雪球作者可抽取首日价格区间验证",
        "",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 语料索引：`{payload['inputs']['corpus_index']}`",
        f"- 本地 replay：`{payload['inputs']['dataset']}`",
        f"- 预测行数：`{summary['forecast_row_count']}`",
        f"- 可评估预测：`{summary['evaluated_forecast_count']}`",
        f"- 区间命中：`{summary['hit_count']}/{summary['evaluated_forecast_count']}`，命中率 `{_fmt_pct(summary['hit_rate'] * 100 if summary['hit_rate'] is not None else None)}`",
        f"- 覆盖本地代码：`{summary['unique_evaluated_code_count']}`，至少一次命中代码 `{summary['unique_hit_code_count']}`，代码级命中率 `{_fmt_pct(summary['unique_hit_rate'] * 100 if summary['unique_hit_rate'] is not None else None)}`",
        "",
        "## 同代码基线参考",
        "",
        f"- 当前 baseline 同代码命中：`{summary['baseline_same_code']['hit_count']}/{summary['baseline_same_code']['comparable_code_count']}`，命中率 `{_fmt_pct(summary['baseline_same_code']['hit_rate'] * 100 if summary['baseline_same_code']['hit_rate'] is not None else None)}`",
        f"- 2026-07-10 扫描最优候选同代码命中：`{summary['best_scan_same_code']['hit_count']}/{summary['best_scan_same_code']['comparable_code_count']}`，命中率 `{_fmt_pct(summary['best_scan_same_code']['hit_rate'] * 100 if summary['best_scan_same_code']['hit_rate'] is not None else None)}`",
        "",
        "说明：同代码基线只在 2026-03-01 以来且原扫描报告覆盖的样本上比较；雪球作者覆盖样本与全量 31 只样本并不完全相同。",
        "",
        "## 分作者",
        "",
        "| 作者 | 可评估 | 命中 | 命中率 | 覆盖代码 | 命中代码 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for author, item in sorted(summary["by_author"].items()):
        lines.append(
            "| {author} | {n} | {hit} | {rate} | {codes} | {hit_codes} |".format(
                author=author,
                n=item["evaluated_forecast_count"],
                hit=item["hit_count"],
                rate=_fmt_pct(item["hit_rate"] * 100 if item["hit_rate"] is not None else None),
                codes=item["unique_code_count"],
                hit_codes=item["unique_hit_code_count"],
            )
        )

    lines.extend(
        [
            "",
            "## 明细",
            "",
            "| 代码 | 简称 | 作者 | 发布时间 | 上市日 | 作者区间 | 首日均价 | 命中 | 涨幅 | 标题 |",
            "|---|---|---|---|---|---:|---:|---|---:|---|",
        ]
    )
    for row in payload["rows"]:
        if row.get("evaluation_status") != "evaluated":
            continue
        title = str(row.get("title") or "").replace("|", "｜")
        lines.append(
            "| {code} | {name} | {author} | {created} | {listing} | {low}-{high} | {avg} | {hit} | {chg} | {title} |".format(
                code=row["code"],
                name=row["name"],
                author=row["author_name"],
                created=row.get("created_at_text") or "",
                listing=row.get("listing_date") or "",
                low=_fmt_num(row.get("forecast_low")),
                high=_fmt_num(row.get("forecast_high")),
                avg=_fmt_num(row.get("actual_average_price")),
                hit="是" if row.get("hit") else "否",
                chg=_fmt_pct(row.get("actual_average_change_pct")),
                title=title,
            )
        )

    excluded = [row for row in payload["rows"] if row.get("evaluation_status") != "evaluated"]
    if excluded:
        lines.extend(["", "## 未评估", "", "| 代码 | 简称 | 作者 | 原因 | 标题 |", "|---|---|---|---|---|"])
        for row in excluded:
            title = str(row.get("title") or "").replace("|", "｜")
            lines.append(
                f"| {row.get('code', '')} | {row.get('name', '')} | {row.get('author_name', '')} | {row.get('evaluation_status', '')} | {title} |"
            )

    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- 这里只验证文章中能抽出的价格区间；显式价格优先，单点目标价和 PE 区间会标记为折算区间。",
            "- 文章必须早于本地识别的上市日；上市日当日或之后发布的内容默认排除。",
            "- 首日均价优先取 replay 数据，缺失时只读本地首日分时 CSV 计算，不联网。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    corpus_index = Path(args.corpus_index)
    dataset_path = Path(args.dataset)
    scan_path = Path(args.scan_report) if args.scan_report else DEFAULT_SCAN_REPORT
    output_dir = Path(args.output_dir)
    intraday_dir = Path(args.intraday_dir)

    dataset_items, by_code = _load_dataset(dataset_path)
    articles = _load_articles(corpus_index)
    scan_context = _load_scan_context(scan_path)

    rows: list[dict[str, Any]] = []
    for article in articles:
        quality = article.get("quality") if isinstance(article.get("quality"), dict) else {}
        if not article.get("readable") and not quality.get("readable"):
            continue
        matches = _find_local_matches(article, dataset_items)
        if not matches:
            continue
        for match in matches:
            code = match["code"]
            dataset_item = by_code.get(code)
            if not dataset_item:
                continue
            price_range, extracted_ranges, single_prices = _select_range_for_match(article, match, matches, dataset_item)
            if not price_range:
                continue
            created_at = _parse_dt(article.get("created_at_ms")) or _parse_dt(article.get("created_at_iso")) or _parse_dt(article.get("created_at_text"))
            listing_date, listing_date_source = _resolve_listing_date(dataset_item, intraday_dir)
            average_price, average_price_source, average_price_reason = _resolve_average_price(dataset_item, intraday_dir)
            target_pe = _extract_target_pe(str(article.get("text") or ""))
            evaluation_status = "evaluated"
            if created_at and listing_date and created_at.date() >= listing_date.date():
                evaluation_status = "excluded_post_or_listing_day_article"
            elif average_price is None:
                evaluation_status = "missing_actual_average_price"
            elif listing_date is None:
                evaluation_status = "missing_listing_date"

            hit = None
            low = price_range["low"]
            high = price_range["high"]
            actual_change = _calc_change_pct(dataset_item.get("ISSUE_PRICE"), average_price)
            midpoint = (low + high) / 2
            if evaluation_status == "evaluated" and average_price is not None:
                hit = low <= average_price <= high

            rows.append(
                {
                    "code": code,
                    "name": match["name"],
                    "author_name": article.get("author_name"),
                    "user_id": article.get("user_id"),
                    "status_id": article.get("status_id"),
                    "title": article.get("title"),
                    "url": article.get("url"),
                    "created_at_text": article.get("created_at_text"),
                    "created_at_iso": article.get("created_at_iso"),
                    "listing_date": _date_text(listing_date),
                    "listing_date_source": listing_date_source,
                    "issue_price": _safe_float(dataset_item.get("ISSUE_PRICE")),
                    "forecast_low": low,
                    "forecast_high": high,
                    "forecast_mid": midpoint,
                    "forecast_kind": price_range.get("kind"),
                    "forecast_text": price_range.get("text"),
                    "all_extracted_ranges": extracted_ranges,
                    "single_prices": single_prices,
                    "target_pe_mentions": target_pe,
                    "actual_average_price": average_price,
                    "actual_average_price_source": average_price_source,
                    "actual_average_price_reason": average_price_reason,
                    "actual_average_change_pct": actual_change,
                    "price_error_to_mid": (average_price - midpoint) if average_price is not None else None,
                    "abs_price_error_to_mid": abs(average_price - midpoint) if average_price is not None else None,
                    "hit": hit,
                    "evaluation_status": evaluation_status,
                }
            )

    rows.sort(key=lambda row: (row.get("listing_date") or "9999", row.get("code") or "", row.get("author_name") or ""))
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "schema": "xueqiu_author_range_validation_v1",
        "generated_at": generated_at,
        "inputs": {
            "corpus_index": str(corpus_index),
            "dataset": str(dataset_path),
            "scan_report": str(scan_path) if scan_path else "",
            "intraday_dir": str(intraday_dir),
        },
        "summary": _summarize(rows, scan_context),
        "scan_context": scan_context,
        "rows": rows,
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"xueqiu_author_range_validation_{timestamp}.json"
    md_path = output_dir / f"xueqiu_author_range_validation_{timestamp}.md"
    _write_json(json_path, payload)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(_build_markdown(payload), encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "markdown": str(md_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate explicit Xueqiu author first-day price ranges against local BJ IPO replay data.")
    parser.add_argument("--corpus-index", default=str(DEFAULT_CORPUS_INDEX))
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--scan-report", default=str(DEFAULT_SCAN_REPORT))
    parser.add_argument("--intraday-dir", default=str(ROOT_DIR / "首日分时走势"))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    payload = run(args)
    print(
        json.dumps(
            {
                "outputs": payload["outputs"],
                "summary": payload["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
