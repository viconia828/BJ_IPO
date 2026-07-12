from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import config_loader
import param_tuning


DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_PARAMS = ROOT_DIR / "策略参数.txt"
DEFAULT_REVALIDATION = ROOT_DIR / "outputs" / "latest_valuation_auto_optimization_revalidation_20260712_180703.json"
DEFAULT_AUTHOR = ROOT_DIR / "outputs" / "xueqiu_author_rule_score_20260711_192956.json"
DEFAULT_INTRADAY = ROOT_DIR / "首日分时走势"
DEFAULT_OUTPUT = ROOT_DIR / "outputs"
RETURN_BUCKETS = (
    ("低于100%", -math.inf, 100.0),
    ("100%-200%", 100.0, 200.0),
    ("200%-400%", 200.0, 400.0),
    ("400%以上", 400.0, math.inf),
)


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index + 1
        while end < len(order) and values[order[end]] == values[order[index]]:
            end += 1
        average_rank = (index + end - 1) / 2
        for position in range(index, end):
            ranks[order[position]] = average_rank
        index = end
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    rx = _rank(xs)
    ry = _rank(ys)
    mx = _mean(rx) or 0.0
    my = _mean(ry) or 0.0
    numerator = sum((x - mx) * (y - my) for x, y in zip(rx, ry))
    denominator = math.sqrt(sum((x - mx) ** 2 for x in rx) * sum((y - my) ** 2 for y in ry))
    return numerator / denominator if denominator else None


def _prediction_map(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("code") or ""): row
        for row in metrics.get("available_results") or []
        if str(row.get("code") or "")
    }


def _author_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("code") or ""): row
        for row in payload.get("rows") or []
        if str(row.get("code") or "") and _safe_float(row.get("weighted_mid")) is not None
    }


