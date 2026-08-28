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
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"
DEFAULT_CUTOFF = "2026-07-14"
EXTERNAL_CODES = {"920176", "920059"}


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
    avg = mean(clean) if clean else None
    std = None
    if len(clean) >= 2 and avg is not None:
        std = math.sqrt(sum((value - avg) ** 2 for value in clean) / (len(clean) - 1))
    return {
        "count": len(clean),
        "mean": avg,
        "median": median(clean) if clean else None,
        "p25": _quantile(clean, 0.25),
        "p75": _quantile(clean, 0.75),
        "min": min(clean) if clean else None,
        "max": max(clean) if clean else None,
        "std": std,
        "cv": (std / avg) if std is not None and avg not in (None, 0) else None,
    }


def _fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
    number = _safe_float(value)
    return "—" if number is None else f"{number:.{digits}f}{suffix}"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_main_rally(bars: list[dict[str, Any]]) -> tuple[int, int] | None:
    """Find the largest low-to-later-high percentage leg with at least two bars."""
    if len(bars) < 3:
        return None
    best: tuple[float, int, int] | None = None
    min_index = 0
    min_price = float(bars[0]["low"])
    for peak_index in range(2, len(bars)):
        candidate_start = peak_index - 2
        candidate_low = float(bars[candidate_start]["low"])
        if candidate_low < min_price:
            min_price = candidate_low
            min_index = candidate_start
        peak = float(bars[peak_index]["high"])
        gain = peak / min_price - 1.0 if min_price > 0 else -1.0
        if best is None or gain > best[0]:
            best = (gain, min_index, peak_index)
    return (best[1], best[2]) if best else None


def _signed_flow_proxies(bars: list[dict[str, Any]], start_index: int, peak_index: int) -> dict[str, float]:
    tick_signed = 0.0
    clv_signed = 0.0
    positive_bar_amount = 0.0
    previous_close = float(bars[start_index - 1]["close"]) if start_index > 0 else float(bars[start_index]["open"])
    for bar in bars[start_index : peak_index + 1]:
        amount = float(bar.get("amount") or 0.0)
        close = float(bar["close"])
        high = float(bar["high"])
        low = float(bar["low"])
        direction = 1.0 if close > previous_close else -1.0 if close < previous_close else 0.0
        tick_signed += direction * amount
        if direction > 0:
            positive_bar_amount += amount
        if high > low:
            clv_multiplier = (2.0 * close - high - low) / (high - low)
            clv_signed += clv_multiplier * amount
        previous_close = close
    return {
        "tick_signed_yi": tick_signed / 100000000.0,
        "clv_signed_yi": clv_signed / 100000000.0,
        "positive_bar_amount_yi": positive_bar_amount / 100000000.0,
    }


def _drawdown_confirmation(bars: list[dict[str, Any]], peak_index: int, peak_price: float, threshold: float = 0.10) -> dict[str, Any]:
    amount = 0.0
    for bar in bars[peak_index + 1 :]:
        amount += float(bar.get("amount") or 0.0)
        if float(bar["close"]) <= peak_price * (1.0 - threshold):
            return {
                "time": bar["dt"].strftime("%H:%M"),
                "delay_minutes": max((bar["dt"] - bars[peak_index]["dt"]).total_seconds() / 60.0, 0.0),
                "amount_after_peak_yi": amount / 100000000.0,
            }
    return {"time": "", "delay_minutes": None, "amount_after_peak_yi": amount / 100000000.0}


def _final_markup_leg(bars: list[dict[str, Any]], peak_index: int, peak_price: float, markup: float = 0.20) -> dict[str, Any]:
    """Locate the last crossing that completes the final markup into the peak."""
    threshold_price = peak_price / (1.0 + markup)
    start_index = 0
    for index in range(peak_index - 1, -1, -1):
        if float(bars[index]["low"]) <= threshold_price:
            start_index = index
            break
    leg_bars = bars[start_index : peak_index + 1]
    amount_yi = sum(float(bar.get("amount") or 0.0) for bar in leg_bars) / 100000000.0
    signed = _signed_flow_proxies(bars, start_index, peak_index)
    return {
        "start_time": bars[start_index]["dt"].strftime("%H:%M"),
        "duration_minutes": max((bars[peak_index]["dt"] - bars[start_index]["dt"]).total_seconds() / 60.0, 0.0),
        "gross_amount_yi": amount_yi,
        "tick_signed_yi": signed["tick_signed_yi"],
        "clv_signed_yi": signed["clv_signed_yi"],
    }


