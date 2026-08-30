from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from datetime import date, time
from pathlib import Path
from statistics import mean, median
from typing import Any

import analyze_path_dependent_sell_regret as regret
import analyze_recent_listing_day_regime as regime
import evaluate_intraday_valuation_guidance as guidance


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_INTRADAY_DIR = ROOT_DIR / "首日分时走势"
DEFAULT_TUSHARE_DIR = ROOT_DIR / "data" / "tushare_db"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"
DEFAULT_CUTOFF = "2026-07-14"
ANALYSIS_NODES = ("09:35", "09:45", "10:00", "10:30", "11:00", "11:30")
NODE_ORDER = {node: index for index, node in enumerate(ANALYSIS_NODES)}
PERIOD_LABELS = {
    "old": "旧样本",
    "recent_normal": "近期普通",
    "recent_external": "近期外力",
}
STATE_LABELS = {
    "double_high": "双高（价>开盘且>VWAP）",
    "mixed": "一高一低",
    "double_low": "双低（价<开盘且<VWAP）",
}
TIER_LABELS = {"low": "低换手", "mid": "中换手", "high": "高换手"}


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
    clean = sorted(float(value) for value in values if _safe_float(value) is not None)
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


def _stats(values: list[float | None]) -> dict[str, Any]:
    clean = [float(value) for value in values if _safe_float(value) is not None]
    return {
        "count": len(clean),
        "mean": mean(clean) if clean else None,
        "median": median(clean) if clean else None,
        "p25": _quantile(clean, 0.25),
        "p75": _quantile(clean, 0.75),
        "min": min(clean) if clean else None,
        "max": max(clean) if clean else None,
    }


