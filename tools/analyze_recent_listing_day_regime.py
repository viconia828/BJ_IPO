from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import date, datetime, time
from pathlib import Path
from statistics import mean, median
from typing import Any

import evaluate_intraday_valuation_guidance as guidance


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_INTRADAY_DIR = ROOT_DIR / "首日分时走势"
DEFAULT_TUSHARE_DIR = ROOT_DIR / "data" / "tushare_db"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"
DEFAULT_CUTOFF = "2026-07-14"

EXTERNAL_EVENT_SAMPLES = {
    "920176": "与长鑫科技同日上市（2026-07-27），单列为外部注意力/资金溢出样本",
    "920059": "与宇树科技同日上市（2026-08-19），单列为外部注意力/资金溢出样本",
}

OBSERVATION_NODES = ("09:35", "09:45", "10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00")


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return (numerator / denominator - 1.0) * 100.0


def _fmt(value: Any, digits: int = 1, suffix: str = "") -> str:
    number = _safe_float(value)
    return "" if number is None else f"{number:.{digits}f}{suffix}"


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _stats(values: list[float]) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return {
        "count": len(clean),
        "mean": mean(clean) if clean else None,
        "median": median(clean) if clean else None,
        "p25": _quantile(clean, 0.25),
        "p75": _quantile(clean, 0.75),
        "min": min(clean) if clean else None,
        "max": max(clean) if clean else None,
    }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixed_new_share_record(code: str, tushare_dir: Path) -> dict[str, Any]:
    path = tushare_dir / "fixed_fields" / f"{code}.BJ.json"
    if not path.exists():
        return {}
    return ((_load_json(path).get("fields") or {}).get("tushare_new_share") or {})


def _next_day_record(code: str, listing_date: str, tushare_dir: Path) -> dict[str, Any]:
    path = tushare_dir / "variable_fields" / f"{code}.BJ.json"
    if not path.exists():
        return {}
    fields = _load_json(path).get("fields") or {}
    performance = fields.get("tushare_ipo_post_listing_performance") or {}
    rows = sorted(performance.get("rows") or [], key=lambda row: str(row.get("trade_date") or ""))
    listing_key = listing_date.replace("-", "")
    for index, row in enumerate(rows):
        if str(row.get("trade_date") or "") == listing_key and index + 1 < len(rows):
            return dict(rows[index + 1])
    return {}


def _time_bucket(value: str) -> str:
    if value < "10:00":
        return "09:30-09:59"
    if value <= "11:00":
        return "10:00-11:00"
    if value <= "11:30":
        return "11:01-11:30"
    return "13:00-15:00"


def _first_hit_time(bars: list[dict[str, Any]], threshold: float) -> str:
    for bar in bars:
        if float(bar["high"]) >= threshold:
            return bar["dt"].strftime("%H:%M")
    return ""


def _turnover_cross(
    bars: list[dict[str, Any]],
    total_volume: float,
    turnover_rate: float | None,
    threshold: float,
) -> dict[str, Any]:
    if not bars or not total_volume or turnover_rate is None:
        return {}
    cumulative_volume = 0.0
    for index, bar in enumerate(bars):
        cumulative_volume += float(bar.get("volume") or 0.0)
        cumulative_turnover = turnover_rate * cumulative_volume / total_volume
        if cumulative_turnover >= threshold:
            later = bars[index:]
            high_bar = max(later, key=lambda row: float(row["high"]))
            reference = float(bar["close"])
            return {
                "time": bar["dt"].strftime("%H:%M"),
                "price": reference,
                "turnover": cumulative_turnover,
                "later_high": float(high_bar["high"]),
                "later_high_time": high_bar["dt"].strftime("%H:%M"),
                "later_high_gain_pct": _pct(float(high_bar["high"]), reference),
            }
    return {}


def _snapshot_at(intraday: dict[str, Any], node: str) -> dict[str, Any]:
    return guidance._snapshot(
        intraday["bars"],
        node,
        intraday.get("turnover_rate"),
        intraday["total_volume"],
    ) or {}


def _attach_forward_outcomes(snapshot: dict[str, Any], bars: list[dict[str, Any]], node: str, closing: float) -> None:
    price = _safe_float(snapshot.get("price"))
    if price is None:
        return
    hh, mm = (int(part) for part in node.split(":"))
    cutoff = time(hh, mm)
    later = [bar for bar in bars if bar["dt"].time() >= cutoff]
    if not later:
        return
    high_bar = max(later, key=lambda bar: float(bar["high"]))
    snapshot["later_high"] = float(high_bar["high"])
    snapshot["later_high_time"] = high_bar["dt"].strftime("%H:%M")
    snapshot["later_high_gain_pct"] = _pct(float(high_bar["high"]), price)
    snapshot["close_from_node_pct"] = _pct(closing, price)