def _first_cross_amount_time(bars: list[dict[str, Any]], threshold_yi: float) -> str:
    cumulative = 0.0
    for bar in bars:
        cumulative += float(bar.get("amount") or 0.0) / 100000000.0
        if cumulative >= threshold_yi:
            return bar["dt"].strftime("%H:%M")
    return ""


def _build_row(path: Path, item: dict[str, Any]) -> dict[str, Any] | None:
    turnover_rate = _safe_float(item.get("TURNOVERRATE"))
    intraday = guidance._read_intraday(path, turnover_rate)
    if intraday is None:
        return None
    float_shares_wan = _safe_float(item.get("float_shares"))
    if turnover_rate is None and float_shares_wan:
        turnover_rate = intraday["total_volume"] / (float_shares_wan * 10000.0) * 100.0
    bars = intraday["bars"]
    pair = _find_main_rally(bars)
    if pair is None:
        return None
    start_index, peak_index = pair
    start_bar = bars[start_index]
    peak_bar = bars[peak_index]
    start_price = float(start_bar["low"])
    peak_price = float(peak_bar["high"])
    rally_bars = bars[start_index : peak_index + 1]
    gross_amount_yi = sum(float(bar.get("amount") or 0.0) for bar in rally_bars) / 100000000.0
    outside_amounts = [
        float(bar.get("amount") or 0.0)
        for index, bar in enumerate(bars)
        if index != 0 and not (start_index <= index <= peak_index)
    ]
    baseline_per_bar = median(outside_amounts) if outside_amounts else 0.0
    baseline_amount_yi = baseline_per_bar * len(rally_bars) / 100000000.0
    cumulative_amount_at_peak_yi = sum(float(bar.get("amount") or 0.0) for bar in bars[: peak_index + 1]) / 100000000.0
    cumulative_volume_at_peak = sum(float(bar.get("volume") or 0.0) for bar in bars[: peak_index + 1])
    cumulative_turnover_at_peak = (
        turnover_rate * cumulative_volume_at_peak / float(intraday["total_volume"])
        if turnover_rate is not None and intraday["total_volume"]
        else None
    )
    signed = _signed_flow_proxies(bars, start_index, peak_index)
    final_20 = _final_markup_leg(bars, peak_index, peak_price, markup=0.20)
    confirmation = _drawdown_confirmation(bars, peak_index, peak_price)
    full_high = float(intraday["high"])
    full_high_time = str(intraday["high_time"])
    peak_is_full_high = abs(peak_price - full_high) <= max(0.01, full_high * 0.0001)
    start_clock = start_bar["dt"]
    peak_clock = peak_bar["dt"]
    total_amount_yi = float(intraday["total_amount"]) / 100000000.0
    result = {
        "code": path.stem,
        "name": str(item.get("SECURITY_NAME_ABBR") or ""),
        "listing_date": intraday["listing_date"],
        "external_event": path.stem in EXTERNAL_CODES,
        "rally_start_time": start_clock.strftime("%H:%M"),
        "rally_peak_time": peak_clock.strftime("%H:%M"),
        "rally_duration_minutes": max((peak_clock - start_clock).total_seconds() / 60.0, 0.0),
        "rally_start_price": start_price,
        "rally_peak_price": peak_price,
        "rally_gain_pct": _pct(peak_price, start_price),
        "rally_gross_amount_yi": gross_amount_yi,
        "rally_baseline_amount_yi": baseline_amount_yi,
        "rally_excess_amount_yi": max(gross_amount_yi - baseline_amount_yi, 0.0),
        **signed,
        "final_20_start_time": final_20["start_time"],
        "final_20_duration_minutes": final_20["duration_minutes"],
        "final_20_gross_amount_yi": final_20["gross_amount_yi"],
        "final_20_tick_signed_yi": final_20["tick_signed_yi"],
        "final_20_clv_signed_yi": final_20["clv_signed_yi"],
        "cumulative_amount_at_peak_yi": cumulative_amount_at_peak_yi,
        "cumulative_turnover_at_peak_pct": cumulative_turnover_at_peak,
        "total_amount_yi": total_amount_yi,
        "amount_after_peak_yi": max(total_amount_yi - cumulative_amount_at_peak_yi, 0.0),
        "peak_is_full_day_high": peak_is_full_high,
        "full_day_high_time": full_high_time,
        "close_drawdown_from_peak_pct": _pct(float(intraday["close"]), peak_price),
        "drawdown_10_confirm_time": confirmation["time"],
        "drawdown_10_confirm_delay_minutes": confirmation["delay_minutes"],
        "drawdown_10_amount_after_peak_yi": confirmation["amount_after_peak_yi"],
        "cumulative_5yi_cross_time": _first_cross_amount_time(bars, 5.0),
        "peak_cumulative_amount_vs_5yi": cumulative_amount_at_peak_yi - 5.0,
        "large_late_rally": bool(_pct(peak_price, start_price) >= 20.0 and peak_clock.time() >= time(9, 40)),
    }
    result["rally_gain_pct_per_yi"] = result["rally_gain_pct"] / gross_amount_yi if gross_amount_yi > 0 else None
    result["tick_signed_share_of_gross"] = signed["tick_signed_yi"] / gross_amount_yi if gross_amount_yi > 0 else None
    result["clv_signed_share_of_gross"] = signed["clv_signed_yi"] / gross_amount_yi if gross_amount_yi > 0 else None
    return result


