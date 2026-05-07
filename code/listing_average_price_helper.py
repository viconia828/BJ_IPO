from __future__ import annotations

import csv
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INTRADAY_DIR = ROOT_DIR / "首日分时走势"
DEFAULT_CACHE_PATH = ROOT_DIR / "data" / "offline_tuning" / "listing_average_prices.json"
CACHE_SCHEMA = "listing_average_price_cache_v3"
AVERAGE_PRICE_CALC_VERSION = 3
INTRADAY_CSV_ENCODINGS = ("utf-8-sig", "gb18030")


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def calc_change_pct(issue_price: Any, target_price: Any) -> float | None:
    issue_price_float = safe_float(issue_price)
    target_price_float = safe_float(target_price)
    if not issue_price_float or target_price_float is None:
        return None
    return (target_price_float / issue_price_float - 1) * 100


def file_signature(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return {"name": path.name, "missing": True}
    return {
        "name": path.name,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _load_json_dict(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_cache(path: str | Path = DEFAULT_CACHE_PATH) -> dict[str, Any]:
    payload = _load_json_dict(path)
    if payload.get("schema") != CACHE_SCHEMA:
        return {"schema": CACHE_SCHEMA, "items": {}}
    if not isinstance(payload.get("items"), dict):
        payload["items"] = {}
    return payload


def save_cache(payload: dict[str, Any], path: str | Path = DEFAULT_CACHE_PATH) -> Path:
    payload["schema"] = CACHE_SCHEMA
    payload["updated_at"] = _now_text()
    payload.setdefault("items", {})
    return _safe_write_json(path, payload)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _sum_intraday_csv(path: Path, encoding: str) -> tuple[float, float, float | None, float | None, float | None]:
    total_amount = 0.0
    total_volume = 0.0
    reference_prices: list[float] = []
    with path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            volume = safe_float(row.get("volume") or row.get("vol"))
            amount = safe_float(row.get("amount"))
            for key in ("open", "close"):
                reference_price = safe_float(row.get(key))
                if reference_price is not None and reference_price > 0:
                    reference_prices.append(reference_price)
            if volume is None or amount is None:
                continue
            if volume <= 0 or amount <= 0:
                continue
            total_volume += volume
            total_amount += amount
    return (
        total_amount,
        total_volume,
        _median(reference_prices),
        min(reference_prices) if reference_prices else None,
        max(reference_prices) if reference_prices else None,
    )


def _distance_to_reference(value: float, reference: float | None) -> float:
    if reference is None or reference <= 0 or value <= 0:
        return float("inf")
    return abs(math.log(value / reference))


def _same_price_magnitude(value: float, low_reference: float | None, high_reference: float | None) -> bool:
    if value <= 0:
        return False
    if low_reference is None or high_reference is None or low_reference <= 0 or high_reference <= 0:
        return True
    return low_reference / 10 <= value <= high_reference * 10


def _infer_average_price(
    total_amount: float,
    total_volume: float,
    reference_price: float | None,
    low_reference: float | None,
    high_reference: float | None,
) -> tuple[float, str]:
    raw_price = total_amount / total_volume
    candidates = [
        (raw_price, "volume_shares_amount_yuan"),
        (raw_price / 100, "volume_hands_amount_yuan"),
        (raw_price * 10, "volume_hands_amount_thousand_yuan"),
    ]
    if reference_price is not None and reference_price > 0:
        magnitude_matches = [
            item
            for item in candidates
            if _same_price_magnitude(item[0], low_reference, high_reference)
        ]
        if magnitude_matches:
            candidates = magnitude_matches
        candidates.sort(key=lambda item: _distance_to_reference(item[0], reference_price))
        return candidates[0]
    return candidates[0]


def calc_intraday_csv_average_price(
    code: str,
    intraday_dir: str | Path = DEFAULT_INTRADAY_DIR,
) -> dict[str, Any]:
    csv_path = Path(intraday_dir) / f"{code}.csv"
    if not csv_path.exists():
        return {"average_price": None, "source": "", "reason": "本地首日分时 CSV 不存在"}

    decode_errors: list[str] = []
    for encoding in INTRADAY_CSV_ENCODINGS:
        try:
            total_amount, total_volume, reference_price, low_reference, high_reference = _sum_intraday_csv(csv_path, encoding)
        except UnicodeDecodeError as exc:
            decode_errors.append(f"{encoding}: {exc}")
            continue
        except OSError as exc:
            return {"average_price": None, "source": "", "reason": str(exc)}

        if total_volume <= 0 or total_amount <= 0:
            return {"average_price": None, "source": "", "reason": "本地首日分时 CSV 缺少有效成交量或成交额"}

        average_price, unit_mode = _infer_average_price(
            total_amount,
            total_volume,
            reference_price,
            low_reference,
            high_reference,
        )
        return {
            "average_price": average_price,
            "source": "intraday_csv",
            "reason": "",
            "csv_encoding": encoding,
            "csv_signature": file_signature(csv_path),
            "unit_mode": unit_mode,
            "price_reference": reference_price,
            "price_reference_low": low_reference,
            "price_reference_high": high_reference,
            "calc_version": AVERAGE_PRICE_CALC_VERSION,
        }

    return {
        "average_price": None,
        "source": "",
        "reason": "本地首日分时 CSV 编码无法识别：" + "；".join(decode_errors),
    }


def get_cached_intraday_average_price(
    code: str,
    intraday_dir: str | Path = DEFAULT_INTRADAY_DIR,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
) -> dict[str, Any] | None:
    csv_path = Path(intraday_dir) / f"{code}.csv"
    signature = file_signature(csv_path)
    payload = load_cache(cache_path)
    item = (payload.get("items") or {}).get(code)
    if not isinstance(item, dict):
        return None
    if item.get("csv_signature") != signature:
        return None
    if item.get("calc_version") != AVERAGE_PRICE_CALC_VERSION:
        return None
    cached = dict(item)
    cached["source"] = cached.get("source") or "intraday_csv_cache"
    return cached


def cache_intraday_average_price(
    code: str,
    item: dict[str, Any],
    cache_path: str | Path = DEFAULT_CACHE_PATH,
) -> None:
    payload = load_cache(cache_path)
    items = payload.setdefault("items", {})
    if not isinstance(items, dict):
        return
    items[code] = dict(item)
    save_cache(payload, cache_path)


def resolve_intraday_average_price(
    code: str,
    intraday_dir: str | Path = DEFAULT_INTRADAY_DIR,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
) -> dict[str, Any]:
    cached = get_cached_intraday_average_price(code, intraday_dir=intraday_dir, cache_path=cache_path)
    if cached is not None:
        return cached

    csv_result = calc_intraday_csv_average_price(code, intraday_dir=intraday_dir)
    average_price = safe_float(csv_result.get("average_price"))
    if average_price is None or average_price <= 0:
        return csv_result

    cache_item = {
        "average_price": average_price,
        "source": "intraday_csv",
        "reason": "",
        "csv_signature": csv_result.get("csv_signature"),
        "unit_mode": csv_result.get("unit_mode"),
        "price_reference": csv_result.get("price_reference"),
        "price_reference_low": csv_result.get("price_reference_low"),
        "price_reference_high": csv_result.get("price_reference_high"),
        "calc_version": AVERAGE_PRICE_CALC_VERSION,
        "updated_at": _now_text(),
    }
    cache_intraday_average_price(code, cache_item, cache_path=cache_path)
    return cache_item