def _build_row(
    path: Path,
    item: dict[str, Any],
    tushare_dir: Path,
) -> dict[str, Any] | None:
    code = path.stem
    float_shares_wan = _safe_float(item.get("float_shares"))
    turnover_rate = _safe_float(item.get("TURNOVERRATE"))
    intraday = guidance._read_intraday(path, turnover_rate)
    if intraday is None:
        return None
    if turnover_rate is None and float_shares_wan:
        turnover_rate = intraday["total_volume"] / (float_shares_wan * 10000.0) * 100.0
        intraday["turnover_rate"] = turnover_rate
        intraday["snapshots"] = {
            node: _snapshot_at(intraday, node) for node in guidance.NODE_TIMES
        }

    fixed = _fixed_new_share_record(code, tushare_dir)
    issue_price = _safe_float(item.get("ISSUE_PRICE")) or _safe_float(fixed.get("price"))
    new_shares_wan = _safe_float(item.get("TOTAL_ISSUE_NUM")) or _safe_float(fixed.get("amount"))
    if float_shares_wan is None:
        old_shares_wan = _safe_float(item.get("old_shares")) or 0.0
        float_shares_wan = (new_shares_wan or 0.0) + old_shares_wan

    opening = float(intraday["open"])
    closing = float(intraday["close"])
    high = float(intraday["high"])
    low = float(intraday["low"])
    average_price = float(intraday["average_price"])
    bars = intraday["bars"]
    snapshots = {node: _snapshot_at(intraday, node) for node in OBSERVATION_NODES}
    for node, snapshot in snapshots.items():
        _attach_forward_outcomes(snapshot, bars, node, closing)
    morning_bars = [bar for bar in bars if bar["dt"].time() <= time(11, 30)]
    afternoon_bars = [bar for bar in bars if bar["dt"].time() >= time(13, 0)]
    morning_high = max(float(bar["high"]) for bar in morning_bars)
    afternoon_high = max(float(bar["high"]) for bar in afternoon_bars)
    afternoon_high_bar = max(afternoon_bars, key=lambda bar: float(bar["high"]))
    high_time = str(intraday["high_time"])
    next_day = _next_day_record(code, intraday["listing_date"], tushare_dir)
    next_open = _safe_float(next_day.get("open"))
    next_high = _safe_float(next_day.get("high"))
    next_close = _safe_float(next_day.get("close"))

    result: dict[str, Any] = {
        "code": code,
        "name": str(item.get("SECURITY_NAME_ABBR") or fixed.get("name") or ""),
        "listing_date": intraday["listing_date"],
        "external_event": code in EXTERNAL_EVENT_SAMPLES,
        "external_event_note": EXTERNAL_EVENT_SAMPLES.get(code, ""),
        "issue_price": issue_price,
        "new_shares_wan": new_shares_wan,
        "float_shares_wan": float_shares_wan,
        "old_shares_wan": _safe_float(item.get("old_shares")) or 0.0,
        "issue_principal_yi": (issue_price * new_shares_wan / 10000.0) if issue_price and new_shares_wan else None,
        "float_cap_at_issue_yi": (issue_price * float_shares_wan / 10000.0) if issue_price and float_shares_wan else None,
        "float_cap_at_50_gain_yi": (issue_price * 1.5 * float_shares_wan / 10000.0) if issue_price and float_shares_wan else None,
        "new_share_value_at_vwap_yi": (average_price * new_shares_wan / 10000.0) if new_shares_wan else None,
        "first_day_turnover_amount_yi": float(intraday["total_amount"]) / 100000000.0,
        "turnover_rate_pct": turnover_rate,
        "open": opening,
        "close": closing,
        "high": high,
        "low": low,
        "vwap": average_price,
        "open_gain_pct": _pct(opening, issue_price),
        "high_gain_pct": _pct(high, issue_price),
        "close_gain_pct": _pct(closing, issue_price),
        "low_gain_pct": _pct(low, issue_price),
        "high_vs_open_pct": _pct(high, opening),
        "close_vs_open_pct": _pct(closing, opening),
        "close_vs_vwap_pct": _pct(closing, average_price),
        "high_time": high_time,
        "high_time_bucket": _time_bucket(high_time),
        "first_open_plus_30_time": _first_hit_time(bars, opening * 1.3),
        "first_open_plus_60_time": _first_hit_time(bars, opening * 1.6),
        "first_issue_plus_50_time": _first_hit_time(bars, issue_price * 1.5) if issue_price else "",
        "morning_high": morning_high,
        "afternoon_high": afternoon_high,
        "afternoon_high_time": afternoon_high_bar["dt"].strftime("%H:%M"),
        "afternoon_vs_morning_high_pct": _pct(afternoon_high, morning_high),
        "afternoon_new_high_1pct": afternoon_high >= morning_high * 1.01,
        "turnover_60_cross": _turnover_cross(bars, intraday["total_volume"], turnover_rate, 60.0),
        "turnover_70_cross": _turnover_cross(bars, intraday["total_volume"], turnover_rate, 70.0),
        "next_trade_date": str(next_day.get("trade_date") or ""),
        "next_open": next_open,
        "next_high": next_high,
        "next_close": next_close,
        "next_open_vs_day1_close_pct": _pct(next_open, closing),
        "next_high_vs_day1_close_pct": _pct(next_high, closing),
        "next_close_vs_day1_close_pct": _pct(next_close, closing),
        "next_day_recovered_day1_close": bool(next_high is not None and next_high >= closing),
        "nodes": snapshots,
    }
    if result["issue_principal_yi"]:
        result["turnover_amount_to_issue_principal"] = result["first_day_turnover_amount_yi"] / result["issue_principal_yi"]
    else:
        result["turnover_amount_to_issue_principal"] = None
    if result["float_cap_at_50_gain_yi"]:
        result["turnover_amount_to_float_cap_50"] = result["first_day_turnover_amount_yi"] / result["float_cap_at_50_gain_yi"]
    else:
        result["turnover_amount_to_float_cap_50"] = None
    return result


