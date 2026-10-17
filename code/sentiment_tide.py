"""A chronological, three-tier gate on positive valuation premiums.

The thresholds describe IPO waves, not issuer quality or a causal market model.
Only completed earlier listing dates enter the state; same-day IPOs form one event.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from itertools import groupby
from typing import Any

VERSION = "ipo_tide_v1"
FACTOR_DEFAULTS = {"low": 0.50, "medium": 0.75, "high": 1.10}
LABELS = {"low": "低", "medium": "中", "high": "高"}
REASONS = {
    "break": "首日表现明显降温，上一轮热度结束",
    "new_impulse": "强势首日表现开启新一轮热度",
    "wave_age": "已延续三批后续新股，回到中档",
    "weakening": "首日涨幅低于本轮峰值的一半",
    "follow_through": "本轮热度延续",
    "ordinary": "普通表现，维持中档",
    "retained": "未形成新的强势信号",
}


def enabled(params: dict[str, Any], key: str = "sentiment_tide_enabled") -> bool:
    return str(params.get(key, False)).strip().lower() in {"1", "true", "yes", "on"}


def factors(params: dict[str, Any]) -> dict[str, float]:
    values = {tier: float(params.get(f"sentiment_tide_{tier}_factor", default))
              for tier, default in FACTOR_DEFAULTS.items()}
    if any(not math.isfinite(v) or not 0 <= v <= 1.5 for v in values.values()):
        raise ValueError("情绪潮汐倍数须为 0 到 1.5 的有限数值")
    if list(values.values()) != sorted(values.values()):
        raise ValueError("情绪潮汐倍数须满足低档 <= 中档 <= 高档")
    return values


def parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value).strip()[:10].replace("/", "-"))
    except ValueError:
        return None


def finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def visible_history(history: list[dict[str, Any]], cutoff: date, days: int,
                    target_code: str = "") -> list[dict[str, Any]]:
    """Deduplicate exact copies; discard conflicting records rather than choose by input order."""
    records: dict[tuple[date, str], tuple[dict[str, Any], float]] = {}
    conflicts = set()
    for item in history:
        code = str(item.get("SECURITY_CODE") or "").strip()
        day = parse_date(item.get("LISTING_DATE"))
        if not code or code == target_code or day is None or not cutoff - timedelta(days=days) <= day < cutoff:
            continue
        gain = finite(item.get("LD_AVERAGE_CHANGE"))
        if gain is None:
            gain = finite(item.get("LD_CLOSE_CHANGE"))
        if gain is None or gain <= -100:
            continue
        key = day, code
        if key in records and records[key][0] != item:
            conflicts.add(key)
        else:
            records[key] = item, gain
    return [dict(records[key][0], LISTING_DATE=key[0].isoformat(), LD_AVERAGE_CHANGE=records[key][1])
            for key in sorted(records) if key not in conflicts]


def classify(history: list[dict[str, Any]], cutoff: date, days: int = 30,
             target_code: str = "") -> dict[str, Any]:
    visible = visible_history(history, cutoff, days, target_code)
    tier, peak, age = "medium", None, 0
    prior: list[float] = []
    events = []
    for day, group in groupby(visible, key=lambda r: parse_date(r["LISTING_DATE"])):
        group = list(group)
        gain = statistics.median(r["LD_AVERAGE_CHANGE"] for r in group)
        recent = statistics.median(prior[-3:]) if prior else None
        before = tier
        if gain <= 100 or (peak is not None and gain < 200 and gain < peak * .5):
            tier, peak, age, reason = "low", None, 0, "break"
        elif gain >= 300 and (recent is None or gain >= recent * 1.25
                             or (tier == "high" and peak is not None and gain > peak * 1.15)):
            tier, peak, age, reason = "high", gain, 0, "new_impulse"
        elif tier == "high":
            age += 1
            if age >= 3:
                tier, reason = "medium", "wave_age"
            elif peak is not None and gain < peak * .5:
                tier, reason = "medium", "weakening"
            else:
                reason = "follow_through"
        elif gain >= 150:
            tier, reason = "medium", "ordinary"
        else:
            reason = "retained"
        prior.append(gain)
        events.append({"date": day.isoformat(), "codes": [r["SECURITY_CODE"] for r in group],
                       "names": [r.get("SECURITY_NAME_ABBR", "") for r in group],
                       "gain_pct": gain, "before": before, "after": tier, "reason": reason})
    return {"version": VERSION, "tier": tier, "label": LABELS[tier],
            "cutoff_exclusive": cutoff.isoformat(), "window_days": days,
            "sample_count": len(visible), "events": events,
            "reason": REASONS[events[-1]["reason"]] if events else "近期无已完成样本，暂用中档"}


def apply(final: dict[str, Any], state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    base, raw = finite(final.get("base_target_price")), finite(final.get("target_price"))
    if not final.get("available") or base is None or raw is None:
        return final
    factor = factors(params)[state["tier"]]
    positive = max(raw - base, 0.0)
    target = raw - positive * (1 - factor)
    width = float(params.get("price_range_width", .1))
    return {**final, "target_price": target, "range_low": target * (1 - width),
            "range_high": target * (1 + width),
            "sentiment_tide": {**state, "factor": factor, "base_price": base,
                               "raw_target_price": raw, "positive_premium": positive,
                               "retained_premium": positive * factor,
                               "price_reduction": raw - target}}
