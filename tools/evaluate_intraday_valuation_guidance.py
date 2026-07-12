from __future__ import annotations

import argparse
import copy
import csv
import importlib.util
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, time
from pathlib import Path
from statistics import mean, median
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import config_loader
import param_tuning


DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_PARAMS = ROOT_DIR / "策略参数.txt"
DEFAULT_SCAN_REPORT = ROOT_DIR / "调参" / "valuation_hit_rate_scan_202603plus_20260710_001437.json"
DEFAULT_INTRADAY_DIR = ROOT_DIR / "首日分时走势"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"
NODE_TIMES = ("09:35", "09:45", "10:00", "10:30", "13:30", "14:00", "14:30")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proxy = _load_module("evaluate_local_proxy_strategy", ROOT_DIR / "tools" / "evaluate_local_proxy_strategy.py")
distill = proxy.distill
blend = proxy.blend


STRICT_STRATEGY = {
    "name": "scan_best_proxy_model_balanced_recent_mood",
    "model": "scan_best",
    "center_policy": "model",
    "center_condition": "never",
    "center_alpha": 0.0,
    "width_policy": "balanced",
    "fallback_policy": "recent_mood",
    "research_only": False,
}
ROLLING_STRATEGY = {
    "name": "scan_best_proxy_all_rolling50_layered_v1_recent_mood",
    "model": "scan_best",
    "center_policy": "all_rolling50",
    "center_condition": "all",
    "center_alpha": 0.50,
    "width_policy": "layered_v1",
    "fallback_policy": "recent_mood",
    "research_only": True,
}


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _mean(values: list[float]) -> float | None:
    return mean(values) if values else None