def _group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    large = [row for row in rows if row["large_late_rally"]]
    return {
        "count": len(rows),
        "large_late_rally_count": len(large),
        "large_late_rally_codes": [row["code"] for row in large],
        "rally_gain_pct": _stats([row["rally_gain_pct"] for row in large]),
        "rally_gross_amount_yi": _stats([row["rally_gross_amount_yi"] for row in large]),
        "rally_excess_amount_yi": _stats([row["rally_excess_amount_yi"] for row in large]),
        "tick_signed_yi": _stats([row["tick_signed_yi"] for row in large]),
        "clv_signed_yi": _stats([row["clv_signed_yi"] for row in large]),
        "positive_bar_amount_yi": _stats([row["positive_bar_amount_yi"] for row in large]),
        "final_20_gross_amount_yi": _stats([row["final_20_gross_amount_yi"] for row in large]),
        "final_20_tick_signed_yi": _stats([row["final_20_tick_signed_yi"] for row in large]),
        "final_20_clv_signed_yi": _stats([row["final_20_clv_signed_yi"] for row in large]),
        "rally_gain_pct_per_yi": _stats([row["rally_gain_pct_per_yi"] for row in large]),
        "tick_signed_share_of_gross": _stats([row["tick_signed_share_of_gross"] for row in large]),
        "clv_signed_share_of_gross": _stats([row["clv_signed_share_of_gross"] for row in large]),
        "cumulative_amount_at_peak_yi": _stats([row["cumulative_amount_at_peak_yi"] for row in large]),
        "cumulative_turnover_at_peak_pct": _stats([row["cumulative_turnover_at_peak_pct"] for row in large if row.get("cumulative_turnover_at_peak_pct") is not None]),
        "amount_after_peak_yi": _stats([row["amount_after_peak_yi"] for row in large]),
        "peak_cumulative_within_4_to_6yi_count": sum(4.0 <= row["cumulative_amount_at_peak_yi"] <= 6.0 for row in large),
        "peak_before_5yi_cross_count": sum(row["cumulative_amount_at_peak_yi"] < 5.0 for row in large),
        "peak_after_5yi_cross_count": sum(row["cumulative_amount_at_peak_yi"] >= 5.0 for row in large),
        "full_day_amount_below_5yi_count": sum(row["total_amount_yi"] < 5.0 for row in large),
        "drawdown_10_confirmed_within_3m_count": sum(
            row.get("drawdown_10_confirm_delay_minutes") is not None
            and row["drawdown_10_confirm_delay_minutes"] <= 3.0
            for row in large
        ),
    }