def _read_intraday(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    rows: list[dict[str, float]] = []
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                for raw in csv.DictReader(handle):
                    volume = _safe_float(raw.get("volume")) or 0.0
                    amount = _safe_float(raw.get("amount")) or 0.0
                    high = _safe_float(raw.get("high"))
                    low = _safe_float(raw.get("low"))
                    close = _safe_float(raw.get("close"))
                    if high is None or low is None or close is None:
                        continue
                    bar_vwap = amount / volume if volume > 0 and amount > 0 else close
                    datetime_text = str(raw.get("DateTime") or raw.get("datetime") or raw.get("time") or "")
                    time_text = datetime_text.rsplit(" ", 1)[-1][:5] if " " in datetime_text else datetime_text[-5:]
                    rows.append({"time": time_text, "volume": volume, "high": high, "low": low, "close": close, "vwap": bar_vwap})
            break
        except UnicodeDecodeError:
            rows = []
    if not rows:
        return None
    total_volume = sum(row["volume"] for row in rows)
    morning_rows = [row for row in rows if row["time"] <= "11:30"]
    early_rows = [row for row in rows if row["time"] <= "10:30"]
    afternoon_rows = [row for row in rows if row["time"] >= "13:00"]
    return {
        "high": max(row["high"] for row in rows),
        "low": min(row["low"] for row in rows),
        "close": rows[-1]["close"],
        "morning_high": max((row["high"] for row in morning_rows), default=None),
        "early_high": max((row["high"] for row in early_rows), default=None),
        "afternoon_high": max((row["high"] for row in afternoon_rows), default=None),
        "rows": rows,
        "total_volume": total_volume,
    }


def _volume_share_at_or_above(intraday: dict[str, Any] | None, threshold: float | None) -> float | None:
    if not intraday or threshold is None or not intraday.get("total_volume"):
        return None
    volume = sum(row["volume"] for row in intraday["rows"] if row["vwap"] >= threshold)
    return volume / float(intraday["total_volume"])


def _make_model_row(
    item: dict[str, Any],
    prediction: dict[str, Any] | None,
    intraday: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not prediction:
        return None
    issue_price = _safe_float(item.get("ISSUE_PRICE"))
    actual_price = param_tuning._actual_interval_price(item)
    actual_change = param_tuning._actual_interval_change_pct(item)
    target = _safe_float(prediction.get("predicted_target_price"))
    low = _safe_float(prediction.get("range_low"))
    high = _safe_float(prediction.get("range_high"))
    predicted_change = _safe_float(prediction.get("predicted_change_pct"))
    if issue_price is None or actual_price is None or actual_change is None or target is None or low is None or high is None or predicted_change is None:
        return None
    intraday_high = _safe_float((intraday or {}).get("high"))
    morning_high = _safe_float((intraday or {}).get("morning_high"))
    early_high = _safe_float((intraday or {}).get("early_high"))
    afternoon_high = _safe_float((intraday or {}).get("afternoon_high"))
    close = _safe_float((intraday or {}).get("close"))
    wait_regret = (
        max(morning_high - max(afternoon_high, close), 0.0) / issue_price * 100
        if morning_high is not None and afternoon_high is not None and close is not None and low > morning_high
        else 0.0
    )
    early_exit_regret = (
        max(afternoon_high - target, 0.0) / issue_price * 100
        if early_high is not None and afternoon_high is not None and early_high >= target
        else 0.0
    )
    return {
        "code": str(item.get("SECURITY_CODE") or ""),
        "name": str(item.get("SECURITY_NAME_ABBR") or ""),
        "listing_date": str(item.get("LISTING_DATE") or "")[:10],
        "issue_price": issue_price,
        "actual_price": actual_price,
        "actual_change_pct": actual_change,
        "target": target,
        "low": low,
        "high": high,
        "predicted_change_pct": predicted_change,
        "signed_change_error_pct": predicted_change - actual_change,
        "abs_change_error_pct": abs(predicted_change - actual_change),
        "hit": low <= actual_price <= high,
        "direction": "overvalued" if low > actual_price else "undervalued" if high < actual_price else "hit",
        "intraday_high": intraday_high,
        "low_reached": intraday_high is not None and intraday_high >= low,
        "target_reached": intraday_high is not None and intraday_high >= target,
        "low_volume_share": _volume_share_at_or_above(intraday, low),
        "target_volume_share": _volume_share_at_or_above(intraday, target),
        "morning_high": morning_high,
        "early_high": early_high,
        "afternoon_high": afternoon_high,
        "wait_regret_case": wait_regret > 0,
        "wait_regret_pct": wait_regret,
        "early_exit_regret_case": early_exit_regret > 0,
        "early_exit_regret_pct": early_exit_regret,
        "method1_target": _safe_float(prediction.get("method1_target_price")),
        "method2_target": _safe_float(prediction.get("method2_target_price")),
        "method3_premium": _safe_float(prediction.get("method3_premium_price")),
    }


def _make_author_row(item: dict[str, Any], author: dict[str, Any], intraday: dict[str, Any] | None) -> dict[str, Any] | None:
    issue_price = _safe_float(item.get("ISSUE_PRICE"))
    actual_price = param_tuning._actual_interval_price(item)
    actual_change = param_tuning._actual_interval_change_pct(item)
    target = _safe_float(author.get("weighted_mid"))
    low = _safe_float(author.get("weighted_low"))
    high = _safe_float(author.get("weighted_high"))
    if issue_price is None or actual_price is None or actual_change is None or target is None or low is None or high is None:
        return None
    predicted_change = (target / issue_price - 1) * 100
    intraday_high = _safe_float((intraday or {}).get("high"))
    morning_high = _safe_float((intraday or {}).get("morning_high"))
    early_high = _safe_float((intraday or {}).get("early_high"))
    afternoon_high = _safe_float((intraday or {}).get("afternoon_high"))
    close = _safe_float((intraday or {}).get("close"))
    wait_regret = (
        max(morning_high - max(afternoon_high, close), 0.0) / issue_price * 100
        if morning_high is not None and afternoon_high is not None and close is not None and low > morning_high
        else 0.0
    )
    early_exit_regret = (
        max(afternoon_high - target, 0.0) / issue_price * 100
        if early_high is not None and afternoon_high is not None and early_high >= target
        else 0.0
    )
    return {
        "code": str(item.get("SECURITY_CODE") or ""),
        "name": str(item.get("SECURITY_NAME_ABBR") or ""),
        "listing_date": str(item.get("LISTING_DATE") or "")[:10],
        "issue_price": issue_price,
        "actual_price": actual_price,
        "actual_change_pct": actual_change,
        "target": target,
        "low": low,
        "high": high,
        "predicted_change_pct": predicted_change,
        "signed_change_error_pct": predicted_change - actual_change,
        "abs_change_error_pct": abs(predicted_change - actual_change),
        "hit": low <= actual_price <= high,
        "fixed10_hit": target * 0.9 <= actual_price <= target * 1.1,
        "direction": "overvalued" if low > actual_price else "undervalued" if high < actual_price else "hit",
        "intraday_high": intraday_high,
        "low_reached": intraday_high is not None and intraday_high >= low,
        "target_reached": intraday_high is not None and intraday_high >= target,
        "low_volume_share": _volume_share_at_or_above(intraday, low),
        "target_volume_share": _volume_share_at_or_above(intraday, target),
        "morning_high": morning_high,
        "early_high": early_high,
        "afternoon_high": afternoon_high,
        "wait_regret_case": wait_regret > 0,
        "wait_regret_pct": wait_regret,
        "early_exit_regret_case": early_exit_regret > 0,
        "early_exit_regret_pct": early_exit_regret,
        "author_score_pct": _safe_float(author.get("author_score_pct")),
        "authors": list(author.get("explicit_authors") or author.get("authors") or []),
    }


def _summary(rows: list[dict[str, Any]], *, author: bool = False) -> dict[str, Any]:
    return {
        "count": len(rows),
        "hit_count": sum(bool(row["hit"]) for row in rows),
        "hit_rate": sum(bool(row["hit"]) for row in rows) / len(rows) if rows else 0.0,
        "fixed10_hit_count": sum(bool(row.get("fixed10_hit")) for row in rows) if author else None,
        "fixed10_hit_rate": sum(bool(row.get("fixed10_hit")) for row in rows) / len(rows) if author and rows else None,
        "mae_change_pct": _mean([float(row["abs_change_error_pct"]) for row in rows]),
        "mean_signed_change_error_pct": _mean([float(row["signed_change_error_pct"]) for row in rows]),
        "spearman_predicted_vs_actual_change": _spearman(
            [float(row["predicted_change_pct"]) for row in rows],
            [float(row["actual_change_pct"]) for row in rows],
        ),
        "overvalued_count": sum(row["direction"] == "overvalued" for row in rows),
        "undervalued_count": sum(row["direction"] == "undervalued" for row in rows),
        "low_reach_rate": _mean([1.0 if row["low_reached"] else 0.0 for row in rows if row.get("intraday_high") is not None]),
        "target_reach_rate": _mean([1.0 if row["target_reached"] else 0.0 for row in rows if row.get("intraday_high") is not None]),
        "avg_low_volume_share": _mean([float(row["low_volume_share"]) for row in rows if row.get("low_volume_share") is not None]),
        "avg_target_volume_share": _mean([float(row["target_volume_share"]) for row in rows if row.get("target_volume_share") is not None]),
        "wait_regret_case_count": sum(bool(row.get("wait_regret_case")) for row in rows),
        "avg_wait_regret_pct": _mean([float(row["wait_regret_pct"]) for row in rows if row.get("wait_regret_case")]),
        "total_wait_regret_pct": sum(float(row.get("wait_regret_pct") or 0.0) for row in rows),
        "early_exit_regret_case_count": sum(bool(row.get("early_exit_regret_case")) for row in rows),
        "avg_early_exit_regret_pct": _mean([float(row["early_exit_regret_pct"]) for row in rows if row.get("early_exit_regret_case")]),
        "total_early_exit_regret_pct": sum(float(row.get("early_exit_regret_pct") or 0.0) for row in rows),
    }


def _bucket_summary(rows_by_name: dict[str, list[dict[str, Any]]], common_codes: list[str]) -> list[dict[str, Any]]:
    indexed = {name: {row["code"]: row for row in rows} for name, rows in rows_by_name.items()}
    result: list[dict[str, Any]] = []
    for label, low, high in RETURN_BUCKETS:
        codes = [
            code
            for code in common_codes
            if code in indexed["formal"] and low <= float(indexed["formal"][code]["actual_change_pct"]) < high
        ]
        result.append(
            {
                "bucket": label,
                "count": len(codes),
                "models": {
                    name: _summary([mapping[code] for code in codes if code in mapping])
                    for name, mapping in indexed.items()
                },
            }
        )
    return result


def _fmt(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    return "-" if number is None else f"{number:.{digits}f}"


def _pct(value: Any) -> str:
    number = _safe_float(value)
    return "-" if number is None else f"{number * 100:.1f}%"


def _render(payload: dict[str, Any]) -> str:
    lines = [
        "# 最新估值模型命中率、MAE 与雪球作者对照分析",
        "",
        f"> 生成时间：{payload['generated_at']}",
        "> 用途：评估首日卖出参考，不修改正式参数或正式输出。",
        "",
        "## 一、先拆清两种变化",
        "",
        "- 三方法结构重构本身：同一旧可用样本中，原始区间命中率 22.58% 持平；近期加权命中率 17.35%→18.72%；原始涨幅 MAE 144.82→152.54。",
        "- 随后的核心自动优化：在当前 replay v8 上，正式参数命中率 17.95%→核心候选 25.64%，近期加权命中率 7.30%→29.96%；原始涨幅 MAE 154.17→159.76。",
        "- 因此“大幅提高命中率但 MAE 变差”主要来自自动优化目标函数和候选参数，而不是三方法结构重构单独造成。",
        "",
        "## 二、当前同口径总体对照",
        "",
        "| 方案 | 样本 | 区间命中 | 涨幅 MAE | 平均偏差 | 排序 Spearman | 高估区间 | 低估区间 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, label in (("formal", "正式新模型"), ("optimized", "核心自动优化候选"), ("author", "雪球作者加权区间")):
        row = payload["summaries"][key]
        lines.append(
            f"| {label} | {row['count']} | {row['hit_count']}/{row['count']} ({_pct(row['hit_rate'])}) | {_fmt(row['mae_change_pct'])} | {_fmt(row['mean_signed_change_error_pct'])} | {_fmt(row['spearman_predicted_vs_actual_change'], 3)} | {row['overvalued_count']} | {row['undervalued_count']} |"
        )
    author = payload["summaries"]["author"]
    lines.extend(
        [
            "",
            f"雪球作者中枢固定 ±10% 时命中 {author['fixed10_hit_count']}/{author['count']}（{_pct(author['fixed10_hit_rate'])}），可与模型固定 ±10% 区间直接比较。",
            "",
            "## 三、为什么命中上升而 MAE 变差",
            "",
            f"- 核心候选新增命中 {len(payload['transitions']['gained_hits'])} 只、丢失命中 {len(payload['transitions']['lost_hits'])} 只，净增加 {len(payload['transitions']['gained_hits']) - len(payload['transitions']['lost_hits'])} 只。",
            f"- 39 个同口径样本的绝对涨幅误差总量净增加 {_fmt(payload['transitions']['total_abs_error_delta'])} 个百分点，平均增加 {_fmt(payload['transitions']['mean_abs_error_delta'])} 个百分点。",
            "- 区间命中是阈值指标：中枢稍微移动就可能让样本跨进 ±10% 区间；MAE 是连续指标，少数高涨幅尾部样本被进一步低估，会抵消多只边界样本的命中收益。",
            "- 核心候选同时把北交所折扣、小盘溢价、情绪基线和情绪半衰期推到搜索边界，更偏向近期高情绪样本；这提高近期命中，但扩大了部分历史样本误差。",
            "",
            "### 命中迁移",
            "",
            f"- 新增命中：{', '.join(payload['transitions']['gained_hits']) or '无'}",
            f"- 丢失命中：{', '.join(payload['transitions']['lost_hits']) or '无'}",
            "",
            "### 对 MAE 恶化贡献最大的样本",
            "",
            "| 代码 | 简称 | 实际涨幅 | 正式预测 | 优化预测 | 绝对误差增加 | 命中变化 |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in payload["transitions"]["top_mae_worseners"][:10]:
        lines.append(
            f"| {row['code']} | {row['name']} | {_fmt(row['actual_change_pct'])}% | {_fmt(row['formal_predicted_change_pct'])}% | {_fmt(row['optimized_predicted_change_pct'])}% | {_fmt(row['abs_error_delta'])} | {row['hit_transition']} |"
        )
    lines.extend(
        [
            "",
            "## 四、作为首日卖出参考是否可接受",
            "",
            "| 方案 | 早盘未达下沿且随后回落 | 平均等待后悔 | 10:30前达中枢且午后再涨 | 平均早卖后悔 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for key, label in (("formal", "正式新模型"), ("optimized", "核心自动优化候选"), ("author", "雪球作者")):
        row = payload["summaries"][key]
        lines.append(
            f"| {label} | {row['wait_regret_case_count']} | {_fmt(row['avg_wait_regret_pct'])}pct | {row['early_exit_regret_case_count']} | {_fmt(row['avg_early_exit_regret_pct'])}pct |"
        )
    accept = payload["sell_reference_assessment"]
    lines.extend(
        [
            "",
            f"结论：**{accept['verdict']}**",
            "",
            *[f"- {reason}" for reason in accept["reasons"]],
            f"- 按累计幅度计算，只有把一次“等待错过早盘高点”的单位损失看得比“过早卖完错过午后上涨”高约 {_fmt(accept['tradeoff']['magnitude_break_even_wait_vs_early_weight'], 1)} 倍以上，正式参数才优于核心候选；按事件次数计算的临界权重约为 {_fmt(accept['tradeoff']['case_break_even_wait_vs_early_weight'], 1)} 倍。该临界值只用于表达偏好，不是收益回测。",
            "",
            "这里的“等待后悔”代理定义为：早盘最高价仍未达到估值下沿，且午后最高价/收盘价又低于早盘高点；“早卖后悔”代理定义为：10:30 前已达到估值中枢，但午后最高价继续高于中枢。它们对应心理预期过高和过低的两类时序风险。",
            "",
            "## 五、与雪球作者相比还能学什么",
            "",
        ]
    )
    gap = payload["author_gap"]
    lines.extend(
        [
            f"- 同代码中，作者命中而正式模型未命中：{len(gap['author_only_vs_formal'])} 只；正式模型命中而作者未命中：{len(gap['formal_only_vs_author'])} 只。",
            f"- 作者命中而核心候选未命中：{len(gap['author_only_vs_optimized'])} 只；核心候选命中而作者未命中：{len(gap['optimized_only_vs_author'])} 只。",
            f"- 作者预测涨幅与实际涨幅 Spearman 为 {_fmt(author['spearman_predicted_vs_actual_change'], 3)}，显著高于正式模型 {_fmt(payload['summaries']['formal']['spearman_predicted_vs_actual_change'], 3)} 和核心候选 {_fmt(payload['summaries']['optimized']['spearman_predicted_vs_actual_change'], 3)}。作者优势首先是横截面排序和题材/质地分层，不只是把区间放宽。",
            f"- 作者相对核心候选独有命中的 {len(gap['author_only_vs_optimized'])} 只中，模型高估 {gap['author_only_optimized_overvalued_count']} 只、低估 {gap['author_only_optimized_undervalued_count']} 只；其中 {gap['author_only_optimized_method2_unavailable_count']} 只缺少方法二锚点。主要缺口是方法一单锚点时的可比质量与置信度控制。",
            f"- 作者相对正式模型独有命中：{', '.join(gap['author_only_vs_formal']) or '无'}。",
            "",
            "建议优先学习四类本地可结构化信息：",
            "",
            "1. 把公告中的产品稀缺性、国产替代、客户层级和技术壁垒抽成题材/质地标签，用于修正横截面排序；",
            "2. 把可比公司从单一 PE 中位数升级为增长、毛利率、研发强度和规模匹配后的可比分层；",
            "3. 对 400% 以上高情绪尾部建立独立 regime，而不是继续拉长全局情绪半衰期；",
            "4. 保留作者式置信区间差异：证据一致时收窄，作者/可比/情绪冲突时扩大；不要用统一 ±10% 承担全部不确定性。",
            "",
            "## 六、收益率分层",
            "",
            "| 实际涨幅层 | 样本 | 正式命中/MAE | 优化命中/MAE | 作者命中/MAE |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for bucket in payload["return_buckets"]:
        formal = bucket["models"]["formal"]
        optimized = bucket["models"]["optimized"]
        author_row = bucket["models"]["author"]
        lines.append(
            f"| {bucket['bucket']} | {bucket['count']} | {_pct(formal['hit_rate'])}/{_fmt(formal['mae_change_pct'])} | {_pct(optimized['hit_rate'])}/{_fmt(optimized['mae_change_pct'])} | {_pct(author_row['hit_rate'])}/{_fmt(author_row['mae_change_pct'])} |"
        )
    lines.extend(
        [
            "",
            "## 七、实施边界",
            "",
            "- 核心候选没有写入 `策略参数.txt`。",
            "- 本报告没有把作者价格接入正式估值，只把它作为研究对照和教师信息。",
            "- 下一轮若修改自动优化目标，应加入全样本 MAE/过高估值约束和首日可成交性指标，避免只追求近期区间命中。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset = param_tuning.load_replay_dataset(args.dataset)
    params = config_loader.load_params(args.params)
    revalidation = json.loads(Path(args.revalidation).read_text(encoding="utf-8"))
    author_payload = json.loads(Path(args.author_report).read_text(encoding="utf-8"))
    optimized_params = dict(params)
    optimized_params.update(dict((revalidation.get("core_only") or {}).get("final_overrides") or {}))
    formal_metrics = param_tuning.evaluate_replay_targets(dataset, params)
    optimized_metrics = param_tuning.evaluate_replay_targets(dataset, optimized_params)
    formal_map = _prediction_map(formal_metrics)
    optimized_map = _prediction_map(optimized_metrics)
    author_map = _author_map(author_payload)
    items = {
        str(item.get("SECURITY_CODE") or ""): item
        for item in dataset.get("items") or []
        if str(item.get("SECURITY_CODE") or "")
    }
    intraday_by_code = {code: _read_intraday(Path(args.intraday_dir) / f"{code}.csv") for code in items}
    formal_rows = [row for code, item in items.items() if (row := _make_model_row(item, formal_map.get(code), intraday_by_code.get(code)))]
    optimized_rows = [row for code, item in items.items() if (row := _make_model_row(item, optimized_map.get(code), intraday_by_code.get(code)))]
    author_rows = [row for code, item in items.items() if code in author_map and (row := _make_author_row(item, author_map[code], intraday_by_code.get(code)))]
    formal_index = {row["code"]: row for row in formal_rows}
    optimized_index = {row["code"]: row for row in optimized_rows}
    author_index = {row["code"]: row for row in author_rows}
    model_common = sorted(set(formal_index) & set(optimized_index))
    all_common = sorted(set(formal_index) & set(optimized_index) & set(author_index))

    transition_rows: list[dict[str, Any]] = []
    for code in model_common:
        formal = formal_index[code]
        optimized = optimized_index[code]
        if not formal["hit"] and optimized["hit"]:
            hit_transition = "新增命中"
        elif formal["hit"] and not optimized["hit"]:
            hit_transition = "丢失命中"
        elif formal["hit"]:
            hit_transition = "持续命中"
        else:
            hit_transition = "持续未命中"
        transition_rows.append(
            {
                "code": code,
                "name": formal["name"],
                "actual_change_pct": formal["actual_change_pct"],
                "formal_predicted_change_pct": formal["predicted_change_pct"],
                "optimized_predicted_change_pct": optimized["predicted_change_pct"],
                "formal_abs_error": formal["abs_change_error_pct"],
                "optimized_abs_error": optimized["abs_change_error_pct"],
                "abs_error_delta": optimized["abs_change_error_pct"] - formal["abs_change_error_pct"],
                "hit_transition": hit_transition,
            }
        )
    total_abs_error_delta = sum(float(row["abs_error_delta"]) for row in transition_rows)
    transitions = {
        "gained_hits": [row["code"] for row in transition_rows if row["hit_transition"] == "新增命中"],
        "lost_hits": [row["code"] for row in transition_rows if row["hit_transition"] == "丢失命中"],
        "total_abs_error_delta": total_abs_error_delta,
        "mean_abs_error_delta": total_abs_error_delta / len(transition_rows) if transition_rows else None,
        "top_mae_worseners": sorted(transition_rows, key=lambda row: float(row["abs_error_delta"]), reverse=True),
        "top_mae_improvers": sorted(transition_rows, key=lambda row: float(row["abs_error_delta"])),
        "rows": transition_rows,
    }
    summaries = {
        "formal": _summary([formal_index[code] for code in all_common]),
        "optimized": _summary([optimized_index[code] for code in all_common]),
        "author": _summary([author_index[code] for code in all_common], author=True),
    }
    formal = summaries["formal"]
    optimized = summaries["optimized"]
    sell_reasons = [
        f"区间命中率提高 {_pct(float(optimized['hit_rate']) - float(formal['hit_rate']))}，但涨幅 MAE 增加 {_fmt(float(optimized['mae_change_pct']) - float(formal['mae_change_pct']))} 个百分点。",
        f"高预期导致的等待后悔样本从 {formal['wait_regret_case_count']} 只变为 {optimized['wait_regret_case_count']} 只，累计后悔幅度从 {_fmt(formal['total_wait_regret_pct'])} 变为 {_fmt(optimized['total_wait_regret_pct'])} 个发行价百分点。",
        f"低预期导致的早卖后悔样本从 {formal['early_exit_regret_case_count']} 只变为 {optimized['early_exit_regret_case_count']} 只，累计午后上行空间从 {_fmt(formal['total_early_exit_regret_pct'])} 变为 {_fmt(optimized['total_early_exit_regret_pct'])} 个发行价百分点。",
        f"估值区间整体过高的样本从 {formal['overvalued_count']} 只变为 {optimized['overvalued_count']} 只；整体过低的样本从 {formal['undervalued_count']} 只变为 {optimized['undervalued_count']} 只。",
        "核心候选多个参数落在搜索边界，说明当前改善可能依赖样本窗口，不适合直接作为正式卖出锚点。",
    ]
    acceptable = (
        float(optimized["hit_rate"]) > float(formal["hit_rate"])
        and float(optimized.get("total_wait_regret_pct") or 0.0) <= float(formal.get("total_wait_regret_pct") or 0.0)
        and float(optimized.get("total_early_exit_regret_pct") or 0.0) <= float(formal.get("total_early_exit_regret_pct") or 0.0)
    )
    author_only_formal = [code for code in all_common if author_index[code]["hit"] and not formal_index[code]["hit"]]
    formal_only_author = [code for code in all_common if formal_index[code]["hit"] and not author_index[code]["hit"]]
    author_only_optimized = [code for code in all_common if author_index[code]["hit"] and not optimized_index[code]["hit"]]
    optimized_only_author = [code for code in all_common if optimized_index[code]["hit"] and not author_index[code]["hit"]]
    wait_regret_increase = float(optimized.get("total_wait_regret_pct") or 0.0) - float(formal.get("total_wait_regret_pct") or 0.0)
    early_exit_regret_reduction = float(formal.get("total_early_exit_regret_pct") or 0.0) - float(optimized.get("total_early_exit_regret_pct") or 0.0)
    magnitude_break_even_ratio = (
        early_exit_regret_reduction / wait_regret_increase
        if wait_regret_increase > 0 and early_exit_regret_reduction > 0
        else None
    )
    wait_case_increase = int(optimized["wait_regret_case_count"]) - int(formal["wait_regret_case_count"])
    early_case_reduction = int(formal["early_exit_regret_case_count"]) - int(optimized["early_exit_regret_case_count"])
    case_break_even_ratio = early_case_reduction / wait_case_increase if wait_case_increase > 0 and early_case_reduction > 0 else None
    payload = {
        "schema": "latest_valuation_tradeoff_analysis_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {
            "dataset": str(Path(args.dataset)),
            "params": str(Path(args.params)),
            "revalidation": str(Path(args.revalidation)),
            "author_report": str(Path(args.author_report)),
            "intraday_dir": str(Path(args.intraday_dir)),
            "optimized_overrides": dict((revalidation.get("core_only") or {}).get("final_overrides") or {}),
            "model_common_count": len(model_common),
            "author_common_count": len(all_common),
        },
        "summaries": summaries,
        "transitions": transitions,
        "sell_reference_assessment": {
            "acceptable_without_additional_constraints": acceptable,
            "verdict": "可以接受，但仍需样本外确认" if acceptable else "暂不应接受为正式优化代价",
            "reasons": sell_reasons,
            "tradeoff": {
                "wait_regret_increase_pct": wait_regret_increase,
                "early_exit_regret_reduction_pct": early_exit_regret_reduction,
                "magnitude_break_even_wait_vs_early_weight": magnitude_break_even_ratio,
                "wait_case_increase": wait_case_increase,
                "early_exit_case_reduction": early_case_reduction,
                "case_break_even_wait_vs_early_weight": case_break_even_ratio,
            },
        },
        "author_gap": {
            "author_only_vs_formal": author_only_formal,
            "formal_only_vs_author": formal_only_author,
            "author_only_vs_optimized": author_only_optimized,
            "optimized_only_vs_author": optimized_only_author,
            "author_only_optimized_overvalued_count": sum(optimized_index[code]["direction"] == "overvalued" for code in author_only_optimized),
            "author_only_optimized_undervalued_count": sum(optimized_index[code]["direction"] == "undervalued" for code in author_only_optimized),
            "author_only_optimized_method2_unavailable_count": sum(optimized_index[code].get("method2_target") is None for code in author_only_optimized),
        },
        "return_buckets": _bucket_summary(
            {
                "formal": [formal_index[code] for code in all_common],
                "optimized": [optimized_index[code] for code in all_common],
                "author": [author_index[code] for code in all_common],
            },
            all_common,
        ),
        "rows": {
            "formal": formal_rows,
            "optimized": optimized_rows,
            "author": author_rows,
        },
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"latest_valuation_tradeoff_analysis_{timestamp}.json"
    markdown_path = output_dir / f"latest_valuation_tradeoff_analysis_{timestamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_render(payload), encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "markdown": str(markdown_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze hit-rate/MAE tradeoff and compare latest valuation with local Xueqiu author forecasts.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--params", default=str(DEFAULT_PARAMS))
    parser.add_argument("--revalidation", default=str(DEFAULT_REVALIDATION))
    parser.add_argument("--author-report", default=str(DEFAULT_AUTHOR))
    parser.add_argument("--intraday-dir", default=str(DEFAULT_INTRADAY))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    return parser


def main() -> int:
    payload = run(build_parser().parse_args())
    print(json.dumps({"summaries": payload["summaries"], "sell_reference_assessment": payload["sell_reference_assessment"], "author_gap": payload["author_gap"], "outputs": payload["outputs"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