def _median(values: list[float]) -> float | None:
    return median(values) if values else None


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _fmt_num(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    return "" if number is None else f"{number:.{digits}f}"


def _fmt_pct(value: Any, digits: int = 1) -> str:
    number = _safe_float(value)
    return "" if number is None else f"{number:.{digits}f}%"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _decode_csv(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", raw, 0, min(1, len(raw)), f"cannot decode {path}")


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip().replace("/", "-")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _read_intraday(path: Path, turnover_rate: float | None) -> dict[str, Any] | None:
    reader = csv.DictReader(_decode_csv(path).splitlines())
    bars: list[dict[str, Any]] = []
    for raw in reader:
        dt = _parse_datetime(raw.get("DateTime") or raw.get("datetime") or raw.get("time"))
        open_price = _safe_float(raw.get("open"))
        high = _safe_float(raw.get("high"))
        low = _safe_float(raw.get("low"))
        close = _safe_float(raw.get("close"))
        if dt is None or None in (open_price, high, low, close):
            continue
        bars.append(
            {
                "dt": dt,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": max(_safe_float(raw.get("volume")) or 0.0, 0.0),
                "amount": max(_safe_float(raw.get("amount")) or 0.0, 0.0),
            }
        )
    if not bars:
        return None
    bars.sort(key=lambda row: row["dt"])
    total_volume = sum(row["volume"] for row in bars)
    total_amount = sum(row["amount"] for row in bars)
    average_price = total_amount / total_volume if total_volume > 0 else None
    opening = bars[0]["open"]
    high_bar = max(bars, key=lambda row: row["high"])
    snapshots = {node: _snapshot(bars, node, turnover_rate, total_volume) for node in NODE_TIMES}
    morning = [row for row in bars if row["dt"].time() <= time(11, 30)]
    morning_high = max((row["high"] for row in morning), default=high_bar["high"])
    return {
        "code": path.stem,
        "listing_date": bars[0]["dt"].strftime("%Y-%m-%d"),
        "bars": bars,
        "open": opening,
        "close": bars[-1]["close"],
        "high": high_bar["high"],
        "high_time": high_bar["dt"].strftime("%H:%M"),
        "low": min(row["low"] for row in bars),
        "average_price": average_price,
        "total_volume": total_volume,
        "total_amount": total_amount,
        "turnover_rate": turnover_rate,
        "snapshots": snapshots,
        "morning_high": morning_high,
    }


def _snapshot(bars: list[dict[str, Any]], node: str, turnover_rate: float | None, total_volume: float) -> dict[str, Any] | None:
    hh, mm = (int(part) for part in node.split(":"))
    cutoff = time(hh, mm)
    selected = [row for row in bars if row["dt"].time() <= cutoff]
    if not selected:
        return None
    volume = sum(row["volume"] for row in selected)
    amount = sum(row["amount"] for row in selected)
    vwap = amount / volume if volume > 0 else None
    cumulative_turnover = turnover_rate * volume / total_volume if turnover_rate is not None and total_volume > 0 else None
    running_peak = selected[0]["high"]
    max_drawdown = 0.0
    for row in selected:
        running_peak = max(running_peak, row["high"])
        if running_peak > 0:
            max_drawdown = min(max_drawdown, row["low"] / running_peak - 1)
    peak_bar = max(selected, key=lambda row: row["high"])
    return {
        "node": node,
        "price": selected[-1]["close"],
        "vwap": vwap,
        "cumulative_turnover": cumulative_turnover,
        "max_drawdown_pct": max_drawdown * 100,
        "high": peak_bar["high"],
        "high_time": peak_bar["dt"].strftime("%H:%M"),
        "low": min(row["low"] for row in selected),
    }


def _price_at(intraday: dict[str, Any], node: str) -> float:
    snapshot = (intraday.get("snapshots") or {}).get(node)
    return float(snapshot["price"]) if snapshot else float(intraday["close"])


def _base_intraday_exit(intraday: dict[str, Any]) -> dict[str, Any]:
    opening = float(intraday["open"])
    s935 = intraday["snapshots"].get("09:35")
    s945 = intraday["snapshots"].get("09:45")
    s1000 = intraday["snapshots"].get("10:00")
    s1030 = intraday["snapshots"].get("10:30")
    s1330 = intraday["snapshots"].get("13:30")
    s1400 = intraday["snapshots"].get("14:00")
    s1430 = intraday["snapshots"].get("14:30")

    weak935 = bool(
        s935
        and s935["vwap"] is not None
        and s935["price"] <= opening * 1.01
        and s935["price"] < s935["vwap"]
        and s935["cumulative_turnover"] is not None
        and s935["cumulative_turnover"] < 30
    )
    if weak935:
        return {"node": "09:35", "price": s935["price"], "reason": "09:35破均线、回到开盘附近且换手不足"}

    weak945 = bool(
        s945
        and s945["vwap"] is not None
        and s945["price"] < opening * 1.02
        and s945["price"] < s945["vwap"]
        and s945["high_time"] <= "09:45"
    )
    if weak945:
        return {"node": "09:45", "price": s945["price"], "reason": "09:45仍未收复开盘与累计均价"}

    strong1000 = bool(
        s1000
        and s1000["vwap"] is not None
        and s1000["cumulative_turnover"] is not None
        and s1000["max_drawdown_pct"] >= -6
        and s1000["cumulative_turnover"] >= 45
        and s1000["price"] >= opening * 1.05
        and s1000["price"] >= s1000["vwap"]
    )
    if not strong1000:
        return {"node": "10:00", "price": _price_at(intraday, "10:00"), "reason": "10:00未形成高换手抬价"}

    strong1030 = bool(
        s1030
        and s1030["vwap"] is not None
        and s1030["cumulative_turnover"] is not None
        and s1030["cumulative_turnover"] >= 60
        and s1030["price"] >= opening * 1.08
        and s1030["price"] >= s1030["vwap"]
        and s1030["price"] >= s1000["price"] * 1.08
    )
    if not strong1030:
        return {"node": "10:30", "price": _price_at(intraday, "10:30"), "reason": "10:30拉升后未达到继续观察条件"}

    if s1330 and s1330["vwap"] is not None and (s1330["price"] < s1330["vwap"] or s1330["price"] < s1030["price"] * 0.97):
        return {"node": "13:30", "price": s1330["price"], "reason": "13:30跌破均价或明显弱于10:30"}

    if s1400 and (s1400["high"] <= intraday["morning_high"] * 1.001 or s1400["price"] <= s1030["price"] * 1.01):
        return {"node": "14:00", "price": s1400["price"], "reason": "14:00未有效突破上午高点"}

    if s1430 and s1430["high"] <= intraday["morning_high"] * 1.001:
        return {"node": "14:30", "price": s1430["price"], "reason": "14:30仍未突破上午高点"}
    return {"node": "15:00", "price": intraday["close"], "reason": "强势结构保持至收盘"}


def _open_position(opening: float, low: float | None, high: float | None) -> dict[str, Any]:
    if low is None or high is None:
        return {"state": "unavailable", "ratio": None, "vs_low_pct": None, "vs_high_pct": None}
    lower, upper = sorted((low, high))
    span = upper - lower
    if opening < lower:
        state = "below_low"
    elif opening > upper:
        state = "above_high"
    elif span <= 0:
        state = "inside_mid"
    else:
        ratio = (opening - lower) / span
        if ratio < 0.25:
            state = "near_low"
        elif ratio > 0.75:
            state = "near_high"
        else:
            state = "inside_mid"
    return {
        "state": state,
        "ratio": (opening - lower) / span if span > 0 else None,
        "vs_low_pct": (opening / lower - 1) * 100 if lower > 0 else None,
        "vs_high_pct": (opening / upper - 1) * 100 if upper > 0 else None,
    }


def _previous_feedback(
    previous: dict[str, Any] | None,
    previous_prediction: dict[str, Any] | None,
) -> dict[str, Any]:
    if not previous:
        return {
            "available": False,
            "weak": False,
            "open_to_close_weak": False,
            "expectation_break": False,
            "reasons": [],
        }
    opening = _safe_float(previous.get("open"))
    closing = _safe_float(previous.get("close"))
    average_price = _safe_float(previous.get("average_price"))
    lower = _safe_float((previous_prediction or {}).get("range_low"))
    s945 = (previous.get("snapshots") or {}).get("09:45")
    open_to_close_weak = bool(opening and closing is not None and closing < opening)
    early_structure_weak = bool(
        s945
        and opening
        and s945.get("vwap") is not None
        and s945.get("price") < opening
        and s945.get("price") < s945.get("vwap")
        and s945.get("high_time") <= "09:45"
    )
    interval_break = bool(average_price is not None and lower is not None and average_price < lower)
    reasons = []
    if open_to_close_weak:
        reasons.append("前序样本收盘低于开盘")
    if early_structure_weak:
        reasons.append("前序样本9:45未修复早盘弱势")
    if interval_break:
        reasons.append("前序样本首日均价低于估值下限")
    return {
        "available": True,
        "weak": open_to_close_weak or early_structure_weak or interval_break,
        "open_to_close_weak": open_to_close_weak,
        "expectation_break": early_structure_weak or interval_break,
        "reasons": reasons,
    }


def _dual_state(opening: float, strict: dict[str, Any], rolling: dict[str, Any]) -> str:
    sl = _safe_float(strict.get("range_low"))
    sh = _safe_float(strict.get("range_high"))
    rl = _safe_float(rolling.get("range_low"))
    rh = _safe_float(rolling.get("range_high"))
    if None in (sl, sh, rl, rh):
        return "unavailable"
    if opening < sl and opening < rl:
        return "double_below"
    if opening > sh and opening > rh:
        return "double_above"
    if opening < sl:
        return "strict_below_only"
    if opening < rl:
        return "rolling_below_only"
    strict_pos = _open_position(opening, sl, sh)["state"]
    rolling_pos = _open_position(opening, rl, rh)["state"]
    if "near_low" in (strict_pos, rolling_pos):
        return "near_low"
    if "near_high" in (strict_pos, rolling_pos):
        return "near_high"
    return "inside_or_crossed"


def _range_reliability(
    strict: dict[str, Any],
    rolling: dict[str, Any],
    previous_feedback: dict[str, Any],
) -> dict[str, Any]:
    strict_source = str(strict.get("source") or "")
    rolling_source = str(rolling.get("source") or "")
    strict_fallback = bool(strict.get("fallback_used") or "fallback" in strict_source)
    rolling_fallback = bool(rolling.get("fallback_used") or "fallback" in rolling_source)
    if strict_fallback and rolling_fallback and previous_feedback.get("expectation_break"):
        return {
            "level": "low_regime_break",
            "reason": "双线均来自情绪兜底，且前序样本已跌破预期；只把区间用于预期破裂提示",
        }
    if strict_fallback and rolling_fallback:
        return {"level": "low_fallback_only", "reason": "双线均来自情绪兜底，缺少可用模型中枢"}
    if strict_fallback or rolling_fallback:
        return {"level": "medium_mixed", "reason": "仅一条估值线来自可用模型中枢"}
    return {"level": "normal", "reason": "两条估值线均有本地模型中枢"}


def _weighted_exit(legs: list[dict[str, Any]]) -> tuple[float, str, str]:
    total_weight = sum(float(leg["weight"]) for leg in legs)
    if total_weight <= 0:
        raise ValueError("exit legs have no weight")
    price = sum(float(leg["price"]) * float(leg["weight"]) for leg in legs) / total_weight
    nodes = "+".join(f"{leg['node']}({float(leg['weight']) * 100:.0f}%)" for leg in legs)
    reasons = "；".join(str(leg.get("reason") or "") for leg in legs if leg.get("reason"))
    return price, nodes, reasons


def _valuation_exit(
    intraday: dict[str, Any],
    strict: dict[str, Any],
    rolling: dict[str, Any],
    mode: str,
    previous_weak: bool,
    escalate_double_below_weight: bool = False,
) -> dict[str, Any]:
    opening = float(intraday["open"])
    base = _base_intraday_exit(intraday)
    strict_pos = _open_position(opening, _safe_float(strict.get("range_low")), _safe_float(strict.get("range_high")))
    rolling_pos = _open_position(opening, _safe_float(rolling.get("range_low")), _safe_float(rolling.get("range_high")))
    dual_state = _dual_state(opening, strict, rolling)

    if mode == "strict":
        position = strict_pos["state"]
        risk = position == "below_low"
        near_low = position == "near_low"
        above = position == "above_high"
    elif mode == "rolling":
        position = rolling_pos["state"]
        risk = position == "below_low"
        near_low = position == "near_low"
        above = position == "above_high"
    elif mode == "dual":
        position = dual_state
        risk = dual_state == "double_below"
        near_low = dual_state in {"strict_below_only", "rolling_below_only", "near_low"}
        above = dual_state == "double_above"
    else:
        raise ValueError(f"unsupported mode: {mode}")

    opening_weight = 0.0
    aggressive = False
    initial_reason = ""
    if risk:
        opening_weight = 0.50 if previous_weak and escalate_double_below_weight else 0.30
        aggressive = True
        initial_reason = "开盘低于风险下限"
        if previous_weak:
            initial_reason += "且前序反馈弱"
        if previous_weak and escalate_double_below_weight:
            initial_reason += "，启用50%极保守开盘仓位"
    elif near_low and previous_weak:
        opening_weight = 0.30
        aggressive = True
        initial_reason = "开盘靠近/低于情绪下限且前序反馈弱"
    elif above:
        opening_weight = 0.30
        initial_reason = "开盘高于估值上限，先兑现部分超预期"

    legs: list[dict[str, Any]] = []
    if opening_weight > 0:
        legs.append({"node": "09:30", "weight": opening_weight, "price": opening, "reason": initial_reason})
    remaining = 1.0 - opening_weight

    if aggressive and remaining > 0:
        s935 = intraday["snapshots"].get("09:35")
        repaired935 = bool(
            s935
            and s935.get("vwap") is not None
            and s935.get("price") > opening
            and s935.get("price") >= s935.get("vwap")
        )
        if not repaired935:
            legs.append(
                {
                    "node": "09:35",
                    "weight": remaining,
                    "price": _price_at(intraday, "09:35"),
                    "reason": "9:35未同时收复开盘与累计均价",
                }
            )
        else:
            s945 = intraday["snapshots"].get("09:45")
            repaired945 = bool(
                s945
                and s945.get("vwap") is not None
                and s945.get("price") >= opening * 1.02
                and s945.get("price") >= s945.get("vwap")
            )
            if not repaired945:
                legs.append(
                    {
                        "node": "09:45",
                        "weight": remaining,
                        "price": _price_at(intraday, "09:45"),
                        "reason": "9:45修复未站稳",
                    }
                )
            else:
                legs.append({"node": base["node"], "weight": remaining, "price": base["price"], "reason": base["reason"]})
    elif remaining > 0:
        legs.append({"node": base["node"], "weight": remaining, "price": base["price"], "reason": base["reason"]})

    price, nodes, reasons = _weighted_exit(legs)
    return {
        "price": price,
        "nodes": nodes,
        "reason": reasons,
        "opening_weight": opening_weight,
        "aggressive": aggressive,
        "position": position,
        "strict_position": strict_pos,
        "rolling_position": rolling_pos,
        "dual_state": dual_state,
    }


_ACTION_NODE_ORDER = {
    "09:30": 0,
    "09:35": 1,
    "09:45": 2,
    "10:00": 3,
    "10:30": 4,
    "13:30": 5,
    "14:00": 6,
    "14:30": 7,
    "15:00": 8,
}


def _latest_action_node(action: dict[str, Any]) -> str:
    nodes = str(action.get("nodes") or "")
    matched = [node for node in _ACTION_NODE_ORDER if node in nodes]
    return max(matched, key=lambda node: _ACTION_NODE_ORDER[node]) if matched else "15:00"


def _dual_reference_levels(strict: dict[str, Any], rolling: dict[str, Any]) -> dict[str, float | None]:
    lows = [value for value in (_safe_float(strict.get("range_low")), _safe_float(rolling.get("range_low"))) if value is not None]
    highs = [value for value in (_safe_float(strict.get("range_high")), _safe_float(rolling.get("range_high"))) if value is not None]
    centers = [
        (low + high) / 2
        for low, high in (
            (_safe_float(strict.get("range_low")), _safe_float(strict.get("range_high"))),
            (_safe_float(rolling.get("range_low")), _safe_float(rolling.get("range_high"))),
        )
        if low is not None and high is not None
    ]
    return {
        "lower_consensus": min(lows) if len(lows) == 2 else (lows[0] if lows else None),
        "center_consensus": max(centers) if len(centers) == 2 else (centers[0] if centers else None),
        "upper_consensus": max(highs) if len(highs) == 2 else (highs[0] if highs else None),
    }


def _runner_exit_after_1000(intraday: dict[str, Any]) -> dict[str, Any]:
    s1000 = intraday["snapshots"].get("10:00")
    s1030 = intraday["snapshots"].get("10:30")
    s1330 = intraday["snapshots"].get("13:30")
    s1400 = intraday["snapshots"].get("14:00")
    s1430 = intraday["snapshots"].get("14:30")
    if s1030 and s1000 and s1030.get("vwap") is not None and (
        s1030["price"] < s1030["vwap"] or s1030["price"] < s1000["price"] * 0.97
    ):
        return {"node": "10:30", "price": s1030["price"], "reason": "保留仓位在10:30跌破均价或弱于10:00"}
    if s1330 and s1030 and s1330.get("vwap") is not None and (
        s1330["price"] < s1330["vwap"] or s1330["price"] < s1030["price"] * 0.97
    ):
        return {"node": "13:30", "price": s1330["price"], "reason": "保留仓位下午转弱"}
    if s1400 and s1030 and (s1400["high"] <= intraday["morning_high"] * 1.001 or s1400["price"] <= s1030["price"] * 1.01):
        return {"node": "14:00", "price": s1400["price"], "reason": "14:00未突破上午高点"}
    if s1430 and s1430["high"] <= intraday["morning_high"] * 1.001:
        return {"node": "14:30", "price": s1430["price"], "reason": "14:30仍未突破上午高点"}
    return {"node": "15:00", "price": intraday["close"], "reason": "保留仓位强势至收盘"}


def _regret_observation_table(
    intraday: dict[str, Any],
    strict: dict[str, Any],
    rolling: dict[str, Any],
) -> list[dict[str, Any]]:
    levels = _dual_reference_levels(strict, rolling)
    lower = _safe_float(levels.get("lower_consensus"))
    center = _safe_float(levels.get("center_consensus"))
    table: list[dict[str, Any]] = []
    for node in NODE_TIMES:
        snapshot = intraday["snapshots"].get(node)
        if not snapshot:
            continue
        below_expectation = lower is not None and snapshot["high"] < lower
        center_reached = center is not None and snapshot["high"] >= center
        weak = bool(
            snapshot.get("vwap") is not None
            and snapshot["price"] < snapshot["vwap"]
            and snapshot["high_time"] <= node
            and snapshot["max_drawdown_pct"] <= (-4.0 if node <= "09:45" else -6.0)
        )
        turnover_floor = 25.0 if node == "09:35" else 35.0 if node == "09:45" else 45.0
        strong = bool(
            snapshot.get("vwap") is not None
            and snapshot.get("cumulative_turnover") is not None
            and snapshot["price"] >= snapshot["vwap"]
            and snapshot["cumulative_turnover"] >= turnover_floor
            and snapshot["max_drawdown_pct"] >= -10.0
        )
        if below_expectation and weak:
            bias = "等待后悔风险"
            if node == "09:35":
                action = "降低心理价；弱势未修复则卖出大部，最多留30%观察9:45"
            elif node == "09:45":
                action = "退出剩余仓位，不再等待10点主升"
            else:
                action = "估值未达且量价已弱，退出剩余仓位"
        elif center_reached and strong:
            bias = "早卖后悔风险"
            if node in {"09:35", "09:45"}:
                action = "可以兑现，但不要卖光；保留30%进入下一节点"
            elif node == "10:00":
                action = "保留30%观察10:30强势确认"
            elif node == "10:30":
                action = "保留30%进入下午，转弱再退出"
            else:
                action = "保留仓位按下午突破/转弱规则退出"
        else:
            bias = "中性"
            action = "沿用原量价观察表动作"
        table.append(
            {
                "node": node,
                "price": snapshot.get("price"),
                "vwap": snapshot.get("vwap"),
                "cumulative_turnover": snapshot.get("cumulative_turnover"),
                "max_drawdown_pct": snapshot.get("max_drawdown_pct"),
                "range_lower": lower,
                "range_center": center,
                "below_expectation": below_expectation,
                "center_reached": center_reached,
                "weak": weak,
                "strong": strong,
                "regret_bias": bias,
                "action": action,
            }
        )
    return table


def _regret_aware_exit(
    intraday: dict[str, Any],
    strict: dict[str, Any],
    rolling: dict[str, Any],
    dual_action: dict[str, Any],
) -> dict[str, Any]:
    table = _regret_observation_table(intraday, strict, rolling)
    by_node = {row["node"]: row for row in table}
    latest_dual_node = _latest_action_node(dual_action)
    wait_trigger = next(
        (
            by_node[node]
            for node in ("09:35", "09:45", "10:00")
            if node in by_node and by_node[node]["regret_bias"] == "等待后悔风险"
        ),
        None,
    )
    if wait_trigger and _ACTION_NODE_ORDER[latest_dual_node] > _ACTION_NODE_ORDER[wait_trigger["node"]]:
        guard_node = str(wait_trigger["node"])
        guard_price = _price_at(intraday, guard_node)
        price = guard_price * 0.70 + float(dual_action["price"]) * 0.30
        return {
            "price": price,
            "nodes": f"{guard_node}(70%)+{latest_dual_node}(30%)",
            "reason": f"{guard_node}估值未达且量价转弱，防止高预期继续等待；剩余30%沿用双线动作",
            "trigger": "wait_regret_guard",
            "observation_table": table,
        }

    early_trigger = next(
        (
            by_node[node]
            for node in ("09:35", "09:45", "10:00")
            if node in by_node and by_node[node]["regret_bias"] == "早卖后悔风险"
        ),
        None,
    )
    if early_trigger and _ACTION_NODE_ORDER[latest_dual_node] <= _ACTION_NODE_ORDER["10:00"]:
        runner = _runner_exit_after_1000(intraday)
        price = float(dual_action["price"]) * 0.70 + float(runner["price"]) * 0.30
        return {
            "price": price,
            "nodes": f"{latest_dual_node}(70%)+{runner['node']}(30%)",
            "reason": f"{early_trigger['node']}已达估值中枢且量价仍强，防止过早卖光；保留30%至{runner['node']}",
            "trigger": "early_exit_runner",
            "observation_table": table,
        }

    return {
        "price": float(dual_action["price"]),
        "nodes": str(dual_action.get("nodes") or ""),
        "reason": "未触发两类后悔修正，沿用双线共识动作",
        "trigger": "unchanged",
        "observation_table": table,
    }


def _strategy_summary(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    valid = [row for row in rows if _safe_float((row.get("exits") or {}).get(key)) is not None]
    vs_open = [((row["exits"][key] / row["open"]) - 1) * 100 for row in valid]
    vs_close = [((row["exits"][key] / row["close"]) - 1) * 100 for row in valid]
    opportunity_gap = [((row["high"] - row["exits"][key]) / row["high"]) * 100 for row in valid if row["high"] > 0]
    return {
        "strategy": key,
        "count": len(valid),
        "avg_exit_price_vs_open_pct": _mean(vs_open),
        "median_exit_price_vs_open_pct": _median(vs_open),
        "p10_exit_price_vs_open_pct": _quantile(vs_open, 0.10),
        "worst_exit_price_vs_open_pct": min(vs_open) if vs_open else None,
        "win_rate_vs_open": sum(value > 0 for value in vs_open) / len(vs_open) if vs_open else None,
        "avg_exit_price_vs_close_pct": _mean(vs_close),
        "median_exit_price_vs_close_pct": _median(vs_close),
        "win_rate_vs_close": sum(value > 0 for value in vs_close) / len(vs_close) if vs_close else None,
        "avg_opportunity_gap_pct": _mean(opportunity_gap),
        "median_opportunity_gap_pct": _median(opportunity_gap),
    }


def _group_summary(rows: list[dict[str, Any]], group_key: str, strategy_key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(group_key) or "unknown")].append(row)
    result = []
    for group, members in sorted(grouped.items()):
        summary = _strategy_summary(members, strategy_key)
        summary["group"] = group
        summary["codes"] = [row["code"] for row in members]
        result.append(summary)
    return result


def _period_summaries(rows: list[dict[str, Any]], strategy_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    periods = [
        ("all", rows),
        ("2026-03-plus", [row for row in rows if row["listing_date"] >= "2026-03-01"]),
        ("recent-10", rows[-10:]),
    ]
    return [
        {
            "period": period,
            "sample_count": len(members),
            "strategies": [_strategy_summary(members, key) for key in strategy_keys],
        }
        for period, members in periods
    ]


def _prediction_context(args: argparse.Namespace, intraday_by_code: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    dataset = param_tuning.load_replay_dataset(args.dataset)
    dataset = copy.deepcopy(dataset)
    items = list(dataset.get("items") or [])
    by_code = distill._dataset_by_code(dataset)
    target_codes = []
    for code, intraday in intraday_by_code.items():
        item = by_code.get(code)
        if not item:
            continue
        average_price = _safe_float(intraday.get("average_price"))
        issue_price = _safe_float(item.get("ISSUE_PRICE"))
        item["LISTING_DATE"] = intraday["listing_date"]
        item["AVERAGE_PRICE"] = average_price
        item["CLOSE_PRICE"] = _safe_float(intraday.get("close"))
        item["LD_AVERAGE_CHANGE"] = (
            (average_price / issue_price - 1) * 100 if average_price is not None and issue_price else None
        )
        target_codes.append(code)
    target_codes.sort(key=lambda code: (intraday_by_code[code]["listing_date"], code))

    params = config_loader.load_params(args.params)
    scan_report = _read_json(Path(args.scan_report))
    best_overrides = dict(((scan_report.get("top_candidates") or [{}])[0]).get("overrides") or {})
    current_metrics = param_tuning.evaluate_replay_targets(dataset, params, target_codes=target_codes)
    best_params = dict(params)
    best_params.update(best_overrides)
    scan_best_metrics = param_tuning.evaluate_replay_targets(dataset, best_params, target_codes=target_codes)
    model_predictions = {
        "current_params": blend._index_model_predictions(current_metrics, "current_params"),
        "scan_best": blend._index_model_predictions(scan_best_metrics, "scan_best"),
    }
    teacher_rows = distill._build_teacher_rows(target_codes, items, by_code, model_predictions, {}, params)
    proxy._attach_regime_break_context(teacher_rows, params)
    proxy._attach_proxy_ranks(teacher_rows)
    strict_result = proxy._evaluate_strategy(STRICT_STRATEGY, teacher_rows, params)
    rolling_result = proxy._evaluate_strategy(ROLLING_STRATEGY, teacher_rows, params)
    strict_by_code = {row["code"]: row for row in strict_result["rows"]}
    rolling_by_code = {row["code"]: row for row in rolling_result["rows"]}
    return teacher_rows, strict_by_code, rolling_by_code


def _build_rows(
    intraday_by_code: dict[str, dict[str, Any]],
    strict_by_code: dict[str, dict[str, Any]],
    rolling_by_code: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered = sorted(intraday_by_code.values(), key=lambda row: (row["listing_date"], row["code"]))
    rows: list[dict[str, Any]] = []
    for intraday in ordered:
        code = intraday["code"]
        strict = strict_by_code.get(code)
        rolling = rolling_by_code.get(code)
        if not strict or not rolling or not strict.get("available") or not rolling.get("available"):
            continue
        prior_rows = [row for row in rows if row["listing_date"] < intraday["listing_date"]]
        previous_row = prior_rows[-1] if prior_rows else None
        previous_intraday = intraday_by_code.get(previous_row["code"]) if previous_row else None
        previous_strict = strict_by_code.get(previous_row["code"]) if previous_row else None
        previous_rolling = rolling_by_code.get(previous_row["code"]) if previous_row else None
        feedback_strict = _previous_feedback(previous_intraday, previous_strict)
        feedback_rolling = _previous_feedback(previous_intraday, previous_rolling)
        feedback_dual = {
            "available": feedback_strict["available"] or feedback_rolling["available"],
            "weak": feedback_strict["weak"] or feedback_rolling["weak"],
            "open_to_close_weak": feedback_strict["open_to_close_weak"] or feedback_rolling["open_to_close_weak"],
            "expectation_break": feedback_strict["expectation_break"] or feedback_rolling["expectation_break"],
            "reasons": list(dict.fromkeys(feedback_strict["reasons"] + feedback_rolling["reasons"])),
        }
        range_reliability = _range_reliability(strict, rolling, feedback_dual)
        base = _base_intraday_exit(intraday)
        strict_exit = _valuation_exit(intraday, strict, rolling, "strict", feedback_strict["weak"])
        rolling_exit = _valuation_exit(intraday, strict, rolling, "rolling", feedback_rolling["weak"])
        dual_exit = _valuation_exit(intraday, strict, rolling, "dual", feedback_dual["weak"])
        dual_no_previous = _valuation_exit(intraday, strict, rolling, "dual", False)
        dual_previous_weight50 = _valuation_exit(
            intraday,
            strict,
            rolling,
            "dual",
            feedback_dual["weak"],
            escalate_double_below_weight=True,
        )
        regret_action = _regret_aware_exit(intraday, strict, rolling, dual_exit)
        exits = {
            "open": intraday["open"],
            "09:35": _price_at(intraday, "09:35"),
            "09:45": _price_at(intraday, "09:45"),
            "10:00": _price_at(intraday, "10:00"),
            "close": intraday["close"],
            "intraday_only": base["price"],
            "strict_guidance": strict_exit["price"],
            "rolling_guidance": rolling_exit["price"],
            "dual_guidance": dual_exit["price"],
            "dual_without_previous": dual_no_previous["price"],
            "dual_previous_weight50": dual_previous_weight50["price"],
            "regret_guidance": regret_action["price"],
        }
        rows.append(
            {
                "code": code,
                "name": strict.get("name") or rolling.get("name"),
                "listing_date": intraday["listing_date"],
                "previous_code": previous_row["code"] if previous_row else "",
                "previous_feedback": feedback_dual,
                "open": intraday["open"],
                "close": intraday["close"],
                "high": intraday["high"],
                "high_time": intraday["high_time"],
                "average_price": intraday["average_price"],
                "strict_range_low": strict.get("range_low"),
                "strict_range_high": strict.get("range_high"),
                "rolling_range_low": rolling.get("range_low"),
                "rolling_range_high": rolling.get("range_high"),
                "strict_source": strict.get("source"),
                "rolling_source": rolling.get("source"),
                "range_reliability": range_reliability,
                "strict_interval_hit": strict.get("interval_hit"),
                "rolling_interval_hit": rolling.get("interval_hit"),
                "dual_state": dual_exit["dual_state"],
                "strict_position": strict_exit["strict_position"],
                "rolling_position": rolling_exit["rolling_position"],
                "base_exit": base,
                "strict_action": strict_exit,
                "rolling_action": rolling_exit,
                "dual_action": dual_exit,
                "dual_no_previous_action": dual_no_previous,
                "dual_previous_weight50_action": dual_previous_weight50,
                "regret_action": regret_action,
                "regret_observation_table": regret_action["observation_table"],
                "exits": exits,
                "snapshots": intraday["snapshots"],
            }
        )
    return rows


def _trigger_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    triggered = [
        row
        for row in rows
        if row["previous_feedback"]["weak"]
        and row["dual_state"] in {"double_below", "strict_below_only", "rolling_below_only", "near_low"}
    ]
    increments = [
        (row["exits"]["dual_guidance"] / row["exits"]["dual_without_previous"] - 1) * 100
        for row in triggered
        if row["exits"]["dual_without_previous"] > 0
    ]
    vs_open = [(row["exits"]["dual_guidance"] / row["open"] - 1) * 100 for row in triggered]
    return {
        "count": len(triggered),
        "codes": [row["code"] for row in triggered],
        "avg_increment_vs_no_previous_pct": _mean(increments),
        "median_increment_vs_no_previous_pct": _median(increments),
        "positive_increment_rate": sum(value > 0 for value in increments) / len(increments) if increments else None,
        "avg_exit_vs_open_pct": _mean(vs_open),
        "median_exit_vs_open_pct": _median(vs_open),
        "observation_only": len(triggered) < 5,
    }


def _regret_trigger_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    triggered = [row for row in rows if row["regret_action"]["trigger"] != "unchanged"]
    wait_rows = [row for row in triggered if row["regret_action"]["trigger"] == "wait_regret_guard"]
    early_rows = [row for row in triggered if row["regret_action"]["trigger"] == "early_exit_runner"]
    increments = [
        (row["exits"]["regret_guidance"] / row["exits"]["dual_guidance"] - 1) * 100
        for row in triggered
        if row["exits"]["dual_guidance"] > 0
    ]
    opportunity_changes = [
        ((row["high"] - row["exits"]["regret_guidance"]) - (row["high"] - row["exits"]["dual_guidance"])) / row["high"] * 100
        for row in triggered
        if row["high"] > 0
    ]
    return {
        "count": len(triggered),
        "codes": [row["code"] for row in triggered],
        "wait_guard_count": len(wait_rows),
        "wait_guard_codes": [row["code"] for row in wait_rows],
        "early_runner_count": len(early_rows),
        "early_runner_codes": [row["code"] for row in early_rows],
        "avg_exit_increment_vs_dual_pct": _mean(increments),
        "median_exit_increment_vs_dual_pct": _median(increments),
        "positive_increment_rate": sum(value > 0 for value in increments) / len(increments) if increments else None,
        "avg_opportunity_gap_change_pct": _mean(opportunity_changes),
        "observation_only": len(triggered) < 5,
    }


def _double_below_previous_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [row for row in rows if row["previous_feedback"]["weak"] and row["dual_state"] == "double_below"]
    weight_increments = [
        (row["exits"]["dual_previous_weight50"] / row["exits"]["dual_guidance"] - 1) * 100
        for row in selected
        if row["exits"]["dual_guidance"] > 0
    ]
    default_vs_open = [(row["exits"]["dual_guidance"] / row["open"] - 1) * 100 for row in selected]
    weight50_vs_open = [(row["exits"]["dual_previous_weight50"] / row["open"] - 1) * 100 for row in selected]
    return {
        "count": len(selected),
        "codes": [row["code"] for row in selected],
        "avg_weight50_increment_vs_weight30_pct": _mean(weight_increments),
        "median_weight50_increment_vs_weight30_pct": _median(weight_increments),
        "weight50_positive_rate": sum(value > 0 for value in weight_increments) / len(weight_increments) if weight_increments else None,
        "default_weight30_avg_vs_open_pct": _mean(default_vs_open),
        "default_weight30_median_vs_open_pct": _median(default_vs_open),
        "weight50_avg_vs_open_pct": _mean(weight50_vs_open),
        "weight50_median_vs_open_pct": _median(weight50_vs_open),
        "observation_only": len(selected) < 5,
    }


def _build_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 估值区间与首日盘中卖点联动回测",
        "",
        f"> 生成时间：{payload['generated_at']}",
        f"> 有效样本：{payload['summary']['sample_count']} 只",
        "",
        "## 策略对比",
        "",
        "| 策略 | 样本 | 相对开盘均值 | 相对开盘中位 | P10 | 最差 | 胜过开盘 | 胜过收盘 | 距全天高点 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    labels = {
        "open": "开盘全卖",
        "09:35": "9:35全卖",
        "09:45": "9:45全卖",
        "10:00": "10:00全卖",
        "close": "收盘全卖",
        "intraday_only": "原盘中量价状态机",
        "strict_guidance": "保守区间联动",
        "rolling_guidance": "滚动区间联动",
        "dual_guidance": "双线共识+前序反馈",
        "dual_without_previous": "双线共识（无前序反馈）",
        "dual_previous_weight50": "双线共识（前序弱时开盘50%）",
        "regret_guidance": "原观察表+两类后悔修正",
    }
    for item in payload["strategy_summaries"]:
        lines.append(
            "| {label} | {count} | {avg} | {med} | {p10} | {worst} | {win_open} | {win_close} | {gap} |".format(
                label=labels.get(item["strategy"], item["strategy"]),
                count=item["count"],
                avg=_fmt_pct(item["avg_exit_price_vs_open_pct"]),
                med=_fmt_pct(item["median_exit_price_vs_open_pct"]),
                p10=_fmt_pct(item["p10_exit_price_vs_open_pct"]),
                worst=_fmt_pct(item["worst_exit_price_vs_open_pct"]),
                win_open=_fmt_pct((item["win_rate_vs_open"] or 0) * 100),
                win_close=_fmt_pct((item["win_rate_vs_close"] or 0) * 100),
                gap=_fmt_pct(item["avg_opportunity_gap_pct"]),
            )
        )

    trigger = payload["previous_feedback_trigger"]
    double_below = payload["double_below_previous_test"]
    regret_trigger = payload["regret_trigger"]
    lines.extend(
        [
            "",
            "## 两类后悔接入原观察表",
            "",
            f"- 触发样本：{regret_trigger['count']} 只；等待后悔防护 {regret_trigger['wait_guard_count']} 只（{'、'.join(regret_trigger['wait_guard_codes']) or '无'}）；早卖后悔保留仓 {regret_trigger['early_runner_count']} 只（{'、'.join(regret_trigger['early_runner_codes']) or '无'}）。",
            f"- 相对原双线动作，触发样本卖出价平均变化 {_fmt_pct(regret_trigger['avg_exit_increment_vs_dual_pct'])}，中位 {_fmt_pct(regret_trigger['median_exit_increment_vs_dual_pct'])}，改善比例 {_fmt_pct((regret_trigger['positive_increment_rate'] or 0) * 100)}。",
            f"- 全天高点机会差变化 {_fmt_pct(regret_trigger['avg_opportunity_gap_change_pct'])}；负数表示更接近全天高点。",
            f"- 结论标签：{'样本少于5只，只作观察' if regret_trigger['observation_only'] else '达到第一版观察样本数，仍需样本外跟踪'}。",
            "",
            "## 前序弱反馈检验",
            "",
            f"- 触发样本：{trigger['count']} 只（{'、'.join(trigger['codes']) or '无'}）。",
            f"- 相对不使用前序反馈，卖出价平均变化：{_fmt_pct(trigger['avg_increment_vs_no_previous_pct'])}；中位变化：{_fmt_pct(trigger['median_increment_vs_no_previous_pct'])}。",
            f"- 触发后卖出价相对开盘均值：{_fmt_pct(trigger['avg_exit_vs_open_pct'])}；中位：{_fmt_pct(trigger['median_exit_vs_open_pct'])}。",
            f"- 状态：{'样本少于5只，只作观察' if trigger['observation_only'] else '达到第一版最小观察样本数'}。",
            "",
            "### 前序弱 + 双线低于下限",
            "",
            f"- 样本：{double_below['count']} 只（{'、'.join(double_below['codes']) or '无'}）。",
            f"- 默认开盘卖 30% 后观察至 9:35：相对开盘均值 {_fmt_pct(double_below['default_weight30_avg_vs_open_pct'])}，中位 {_fmt_pct(double_below['default_weight30_median_vs_open_pct'])}。",
            f"- 把开盘比例提高到 50% 后，相对 30% 方案平均变化 {_fmt_pct(double_below['avg_weight50_increment_vs_weight30_pct'])}，中位 {_fmt_pct(double_below['median_weight50_increment_vs_weight30_pct'])}，改善比例 {_fmt_pct((double_below['weight50_positive_rate'] or 0) * 100)}。",
            f"- 结论标签：{'样本少于5只，只作观察' if double_below['observation_only'] else '可作为第一版方向证据，但仍需样本外跟踪'}。",
            "",
            "## 时间切片",
            "",
            "| 区间 | 样本 | 策略 | 相对开盘均值 | 中位 | P10 | 最差 |",
            "|---|---:|---|---:|---:|---:|---:|",
        ]
    )
    period_labels = {
        "intraday_only": "原量价",
        "strict_guidance": "保守联动",
        "rolling_guidance": "滚动联动",
        "dual_guidance": "双线联动",
        "regret_guidance": "后悔修正",
    }
    for period in payload["period_summaries"]:
        for item in period["strategies"]:
            lines.append(
                "| {period} | {count} | {strategy} | {avg} | {med} | {p10} | {worst} |".format(
                    period=period["period"],
                    count=period["sample_count"],
                    strategy=period_labels.get(item["strategy"], item["strategy"]),
                    avg=_fmt_pct(item["avg_exit_price_vs_open_pct"]),
                    med=_fmt_pct(item["median_exit_price_vs_open_pct"]),
                    p10=_fmt_pct(item["p10_exit_price_vs_open_pct"]),
                    worst=_fmt_pct(item["worst_exit_price_vs_open_pct"]),
                )
            )
    lines.extend(
        [
            "",
            "## 双线开盘状态",
            "",
            "| 状态 | 样本 | 相对开盘均值 | 相对开盘中位 | P10 | 最差 | 代码 |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in payload["dual_state_summary"]:
        lines.append(
            "| {group} | {count} | {avg} | {med} | {p10} | {worst} | {codes} |".format(
                group=item["group"],
                count=item["count"],
                avg=_fmt_pct(item["avg_exit_price_vs_open_pct"]),
                med=_fmt_pct(item["median_exit_price_vs_open_pct"]),
                p10=_fmt_pct(item["p10_exit_price_vs_open_pct"]),
                worst=_fmt_pct(item["worst_exit_price_vs_open_pct"]),
                codes="、".join(item["codes"]),
            )
        )

    latest = payload.get("latest_case")
    if latest:
        lines.extend(
            [
                "",
                f"## 最新案例：{latest['code']} {latest.get('name') or ''}",
                "",
                f"- 上一只样本：{latest.get('previous_code') or '无'}；前序反馈：{'弱' if latest['previous_feedback']['weak'] else '未转弱'}（{'、'.join(latest['previous_feedback']['reasons']) or '无触发项'}）。",
                f"- 开盘价：{_fmt_num(latest['open'])}；保守区间：{_fmt_num(latest['strict_range_low'])}-{_fmt_num(latest['strict_range_high'])}；滚动区间：{_fmt_num(latest['rolling_range_low'])}-{_fmt_num(latest['rolling_range_high'])}。",
                f"- 区间可靠性：{latest['range_reliability']['level']}（{latest['range_reliability']['reason']}）。",
                f"- 双线状态：{latest['dual_state']}；动作：{latest['dual_action']['nodes']}；回放卖出均价：{_fmt_num(latest['exits']['dual_guidance'])}。",
                f"- 极保守 50% 分支：{latest['dual_previous_weight50_action']['nodes']}；回放卖出均价：{_fmt_num(latest['exits']['dual_previous_weight50'])}。",
                f"- 开盘/9:35/9:45/收盘：{_fmt_num(latest['open'])}/{_fmt_num(latest['exits']['09:35'])}/{_fmt_num(latest['exits']['09:45'])}/{_fmt_num(latest['close'])}。",
                f"- 动作原因：{latest['dual_action']['reason']}。",
                f"- 两类后悔修正：{latest['regret_action']['nodes']}；回放卖出均价：{_fmt_num(latest['exits']['regret_guidance'])}；原因：{latest['regret_action']['reason']}。",
                "",
                "### 原观察表接入结果",
                "",
                "| 节点 | 价格 | VWAP | 累计换手 | 最大回撤 | 后悔偏向 | 行动 |",
                "|---|---:|---:|---:|---:|---|---|",
            ]
        )
        for item in latest.get("regret_observation_table") or []:
            lines.append(
                "| {node} | {price} | {vwap} | {turnover} | {drawdown} | {bias} | {action} |".format(
                    node=item["node"],
                    price=_fmt_num(item.get("price")),
                    vwap=_fmt_num(item.get("vwap")),
                    turnover=_fmt_pct(item.get("cumulative_turnover")),
                    drawdown=_fmt_pct(item.get("max_drawdown_pct")),
                    bias=item.get("regret_bias"),
                    action=item.get("action"),
                )
            )

    lines.extend(
        [
            "",
            "## 逐样本动作",
            "",
            "| 代码 | 日期 | 前序 | 双线状态 | 可靠性 | 开盘 | 双线卖价 | 后悔修正卖价 | 修正动作 | 相对开盘 |",
            "|---|---|---|---|---|---:|---:|---:|---|---:|",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {code} | {date} | {previous} | {state} | {reliability} | {open} | {dual_exit} | {regret_exit} | {nodes} | {delta} |".format(
                code=row["code"],
                date=row["listing_date"],
                previous=row.get("previous_code") or "",
                state=row["dual_state"],
                reliability=row["range_reliability"]["level"],
                open=_fmt_num(row["open"]),
                dual_exit=_fmt_num(row["exits"]["dual_guidance"]),
                regret_exit=_fmt_num(row["exits"]["regret_guidance"]),
                nodes=row["regret_action"]["nodes"],
                delta=_fmt_pct((row["exits"]["regret_guidance"] / row["open"] - 1) * 100),
            )
        )
    lines.extend(
        [
            "",
            "## 口径",
            "",
            "- 两条估值线只使用本地 replay、参数、扫描结果和目标上市日前已完成的本地样本；未接入雪球目标价。",
            "- 前序反馈只连接更早交易日已经完成首日交易的样本，同日样本不互相引用。",
            "- 当前结果是小样本规则回放，不是可直接自动下单的实盘策略；分组少于5只只作观察。",
            "- 滚动中枢仍是研究项，卖点联动改善不等于中枢估值已通过样本外验证。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset = param_tuning.load_replay_dataset(args.dataset)
    dataset_by_code = distill._dataset_by_code(dataset)
    intraday_by_code: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    for path in sorted(Path(args.intraday_dir).glob("*.csv")):
        item = dataset_by_code.get(path.stem) or {}
        turnover = _safe_float(item.get("TURNOVERRATE"))
        try:
            intraday = _read_intraday(path, turnover)
        except Exception as exc:
            errors.append({"code": path.stem, "error": str(exc)})
            continue
        if intraday:
            intraday_by_code[path.stem] = intraday

    teacher_rows, strict_by_code, rolling_by_code = _prediction_context(args, intraday_by_code)
    rows = _build_rows(intraday_by_code, strict_by_code, rolling_by_code)
    strategy_keys = (
        "open",
        "09:35",
        "09:45",
        "10:00",
        "close",
        "intraday_only",
        "strict_guidance",
        "rolling_guidance",
        "dual_guidance",
        "dual_without_previous",
        "dual_previous_weight50",
        "regret_guidance",
    )
    summaries = [_strategy_summary(rows, key) for key in strategy_keys]
    period_strategy_keys = ("intraday_only", "strict_guidance", "rolling_guidance", "dual_guidance", "regret_guidance")
    latest_case = rows[-1] if rows else None
    payload = {
        "schema": "intraday_valuation_guidance_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {
            "dataset": str(Path(args.dataset)),
            "params": str(Path(args.params)),
            "scan_report": str(Path(args.scan_report)),
            "intraday_dir": str(Path(args.intraday_dir)),
        },
        "strategies": {"strict": STRICT_STRATEGY, "rolling": ROLLING_STRATEGY},
        "summary": {
            "intraday_file_count": len(intraday_by_code),
            "teacher_row_count": len(teacher_rows),
            "sample_count": len(rows),
            "parse_error_count": len(errors),
            "dual_state_distribution": dict(Counter(row["dual_state"] for row in rows)),
        },
        "strategy_summaries": summaries,
        "period_summaries": _period_summaries(rows, period_strategy_keys),
        "previous_feedback_trigger": _trigger_summary(rows),
        "regret_trigger": _regret_trigger_summary(rows),
        "double_below_previous_test": _double_below_previous_summary(rows),
        "dual_state_summary": _group_summary(rows, "dual_state", "dual_guidance"),
        "latest_case": latest_case,
        "parse_errors": errors,
        "rows": rows,
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_dir = Path(args.output_dir)
    json_path = output_dir / f"intraday_valuation_guidance_{timestamp}.json"
    md_path = output_dir / f"intraday_valuation_guidance_{timestamp}.md"
    _write_json(json_path, payload)
    md_path.write_text(_build_markdown(payload), encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "markdown": str(md_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate pre-listing valuation ranges as listing-day sell guidance.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--params", default=str(DEFAULT_PARAMS))
    parser.add_argument("--scan-report", default=str(DEFAULT_SCAN_REPORT))
    parser.add_argument("--intraday-dir", default=str(DEFAULT_INTRADAY_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> None:
    payload = run(build_parser().parse_args())
    print(
        json.dumps(
            {
                "outputs": payload["outputs"],
                "summary": payload["summary"],
                "previous_feedback_trigger": payload["previous_feedback_trigger"],
                "latest_case": {
                    "code": (payload.get("latest_case") or {}).get("code"),
                    "dual_state": (payload.get("latest_case") or {}).get("dual_state"),
                    "dual_action": (payload.get("latest_case") or {}).get("dual_action"),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