def _condition_outcome(rows: list[dict[str, Any]], node: str, predicate) -> dict[str, Any]:
    members = []
    for row in rows:
        snapshot = (row.get("nodes") or {}).get(node) or {}
        if snapshot and predicate(row, snapshot):
            members.append((row, snapshot))
    later_high = [
        snapshot["later_high_gain_pct"]
        for _, snapshot in members
        if snapshot.get("later_high_gain_pct") is not None
    ]
    close_returns = [
        snapshot["close_from_node_pct"]
        for _, snapshot in members
        if snapshot.get("close_from_node_pct") is not None
    ]
    return {
        "count": len(members),
        "codes": [row["code"] for row, _ in members],
        "later_high_ge_5_count": sum(value >= 5.0 for value in later_high),
        "later_high_gain_pct": _stats(later_high),
        "close_from_node_pct": _stats(close_returns),
        "close_above_node_count": sum(value > 0 for value in close_returns),
    }


def _group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    next_rows = [row for row in rows if row.get("next_close_vs_day1_close_pct") is not None]
    high_turnover = [row for row in next_rows if (row.get("turnover_rate_pct") or 0.0) >= 90.0]
    lower_turnover = [row for row in next_rows if (row.get("turnover_rate_pct") or 0.0) < 90.0]
    node_summary: dict[str, Any] = {}
    for node in OBSERVATION_NODES:
        node_values = []
        for row in rows:
            snapshot = (row.get("nodes") or {}).get(node) or {}
            price = _safe_float(snapshot.get("price"))
            if price is not None:
                node_values.append(_pct(price, row["open"]) or 0.0)
        node_summary[node] = {
            **_stats(node_values),
            "above_open_count": sum(value > 0 for value in node_values),
        }

    return {
        "count": len(rows),
        "close_gain_pct": _stats([row["close_gain_pct"] for row in rows if row.get("close_gain_pct") is not None]),
        "high_gain_ge_50_count": sum((row.get("high_gain_pct") or -999.0) >= 50.0 for row in rows),
        "close_gain_ge_50_count": sum((row.get("close_gain_pct") or -999.0) >= 50.0 for row in rows),
        "touch_50_but_close_below_count": sum(
            (row.get("high_gain_pct") or -999.0) >= 50.0 and (row.get("close_gain_pct") or -999.0) < 50.0
            for row in rows
        ),
        "intraday_low_gain_ge_50_count": sum((row.get("low_gain_pct") or -999.0) >= 50.0 for row in rows),
        "issue_principal_yi": _stats([row["issue_principal_yi"] for row in rows if row.get("issue_principal_yi") is not None]),
        "float_cap_at_50_gain_yi": _stats([row["float_cap_at_50_gain_yi"] for row in rows if row.get("float_cap_at_50_gain_yi") is not None]),
        "new_share_value_at_vwap_yi": _stats([row["new_share_value_at_vwap_yi"] for row in rows if row.get("new_share_value_at_vwap_yi") is not None]),
        "first_day_turnover_amount_yi": _stats([row["first_day_turnover_amount_yi"] for row in rows]),
        "turnover_amount_to_issue_principal": _stats([row["turnover_amount_to_issue_principal"] for row in rows if row.get("turnover_amount_to_issue_principal") is not None]),
        "turnover_amount_to_float_cap_50": _stats([row["turnover_amount_to_float_cap_50"] for row in rows if row.get("turnover_amount_to_float_cap_50") is not None]),
        "turnover_rate_pct": _stats([row["turnover_rate_pct"] for row in rows if row.get("turnover_rate_pct") is not None]),
        "high_time_bucket_counts": {
            bucket: sum(row["high_time_bucket"] == bucket for row in rows)
            for bucket in ("09:30-09:59", "10:00-11:00", "11:01-11:30", "13:00-15:00")
        },
        "open_plus_30_hit_count": sum(bool(row.get("first_open_plus_30_time")) for row in rows),
        "open_plus_30_10_to_11_count": sum("10:00" <= str(row.get("first_open_plus_30_time") or "") <= "11:00" for row in rows),
        "open_plus_30_afternoon_count": sum(str(row.get("first_open_plus_30_time") or "") >= "13:00" for row in rows),
        "afternoon_new_high_1pct_count": sum(bool(row.get("afternoon_new_high_1pct")) for row in rows),
        "next_day": {
            "count": len(next_rows),
            "close_down_count": sum((row["next_close_vs_day1_close_pct"] or 0.0) < 0 for row in next_rows),
            "recovered_day1_close_count": sum(bool(row.get("next_day_recovered_day1_close")) for row in next_rows),
            "next_open_vs_day1_close_pct": _stats([row["next_open_vs_day1_close_pct"] for row in next_rows if row.get("next_open_vs_day1_close_pct") is not None]),
            "next_high_vs_day1_close_pct": _stats([row["next_high_vs_day1_close_pct"] for row in next_rows if row.get("next_high_vs_day1_close_pct") is not None]),
            "next_close_vs_day1_close_pct": _stats([row["next_close_vs_day1_close_pct"] for row in next_rows]),
        },
        "next_day_by_turnover": {
            "ge_90": {
                "count": len(high_turnover),
                "close_down_count": sum((row["next_close_vs_day1_close_pct"] or 0.0) < 0 for row in high_turnover),
                "recovered_day1_close_count": sum(bool(row.get("next_day_recovered_day1_close")) for row in high_turnover),
                "next_close_vs_day1_close_pct": _stats([row["next_close_vs_day1_close_pct"] for row in high_turnover]),
            },
            "lt_90": {
                "count": len(lower_turnover),
                "close_down_count": sum((row["next_close_vs_day1_close_pct"] or 0.0) < 0 for row in lower_turnover),
                "recovered_day1_close_count": sum(bool(row.get("next_day_recovered_day1_close")) for row in lower_turnover),
                "next_close_vs_day1_close_pct": _stats([row["next_close_vs_day1_close_pct"] for row in lower_turnover]),
            },
        },
        "node_vs_open_pct": node_summary,
        "conditional_nodes": {
            "above_935": _condition_outcome(
                rows,
                "09:35",
                lambda row, snapshot: (
                    (snapshot.get("price") or 0.0) > row["open"]
                    and (snapshot.get("price") or 0.0) > (snapshot.get("vwap") or float("inf"))
                ),
            ),
            "strong_935": _condition_outcome(
                rows,
                "09:35",
                lambda row, snapshot: (
                    (snapshot.get("price") or 0.0) > row["open"]
                    and (snapshot.get("price") or 0.0) > (snapshot.get("vwap") or float("inf"))
                    and (snapshot.get("cumulative_turnover") is None or snapshot["cumulative_turnover"] >= 30.0)
                ),
            ),
            "weak_935": _condition_outcome(
                rows,
                "09:35",
                lambda row, snapshot: (
                    (snapshot.get("price") or float("inf")) < row["open"]
                    and (snapshot.get("price") or float("inf")) < (snapshot.get("vwap") or -float("inf"))
                ),
            ),
            "above_1000": _condition_outcome(
                rows,
                "10:00",
                lambda row, snapshot: (
                    (snapshot.get("price") or 0.0) > row["open"]
                    and (snapshot.get("price") or 0.0) > (snapshot.get("vwap") or float("inf"))
                ),
            ),
            "strong_1000": _condition_outcome(
                rows,
                "10:00",
                lambda row, snapshot: (
                    (snapshot.get("price") or 0.0) >= row["open"] * 1.05
                    and (snapshot.get("price") or 0.0) > (snapshot.get("vwap") or float("inf"))
                    and (snapshot.get("cumulative_turnover") or 0.0) >= 45.0
                    and (snapshot.get("max_drawdown_pct") or -999.0) >= -6.0
                ),
            ),
            "weak_1000": _condition_outcome(
                rows,
                "10:00",
                lambda row, snapshot: (
                    (snapshot.get("price") or float("inf")) < row["open"]
                    and (snapshot.get("price") or float("inf")) < (snapshot.get("vwap") or -float("inf"))
                ),
            ),
        },
    }


