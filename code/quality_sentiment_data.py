"""Local, auditable input bridge and immutable forward prediction snapshots."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import uuid

import listing_average_price_helper
from quality_sentiment import day, number

ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _completed_local_listing(code: str, root: Path) -> dict | None:
    """Recover a missing listing date from its dated, completed first-day tape."""
    if not code.isdigit() or len(code) != 6:
        return None
    path = root / '首日分时走势' / f'{code}.csv'
    if not path.is_file():
        return None
    for encoding in listing_average_price_helper.INTRADAY_CSV_ENCODINGS:
        try:
            with path.open(encoding=encoding, newline='') as handle:
                rows = list(csv.DictReader(handle))
            times = [datetime.fromisoformat(row['DateTime'].strip().replace('/', '-')) for row in rows]
            if (not times or len({t.date() for t in times}) != 1 or times != sorted(set(times))
                    or times[0].strftime('%H:%M') > '09:30' or times[-1].strftime('%H:%M') < '15:00'):
                return None
            average = listing_average_price_helper.calc_intraday_csv_average_price(code, path.parent)
            value, close = number(average.get('average_price')), number(rows[-1].get('close'))
            if not all(v is not None and math.isfinite(v) and v > 0 for v in (value, close)):
                return None
            return {'LISTING_DATE': times[0].date().isoformat(), 'AVERAGE_PRICE': value,
                    'CLOSE_PRICE': close, 'path': str(path.relative_to(root)),
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        except UnicodeError:
            continue
        except (OSError, ValueError, KeyError, TypeError):
            return None
    return None


def normalize_history(items: list[dict], root: Path = ROOT) -> tuple[list[dict], list[dict]]:
    reference = _json(root / "data/quality_sentiment/historical_reference.json").get("items", {})
    result, audit = [], []
    for original in items:
        item = dict(original)
        item = with_fundamental_review(item, root)
        code = str(item.get("SECURITY_CODE") or "")
        source = str(item.get("listing_date_source") or item.get("LISTING_DATE_SOURCE") or "").strip()
        if source == "apply_date_fallback":
            # The replay builder keeps application dates as placeholders for
            # upcoming IPOs. Only a real listing source below may restore one.
            placeholder = item.get("LISTING_DATE")
            item["LISTING_DATE"] = None
            if placeholder is not None:
                audit.append({"code": code, "reason": "apply_date_is_not_listing_date",
                              "changes": {"LISTING_DATE": {"before": placeholder, "after": None}}})
        ref = reference.get(code, {})
        # Do not apply real-stock corrections to synthetic fixtures or mismatched issues.
        if ref and number(item.get("ISSUE_PRICE")) == ref.get("ISSUE_PRICE"):
            changes = {}
            for key, value in ref.items():
                if key in {"SECURITY_CODE", "ISSUE_PRICE"}:
                    continue
                if item.get(key) != value:
                    changes[key] = {"before": item.get(key), "after": value}
                    item[key] = value
            if changes:
                audit.append({"code": code, "changes": changes})
            if day(ref.get("LISTING_DATE")):
                item["listing_date_source"] = "historical_reference"
                if "LISTING_DATE_SOURCE" in item:
                    item["LISTING_DATE_SOURCE"] = "historical_reference"
        fields = _json(root / f"data/tushare_db/variable_fields/{code}.BJ.json").get("fields", {})
        listing = fields.get("tushare_ipo_listing_day") or {}
        if day(listing.get("listing_date")) and number(listing.get("average_price")):
            item["LISTING_DATE"] = day(listing["listing_date"]).isoformat()
            item["listing_date_source"] = "tushare_ipo_listing_day"
            if "LISTING_DATE_SOURCE" in item:
                item["LISTING_DATE_SOURCE"] = "tushare_ipo_listing_day"
            item["AVERAGE_PRICE"] = number(listing["average_price"])
            item["CLOSE_PRICE"] = number(listing.get("close"))
        if not day(item.get('LISTING_DATE')):
            local = _completed_local_listing(code, root)
            if local:
                changes = {key: {'before': item.get(key), 'after': local[key]}
                           for key in ('LISTING_DATE', 'AVERAGE_PRICE', 'CLOSE_PRICE')}
                item.update({key: local[key] for key in changes})
                item['listing_date_source'] = 'dated_listing_intraday_csv'
                if 'LISTING_DATE_SOURCE' in item:
                    item['LISTING_DATE_SOURCE'] = 'dated_listing_intraday_csv'
                audit.append({'code': code, 'reason': 'completed_local_listing_tape',
                              'changes': changes, 'source': local['path'], 'sha256': local['sha256']})
        issue = number(item.get("ISSUE_PRICE"))
        for price, change in (("AVERAGE_PRICE", "LD_AVERAGE_CHANGE"), ("CLOSE_PRICE", "LD_CLOSE_CHANGE")):
            value = number(item.get(price))
            if value and issue:
                item[change] = (value / issue - 1) * 100
        post = fields.get("tushare_ipo_post_listing_performance") or {}
        if day(post.get("listing_date")) == day(item.get("LISTING_DATE")):
            for prefix, key in (("NEXT", "next"), ("THIRD", "third")):
                item[f"{prefix}_TRADE_DATE"] = post.get(f"{key}_trade_date") or item.get(f"{prefix}_TRADE_DATE")
                item[f"{prefix}_DAY_CLOSE"] = post.get(f"{key}_close")
                item[f"{prefix}_DAY_FROM_LISTING_CLOSE_PCT"] = post.get(f"{key}_day_from_listing_close_pct")
        snapshots = sorted((root / "data/quality_sentiment/predictions").glob(f"{code}_*.json"))
        for path in reversed(snapshots):
            saved = _json(path)
            issued = day(saved.get("generated_at"))
            listing_date = day(item.get("LISTING_DATE"))
            if issued and listing_date and issued < listing_date and saved.get("result", {}).get("available"):
                item["fundamental_snapshot"] = {**saved["result"]["anchor"], "source": str(path), "available_date": issued.isoformat()}
                item["sentiment_snapshot"] = {"center": saved["result"]["sentiment"]["center"],
                                              "as_of": issued.isoformat(), "source": str(path), "origin": "archived"}
                break
        result.append(item)
    result.sort(key=lambda r: (str(day(r.get("LISTING_DATE")) or ""), str(r.get("SECURITY_CODE"))))
    return result, audit


def load_history(root: Path = ROOT) -> tuple[list[dict], list[dict]]:
    dataset = _json(root / "data/offline_tuning/replay_dataset.json")
    return normalize_history(dataset.get("items") or [], root)


def with_fundamental_review(item: dict, root: Path = ROOT) -> dict:
    reviews = _json(root / "data/quality_sentiment/fundamental_reviews.json").get("items", {})
    review = reviews.get(str(item.get("SECURITY_CODE") or ""))
    result = {**item, "fundamental_review": review} if isinstance(review, dict) else dict(item)
    index = _json(root / 'data/fundamental_cases/index.json').get('items', {})
    entry = index.get(str(item.get('SECURITY_CODE') or ''))
    if isinstance(entry, dict):
        path = (root / str(entry.get('path') or '')).resolve()
        folder = (root / 'data/fundamental_cases/cases').resolve()
        if path.parent != folder or not path.is_file():
            result['fundamental_case'] = {'integrity_error': '基本面复核文件路径无效'}
        elif hashlib.sha256(path.read_bytes()).hexdigest() != entry.get('sha256'):
            result['fundamental_case'] = {'integrity_error': '基本面复核文件哈希不一致，请重新审计底稿'}
        else:
            case = _json(path)
            result['fundamental_case'] = (case if case.get('code') == str(item.get('SECURITY_CODE') or '')
                                         else {'integrity_error': '基本面复核公司代码不匹配'})
    return result


def save_forward_snapshot(target: dict, result: dict, root: Path = ROOT) -> dict:
    now = datetime.now(timezone(timedelta(hours=8)))
    listing = day(target.get("LISTING_DATE"))
    if listing and listing <= now.date():
        return {"saved": False, "reason": "已上市标的不回填为事前预测"}
    directory = root / "data/quality_sentiment/predictions"
    directory.mkdir(parents=True, exist_ok=True)
    code = str(target.get("SECURITY_CODE") or "")
    if not code.isdigit() or len(code) != 6:
        return {"saved": False, "reason": "无效股票代码"}
    previous = sorted(directory.glob(f"{code}_*.json"))
    comparison = {}
    if previous:
        old = _json(previous[-1]).get("result", {})
        for key in ("anchor", "sentiment"):
            before, after = number(old.get(key, {}).get("center")), number(result.get(key, {}).get("center"))
            comparison[f"{key}_change_pct"] = (after / before - 1) * 100 if before and after else None
        comparison["previous_path"] = str(previous[-1])
    payload = {"schema": "ipo_forward_quality_sentiment_v1", "generated_at": now.isoformat(),
               "target": target, "result": result}
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path = directory / f"{code}_{now.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:12]}.json"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return {"saved": True, "path": str(path), "sha256": hashlib.sha256(text.encode()).hexdigest(), **comparison}