def _rank(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[index][1]:
            end += 1
        average_rank = (index + end) / 2.0 + 1.0
        for position in range(index, end + 1):
            ranks[ordered[position][0]] = average_rank
        index = end + 1
    return ranks


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3 or len(left) != len(right):
        return None
    left_mean = mean(left)
    right_mean = mean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_ss = sum((x - left_mean) ** 2 for x in left)
    right_ss = sum((y - right_mean) ** 2 for y in right)
    if left_ss <= 0 or right_ss <= 0:
        return None
    return numerator / math.sqrt(left_ss * right_ss)


def _spearman(rows: list[dict[str, Any]], left_key: str, right_key: str) -> float | None:
    pairs = [
        (float(row[left_key]), float(row[right_key]))
        for row in rows
        if _safe_float(row.get(left_key)) is not None and _safe_float(row.get(right_key)) is not None
    ]
    if len(pairs) < 3:
        return None
    return _pearson(_rank([pair[0] for pair in pairs]), _rank([pair[1] for pair in pairs]))


def _fmt(value: Any, digits: int = 1, suffix: str = "") -> str:
    number = _safe_float(value)
    return "—" if number is None else f"{number:.{digits}f}{suffix}"


def _fmt_corr(value: Any) -> str:
    return _fmt(value, 2)


def _time_value(node: str) -> time:
    return time(*(int(part) for part in node.split(":")))


def _forward_peak_gain(
    bars: list[dict[str, Any]],
    node: str,
    price: float,
    end: str | None = None,
) -> float:
    start_time = _time_value(node)
    end_time = _time_value(end) if end else None
    later = [
        bar
        for bar in bars
        if bar["dt"].time() > start_time and (end_time is None or bar["dt"].time() <= end_time)
    ]
    later_high = max((float(bar["high"]) for bar in later), default=price)
    return max(_pct(later_high, price) or 0.0, 0.0)


def _price_state(price: float, opening: float, vwap: float) -> str:
    if price > opening and price > vwap:
        return "double_high"
    if price < opening and price < vwap:
        return "double_low"
    return "mixed"


def _period(listing_date: str, cutoff: date, code: str) -> str:
    if date.fromisoformat(listing_date) <= cutoff:
        return "old"
    if code in regime.EXTERNAL_EVENT_SAMPLES:
        return "recent_external"
    return "recent_normal"


def _tier(value: float | None, low_cut: float | None, high_cut: float | None) -> str:
    if value is None or low_cut is None or high_cut is None:
        return "unknown"
    if value <= low_cut:
        return "low"
    if value >= high_cut:
        return "high"
    return "mid"


def _build_detail_rows(
    dataset_path: Path,
    intraday_dir: Path,
    tushare_dir: Path,
    cutoff: date,
) -> list[dict[str, Any]]:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    items = {str(item.get("SECURITY_CODE") or ""): item for item in dataset.get("items") or []}
    detail_rows: list[dict[str, Any]] = []
    for path in sorted(intraday_dir.glob("*.csv")):
        item = items.get(path.stem)
        if not item:
            continue
        base = regime._build_row(path, item, tushare_dir)
        if base is None:
            continue
        intraday = guidance._read_intraday(path, _safe_float(base.get("turnover_rate_pct")))
        if intraday is None:
            continue
        snapshots = regret._snapshot_map(intraday)
        flags = regret._rule_flags(intraday, snapshots)
        exit_node, _ = regret._path_exit(flags, snapshots)
        period = _period(intraday["listing_date"], cutoff, path.stem)
        previous_turnover = 0.0
        for node in ANALYSIS_NODES:
            snapshot = snapshots[node]
            price = float(snapshot["price"])
            vwap = float(snapshot["vwap"])
            cumulative_turnover = _safe_float(snapshot.get("cumulative_turnover"))
            incremental_turnover = (
                cumulative_turnover - previous_turnover if cumulative_turnover is not None else None
            )
            if cumulative_turnover is not None:
                previous_turnover = cumulative_turnover
            detail_rows.append(
                {
                    "code": path.stem,
                    "name": base.get("name") or "",
                    "listing_date": intraday["listing_date"],
                    "period": period,
                    "external_event": path.stem in regime.EXTERNAL_EVENT_SAMPLES,
                    "node": node,
                    "price": price,
                    "vwap": vwap,
                    "open": float(intraday["open"]),
                    "price_state": _price_state(price, float(intraday["open"]), vwap),
                    "cumulative_turnover_pct": cumulative_turnover,
                    "incremental_turnover_pct": incremental_turnover,
                    "path_exit_node": exit_node,
                    "entered_node": NODE_ORDER[exit_node] >= NODE_ORDER[node],
                    "held_after_node": NODE_ORDER[exit_node] > NODE_ORDER[node],
                    "local_price_exit": bool(flags.get(node, False)),
                    "future_peak_to_1130_pct": _forward_peak_gain(intraday["bars"], node, price, "11:30"),
                    "future_peak_full_day_pct": _forward_peak_gain(intraday["bars"], node, price),
                    "close_from_node_pct": _pct(float(intraday["close"]), price),
                }
            )
    return detail_rows


def _add_old_thresholds(detail_rows: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    thresholds: dict[str, dict[str, float | None]] = {}
    for node in ANALYSIS_NODES:
        old = [row for row in detail_rows if row["period"] == "old" and row["node"] == node]
        cumulative = [row["cumulative_turnover_pct"] for row in old]
        incremental = [row["incremental_turnover_pct"] for row in old]
        thresholds[node] = {
            "cumulative_p33": _quantile(cumulative, 1 / 3),
            "cumulative_p67": _quantile(cumulative, 2 / 3),
            "incremental_p33": _quantile(incremental, 1 / 3),
            "incremental_p67": _quantile(incremental, 2 / 3),
        }
    for row in detail_rows:
        node_thresholds = thresholds[row["node"]]
        row["cumulative_turnover_tier"] = _tier(
            row["cumulative_turnover_pct"],
            node_thresholds["cumulative_p33"],
            node_thresholds["cumulative_p67"],
        )
        row["incremental_turnover_tier"] = _tier(
            row["incremental_turnover_pct"],
            node_thresholds["incremental_p33"],
            node_thresholds["incremental_p67"],
        )
    return thresholds


def _outcome_key(node: str) -> str:
    return "future_peak_full_day_pct" if node == "11:30" else "future_peak_to_1130_pct"


def _tier_summary(rows: list[dict[str, Any]], tier_key: str, outcome_key: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for tier in ("low", "mid", "high"):
        selected = [row for row in rows if row.get(tier_key) == tier]
        outcomes = [row[outcome_key] for row in selected]
        result[tier] = {
            "count": len(selected),
            "outcome": _stats(outcomes),
            "gain_ge_5_count": sum(float(value) >= 5.0 for value in outcomes),
            "gain_ge_10_count": sum(float(value) >= 10.0 for value in outcomes),
            "codes": [row["code"] for row in selected],
        }
    return result


def _summarize_scope(rows: list[dict[str, Any]], node: str) -> dict[str, Any]:
    outcome_key = _outcome_key(node)
    return {
        "count": len(rows),
        "turnover": _stats([row["cumulative_turnover_pct"] for row in rows]),
        "incremental_turnover": _stats([row["incremental_turnover_pct"] for row in rows]),
        "outcome": _stats([row[outcome_key] for row in rows]),
        "cumulative_spearman": _spearman(rows, "cumulative_turnover_pct", outcome_key),
        "incremental_spearman": _spearman(rows, "incremental_turnover_pct", outcome_key),
        "cumulative_tiers": _tier_summary(rows, "cumulative_turnover_tier", outcome_key),
        "incremental_tiers": _tier_summary(rows, "incremental_turnover_tier", outcome_key),
        "price_states": dict(Counter(row["price_state"] for row in rows)),
    }


def _summaries(detail_rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for period in PERIOD_LABELS:
        result[period] = {}
        for node in ANALYSIS_NODES:
            node_rows = [row for row in detail_rows if row["period"] == period and row["node"] == node]
            result[period][node] = {
                "all": _summarize_scope(node_rows, node),
                "entered": _summarize_scope([row for row in node_rows if row["entered_node"]], node),
                "held_after": _summarize_scope([row for row in node_rows if row["held_after_node"]], node),
            }
    return result


def _state_interactions(detail_rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for period in PERIOD_LABELS:
        result[period] = {}
        for node in ANALYSIS_NODES:
            result[period][node] = {}
            outcome_key = _outcome_key(node)
            base = [row for row in detail_rows if row["period"] == period and row["node"] == node]
            for state in STATE_LABELS:
                state_rows = [row for row in base if row["price_state"] == state]
                result[period][node][state] = {
                    "count": len(state_rows),
                    "outcome": _stats([row[outcome_key] for row in state_rows]),
                    "turnover": _stats([row["cumulative_turnover_pct"] for row in state_rows]),
                    "spearman": _spearman(state_rows, "cumulative_turnover_pct", outcome_key),
                    "tiers": _tier_summary(state_rows, "cumulative_turnover_tier", outcome_key),
                }
    return result


def _tier_cell(summary: dict[str, Any], tier: str) -> str:
    item = summary["cumulative_tiers"][tier]
    if not item["count"]:
        return "0/—"
    return f"{item['count']}/{_fmt(item['outcome']['median'], 1, '%')}"


def _high_contrast(
    rows: list[dict[str, Any]],
    node: str,
    tier_key: str,
) -> dict[str, Any]:
    outcome_key = _outcome_key(node)
    high = [row for row in rows if row.get(tier_key) == "high"]
    lower = [row for row in rows if row.get(tier_key) in {"low", "mid"}]

    def summarize(selected: list[dict[str, Any]]) -> dict[str, Any]:
        outcomes = [float(row[outcome_key]) for row in selected]
        return {
            "count": len(selected),
            "median": median(outcomes) if outcomes else None,
            "gain_ge_10_count": sum(value >= 10.0 for value in outcomes),
        }

    return {"high": summarize(high), "lower": summarize(lower)}


def _contrast_text(contrast: dict[str, Any]) -> str:
    high = contrast["high"]
    lower = contrast["lower"]
    return (
        f"高档 {high['gain_ge_10_count']}/{high['count']} 后续拉升≥10%，中位 {_fmt(high['median'], 1, '%')}；"
        f"非高档 {lower['gain_ge_10_count']}/{lower['count']}，中位 {_fmt(lower['median'], 1, '%')}"
    )


def _render_report(
    detail_rows: list[dict[str, Any]],
    thresholds: dict[str, dict[str, float | None]],
    summaries: dict[str, Any],
    interactions: dict[str, Any],
    cutoff: str,
) -> str:
    stock_counts = Counter(row["period"] for row in detail_rows if row["node"] == "09:35")
    recent_rows = [row for row in detail_rows if row["period"] == "recent_normal"]
    old_rows = [row for row in detail_rows if row["period"] == "old"]

    def after_hold(rows: list[dict[str, Any]], node: str) -> list[dict[str, Any]]:
        return [row for row in rows if row["node"] == node and row["held_after_node"]]

    early_935 = _high_contrast(after_hold(recent_rows, "09:35"), "09:35", "cumulative_turnover_tier")
    early_945 = _high_contrast(after_hold(recent_rows, "09:45"), "09:45", "cumulative_turnover_tier")
    mid_1000 = _high_contrast(after_hold(recent_rows, "10:00"), "10:00", "cumulative_turnover_tier")
    late_1030 = _high_contrast(after_hold(recent_rows, "10:30"), "10:30", "incremental_turnover_tier")
    late_1100 = _high_contrast(after_hold(recent_rows, "11:00"), "11:00", "incremental_turnover_tier")
    late_1100_old = _high_contrast(after_hold(old_rows, "11:00"), "11:00", "incremental_turnover_tier")
    afternoon_1130 = _high_contrast(
        [row for row in recent_rows if row["node"] == "11:30"],
        "11:30",
        "cumulative_turnover_tier",
    )
    lines = [
        "# 首日各节点换手对后续拉升的影响",
        "",
        f"> 样本：旧样本 {stock_counts['old']} 只（截至 {cutoff}），近期普通 {stock_counts['recent_normal']} 只，近期外力 {stock_counts['recent_external']} 只。",
        "> 口径：节点换手为当日累计换手；区间换手为上一个观察节点至当前节点的新增换手。09:35—11:00 的“后续拉升”取节点价到 11:30 前后续最高价，11:30 取到收盘的后续最高价。",
        "> 低/中/高换手均使用旧样本各节点的 33%/67% 分位作固定阈值，再原样应用到近期样本。相关系数为 Spearman；样本很小时只作方向提示。",
        "",
        "## 先说结论",
        "",
        "1. **09:35—09:45，累计换手有用，而且主要是用来确认“还有主升”，不是用来推翻价格卖点。**在近期普通样本中，只看价格规则在节点后仍持有的票：",
        f"   - 09:35 累计换手高档（≥{_fmt(thresholds['09:35']['cumulative_p67'], 1, '%')}）：{_contrast_text(early_935)}。",
        f"   - 09:45 累计换手高档（≥{_fmt(thresholds['09:45']['cumulative_p67'], 1, '%')}）：{_contrast_text(early_945)}。",
        "   - 华大海天在 09:35/09:45 分别为 28.5%/45.9%，都落在高档，因此它虽是一高一低，却应继续等待，而不是因低于 VWAP 提前卖出。",
        f"2. **10:00 是累计换手信号的衰减点。**近期高档组仍较强（{_contrast_text(mid_1000)}），但旧样本不是单调关系，因此只能当确认项，不能单独设卖点。",
        f"3. **10:30 后不再看累计换手绝对值，改看本段新增换手。**10:00—10:30 新增换手高档（≥{_fmt(thresholds['10:30']['incremental_p67'], 1, '%')}）在近期样本中：{_contrast_text(late_1030)}；区分度已有明显下降。",
        f"4. **11:00 的有效信号是“重新放量 + 价格突破”。**10:30—11:00 新增换手高档（≥{_fmt(thresholds['11:00']['incremental_p67'], 1, '%')}）近期只有华大海天 1 只，之后再涨 29.3%；其余 3 只中位仅 {_fmt(late_1100['lower']['median'], 1, '%')}。旧样本高档与非高档的对照为：{_contrast_text(late_1100_old)}。样本仍小，只能作为强确认。",
        f"5. **11:30 累计换手高并不支持留尾仓。**近期普通样本高档组下午后续拉升中位 {_fmt(afternoon_1130['high']['median'], 1, '%')}、0/{afternoon_1130['high']['count']} 达到 10%；非高档也只有中位 {_fmt(afternoon_1130['lower']['median'], 1, '%')}。高换手到这里更像已经完成充分交换，而不是还有增量资金。",
        "6. **双低仍优先于换手。**近期普通样本的双低票，09:35 后续拉升中位仅 2.9%，10:00 仅 1.8%；维琪科技是外力样本中的反例，不应拿来修改普通样本规则。",
        "",
        "可写成一句实盘口径：**早盘看累计换手确认热度，10:30 后看新增换手确认接力；换手只增强或削弱持有信心，不覆盖双低卖出、11:00 无突破卖出和 11:30 普通样本清仓。**",
        "",
        "## 一、旧样本给出的节点换手尺子",
        "",
        "| 节点 | 累计换手低档上限 | 累计换手高档下限 | 本段新增换手低档上限 | 本段新增换手高档下限 |",
        "|---|---:|---:|---:|---:|",
    ]
    for node in ANALYSIS_NODES:
        item = thresholds[node]
        lines.append(
            f"| {node} | {_fmt(item['cumulative_p33'], 1, '%')} | {_fmt(item['cumulative_p67'], 1, '%')} | "
            f"{_fmt(item['incremental_p33'], 1, '%')} | {_fmt(item['incremental_p67'], 1, '%')} |"
        )

    lines.extend(
        [
            "",
            "## 二、累计换手与后续拉升：旧样本 vs 近期普通样本",
            "",
            "单元格为 `样本数/后续拉升中位数`。11:30 以前看余下上午，11:30 看下午。",
            "",
            "| 时点 | 样本 | n | 换手中位数 | 低换手 | 中换手 | 高换手 | 换手-拉升秩相关 |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for node in ANALYSIS_NODES:
        for period in ("old", "recent_normal"):
            item = summaries[period][node]["all"]
            lines.append(
                f"| {node} | {PERIOD_LABELS[period]} | {item['count']} | {_fmt(item['turnover']['median'], 1, '%')} | "
                f"{_tier_cell(item, 'low')} | {_tier_cell(item, 'mid')} | {_tier_cell(item, 'high')} | "
                f"{_fmt_corr(item['cumulative_spearman'])} |"
            )

    lines.extend(
        [
            "",
            "### 本段新增换手的分层结果（价格规则仍持有）",
            "",
            "单元格为 `样本数/后续拉升中位数`；分档仍使用旧样本在该区间新增换手的 33%/67% 分位。",
            "",
            "| 时点 | 样本 | 节点后仍持有 n | 新增低档 | 新增中档 | 新增高档 | 新增换手相关 |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for node in ANALYSIS_NODES[:-1]:
        for period in ("old", "recent_normal"):
            item = summaries[period][node]["held_after"]
            low = item["incremental_tiers"]["low"]
            mid = item["incremental_tiers"]["mid"]
            high = item["incremental_tiers"]["high"]

            def increment_cell(value: dict[str, Any]) -> str:
                return (
                    "0/—"
                    if not value["count"]
                    else f"{value['count']}/{_fmt(value['outcome']['median'], 1, '%')}"
                )

            lines.append(
                f"| {node} | {PERIOD_LABELS[period]} | {item['count']} | {increment_cell(low)} | "
                f"{increment_cell(mid)} | {increment_cell(high)} | {_fmt_corr(item['incremental_spearman'])} |"
            )

    lines.extend(
        [
            "",
            "## 三、只看价格规则决定继续持有的样本",
            "",
            "这里排除在该节点已经触发价格卖出条件的票，检验换手能否为现有规则增加信息。单元格仍为 `样本数/后续拉升中位数`。",
            "",
            "| 时点 | 样本 | 节点后仍持有 n | 低换手 | 中换手 | 高换手 | 累计换手相关 | 本段新增换手相关 |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for node in ANALYSIS_NODES[:-1]:
        for period in ("old", "recent_normal"):
            item = summaries[period][node]["held_after"]
            lines.append(
                f"| {node} | {PERIOD_LABELS[period]} | {item['count']} | {_tier_cell(item, 'low')} | "
                f"{_tier_cell(item, 'mid')} | {_tier_cell(item, 'high')} | {_fmt_corr(item['cumulative_spearman'])} | "
                f"{_fmt_corr(item['incremental_spearman'])} |"
            )

    lines.extend(
        [
            "",
            "## 四、换手必须和价格位置一起看",
            "",
            "| 时点 | 样本 | 价格状态 | n | 换手中位数 | 后续拉升中位数 | 换手-拉升相关 |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for node in ANALYSIS_NODES:
        for period in ("old", "recent_normal"):
            for state in STATE_LABELS:
                item = interactions[period][node][state]
                if not item["count"]:
                    continue
                lines.append(
                    f"| {node} | {PERIOD_LABELS[period]} | {STATE_LABELS[state]} | {item['count']} | "
                    f"{_fmt(item['turnover']['median'], 1, '%')} | {_fmt(item['outcome']['median'], 1, '%')} | "
                    f"{_fmt_corr(item['spearman'])} |"
                )

    external_rows = [row for row in detail_rows if row["period"] == "recent_external"]
    lines.extend(
        [
            "",
            "## 五、外力样本明细",
            "",
            "| 代码 | 名称 | 节点 | 价格状态 | 累计换手 | 本段新增换手 | 后续拉升 |",
            "|---|---|---|---|---:|---:|---:|",
        ]
    )
    for row in external_rows:
        lines.append(
            f"| {row['code']} | {row['name']} | {row['node']} | {STATE_LABELS[row['price_state']]} | "
            f"{_fmt(row['cumulative_turnover_pct'], 1, '%')} | {_fmt(row['incremental_turnover_pct'], 1, '%')} | "
            f"{_fmt(row[_outcome_key(row['node'])], 1, '%')} |"
        )

    lines.extend(
        [
            "",
            "## 六、使用边界",
            "",
            "- 累计换手同时受流通盘、行情热度与价格路径影响，相关性不是因果关系。",
            "- 近期普通样本只有 11 只，按节点、价格状态再分层后样本更小；不据此单独设置机械卖点。",
            "- 换手若要进入实盘规则，优先作为同一价格状态下的强弱修正项，而不是覆盖“双低卖出、11:30 普通样本清仓”的主规则。",
            "- 明细 CSV 保留每只股票每个节点的价格状态、路径资格、换手和后续涨幅，便于新增样本后滚动重算。",
            "",
        ]
    )
    return "\n".join(lines)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "code",
        "name",
        "listing_date",
        "period",
        "external_event",
        "node",
        "price",
        "vwap",
        "open",
        "price_state",
        "cumulative_turnover_pct",
        "cumulative_turnover_tier",
        "incremental_turnover_pct",
        "incremental_turnover_tier",
        "path_exit_node",
        "entered_node",
        "held_after_node",
        "local_price_exit",
        "future_peak_to_1130_pct",
        "future_peak_full_day_pct",
        "close_from_node_pct",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in fields} for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="分析首日各观察节点换手对后续拉升的影响")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--intraday-dir", default=str(DEFAULT_INTRADAY_DIR))
    parser.add_argument("--tushare-dir", default=str(DEFAULT_TUSHARE_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    parser.add_argument("--date-tag", default="20260830")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cutoff = date.fromisoformat(args.cutoff)
    detail_rows = _build_detail_rows(
        Path(args.dataset),
        Path(args.intraday_dir),
        Path(args.tushare_dir),
        cutoff,
    )
    thresholds = _add_old_thresholds(detail_rows)
    summaries = _summaries(detail_rows)
    interactions = _state_interactions(detail_rows)

    stem = f"node_turnover_forward_rally_{args.date_tag}"
    csv_path = output_dir / f"{stem}.csv"
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    _write_csv(csv_path, detail_rows)
    json_path.write_text(
        json.dumps(
            {
                "methodology": {
                    "cutoff": args.cutoff,
                    "nodes": list(ANALYSIS_NODES),
                    "tier_definition": "old-sample node-specific p33/p67",
                    "morning_outcome": "node price to later peak through 11:30",
                    "1130_outcome": "11:30 node price to later peak through close",
                },
                "thresholds": thresholds,
                "summaries": summaries,
                "state_interactions": interactions,
                "details": detail_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    md_path.write_text(
        _render_report(detail_rows, thresholds, summaries, interactions, args.cutoff),
        encoding="utf-8",
    )
    counts = Counter(row["period"] for row in detail_rows if row["node"] == "09:35")
    print(json.dumps({"counts": counts, "csv": str(csv_path), "json": str(json_path), "report": str(md_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
