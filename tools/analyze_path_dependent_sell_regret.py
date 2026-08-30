from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path
from statistics import mean, median
from typing import Any

import evaluate_intraday_valuation_guidance as guidance


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"
DEFAULT_CUTOFF = "2026-07-14"
EXTERNAL_EVENT_SAMPLES = {"920176", "920059"}
POLICY_NODES = ("09:35", "09:45", "10:00", "11:00", "11:30")
SNAPSHOT_NODES = ("09:35", "09:45", "10:00", "10:30", "11:00", "11:30")
NODE_ORDER = {node: index for index, node in enumerate(SNAPSHOT_NODES)}


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


def _quantile(values: list[float], q: float) -> float | None:
    clean = sorted(float(value) for value in values if value is not None and math.isfinite(float(value)))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    position = (len(clean) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return clean[lower]
    return clean[lower] * (upper - position) + clean[upper] * (position - lower)


def _stats(values: list[float]) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return {
        "count": len(clean),
        "mean": mean(clean) if clean else None,
        "median": median(clean) if clean else None,
        "p10": _quantile(clean, 0.10),
        "p25": _quantile(clean, 0.25),
        "p75": _quantile(clean, 0.75),
        "p90": _quantile(clean, 0.90),
        "min": min(clean) if clean else None,
        "max": max(clean) if clean else None,
    }


def _fmt(value: Any, digits: int = 1, suffix: str = "") -> str:
    number = _safe_float(value)
    return "—" if number is None else f"{number:.{digits}f}{suffix}"


def _bars_between(bars: list[dict[str, Any]], start: str, end: str) -> list[dict[str, Any]]:
    start_time = time(*(int(part) for part in start.split(":")))
    end_time = time(*(int(part) for part in end.split(":")))
    return [bar for bar in bars if start_time <= bar["dt"].time() <= end_time]


def _bars_after_until(bars: list[dict[str, Any]], start: str, end: str) -> list[dict[str, Any]]:
    start_time = time(*(int(part) for part in start.split(":")))
    end_time = time(*(int(part) for part in end.split(":")))
    return [bar for bar in bars if start_time < bar["dt"].time() <= end_time]


def _window_vwap(bars: list[dict[str, Any]]) -> float | None:
    volume = sum(float(bar.get("volume") or 0.0) for bar in bars)
    amount = sum(float(bar.get("amount") or 0.0) for bar in bars)
    return amount / volume if volume > 0 else None


def _snapshot_map(intraday: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        node: guidance._snapshot(
            intraday["bars"],
            node,
            intraday.get("turnover_rate"),
            intraday["total_volume"],
        )
        or {}
        for node in SNAPSHOT_NODES
    }


def _both_below(snapshot: dict[str, Any], opening: float) -> bool:
    price = _safe_float(snapshot.get("price"))
    vwap = _safe_float(snapshot.get("vwap"))
    return bool(price is not None and vwap is not None and price < opening and price < vwap)


def _rule_flags(intraday: dict[str, Any], snapshots: dict[str, dict[str, Any]]) -> dict[str, bool]:
    opening = float(intraday["open"])
    flags = {node: _both_below(snapshots[node], opening) for node in ("09:35", "09:45", "10:00")}
    before_1000 = [bar for bar in intraday["bars"] if bar["dt"].time() <= time(10, 0)]
    after_1000 = [bar for bar in intraday["bars"] if time(10, 0) < bar["dt"].time() <= time(11, 0)]
    early_high = max((float(bar["high"]) for bar in before_1000), default=opening)
    later_high = max((float(bar["high"]) for bar in after_1000), default=-math.inf)
    s1100 = snapshots["11:00"]
    price_1100 = _safe_float(s1100.get("price"))
    vwap_1100 = _safe_float(s1100.get("vwap"))
    no_breakout = later_high <= early_high
    below_either = bool(
        price_1100 is not None
        and vwap_1100 is not None
        and (price_1100 < opening or price_1100 < vwap_1100)
    )
    flags["11:00"] = bool(no_breakout or below_either)
    flags["11:30"] = True
    return flags


def _path_exit(flags: dict[str, bool], snapshots: dict[str, dict[str, Any]]) -> tuple[str, float]:
    for node in POLICY_NODES:
        if flags[node]:
            return node, float(snapshots[node]["price"])
    raise RuntimeError("11:30 final-exit rule was not applied")


def _forced_10_11_exit(flags: dict[str, bool], snapshots: dict[str, dict[str, Any]]) -> tuple[str, float]:
    for node in ("10:00", "11:00", "11:30"):
        if flags[node]:
            return node, float(snapshots[node]["price"])
    raise RuntimeError("forced 10-11 exit has no terminal node")


def _valuation_levels(row: dict[str, Any]) -> dict[str, float | None]:
    strict_low = _safe_float(row.get("strict_range_low"))
    strict_high = _safe_float(row.get("strict_range_high"))
    rolling_low = _safe_float(row.get("rolling_range_low"))
    rolling_high = _safe_float(row.get("rolling_range_high"))
    if None in (strict_low, strict_high, rolling_low, rolling_high):
        return {"lower": None, "center": None, "upper": None}
    return {
        "lower": min(strict_low, rolling_low),
        "center": max((strict_low + strict_high) / 2.0, (rolling_low + rolling_high) / 2.0),
        "upper": max(strict_high, rolling_high),
    }


def _valuation_state(price: float, levels: dict[str, float | None]) -> str:
    lower = levels["lower"]
    center = levels["center"]
    upper = levels["upper"]
    if None in (lower, center, upper):
        return "unavailable"
    if price < lower:
        return "below_lower"
    if price < center:
        return "lower_to_center"
    if price <= upper:
        return "center_to_upper"
    return "above_upper"


def _build_row(
    intraday: dict[str, Any],
    valuation_row: dict[str, Any],
    cutoff: date,
) -> dict[str, Any]:
    snapshots = _snapshot_map(intraday)
    flags = _rule_flags(intraday, snapshots)
    exit_node, exit_price = _path_exit(flags, snapshots)
    early_exit = exit_node in {"09:35", "09:45"}
    forced_node = ""
    forced_price = None
    if early_exit:
        forced_node, forced_price = _forced_10_11_exit(flags, snapshots)

    bars_10_11 = _bars_between(intraday["bars"], "10:00", "11:00")
    vwap_10_11 = _window_vwap(bars_10_11)
    high_10_11 = max((float(bar["high"]) for bar in bars_10_11), default=None)
    later_nodes = [node for node in SNAPSHOT_NODES if NODE_ORDER[node] > NODE_ORDER[exit_node]]
    later_node_prices = [float(snapshots[node]["price"]) for node in later_nodes if snapshots[node].get("price") is not None]
    later_bars = _bars_after_until(intraday["bars"], exit_node, "11:30")
    later_high = max((float(bar["high"]) for bar in later_bars), default=exit_price)

    levels = _valuation_levels(valuation_row)
    valuation_state = _valuation_state(exit_price, levels)
    center = levels["center"]
    lower = levels["lower"]
    upper = levels["upper"]
    reliable = str((valuation_row.get("range_reliability") or {}).get("level") or "") in {
        "normal",
        "medium_mixed",
    }

    forced_delta = _pct(forced_price, exit_price)
    vwap_delta = _pct(vwap_10_11, exit_price) if early_exit else None
    best_node_delta = _pct(max(later_node_prices), exit_price) if early_exit and later_node_prices else None
    peak_delta = _pct(max(later_high, exit_price), exit_price) if early_exit else None
    runner30_below_center = exit_price
    runner30_below_lower = exit_price
    runner30_below_center_reliable = exit_price
    if early_exit and forced_price is not None and center is not None and exit_price < center:
        runner30_below_center = exit_price * 0.70 + forced_price * 0.30
        if reliable:
            runner30_below_center_reliable = runner30_below_center
    if early_exit and forced_price is not None and lower is not None and exit_price < lower:
        runner30_below_lower = exit_price * 0.70 + forced_price * 0.30

    listing_date = date.fromisoformat(intraday["listing_date"])
    if listing_date <= cutoff:
        period = "old"
    elif intraday["code"] in EXTERNAL_EVENT_SAMPLES:
        period = "recent_external"
    else:
        period = "recent_normal"

    local_trigger = {node: bool(flags[node]) for node in POLICY_NODES}
    ghost_trigger = {
        node: bool(flags[node] and NODE_ORDER[node] > NODE_ORDER[exit_node])
        for node in ("09:35", "09:45", "10:00", "11:00")
    }
    ghost_price_vs_exit_pct = {
        node: _pct(float(snapshots[node]["price"]), exit_price) if ghost_trigger[node] else None
        for node in ghost_trigger
    }

    return {
        "code": intraday["code"],
        "name": valuation_row.get("name") or "",
        "listing_date": intraday["listing_date"],
        "period": period,
        "external_event": intraday["code"] in EXTERNAL_EVENT_SAMPLES,
        "open": float(intraday["open"]),
        "close": float(intraday["close"]),
        "high": float(intraday["high"]),
        "path_exit_node": exit_node,
        "path_exit_price": exit_price,
        "path_exit_vs_open_pct": _pct(exit_price, float(intraday["open"])),
        "path_exit_vs_close_pct": _pct(exit_price, float(intraday["close"])),
        "early_exit": early_exit,
        "forced_10_11_exit_node": forced_node,
        "forced_10_11_exit_price": forced_price,
        "forced_10_11_regret_signed_pct": forced_delta,
        "forced_10_11_regret_positive_pct": max(forced_delta or 0.0, 0.0) if forced_delta is not None else None,
        "forced_10_11_wait_loss_avoided_pct": max(-(forced_delta or 0.0), 0.0) if forced_delta is not None else None,
        "vwap_10_11": vwap_10_11,
        "vwap_10_11_regret_signed_pct": vwap_delta,
        "high_10_11": high_10_11,
        "best_later_node_regret_signed_pct": best_node_delta,
        "best_later_node_regret_positive_pct": max(best_node_delta or 0.0, 0.0) if best_node_delta is not None else None,
        "later_morning_peak_regret_pct": peak_delta,
        "valuation_lower": lower,
        "valuation_center": center,
        "valuation_upper": upper,
        "exit_valuation_state": valuation_state,
        "exit_vs_valuation_center_pct": _pct(exit_price, center),
        "center_gap_above_exit_pct": _pct(center, exit_price),
        "valuation_realization_ratio": (
            (exit_price - lower) / (upper - lower)
            if None not in (lower, upper) and upper != lower
            else None
        ),
        "open_dual_state": valuation_row.get("dual_state") or "",
        "valuation_reliability": str((valuation_row.get("range_reliability") or {}).get("level") or ""),
        "runner30_below_center_price": runner30_below_center,
        "runner30_below_lower_price": runner30_below_lower,
        "runner30_below_center_reliable_price": runner30_below_center_reliable,
        "local_trigger": local_trigger,
        "ghost_trigger": ghost_trigger,
        "ghost_price_vs_exit_pct": ghost_price_vs_exit_pct,
        "snapshots": snapshots,
    }


def _stage_summary(rows: list[dict[str, Any]], node: str) -> dict[str, Any]:
    locally_triggered = [row for row in rows if row["local_trigger"].get(node)]
    eligible = [row for row in rows if NODE_ORDER[row["path_exit_node"]] >= NODE_ORDER[node]]
    actual = [row for row in rows if row["path_exit_node"] == node]
    ghost = [row for row in rows if row["ghost_trigger"].get(node)]
    ghost_deltas = [row["ghost_price_vs_exit_pct"][node] for row in ghost]
    return {
        "node": node,
        "sample_count": len(rows),
        "survivor_count": len(eligible),
        "local_trigger_count": len(locally_triggered),
        "actual_exit_count": len(actual),
        "ghost_trigger_count": len(ghost),
        "ghost_share_of_local_trigger": len(ghost) / len(locally_triggered) if locally_triggered else None,
        "ghost_codes": [row["code"] for row in ghost],
        "ghost_price_vs_early_exit_pct": _stats(ghost_deltas),
    }


def _runner_summary(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    changed = [row for row in rows if abs(float(row[key]) - float(row["path_exit_price"])) > 1e-12]
    increments = [_pct(float(row[key]), float(row["path_exit_price"])) for row in changed]
    all_exit_vs_open = [_pct(float(row[key]), float(row["open"])) for row in rows]
    return {
        "changed_count": len(changed),
        "changed_codes": [row["code"] for row in changed],
        "increment_vs_path_pct": _stats(increments),
        "improved_count": sum((value or 0.0) > 0 for value in increments),
        "worsened_count": sum((value or 0.0) < 0 for value in increments),
        "all_exit_vs_open_pct": _stats(all_exit_vs_open),
    }


def _valuation_group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for state in ("below_lower", "lower_to_center", "center_to_upper", "above_upper", "unavailable"):
        members = [row for row in rows if row["exit_valuation_state"] == state]
        early = [row for row in members if row["early_exit"]]
        forced = [row["forced_10_11_regret_signed_pct"] for row in early]
        result[state] = {
            "count": len(members),
            "early_exit_count": len(early),
            "early_exit_codes": [row["code"] for row in early],
            "forced_10_11_regret_signed_pct": _stats(forced),
            "positive_regret_count": sum((value or 0.0) > 0 for value in forced),
            "wait_loss_avoided_count": sum((value or 0.0) < 0 for value in forced),
        }
    return result


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    x_mean = mean(xs)
    y_mean = mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    x_var = sum((x - x_mean) ** 2 for x in xs)
    y_var = sum((y - y_mean) ** 2 for y in ys)
    denominator = math.sqrt(x_var * y_var)
    return numerator / denominator if denominator > 0 else None


def _group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    early = [row for row in rows if row["early_exit"]]
    center_gaps = [
        (float(row["center_gap_above_exit_pct"]), float(row["forced_10_11_regret_signed_pct"]))
        for row in early
        if row.get("center_gap_above_exit_pct") is not None
        and row.get("forced_10_11_regret_signed_pct") is not None
    ]
    forced = [row["forced_10_11_regret_signed_pct"] for row in early]
    return {
        "count": len(rows),
        "exit_node_distribution": dict(Counter(row["path_exit_node"] for row in rows)),
        "path_exit_vs_open_pct": _stats([row["path_exit_vs_open_pct"] for row in rows]),
        "path_exit_vs_close_pct": _stats([row["path_exit_vs_close_pct"] for row in rows]),
        "early_exit": {
            "count": len(early),
            "codes": [row["code"] for row in early],
            "forced_10_11_regret_signed_pct": _stats(forced),
            "positive_regret_count": sum((value or 0.0) > 0 for value in forced),
            "regret_ge_5_count": sum((value or 0.0) >= 5.0 for value in forced),
            "regret_ge_10_count": sum((value or 0.0) >= 10.0 for value in forced),
            "wait_loss_avoided_count": sum((value or 0.0) < 0 for value in forced),
            "vwap_10_11_regret_signed_pct": _stats([row["vwap_10_11_regret_signed_pct"] for row in early]),
            "best_later_node_regret_positive_pct": _stats([row["best_later_node_regret_positive_pct"] for row in early]),
            "later_morning_peak_regret_pct": _stats([row["later_morning_peak_regret_pct"] for row in early]),
            "center_gap_regret_correlation": _correlation(
                [pair[0] for pair in center_gaps],
                [pair[1] for pair in center_gaps],
            ),
        },
        "stages": {node: _stage_summary(rows, node) for node in ("09:35", "09:45", "10:00", "11:00")},
        "valuation_groups": _valuation_group_summary(rows),
        "valuation_overlay": {
            "runner30_below_center": _runner_summary(rows, "runner30_below_center_price"),
            "runner30_below_lower": _runner_summary(rows, "runner30_below_lower_price"),
            "runner30_below_center_reliable": _runner_summary(rows, "runner30_below_center_reliable_price"),
        },
    }


def _flat_row(row: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "code",
        "name",
        "listing_date",
        "period",
        "external_event",
        "open",
        "close",
        "high",
        "path_exit_node",
        "path_exit_price",
        "path_exit_vs_open_pct",
        "path_exit_vs_close_pct",
        "early_exit",
        "forced_10_11_exit_node",
        "forced_10_11_exit_price",
        "forced_10_11_regret_signed_pct",
        "forced_10_11_regret_positive_pct",
        "forced_10_11_wait_loss_avoided_pct",
        "vwap_10_11",
        "vwap_10_11_regret_signed_pct",
        "high_10_11",
        "best_later_node_regret_signed_pct",
        "best_later_node_regret_positive_pct",
        "later_morning_peak_regret_pct",
        "valuation_lower",
        "valuation_center",
        "valuation_upper",
        "exit_valuation_state",
        "exit_vs_valuation_center_pct",
        "center_gap_above_exit_pct",
        "valuation_realization_ratio",
        "open_dual_state",
        "valuation_reliability",
        "runner30_below_center_price",
        "runner30_below_lower_price",
        "runner30_below_center_reliable_price",
    )
    flat = {field: row.get(field) for field in fields}
    for node in ("09:35", "09:45", "10:00", "11:00"):
        flat[f"local_trigger_{node.replace(':', '')}"] = row["local_trigger"].get(node)
        flat[f"ghost_trigger_{node.replace(':', '')}"] = row["ghost_trigger"].get(node)
        flat[f"ghost_price_vs_exit_{node.replace(':', '')}_pct"] = row["ghost_price_vs_exit_pct"].get(node)
    return flat


def _markdown(payload: dict[str, Any]) -> str:
    summaries = payload["summaries"]
    old = summaries["old"]
    normal = summaries["recent_normal"]
    external = summaries["recent_external"]
    labels = {
        "old": "旧样本",
        "recent_normal": "新增常态",
        "recent_external": "新增外力",
        "below_lower": "低于估值下沿",
        "lower_to_center": "下沿—中枢",
        "center_to_upper": "中枢—上沿",
        "above_upper": "超过估值上沿",
        "unavailable": "估值不可用",
    }
    lines = [
        "# 北交所新股首日卖出：路径后悔与估值预期复核",
        "",
        f"> 生成日期：{payload['generated_at'][:10]}",
        f"> 样本：旧样本 {old['count']} 只；新增常态 {normal['count']} 只；新增外力 {external['count']} 只。",
        "> 本报告把每只股票只在首个触发节点卖出，后续节点仅用于反事实后悔评分，避免重复把已卖股票当作仍持仓。",
        "",
        "## 口径",
        "",
        "- 路径规则：9:35、9:45、10:00 同时低于开盘价与累计 VWAP 则退出；仍持仓者在 11:00 未突破 10:00 前高点、或跌到开盘价/VWAP 任一项下方时退出；其余 11:30 清仓。",
        "- 主后悔值：对 9:35/9:45 已退出样本，强制忽略早卖信号，改在 10:00、11:00、11:30 的首个后续卖点退出；`后续价/早卖价-1`。正值是早卖后悔，负值是早卖避免的等待损失。",
        "- 平滑后悔：10:00—11:00 区间 VWAP 相对早卖价；机会后悔：后续固定节点最好价格及 11:30 前最高价相对早卖价，只作事后上界。",
        "- 心理预期：沿用严格估值线和滚动估值线，取双线最宽下沿、较高中枢和最高上沿；按退出价格分为未到下沿、下沿—中枢、中枢—上沿、超过上沿。",
        "",
        "## 结论先行",
        "",
        f"1. 路径化后，旧样本早卖 {old['early_exit']['count']}/{old['count']}，新增常态早卖 {normal['early_exit']['count']}/{normal['count']}。强制等到 10:00—11:00 再卖的主后悔中位数从 {_fmt(old['early_exit']['forced_10_11_regret_signed_pct']['median'], 1, '%')} 变为 {_fmt(normal['early_exit']['forced_10_11_regret_signed_pct']['median'], 1, '%')}。旧样本均值仍有 {_fmt(old['early_exit']['forced_10_11_regret_signed_pct']['mean'], 1, '%')}：原因是 {old['early_exit']['regret_ge_10_count']} 只出现 10% 以上的少数大后悔，分布存在肥尾。",
        f"2. 新增常态早卖样本中，{normal['early_exit']['positive_regret_count']} 只等待后价格更高、{normal['early_exit']['wait_loss_avoided_count']} 只早卖避免了进一步损失；10:00—11:00 VWAP 相对早卖价中位 {_fmt(normal['early_exit']['vwap_10_11_regret_signed_pct']['median'], 1, '%')}。",
        f"3. 10:00 的局部卖出触发里，旧样本有 {old['stages']['10:00']['ghost_trigger_count']}/{old['stages']['10:00']['local_trigger_count']} 已在此前退出；新增常态有 {normal['stages']['10:00']['ghost_trigger_count']}/{normal['stages']['10:00']['local_trigger_count']}。这些是原固定时点比较中不可再次执行的“幽灵触发”。",
        f"4. 估值缺口与主后悔的相关系数，旧样本为 {_fmt(old['early_exit']['center_gap_regret_correlation'], 2)}，新增常态为 {_fmt(normal['early_exit']['center_gap_regret_correlation'], 2)}。仅凭“离估值中枢还很远”是否能支持继续等待，要结合分组和 30% 尾仓影子回放判断。",
        "",
        "## 新旧样本的路径后悔",
        "",
        "| 分组 | 样本 | 早卖 | 主后悔均值 | 主后悔中位 | 主后悔P25—P75 | ≥10%大后悔 | 正后悔 | 等待损失被避免 | 10—11 VWAP后悔 | 最好节点机会后悔 | 分钟高点后悔上界 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key in ("old", "recent_normal", "recent_external"):
        summary = summaries[key]
        early = summary["early_exit"]
        lines.append(
            f"| {labels[key]} | {summary['count']} | {early['count']} | "
            f"{_fmt(early['forced_10_11_regret_signed_pct']['mean'], 1, '%')} | "
            f"{_fmt(early['forced_10_11_regret_signed_pct']['median'], 1, '%')} | "
            f"{_fmt(early['forced_10_11_regret_signed_pct']['p25'], 1, '%')}—{_fmt(early['forced_10_11_regret_signed_pct']['p75'], 1, '%')} | "
            f"{early['regret_ge_10_count']} | {early['positive_regret_count']} | {early['wait_loss_avoided_count']} | "
            f"{_fmt(early['vwap_10_11_regret_signed_pct']['median'], 1, '%')} | "
            f"{_fmt(early['best_later_node_regret_positive_pct']['median'], 1, '%')} | "
            f"{_fmt(early['later_morning_peak_regret_pct']['median'], 1, '%')} |"
        )

    lines.extend(["", "## 路径化后的节点样本", ""])
    for key in ("old", "recent_normal"):
        summary = summaries[key]
        lines.extend(
            [
                f"### {labels[key]}",
                "",
                "| 节点 | 尚持仓 | 局部触发（不管前序） | 实际退出 | 幽灵触发 | 幽灵节点价较早卖价中位 |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for node in ("09:35", "09:45", "10:00", "11:00"):
            stage = summary["stages"][node]
            lines.append(
                f"| {node} | {stage['survivor_count']} | {stage['local_trigger_count']} | "
                f"{stage['actual_exit_count']} | {stage['ghost_trigger_count']} | "
                f"{_fmt(stage['ghost_price_vs_early_exit_pct']['median'], 1, '%')} |"
            )
        lines.append("")

    lines.extend(
        [
            "## 后悔值与估值兑现程度",
            "",
            "下表只看 9:35/9:45 已退出样本。正后悔表示强制等到 10:00—11:00 后卖价更高；负值表示早卖反而保护了收益。",
            "",
            "| 分组 | 退出时估值状态 | 全组样本 | 早卖样本 | 主后悔中位 | 正后悔 | 早卖保护 |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for key in ("old", "recent_normal", "recent_external"):
        summary = summaries[key]
        for state in ("below_lower", "lower_to_center", "center_to_upper", "above_upper"):
            group = summary["valuation_groups"][state]
            if not group["count"]:
                continue
            lines.append(
                f"| {labels[key]} | {labels[state]} | {group['count']} | {group['early_exit_count']} | "
                f"{_fmt(group['forced_10_11_regret_signed_pct']['median'], 1, '%')} | "
                f"{group['positive_regret_count']} | {group['wait_loss_avoided_count']} |"
            )

    lines.extend(
        [
            "",
            "## 估值尾仓的影子回放",
            "",
            "若 9:35/9:45 弱势退出时估值尚未兑现，假设只卖 70%、保留 30% 到强制 10:00—11:00 反事实卖点。该测试只判断估值能否降低后悔，不直接修改正式动作。",
            "",
            "| 分组 | 尾仓条件 | 触发 | 改善 | 恶化 | 相对全卖增量中位 | P10—P90 |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    overlay_labels = {
        "runner30_below_center": "退出价低于估值中枢",
        "runner30_below_lower": "退出价低于估值下沿",
        "runner30_below_center_reliable": "低于中枢且估值可靠性非低",
    }
    for key in ("old", "recent_normal", "recent_external"):
        for overlay_key, overlay_label in overlay_labels.items():
            result = summaries[key]["valuation_overlay"][overlay_key]
            if not result["changed_count"]:
                continue
            stats = result["increment_vs_path_pct"]
            lines.append(
                f"| {labels[key]} | {overlay_label} | {result['changed_count']} | {result['improved_count']} | "
                f"{result['worsened_count']} | {_fmt(stats['median'], 1, '%')} | {_fmt(stats['p10'], 1, '%')}—{_fmt(stats['p90'], 1, '%')} |"
            )

    early_rows = [row for row in payload["rows"] if row["early_exit"]]
    lines.extend(
        [
            "",
            "## 早卖样本明细",
            "",
            "| 代码 | 名称 | 分组 | 早卖 | 估值状态 | 距估值中枢 | 强制后卖 | 主后悔 | 10—11 VWAP后悔 | 最好节点机会后悔 |",
            "|---|---|---|---:|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in early_rows:
        lines.append(
            f"| {row['code']} | {row['name']} | {labels[row['period']]} | "
            f"{row['path_exit_node']} / {_fmt(row['path_exit_price'], 2)} | {labels.get(row['exit_valuation_state'], row['exit_valuation_state'])} | "
            f"{_fmt(row['exit_vs_valuation_center_pct'], 1, '%')} | {row['forced_10_11_exit_node']} / {_fmt(row['forced_10_11_exit_price'], 2)} | "
            f"{_fmt(row['forced_10_11_regret_signed_pct'], 1, '%')} | {_fmt(row['vwap_10_11_regret_signed_pct'], 1, '%')} | "
            f"{_fmt(row['best_later_node_regret_positive_pct'], 1, '%')} |"
        )

    lines.extend(
        [
            "",
            "## 限制",
            "",
            "- 节点价格使用对应分钟收盘，临停、复牌集合竞价、盘口深度和实际排队成交仍无法还原。",
            "- 主后悔是预先定义的反事实执行路径；最好节点和分钟高点含事后信息，只能当机会成本上界。",
            "- 双估值线是模型输出，不等同于真实可成交价值；低可靠性估值应降低心理锚权重。",
            "- 新增常态仅 11 只，任何估值分层少于 5 只均只作观察，不升级为自动卖出规则。",
            "",
        ]
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset = guidance.param_tuning.load_replay_dataset(args.dataset)
    dataset_by_code = guidance.distill._dataset_by_code(dataset)
    intraday_by_code: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    for path in sorted(Path(args.intraday_dir).glob("*.csv")):
        item = dataset_by_code.get(path.stem) or {}
        turnover = _safe_float(item.get("TURNOVERRATE"))
        try:
            intraday = guidance._read_intraday(path, turnover)
        except Exception as exc:
            errors.append({"code": path.stem, "error": str(exc)})
            continue
        if intraday:
            intraday_by_code[path.stem] = intraday

    _, strict_by_code, rolling_by_code = guidance._prediction_context(args, intraday_by_code)
    valuation_rows = guidance._build_rows(intraday_by_code, strict_by_code, rolling_by_code)
    valuation_by_code = {row["code"]: row for row in valuation_rows}
    cutoff = date.fromisoformat(args.cutoff)
    rows = [
        _build_row(intraday, valuation_by_code[code], cutoff)
        for code, intraday in intraday_by_code.items()
        if code in valuation_by_code
    ]
    rows.sort(key=lambda row: (row["listing_date"], row["code"]))
    groups = {
        "old": [row for row in rows if row["period"] == "old"],
        "recent_normal": [row for row in rows if row["period"] == "recent_normal"],
        "recent_external": [row for row in rows if row["period"] == "recent_external"],
    }
    payload = {
        "schema": "path_dependent_sell_regret_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cutoff": args.cutoff,
        "external_event_samples": sorted(EXTERNAL_EVENT_SAMPLES),
        "summaries": {key: _group_summary(members) for key, members in groups.items()},
        "parse_errors": errors,
        "rows": rows,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    json_path = output_dir / f"path_dependent_sell_regret_{stamp}.json"
    csv_path = output_dir / f"path_dependent_sell_regret_{stamp}.csv"
    markdown_path = output_dir / f"path_dependent_sell_regret_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    flat_rows = [_flat_row(row) for row in rows]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat_rows[0]) if flat_rows else [])
        if flat_rows:
            writer.writeheader()
            writer.writerows(flat_rows)
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "csv": str(csv_path), "markdown": str(markdown_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="回放北交所新股首日卖出路径，并量化时点后悔与估值心理预期。")
    parser.add_argument("--dataset", default=str(guidance.DEFAULT_DATASET))
    parser.add_argument("--params", default=str(guidance.DEFAULT_PARAMS))
    parser.add_argument("--scan-report", default=str(guidance.DEFAULT_SCAN_REPORT))
    parser.add_argument("--intraday-dir", default=str(guidance.DEFAULT_INTRADAY_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    return parser


def main() -> int:
    payload = run(build_parser().parse_args())
    print(
        json.dumps(
            {
                "outputs": payload["outputs"],
                "sample_count": len(payload["rows"]),
                "parse_error_count": len(payload["parse_errors"]),
                "old_early": payload["summaries"]["old"]["early_exit"],
                "recent_normal_early": payload["summaries"]["recent_normal"]["early_exit"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