def _markdown(payload: dict[str, Any]) -> str:
    normal = payload["summaries"]["recent_normal"]
    external = payload["summaries"]["recent_external"]
    old = payload["summaries"]["old"]
    recent = payload["recent_rows"]
    large_normal = [row for row in recent if not row["external_event"] and row["large_late_rally"]]
    large_external = [row for row in recent if row["external_event"] and row["large_late_rally"]]
    lines = [
        "# 大幅拉升—力竭段的资金代理分析",
        "",
        f"> 生成日期：{payload['generated_at'][:10]}",
        "> 目的：检验“操盘资金约 5 亿元、资金耗尽时主拉力竭”的假说。",
        "> 重要限制：分钟 OHLCV 没有账户身份和真实主动买卖方向，只能给出总成交上界与弱方向代理，不能识别操盘手真实资金余额。",
        "",
        "## 识别规则",
        "",
        "- 主拉段：寻找当日分钟线中涨幅最大的“某分钟低点→之后分钟高点”，至少跨越两根分钟线。",
        "- 大幅晚拉：主拉涨幅不低于 20%，且峰值时间不早于 9:40。",
        "- 力竭点：主拉峰值；首次从峰值回撤 10% 仅用于事后确认。",
        "- 总成交上界：主拉段全部成交额；操盘资金的净买入不可能可靠地由该数直接推出。",
        "- 弱方向代理一：按分钟收盘相对前一分钟涨跌给成交额加正负号；弱方向代理二：按收盘在分钟高低区间的位置计算 CLV 资金流。",
        "- 超额成交代理：主拉段成交额减去同一股票非主拉分钟的中位成交额基线。",
        "",
        "## 结论",
        "",
        f"新增常态样本中识别出 {normal['large_late_rally_count']} 次大幅晚拉（{'、'.join(normal['large_late_rally_codes']) or '无'}）。峰值时累计成交额中位数为 {_fmt(normal['cumulative_amount_at_peak_yi']['median'])} 亿元，落在 4—6 亿元的有 {normal['peak_cumulative_within_4_to_6yi_count']}/{normal['large_late_rally_count']}。",
        f"但是主拉段本身的总成交额中位数为 {_fmt(normal['rally_gross_amount_yi']['median'])} 亿元，扣除分钟基线后的超额成交中位数 {_fmt(normal['rally_excess_amount_yi']['median'])} 亿元；最后完成 +20% 标价上冲的成交额中位数只有 {_fmt(normal['final_20_gross_amount_yi']['median'])} 亿元。分钟方向代理的中位数分别为 tick {_fmt(normal['tick_signed_yi']['median'])} 亿元、CLV {_fmt(normal['clv_signed_yi']['median'])} 亿元，只占主拉总成交的约 {_fmt(normal['tick_signed_share_of_gross']['median'] * 100 if normal['tick_signed_share_of_gross']['median'] is not None else None, 0, '%')}—{_fmt(normal['clv_signed_share_of_gross']['median'] * 100 if normal['clv_signed_share_of_gross']['median'] is not None else None, 0, '%')}。",
        f"因此，“累计成交到约 5 亿元附近容易见顶”可以作为待跟踪的盘面阈值，但不能等同于“操盘手花光 5 亿元”。在常态大幅晚拉中，{normal['peak_before_5yi_cross_count']} 次峰值发生在累计成交未到 5 亿元时，{normal['peak_after_5yi_cross_count']} 次发生在达到 5 亿元后。",
        f"外力样本识别出 {external['large_late_rally_count']} 次大幅晚拉，峰值累计成交额中位数 {_fmt(external['cumulative_amount_at_peak_yi']['median'])} 亿元；tick 方向代理占主拉总额中位约 {_fmt(external['tick_signed_share_of_gross']['median'] * 100 if external['tick_signed_share_of_gross']['median'] is not None else None, 0, '%')}。其中维琪科技全天总成交额也低于 5 亿元，却完成极端拉升，说明外部自然买盘提高的是资金方向性和价格冲击效率，而不是简单追加一个更大的成交额预算。",
        f"力竭确认也很快：常态大幅晚拉中有 {normal['drawdown_10_confirmed_within_3m_count']}/{normal['large_late_rally_count']} 在峰值后 3 分钟内回撤 10%。结合最后 +20% 只需约半亿元成交，更像达到换手/标价目标后买盘突然撤退，而不是边际拉升已经越来越费钱。",
        "",
        "## 三层资金估计",
        "",
        "| 层次 | 可计算量 | 含义 | 能否当操盘资金 |",
        "|---|---|---|---|",
        "| 上界 | 主拉段总成交额 | 所有买卖双方成交，包含自主资金和筹码反复换手 | 不能，只是很宽的上界 |",
        "| 中间代理 | 主拉段超额成交、最后 +20% 标价段成交额、上涨分钟成交额 | 尝试扣除自然交易背景，但仍包含跟风盘 | 只能比较样本，不宜报成真实金额 |",
        "| 净主动代理 | tick/CLV 签名成交额 | 粗略估计主动买卖不平衡 | 方向可参考，绝对值误差很大 |",
        "",
        "更稳妥的盘中用法不是判断“还剩多少子弹”，而是监控三个同步信号：累计成交额是否接近历史峰值带、是否出现低成交额快速标价冲刺、创新高后是否迅速回撤。三者同时出现时，更适合解释为标价/换手目标完成后承接撤退，而不是账户预算被精确耗尽。",
        "",
        "## 新增样本的大幅晚拉",
        "",
        "| 分层 | 代码 | 名称 | 主拉 | 起点→峰值 | 主拉总额 | 最后+20%成交 | 超额成交 | tick净主动代理 | CLV代理 | 峰值累计成交 | 峰值累计换手 | 10%回撤确认 | 收盘较峰值 |",
        "|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for row in large_normal + large_external:
        lines.append(
            f"| {'外力' if row['external_event'] else '常态'} | {row['code']} | {row['name']} | {_fmt(row['rally_gain_pct'], 1, '%')} | "
            f"{row['rally_start_time']}→{row['rally_peak_time']} | {_fmt(row['rally_gross_amount_yi'])}亿 | {_fmt(row['final_20_gross_amount_yi'])}亿 | {_fmt(row['rally_excess_amount_yi'])}亿 | "
            f"{_fmt(row['tick_signed_yi'])}亿 | {_fmt(row['clv_signed_yi'])}亿 | {_fmt(row['cumulative_amount_at_peak_yi'])}亿 | "
            f"{_fmt(row['cumulative_turnover_at_peak_pct'], 1, '%')} | {row['drawdown_10_confirm_time'] or '未确认'} | {_fmt(row['close_drawdown_from_peak_pct'], 1, '%')} |"
        )
    lines.extend(
        [
            "",
            "## 历史对照",
            "",
            "| 分组 | 大幅晚拉 | 峰值累计成交中位 | 主拉总额中位 | 最后+20%成交中位 | 超额成交中位 | 峰值累计额CV | 4—6亿见顶 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
            f"| 旧样本 | {old['large_late_rally_count']} | {_fmt(old['cumulative_amount_at_peak_yi']['median'])}亿 | {_fmt(old['rally_gross_amount_yi']['median'])}亿 | {_fmt(old['final_20_gross_amount_yi']['median'])}亿 | {_fmt(old['rally_excess_amount_yi']['median'])}亿 | {_fmt(old['cumulative_amount_at_peak_yi']['cv'])} | {old['peak_cumulative_within_4_to_6yi_count']}/{old['large_late_rally_count']} |",
            f"| 新增常态 | {normal['large_late_rally_count']} | {_fmt(normal['cumulative_amount_at_peak_yi']['median'])}亿 | {_fmt(normal['rally_gross_amount_yi']['median'])}亿 | {_fmt(normal['final_20_gross_amount_yi']['median'])}亿 | {_fmt(normal['rally_excess_amount_yi']['median'])}亿 | {_fmt(normal['cumulative_amount_at_peak_yi']['cv'])} | {normal['peak_cumulative_within_4_to_6yi_count']}/{normal['large_late_rally_count']} |",
            f"| 新增外力 | {external['large_late_rally_count']} | {_fmt(external['cumulative_amount_at_peak_yi']['median'])}亿 | {_fmt(external['rally_gross_amount_yi']['median'])}亿 | {_fmt(external['final_20_gross_amount_yi']['median'])}亿 | {_fmt(external['rally_excess_amount_yi']['median'])}亿 | {_fmt(external['cumulative_amount_at_peak_yi']['cv'])} | {external['peak_cumulative_within_4_to_6yi_count']}/{external['large_late_rally_count']} |",
            "",
            "CV（变异系数）越低才越像固定资金天花板；若峰值累计成交分散很大，则 5 亿元更可能只是近期样本的中位量级而非硬预算。",
            "",
        ]
    )
    return "\n".join(lines)


def _csv_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = (
        "code",
        "name",
        "listing_date",
        "external_event",
        "large_late_rally",
        "rally_start_time",
        "rally_peak_time",
        "rally_duration_minutes",
        "rally_gain_pct",
        "rally_gross_amount_yi",
        "rally_baseline_amount_yi",
        "rally_excess_amount_yi",
        "tick_signed_yi",
        "clv_signed_yi",
        "positive_bar_amount_yi",
        "final_20_start_time",
        "final_20_duration_minutes",
        "final_20_gross_amount_yi",
        "final_20_tick_signed_yi",
        "final_20_clv_signed_yi",
        "cumulative_amount_at_peak_yi",
        "cumulative_turnover_at_peak_pct",
        "total_amount_yi",
        "amount_after_peak_yi",
        "full_day_high_time",
        "drawdown_10_confirm_time",
        "drawdown_10_confirm_delay_minutes",
        "close_drawdown_from_peak_pct",
        "rally_gain_pct_per_yi",
        "tick_signed_share_of_gross",
        "clv_signed_share_of_gross",
        "cumulative_5yi_cross_time",
    )
    return [{field: row.get(field) for field in fields} for row in rows]


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = Path(args.dataset)
    intraday_dir = Path(args.intraday_dir)
    output_dir = Path(args.output_dir)
    cutoff = date.fromisoformat(args.cutoff)
    items = {str(item.get("SECURITY_CODE") or ""): item for item in (_load_json(dataset_path).get("items") or [])}
    rows = []
    for path in sorted(intraday_dir.glob("*.csv")):
        item = items.get(path.stem)
        if not item:
            continue
        row = _build_row(path, item)
        if row:
            rows.append(row)
    rows.sort(key=lambda row: (row["listing_date"], row["code"]))
    old_rows = [row for row in rows if date.fromisoformat(row["listing_date"]) <= cutoff]
    recent_rows = [row for row in rows if date.fromisoformat(row["listing_date"]) > cutoff]
    normal_rows = [row for row in recent_rows if not row["external_event"]]
    external_rows = [row for row in recent_rows if row["external_event"]]
    payload = {
        "schema": "rally_capital_proxy_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cutoff_exclusive": args.cutoff,
        "large_rally_definition": {"gain_pct": 20.0, "peak_not_before": "09:40"},
        "summaries": {
            "old": _group_summary(old_rows),
            "recent_normal": _group_summary(normal_rows),
            "recent_external": _group_summary(external_rows),
        },
        "recent_rows": recent_rows,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    json_path = output_dir / f"rally_capital_proxy_{stamp}.json"
    csv_path = output_dir / f"rally_capital_proxy_{stamp}.csv"
    md_path = output_dir / f"rally_capital_proxy_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    flat_rows = _csv_rows(recent_rows)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat_rows[0]) if flat_rows else [])
        if flat_rows:
            writer.writeheader()
            writer.writerows(flat_rows)
    md_path.write_text(_markdown(payload), encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "csv": str(csv_path), "markdown": str(md_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="用分钟线估计北交所新股主拉—力竭段的资金代理。")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--intraday-dir", default=str(DEFAULT_INTRADAY_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    return parser


def main() -> int:
    payload = run(build_parser().parse_args())
    print(json.dumps({"outputs": payload["outputs"], "summaries": payload["summaries"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