def _markdown(payload: dict[str, Any]) -> str:
    normal = payload["summaries"]["recent_normal"]
    external = payload["summaries"]["recent_external"]
    old = payload["summaries"]["old_in_sample"]
    rows = payload["recent_rows"]
    next_day = normal["next_day"]
    high_turnover = normal["next_day_by_turnover"]["ge_90"]
    lower_turnover = normal["next_day_by_turnover"]["lt_90"]
    lines = [
        "# 北交所新股首日卖出规律：新增样本复核",
        "",
        f"> 生成日期：{payload['generated_at'][:10]}",
        f"> 样本外区间：{payload['cutoff_exclusive']} 之后上市；新增 {len(rows)} 只，其中常态样本 {normal['count']} 只、外力样本 {external['count']} 只。",
        "> 价格、分钟成交和换手来自本地首日分钟线及 Tushare 本地缓存；外力标签仅用于分层，不把因果关系当成已证明事实。",
        "",
        "## 结论先行",
        "",
        f"1. **把 +50% 定义为“操盘目标/软托底锚”后，证据很强。**常态样本 {normal['count']} 只全部在盘中触及或超过 +50%，其中 {normal['close_gain_ge_50_count']} 只收盘仍守在 +50% 以上；唯一失守的是乔路铭，盘中最高约 +65%、收盘约 +31%。这更像“托底目标曾被完成但尾盘守不住”，而不是不存在 +50% 锚。",
        f"2. **“5 亿子弹”只有在首日新增流通筹码的发行价本金口径下勉强接近。**该口径中位数为 {_fmt(normal['issue_principal_yi']['median'], 2)} 亿元；但 +50% 处的首日流通市值中位数为 {_fmt(normal['float_cap_at_50_gain_yi']['median'], 2)} 亿元，首日成交额中位数为 {_fmt(normal['first_day_turnover_amount_yi']['median'], 2)} 亿元。无法从公开逐笔/分钟数据识别某个“操盘手”的真实资金池。",
        f"3. **“10:00—11:00 拉一次”不具有普遍性。**常态样本只有 {normal['open_plus_30_hit_count']} 只曾较开盘上涨 30% 触发第一档临停线，其中 {normal['open_plus_30_10_to_11_count']} 只首次发生在 10:00—11:00；全天高点落在该窗口的样本数是 {normal['high_time_bucket_counts']['10:00-11:00']}。",
        f"4. **“无外力时下午基本没戏”得到新增样本支持。**常态样本 0/{normal['count']} 在下午把上午高点再提高至少 1%，全天高点也没有一只落在下午；旧样本曾有 {old['afternoon_new_high_1pct_count']}/{old['count']} 下午创新高，说明这里确实发生了时段结构变化。两只外力样本中则有 {external['afternoon_new_high_1pct_count']} 只下午创新高。",
        f"5. **“第二天不好出货”得到最强支持。**有次日数据的常态样本 {next_day['count']} 只中，{next_day['close_down_count']} 只次日收盘低于首日收盘；次日收盘相对首日收盘中位数为 {_fmt(next_day['next_close_vs_day1_close_pct']['median'], 1, '%')}，只有 {next_day['recovered_day1_close_count']} 只在次日盘中重新触及首日收盘价。",
        "",
        "## 逐条验证",
        "",
        "### 0. 相比旧样本，变化发生在哪里",
        "",
        "| 指标 | 旧样本（40只） | 新增常态（11只） | 方向 |",
        "|---|---:|---:|---|",
        f"| 新增流通筹码发行价本金中位数 | {_fmt(old['issue_principal_yi']['median'], 2)} 亿 | {_fmt(normal['issue_principal_yi']['median'], 2)} 亿 | 上升 |",
        f"| +50% 处流通市值中位数 | {_fmt(old['float_cap_at_50_gain_yi']['median'], 2)} 亿 | {_fmt(normal['float_cap_at_50_gain_yi']['median'], 2)} 亿 | 上升 |",
        f"| 首日成交额中位数 | {_fmt(old['first_day_turnover_amount_yi']['median'], 2)} 亿 | {_fmt(normal['first_day_turnover_amount_yi']['median'], 2)} 亿 | 基本持平 |",
        f"| 成交额 / +50% 流通市值中位数 | {_fmt(old['turnover_amount_to_float_cap_50']['median'], 2)} 倍 | {_fmt(normal['turnover_amount_to_float_cap_50']['median'], 2)} 倍 | 下降 |",
        f"| 下午再创上午高点 1%+ | {old['afternoon_new_high_1pct_count']}/{old['count']} | {normal['afternoon_new_high_1pct_count']}/{normal['count']} | 消失 |",
        f"| 次日收盘相对首日收盘中位数 | {_fmt(old['next_day']['next_close_vs_day1_close_pct']['median'], 1, '%')} | {_fmt(normal['next_day']['next_close_vs_day1_close_pct']['median'], 1, '%')} | 恶化 |",
        "",
        "最值得更新的解释是：近期新股需要承接的静态盘子变大，但首日总成交额没有同步扩张，资金周转效率下降；结果表现为高点前移、下午增量行情消失、次日承接恶化。这个证据比“固定 5 亿元操盘资金”更可复核。",
        "",
        "### 1. 50% 涨幅：更适合解释为操盘目标/软托底锚",
        "",
        f"常态样本 {normal['high_gain_ge_50_count']}/{normal['count']} 盘中达到 +50%，{normal['close_gain_ge_50_count']}/{normal['count']} 收盘守住，只有 {normal['touch_50_but_close_below_count']} 只“触及后失守”。收盘涨幅中位数 {_fmt(normal['close_gain_pct']['median'], 1, '%')}，四分位区间 {_fmt(normal['close_gain_pct']['p25'], 1, '%')}—{_fmt(normal['close_gain_pct']['p75'], 1, '%')}。因此 +50% 可作为近期市场的软锚或操盘目标，但仍不能当作无条件成交底价。",
        "",
        "### 2. 5 亿元约束：三个口径不能混用",
        "",
        "| 口径（常态样本） | 中位数 | P25—P75 | 含义 |",
        "|---|---:|---:|---|",
        f"| 首日新增流通股份 × 发行价 | {_fmt(normal['issue_principal_yi']['median'], 2)} 亿 | {_fmt(normal['issue_principal_yi']['p25'], 2)}—{_fmt(normal['issue_principal_yi']['p75'], 2)} 亿 | 首日需要换手的新增筹码本金，最接近“5 亿” |",
        f"| 首日流通股份 × 发行价 × 1.5 | {_fmt(normal['float_cap_at_50_gain_yi']['median'], 2)} 亿 | {_fmt(normal['float_cap_at_50_gain_yi']['p25'], 2)}—{_fmt(normal['float_cap_at_50_gain_yi']['p75'], 2)} 亿 | 在 +50% 价位承接全部首日流通盘的静态上限 |",
        f"| 首日成交额 | {_fmt(normal['first_day_turnover_amount_yi']['median'], 2)} 亿 | {_fmt(normal['first_day_turnover_amount_yi']['p25'], 2)}—{_fmt(normal['first_day_turnover_amount_yi']['p75'], 2)} 亿 | 含筹码多次换手，不能当净投入 |",
        f"| 首日新增流通股份 × 首日 VWAP | {_fmt(normal['new_share_value_at_vwap_yi']['median'], 2)} 亿 | {_fmt(normal['new_share_value_at_vwap_yi']['p25'], 2)}—{_fmt(normal['new_share_value_at_vwap_yi']['p75'], 2)} 亿 | 新筹码按均价一次换手的资金规模代理 |",
        "",
            "因此，数据支持的是“多数新股首日新增流通筹码的发行价本金集中在数亿元”，不支持“每只股票存在固定 5 亿元操盘资金”。战略配售、超额配售与首日不可流通部分不应混入这个承接口径。",
        "",
        "### 3. 拉升时段与下午机会",
        "",
        "| 全天高点时段 | 常态样本数 | 外力样本数 |",
        "|---|---:|---:|",
    ]
    for bucket in ("09:30-09:59", "10:00-11:00", "11:01-11:30", "13:00-15:00"):
        lines.append(f"| {bucket} | {normal['high_time_bucket_counts'][bucket]} | {external['high_time_bucket_counts'][bucket]} |")
    lines.extend(
        [
            "",
            "把“拉涨停”严格解释为较开盘价首次触及 +30% 临停线更合适；仅看肉眼上的快速上冲很容易事后挑图。新增常态样本显示，拉升窗口分散，10:00—11:00 并非唯一窗口；无外力样本的下午机会在本批样本中已经消失，下午强拉只出现在预先单列的外力样本。",
            "",
            "### 4. 次日承接：高换手是最清楚的新风险信号",
            "",
            "| 首日总换手 | 样本 | 次日收跌 | 次日盘中重回首日收盘 | 次日收盘相对首日收盘中位 |",
            "|---|---:|---:|---:|---:|",
            f"| ≥90% | {high_turnover['count']} | {high_turnover['close_down_count']} | {high_turnover['recovered_day1_close_count']} | {_fmt(high_turnover['next_close_vs_day1_close_pct']['median'], 1, '%')} |",
            f"| <90% | {lower_turnover['count']} | {lower_turnover['close_down_count']} | {lower_turnover['recovered_day1_close_count']} | {_fmt(lower_turnover['next_close_vs_day1_close_pct']['median'], 1, '%')} |",
            "",
            "新增样本里，首日换手达到 90% 后，第二天几乎没有容错。这里更合理的解释是筹码在首日充分松动、短线需求被透支；它是结果相关性，不足以单独证明某个资金主体“承接不了”。",
            "",
            "## 对旧指南的修订建议",
            "",
            "1. 保留 9:35、9:45 的价—均价弱势退出；新增样本没有推翻“弱势早确认”的主框架。",
            "2. 不再把 10:00—10:30 描述为常见主拉窗口，改成“候选窗口之一”。是否继续留仓，仍要求价格在开盘价和 VWAP 上方，而不是只看换手。",
            "3. 新增“首日总换手接近/超过 90%”的隔夜禁入信号：除非有明确事件外力且能承受次日大幅低开，否则不把尾仓带到第二天。",
            "4. 常态样本把 11:30 提升为清仓节点：上午未创新高、未触发临停或已跌回 VWAP 下方，原则上不留到下午；只有预先确认的事件外力样本才保留下午观察分支，14:30 仍执行清仓。",
            "5. +50% 升级为“软托底锚/操盘目标”观察项：盘中未能触及属于明显弱于近期常态；触及后失守则属于托底失败。它仍不直接作为挂单价或无条件止损位。",
            "",
            "## 旧规则的样本外条件复核",
            "",
            "| 条件 | 样本 | 之后仍有 5%+ 更高价 | 节点后高点空间中位 | 持有到收盘收益中位 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    condition_labels = (
        ("above_935", "9:35 价在开盘和 VWAP 上方"),
        ("strong_935", "9:35 价在开盘和 VWAP 上方，换手≥30%"),
        ("weak_935", "9:35 价在开盘和 VWAP 下方"),
        ("above_1000", "10:00 价在开盘和 VWAP 上方"),
        ("strong_1000", "10:00 满足旧指南强势四条件"),
        ("weak_1000", "10:00 价在开盘和 VWAP 下方"),
    )
    for key, label in condition_labels:
        outcome = normal["conditional_nodes"][key]
        lines.append(
            f"| {label} | {outcome['count']} | {outcome['later_high_ge_5_count']} | "
            f"{_fmt(outcome['later_high_gain_pct']['median'], 1, '%')} | {_fmt(outcome['close_from_node_pct']['median'], 1, '%')} |"
        )
    lines.extend(
        [
            "",
            "强势条件若样本数过少，只能用于否决弱票、不能用来预测一定主升。尤其是高换手本身不构成强势，必须同时看到价格抬升和 VWAP 支撑。",
            "",
            "## 固定时点相对开盘表现（常态样本）",
            "",
            "| 时点 | 均值 | 中位 | P25—P75 | 高于开盘 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for node in OBSERVATION_NODES:
        stats = normal["node_vs_open_pct"][node]
        lines.append(
            f"| {node} | {_fmt(stats['mean'], 1, '%')} | {_fmt(stats['median'], 1, '%')} | "
            f"{_fmt(stats['p25'], 1, '%')}—{_fmt(stats['p75'], 1, '%')} | {stats['above_open_count']}/{stats['count']} |"
        )
    lines.extend(
        [
            "",
            "> 固定时点只用于比较，不等于可成交回放；节点落在临停期间时，必须以复牌集合竞价的实际成交价为准。",
            "",
            "## 新增样本明细",
            "",
            "| 代码 | 名称 | 日期 | 分层 | 收盘涨幅 | 新增流通本金 | +50% 流通市值 | 首日成交额 | 换手 | 高点时刻 | 首次 +30% | 次日高点/首日收盘 | 次日收盘 |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        lines.append(
            "| {code} | {name} | {date} | {layer} | {close_gain} | {issue_principal} | {float50} | {amount} | {turnover} | {high_time} | {hit30} | {next_high} | {next_close} |".format(
                code=row["code"],
                name=row["name"],
                date=row["listing_date"],
                layer="外力" if row["external_event"] else "常态",
                close_gain=_fmt(row.get("close_gain_pct"), 1, "%"),
                issue_principal=_fmt(row.get("issue_principal_yi"), 2, "亿"),
                float50=_fmt(row.get("float_cap_at_50_gain_yi"), 2, "亿"),
                amount=_fmt(row.get("first_day_turnover_amount_yi"), 2, "亿"),
                turnover=_fmt(row.get("turnover_rate_pct"), 1, "%"),
                high_time=row.get("high_time") or "",
                hit30=row.get("first_open_plus_30_time") or "—",
                next_high=_fmt(row.get("next_high_vs_day1_close_pct"), 1, "%") or "—",
                next_close=_fmt(row.get("next_close_vs_day1_close_pct"), 1, "%") or "—",
            )
        )
    lines.extend(
        [
            "",
            "## 口径与限制",
            "",
            "- 分界日固定为旧版指南日期 2026-07-14；样本外定义为其后上市的本地分钟线。",
            "- 维琪科技、双英集团按用户提出的外力假设预先分层；公开信息只确认同日上市，不能仅凭同日关系证明资金确已从另一只新股溢出。",
            "- “+30%”按相对开盘价的第一档临停阈值识别；分钟线无法还原挂单队列和账户级净流入。",
            "- 样本只有十余只，结论适合更新执行规则，不适合宣称稳定因果或自动交易。",
            "",
        ]
    )
    return "\n".join(lines)


def _flat_csv_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = (
        "code",
        "name",
        "listing_date",
        "external_event",
        "issue_price",
        "new_shares_wan",
        "float_shares_wan",
        "old_shares_wan",
        "issue_principal_yi",
        "float_cap_at_50_gain_yi",
        "new_share_value_at_vwap_yi",
        "first_day_turnover_amount_yi",
        "turnover_rate_pct",
        "turnover_amount_to_issue_principal",
        "turnover_amount_to_float_cap_50",
        "open_gain_pct",
        "high_gain_pct",
        "close_gain_pct",
        "low_gain_pct",
        "high_vs_open_pct",
        "close_vs_open_pct",
        "close_vs_vwap_pct",
        "high_time",
        "high_time_bucket",
        "first_open_plus_30_time",
        "first_open_plus_60_time",
        "first_issue_plus_50_time",
        "afternoon_vs_morning_high_pct",
        "afternoon_new_high_1pct",
        "next_trade_date",
        "next_open_vs_day1_close_pct",
        "next_high_vs_day1_close_pct",
        "next_close_vs_day1_close_pct",
        "next_day_recovered_day1_close",
    )
    return [{field: row.get(field) for field in fields} for row in rows]


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = Path(args.dataset)
    intraday_dir = Path(args.intraday_dir)
    tushare_dir = Path(args.tushare_dir)
    output_dir = Path(args.output_dir)
    cutoff = date.fromisoformat(args.cutoff)
    items = {str(item.get("SECURITY_CODE") or ""): item for item in (_load_json(dataset_path).get("items") or [])}

    all_rows: list[dict[str, Any]] = []
    for path in sorted(intraday_dir.glob("*.csv")):
        item = items.get(path.stem)
        if not item:
            continue
        row = _build_row(path, item, tushare_dir)
        if row:
            all_rows.append(row)
    all_rows.sort(key=lambda row: (row["listing_date"], row["code"]))
    recent_rows = [row for row in all_rows if date.fromisoformat(row["listing_date"]) > cutoff]
    normal_rows = [row for row in recent_rows if not row["external_event"]]
    external_rows = [row for row in recent_rows if row["external_event"]]
    old_rows = [row for row in all_rows if date.fromisoformat(row["listing_date"]) <= cutoff]

    payload = {
        "schema": "recent_listing_day_regime_review_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cutoff_exclusive": args.cutoff,
        "external_event_samples": EXTERNAL_EVENT_SAMPLES,
        "source_paths": {
            "dataset": str(dataset_path),
            "intraday_dir": str(intraday_dir),
            "tushare_dir": str(tushare_dir),
        },
        "summaries": {
            "old_in_sample": _group_summary(old_rows),
            "recent_all": _group_summary(recent_rows),
            "recent_normal": _group_summary(normal_rows),
            "recent_external": _group_summary(external_rows),
        },
        "recent_rows": recent_rows,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    json_path = output_dir / f"recent_listing_day_pattern_review_{stamp}.json"
    csv_path = output_dir / f"recent_listing_day_pattern_review_{stamp}.csv"
    markdown_path = output_dir / f"recent_listing_day_pattern_review_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    flat_rows = _flat_csv_rows(recent_rows)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat_rows[0]) if flat_rows else [])
        if flat_rows:
            writer.writeheader()
            writer.writerows(flat_rows)
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "csv": str(csv_path), "markdown": str(markdown_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="复核旧版北交所新股首日卖出规律在新增样本上的表现。")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--intraday-dir", default=str(DEFAULT_INTRADAY_DIR))
    parser.add_argument("--tushare-dir", default=str(DEFAULT_TUSHARE_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--cutoff", default=DEFAULT_CUTOFF, help="旧指南样本截止日期，新增样本取该日之后。")
    return parser


def main() -> int:
    payload = run(build_parser().parse_args())
    normal = payload["summaries"]["recent_normal"]
    print(
        json.dumps(
            {
                "outputs": payload["outputs"],
                "recent_count": len(payload["recent_rows"]),
                "normal_count": normal["count"],
                "close_gain_ge_50_count": normal["close_gain_ge_50_count"],
                "next_day": normal["next_day"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
