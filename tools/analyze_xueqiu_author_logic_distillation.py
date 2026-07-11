from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from collections import defaultdict
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
DEFAULT_SCAN_REPORT = ROOT_DIR / "调参" / "valuation_hit_rate_scan_202603plus_20260710_001437.json"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"

PROXY_STEPS = [0, 5, 10, 15, 20, 30, 40]
PROXY_WIDTHS = [0.10, 0.15, 0.20]
ROLLING_ALPHAS = [0.25, 0.50, 0.75, 1.00]
MODEL_NAMES = ["current_params", "scan_best"]

FEATURE_LABELS = {
    "issue_price": "发行价",
    "float_market_cap_yi": "流通市值",
    "old_share_ratio": "老股/流通股",
    "after_issue_pe": "发行PE",
    "pe_to_industry": "发行PE/行业PE",
    "recent5_median_change": "近5只中位涨幅",
    "recent5_avg_change": "近5只平均涨幅",
    "same_industry_recent_median": "同业近端中位涨幅",
    "top_apply_marketcap": "顶格资金",
    "model_uncertainty_score": "模型不确定性",
    "proxy_score": "proxy总分",
}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


blend = _load_module("evaluate_xueqiu_author_model_blend", ROOT_DIR / "tools" / "evaluate_xueqiu_author_model_blend.py")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


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


def _fmt_num(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:.{digits}f}"


def _fmt_pct(value: Any, digits: int = 1) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:.{digits}f}%"


def _fmt_rate(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number * 100:.1f}%"


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("T", " ").replace("Z", "")
    text = text.split(".", 1)[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _calc_change_pct(issue_price: Any, target_price: Any) -> float | None:
    issue = _safe_float(issue_price)
    target = _safe_float(target_price)
    if not issue or target is None:
        return None
    return (target / issue - 1) * 100


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _quantile(values: list[float], q: float) -> float | None:
    clean = sorted(value for value in values if _safe_float(value) is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    pos = (len(clean) - 1) * q
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return clean[low]
    return clean[low] * (high - pos) + clean[high] * (pos - low)


def _rank(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i + 1
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + 1 + j) / 2
        for k in range(i, j):
            ranks[indexed[k][0]] = avg_rank
        i = j
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx = _mean(xs)
    my = _mean(ys)
    if mx is None or my is None:
        return None
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    sx = math.sqrt(sum(x * x for x in dx))
    sy = math.sqrt(sum(y * y for y in dy))
    if sx <= 0 or sy <= 0:
        return None
    return sum(x * y for x, y in zip(dx, dy)) / (sx * sy)


def _spearman_pairs(pairs: list[tuple[float | None, float | None]]) -> float | None:
    clean = [(float(x), float(y)) for x, y in pairs if _safe_float(x) is not None and _safe_float(y) is not None]
    if len(clean) < 3:
        return None
    xs = [x for x, _ in clean]
    ys = [y for _, y in clean]
    return _pearson(_rank(xs), _rank(ys))


def _dataset_by_code(dataset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("SECURITY_CODE") or "").strip(): item
        for item in dataset.get("items") or []
        if str(item.get("SECURITY_CODE") or "").strip()
    }


def _latest_author_score_report(output_dir: Path) -> Path:
    return blend._latest_author_score_report(output_dir)


def _actual_interval_price(item: dict[str, Any]) -> float | None:
    return param_tuning._actual_interval_price(item)


def _actual_interval_change_pct(item: dict[str, Any]) -> float | None:
    return param_tuning._actual_interval_change_pct(item)


def _target_codes(target: str, scan_report: dict[str, Any], dataset_by_code: dict[str, dict[str, Any]], author_predictions: dict[str, dict[str, Any]]) -> list[str]:
    if target == "scan_sample":
        return [str(code) for code in scan_report.get("sample_codes") or []]
    if target == "author_scored":
        sample = [str(code) for code in scan_report.get("sample_codes") or []]
        return [code for code in sample if code in author_predictions]
    if target == "all_actual":
        return [code for code, item in dataset_by_code.items() if _actual_interval_price(item) is not None]
    raise ValueError(f"unsupported target: {target}")


def _float_market_cap_yi(item: dict[str, Any]) -> float | None:
    issue_price = _safe_float(item.get("ISSUE_PRICE"))
    float_shares = _safe_float(item.get("float_shares"))
    if issue_price is None or float_shares is None:
        return None
    return issue_price * float_shares / 10000


def _old_share_ratio(item: dict[str, Any]) -> float | None:
    old_shares = _safe_float(item.get("old_shares"))
    float_shares = _safe_float(item.get("float_shares"))
    if old_shares is None or not float_shares:
        return None
    return old_shares / float_shares


def _pe_to_industry(item: dict[str, Any]) -> float | None:
    pe = _safe_float(item.get("AFTER_ISSUE_PE"))
    industry_pe = _safe_float(item.get("INDUSTRY_PE_NEW"))
    if pe is None or not industry_pe or industry_pe <= 0:
        return None
    return pe / industry_pe


def _previous_items(item: dict[str, Any], all_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    listing_dt = _parse_dt(item.get("LISTING_DATE"))
    if listing_dt is None:
        return []
    previous = []
    for candidate in all_items:
        other_dt = _parse_dt(candidate.get("LISTING_DATE"))
        if other_dt is None or other_dt >= listing_dt:
            continue
        if _actual_interval_change_pct(candidate) is None:
            continue
        previous.append(candidate)
    previous.sort(key=lambda row: _parse_dt(row.get("LISTING_DATE")) or datetime.min)
    return previous


def _recent_mood(item: dict[str, Any], all_items: list[dict[str, Any]]) -> dict[str, Any]:
    previous = _previous_items(item, all_items)
    changes = [_actual_interval_change_pct(row) for row in previous]
    changes = [value for value in changes if value is not None]
    recent3 = changes[-3:]
    recent5 = changes[-5:]
    recent10 = changes[-10:]

    primary = str(item.get("industry_primary") or item.get("INDUSTRY") or "").strip()
    secondary = str(item.get("industry_secondary") or "").strip()
    same_industry = []
    for row in previous:
        row_primary = str(row.get("industry_primary") or row.get("INDUSTRY") or "").strip()
        row_secondary = str(row.get("industry_secondary") or "").strip()
        if secondary and row_secondary == secondary:
            same_industry.append(row)
        elif primary and row_primary == primary:
            same_industry.append(row)
    same_changes = [_actual_interval_change_pct(row) for row in same_industry[-5:]]
    same_changes = [value for value in same_changes if value is not None]
    return {
        "previous_count": len(previous),
        "recent3_avg_change": _mean(recent3),
        "recent3_median_change": _median(recent3),
        "recent5_avg_change": _mean(recent5),
        "recent5_median_change": _median(recent5),
        "recent10_avg_change": _mean(recent10),
        "recent10_median_change": _median(recent10),
        "same_industry_previous_count": len(same_industry),
        "same_industry_recent_avg": _mean(same_changes),
        "same_industry_recent_median": _median(same_changes),
    }


def _model_hit(prediction: dict[str, Any] | None, actual_price: float | None) -> bool | None:
    if not prediction or not prediction.get("available"):
        return None
    low = _safe_float(prediction.get("low"))
    high = _safe_float(prediction.get("high"))
    if low is None or high is None or actual_price is None:
        return None
    return min(low, high) <= actual_price <= max(low, high)


def _method_count(prediction: dict[str, Any] | None) -> int:
    if not prediction or not prediction.get("available"):
        return 0
    return sum(1 for key in ("method1_available", "method2_available", "method3_available") if prediction.get(key))


def _build_thresholds(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    fields = [
        "issue_price",
        "float_market_cap_yi",
        "old_share_ratio",
        "after_issue_pe",
        "pe_to_industry",
        "top_apply_marketcap",
        "online_issue_num",
        "recent5_median_change",
    ]
    thresholds: dict[str, dict[str, float | None]] = {}
    for field in fields:
        values = [_safe_float(row.get(field)) for row in rows]
        values = [value for value in values if value is not None]
        thresholds[field] = {
            "p25": _quantile(values, 0.25),
            "p50": _quantile(values, 0.50),
            "p75": _quantile(values, 0.75),
        }
    return thresholds


def _score_proxy(row: dict[str, Any], thresholds: dict[str, dict[str, float | None]]) -> tuple[float, dict[str, float], list[str]]:
    components: dict[str, float] = {}
    reasons: list[str] = []

    issue = _safe_float(row.get("issue_price"))
    float_cap = _safe_float(row.get("float_market_cap_yi"))
    old_ratio = _safe_float(row.get("old_share_ratio"))
    pe = _safe_float(row.get("after_issue_pe"))
    pe_ratio = _safe_float(row.get("pe_to_industry"))
    recent5 = _safe_float(row.get("recent5_median_change"))
    same_industry = _safe_float(row.get("same_industry_recent_median"))
    top_apply = _safe_float(row.get("top_apply_marketcap"))
    online_issue = _safe_float(row.get("online_issue_num"))
    current_available = bool(row.get("current_available"))
    method_count = int(row.get("current_method_count") or 0)
    current_change = _safe_float(row.get("current_predicted_change_pct"))
    scan_change = _safe_float(row.get("scan_best_predicted_change_pct"))

    liquidity = 0.0
    float_q = thresholds.get("float_market_cap_yi") or {}
    if float_cap is not None:
        if float_q.get("p25") is not None and float_cap <= float_q["p25"]:
            liquidity += 2
            reasons.append("小流通市值")
        elif float_q.get("p50") is not None and float_cap <= float_q["p50"]:
            liquidity += 1
            reasons.append("中小流通市值")
        elif float_q.get("p75") is not None and float_cap >= float_q["p75"]:
            liquidity -= 1
            reasons.append("流通市值偏大")
    if issue is not None:
        if issue <= 15:
            liquidity += 1
            reasons.append("低发行价")
        elif issue >= 30:
            liquidity -= 1
            reasons.append("高发行价")
    components["liquidity_elasticity"] = liquidity

    valuation = 0.0
    if pe_ratio is not None:
        if pe_ratio <= 0.55:
            valuation += 2
            reasons.append("发行估值相对行业偏低")
        elif pe_ratio <= 0.80:
            valuation += 1
            reasons.append("发行估值相对行业不高")
        elif pe_ratio >= 1.50:
            valuation -= 2
            reasons.append("发行估值显著高于行业")
        elif pe_ratio >= 1.15:
            valuation -= 1
            reasons.append("发行估值高于行业")
    if pe is not None:
        if pe <= 15:
            valuation += 1
            reasons.append("发行PE低")
        elif pe >= 35:
            valuation -= 1
            reasons.append("发行PE高")
    components["valuation_tolerance"] = valuation

    mood = 0.0
    if recent5 is not None:
        if recent5 >= 180:
            mood += 3
            reasons.append("近端新股情绪极强")
        elif recent5 >= 120:
            mood += 2
            reasons.append("近端新股情绪强")
        elif recent5 >= 70:
            mood += 1
            reasons.append("近端新股情绪偏强")
        elif recent5 < 20:
            mood -= 2
            reasons.append("近端新股情绪弱")
        elif recent5 < 50:
            mood -= 1
            reasons.append("近端新股情绪偏弱")
    components["recent_mood"] = mood

    sector = 0.0
    if same_industry is not None:
        if same_industry >= 160:
            sector += 2
            reasons.append("同业近端表现强")
        elif same_industry >= 90:
            sector += 1
            reasons.append("同业近端表现偏强")
        elif same_industry < 30:
            sector -= 1
            reasons.append("同业近端表现弱")
    components["sector_proxy"] = sector

    attention = 0.0
    top_q = thresholds.get("top_apply_marketcap") or {}
    online_q = thresholds.get("online_issue_num") or {}
    if top_apply is not None and top_q.get("p25") is not None and top_q.get("p75") is not None:
        if top_apply <= top_q["p25"]:
            attention += 1
            reasons.append("顶格资金门槛低")
        elif top_apply >= top_q["p75"]:
            attention -= 0.5
            reasons.append("顶格资金门槛高")
    if online_issue is not None and online_q.get("p25") is not None and online_issue <= online_q["p25"]:
        attention += 0.5
        reasons.append("网上发行规模小")
    components["subscription_attention"] = attention

    supply_penalty = 0.0
    if old_ratio is not None:
        if old_ratio >= 0.25:
            supply_penalty -= 2
            reasons.append("老股占流通比例高")
        elif old_ratio >= 0.12:
            supply_penalty -= 1
            reasons.append("存在老股抛压")
    components["supply_overhang"] = supply_penalty

    uncertainty = 0.0
    if not current_available:
        uncertainty += 2
        reasons.append("当前模型不可用")
    elif method_count <= 1:
        uncertainty += 1
        reasons.append("当前模型可用方法少")
    if current_change is not None and scan_change is not None and abs(scan_change - current_change) >= 80:
        uncertainty += 1
        reasons.append("当前与扫描最优分歧大")
    components["model_uncertainty"] = uncertainty

    total = sum(components.values())
    return total, components, reasons


def _linear_proxy_prediction(previous: list[dict[str, Any]], score: float) -> tuple[float | None, str]:
    pairs = [
        (_safe_float(row.get("proxy_score")), _safe_float(row.get("actual_change_pct")))
        for row in previous
        if _safe_float(row.get("proxy_score")) is not None and _safe_float(row.get("actual_change_pct")) is not None
    ]
    pairs = [(float(x), float(y)) for x, y in pairs if x is not None and y is not None]
    if len(pairs) >= 5:
        xs = [x for x, _ in pairs]
        ys = [y for _, y in pairs]
        mx = _mean(xs)
        my = _mean(ys)
        if mx is not None and my is not None:
            variance = sum((x - mx) ** 2 for x in xs)
            if variance > 1e-9:
                beta = sum((x - mx) * (y - my) for x, y in pairs) / variance
                # Small samples are noisy; clamp the slope to keep the proxy from exploding.
                beta = max(-20.0, min(35.0, beta))
                alpha = my - beta * mx
                predicted = alpha + beta * score
                return max(-50.0, min(900.0, predicted)), "rolling_linear_proxy"
    if len(pairs) >= 3:
        similar = [y for x, y in pairs if abs(x - score) <= 2.5]
        if len(similar) >= 2:
            return _median(similar), "rolling_similar_score_median"
        return _median([y for _, y in pairs]), "rolling_median"
    return None, "insufficient_history"


def _attach_rolling_proxy_estimates(rows: list[dict[str, Any]], params: dict[str, Any]) -> None:
    previous: list[dict[str, Any]] = []
    baseline = _safe_float(params.get("sentiment_first_day_baseline_pct"))
    for row in rows:
        score = _safe_float(row.get("proxy_score")) or 0.0
        predicted, source = _linear_proxy_prediction(previous, score)
        if predicted is None:
            predicted = _fallback_base_change(row, params)
            source = "recent_mood_or_param_baseline" if predicted is not None else source
        row["rolling_proxy_expected_change_pct"] = predicted
        row["rolling_proxy_expected_source"] = source
        row["rolling_proxy_history_count"] = len(previous)
        if predicted is None and baseline is not None:
            row["rolling_proxy_expected_change_pct"] = baseline
            row["rolling_proxy_expected_source"] = "param_baseline"
        if _safe_float(row.get("actual_change_pct")) is not None:
            previous.append(row)


def _build_teacher_rows(
    target_codes: list[str],
    dataset_items: list[dict[str, Any]],
    dataset_by_code: dict[str, dict[str, Any]],
    model_predictions: dict[str, dict[str, dict[str, Any]]],
    author_predictions: dict[str, dict[str, Any]],
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code in target_codes:
        item = dataset_by_code.get(code)
        if not item:
            continue
        issue_price = _safe_float(item.get("ISSUE_PRICE"))
        actual_price = _actual_interval_price(item)
        actual_change = _actual_interval_change_pct(item)
        current = (model_predictions.get("current_params") or {}).get(code)
        scan_best = (model_predictions.get("scan_best") or {}).get(code)
        author = author_predictions.get(code)
        current_target = _safe_float((current or {}).get("target"))
        scan_target = _safe_float((scan_best or {}).get("target"))
        author_target = _safe_float((author or {}).get("target"))
        current_pred_change = _calc_change_pct(issue_price, current_target)
        scan_pred_change = _calc_change_pct(issue_price, scan_target)
        author_pred_change = _calc_change_pct(issue_price, author_target)
        current_hit = _model_hit(current, actual_price)
        scan_hit = _model_hit(scan_best, actual_price)
        author_weighted_hit = _model_hit(author, actual_price)
        author_fixed_hit = None
        if author and actual_price is not None:
            fixed_low = _safe_float(author.get("fixed10_low"))
            fixed_high = _safe_float(author.get("fixed10_high"))
            if fixed_low is not None and fixed_high is not None:
                author_fixed_hit = min(fixed_low, fixed_high) <= actual_price <= max(fixed_low, fixed_high)

        if current_hit is None and author_weighted_hit is True:
            category = "model_unavailable_author_hit"
        elif current_hit is False and author_weighted_hit is True:
            category = "model_miss_author_hit"
        elif current_hit is True and author_weighted_hit is True:
            category = "model_hit_author_hit"
        elif current_hit is True and author_weighted_hit is False:
            category = "model_hit_author_miss"
        elif current_hit is False and author_weighted_hit is False:
            category = "model_miss_author_miss"
        elif author is None:
            category = "no_author"
        else:
            category = "other"

        mood = _recent_mood(item, dataset_items)
        row = {
            "code": code,
            "name": item.get("SECURITY_NAME_ABBR"),
            "listing_date": item.get("LISTING_DATE"),
            "industry_primary": item.get("industry_primary") or item.get("INDUSTRY"),
            "industry_secondary": item.get("industry_secondary"),
            "issue_price": issue_price,
            "after_issue_pe": _safe_float(item.get("AFTER_ISSUE_PE")),
            "industry_pe_new": _safe_float(item.get("INDUSTRY_PE_NEW")),
            "pe_to_industry": _pe_to_industry(item),
            "float_shares": _safe_float(item.get("float_shares")),
            "float_market_cap_yi": _float_market_cap_yi(item),
            "old_shares": _safe_float(item.get("old_shares")),
            "old_share_ratio": _old_share_ratio(item),
            "online_issue_num": _safe_float(item.get("ONLINE_ISSUE_NUM")),
            "top_apply_marketcap": _safe_float(item.get("TOP_APPLY_MARKETCAP")),
            "actual_price": actual_price,
            "actual_change_pct": actual_change,
            "current_available": bool(current and current.get("available")),
            "current_target": current_target,
            "current_low": _safe_float((current or {}).get("low")),
            "current_high": _safe_float((current or {}).get("high")),
            "current_predicted_change_pct": current_pred_change,
            "current_hit": current_hit,
            "current_method_count": _method_count(current),
            "scan_best_available": bool(scan_best and scan_best.get("available")),
            "scan_best_target": scan_target,
            "scan_best_low": _safe_float((scan_best or {}).get("low")),
            "scan_best_high": _safe_float((scan_best or {}).get("high")),
            "scan_best_predicted_change_pct": scan_pred_change,
            "scan_best_hit": scan_hit,
            "author_available": author is not None,
            "author_target": author_target,
            "author_low": _safe_float((author or {}).get("low")),
            "author_high": _safe_float((author or {}).get("high")),
            "author_predicted_change_pct": author_pred_change,
            "author_weighted_hit": author_weighted_hit,
            "author_fixed10_hit": author_fixed_hit,
            "authors": (author or {}).get("authors") or [],
            "evidence_count": (author or {}).get("evidence_count"),
            "category": category,
            "author_delta_vs_current_pct": (
                author_pred_change - current_pred_change
                if author_pred_change is not None and current_pred_change is not None
                else None
            ),
            "actual_residual_vs_current_pct": (
                actual_change - current_pred_change
                if actual_change is not None and current_pred_change is not None
                else None
            ),
            "author_error_pct": (
                author_pred_change - actual_change
                if author_pred_change is not None and actual_change is not None
                else None
            ),
            **mood,
        }
        rows.append(row)
    rows.sort(key=lambda item: (str(item.get("listing_date") or ""), str(item.get("code") or "")))
    thresholds = _build_thresholds(rows)
    for row in rows:
        proxy_score, components, reasons = _score_proxy(row, thresholds)
        row["proxy_score"] = proxy_score
        row["proxy_components"] = components
        row["proxy_reasons"] = reasons
        row["model_uncertainty_score"] = components.get("model_uncertainty")
    _attach_rolling_proxy_estimates(rows, params)
    return rows


def _category_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for category, group in sorted(_group_by(rows, "category").items(), key=lambda item: item[0]):
        result.append(
            {
                "category": category,
                "count": len(group),
                "codes": [row["code"] for row in group],
                "avg_actual_change_pct": _mean([row["actual_change_pct"] for row in group if row.get("actual_change_pct") is not None]),
                "median_actual_change_pct": _median([row["actual_change_pct"] for row in group if row.get("actual_change_pct") is not None]),
                "avg_current_predicted_change_pct": _mean([row["current_predicted_change_pct"] for row in group if row.get("current_predicted_change_pct") is not None]),
                "avg_author_predicted_change_pct": _mean([row["author_predicted_change_pct"] for row in group if row.get("author_predicted_change_pct") is not None]),
                "avg_author_delta_vs_current_pct": _mean([row["author_delta_vs_current_pct"] for row in group if row.get("author_delta_vs_current_pct") is not None]),
                "avg_actual_residual_vs_current_pct": _mean([row["actual_residual_vs_current_pct"] for row in group if row.get("actual_residual_vs_current_pct") is not None]),
                "avg_proxy_score": _mean([row["proxy_score"] for row in group if row.get("proxy_score") is not None]),
                "median_float_market_cap_yi": _median([row["float_market_cap_yi"] for row in group if row.get("float_market_cap_yi") is not None]),
                "median_recent5_change_pct": _median([row["recent5_median_change"] for row in group if row.get("recent5_median_change") is not None]),
            }
        )
    return result


def _group_by(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        result[str(row.get(key) or "")].append(row)
    return result


def _feature_correlations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = [
        "issue_price",
        "float_market_cap_yi",
        "old_share_ratio",
        "after_issue_pe",
        "pe_to_industry",
        "recent5_median_change",
        "recent5_avg_change",
        "same_industry_recent_median",
        "top_apply_marketcap",
        "model_uncertainty_score",
        "proxy_score",
    ]
    result = []
    for field in fields:
        pairs_author = [(_safe_float(row.get(field)), _safe_float(row.get("author_delta_vs_current_pct"))) for row in rows]
        pairs_actual = [(_safe_float(row.get(field)), _safe_float(row.get("actual_residual_vs_current_pct"))) for row in rows]
        pairs_return = [(_safe_float(row.get(field)), _safe_float(row.get("actual_change_pct"))) for row in rows]
        author_corr = _spearman_pairs(pairs_author)
        actual_corr = _spearman_pairs(pairs_actual)
        return_corr = _spearman_pairs(pairs_return)
        result.append(
            {
                "feature": field,
                "label": FEATURE_LABELS.get(field, field),
                "spearman_vs_author_delta": author_corr,
                "spearman_vs_actual_residual": actual_corr,
                "spearman_vs_actual_change": return_corr,
                "alignment_score": (
                    abs(author_corr) + abs(actual_corr)
                    if author_corr is not None and actual_corr is not None and author_corr * actual_corr > 0
                    else 0
                ),
            }
        )
    result.sort(key=lambda item: item.get("alignment_score") or 0, reverse=True)
    return result


def _bucket_summary(rows: list[dict[str, Any]], score_key: str) -> dict[str, Any]:
    evaluated = [row for row in rows if _safe_float(row.get(score_key)) is not None and _safe_float(row.get("actual_change_pct")) is not None]
    if not evaluated:
        return {}
    ordered = sorted(evaluated, key=lambda row: _safe_float(row.get(score_key)) or 0.0)
    size = min(max(1, math.ceil(len(ordered) / 3)), len(ordered))
    low = ordered[:size]
    high = ordered[-size:]
    return {
        "score_key": score_key,
        "spearman_vs_actual_change": _spearman_pairs([
            (_safe_float(row.get(score_key)), _safe_float(row.get("actual_change_pct"))) for row in evaluated
        ]),
        "bucket_size": size,
        "low_codes": [row["code"] for row in low],
        "high_codes": [row["code"] for row in high],
        "low_avg_actual_change_pct": _mean([row["actual_change_pct"] for row in low if row.get("actual_change_pct") is not None]),
        "low_median_actual_change_pct": _median([row["actual_change_pct"] for row in low if row.get("actual_change_pct") is not None]),
        "high_avg_actual_change_pct": _mean([row["actual_change_pct"] for row in high if row.get("actual_change_pct") is not None]),
        "high_median_actual_change_pct": _median([row["actual_change_pct"] for row in high if row.get("actual_change_pct") is not None]),
    }


def _proxy_candidates() -> list[dict[str, Any]]:
    candidates = []
    for model_name in MODEL_NAMES:
        for step in PROXY_STEPS:
            for width in PROXY_WIDTHS:
                for fallback in (False, True):
                    candidates.append(
                        {
                            "name": f"{model_name}_proxy_step{step}_w{width:.2f}" + ("_fallback" if fallback else ""),
                            "kind": "proxy_fixed_width",
                            "model": model_name,
                            "step_pct": step,
                            "width": width,
                            "fallback": fallback,
                        }
                    )
            for fallback in (False, True):
                candidates.append(
                    {
                        "name": f"{model_name}_proxy_step{step}_layered" + ("_fallback" if fallback else ""),
                        "kind": "proxy_layered_width",
                        "model": model_name,
                        "step_pct": step,
                        "fallback": fallback,
                    }
                )
        for alpha in ROLLING_ALPHAS:
            for width in PROXY_WIDTHS:
                for fallback in (False, True):
                    candidates.append(
                        {
                            "name": f"{model_name}_rolling_proxy_a{alpha:.2f}_w{width:.2f}" + ("_fallback" if fallback else ""),
                            "kind": "rolling_proxy_fixed_width",
                            "model": model_name,
                            "alpha": alpha,
                            "width": width,
                            "fallback": fallback,
                        }
                    )
            for fallback in (False, True):
                candidates.append(
                    {
                        "name": f"{model_name}_rolling_proxy_a{alpha:.2f}_layered" + ("_fallback" if fallback else ""),
                        "kind": "rolling_proxy_layered_width",
                        "model": model_name,
                        "alpha": alpha,
                        "fallback": fallback,
                    }
                )
    return candidates


def _fallback_base_change(row: dict[str, Any], params: dict[str, Any]) -> float | None:
    for key in ("recent5_median_change", "recent3_median_change", "recent10_median_change"):
        value = _safe_float(row.get(key))
        if value is not None:
            return value
    return _safe_float(params.get("sentiment_first_day_baseline_pct"))


def _layered_width(row: dict[str, Any]) -> float:
    score = abs(_safe_float(row.get("proxy_score")) or 0.0)
    uncertainty = _safe_float(row.get("model_uncertainty_score")) or 0.0
    recent5 = _safe_float(row.get("recent5_median_change"))
    if not row.get("current_available") or uncertainty >= 2 or score >= 5:
        return 0.20
    if score >= 3 or (recent5 is not None and recent5 >= 150):
        return 0.15
    return 0.10


def _proxy_prediction(row: dict[str, Any], candidate: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    model = str(candidate.get("model") or "current_params")
    fallback = bool(candidate.get("fallback"))
    issue_price = _safe_float(row.get("issue_price"))
    if issue_price is None:
        return {"available": False, "reason": "issue price unavailable"}

    model_prefix = "current" if model == "current_params" else model
    model_available = bool(row.get(f"{model_prefix}_available"))
    base_change = _safe_float(row.get(f"{model_prefix}_predicted_change_pct"))
    source = model
    kind = str(candidate.get("kind") or "")
    score = _safe_float(row.get("proxy_score")) or 0.0
    if kind.startswith("rolling_proxy"):
        rolling_change = _safe_float(row.get("rolling_proxy_expected_change_pct"))
        if model_available and base_change is not None and rolling_change is not None:
            alpha = float(candidate.get("alpha") or 0.5)
            predicted_change = base_change * (1 - alpha) + rolling_change * alpha
            source = f"{model}+rolling_proxy"
        elif fallback and rolling_change is not None:
            predicted_change = rolling_change
            source = str(row.get("rolling_proxy_expected_source") or "rolling_proxy_fallback")
        elif model_available and base_change is not None:
            predicted_change = base_change
            source = f"{model}_only_no_proxy"
        else:
            return {"available": False, "reason": f"{model} and rolling proxy unavailable"}
    else:
        step_pct = float(candidate.get("step_pct") or 0.0)
        if base_change is None or not model_available:
            if not fallback:
                return {"available": False, "reason": f"{model} unavailable and fallback disabled"}
            base_change = _fallback_base_change(row, params)
            source = "proxy_recent_mood_fallback"
        if base_change is None:
            return {"available": False, "reason": "fallback base unavailable"}
        predicted_change = base_change + score * step_pct
    target = issue_price * (1 + predicted_change / 100)
    if candidate.get("kind") in {"proxy_layered_width", "rolling_proxy_layered_width"}:
        width = _layered_width(row)
    else:
        width = float(candidate.get("width") or 0.10)
    return {
        "available": True,
        "source": source,
        "target": target,
        "low": target * (1 - width),
        "high": target * (1 + width),
        "predicted_change_pct": predicted_change,
        "width": width,
        "proxy_score": score,
    }


def _evaluate_proxy_candidate(candidate: dict[str, Any], rows: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    result_rows = []
    hit_count = 0
    available_count = 0
    price_errors: list[float] = []
    change_errors: list[float] = []
    signed_errors: list[float] = []
    rank_pairs: list[tuple[float | None, float | None]] = []
    for row in rows:
        prediction = _proxy_prediction(row, candidate, params)
        actual_price = _safe_float(row.get("actual_price"))
        actual_change = _safe_float(row.get("actual_change_pct"))
        target = _safe_float(prediction.get("target"))
        low = _safe_float(prediction.get("low"))
        high = _safe_float(prediction.get("high"))
        predicted_change = _safe_float(prediction.get("predicted_change_pct"))
        available = bool(prediction.get("available") and target is not None and low is not None and high is not None)
        hit = None
        if available:
            available_count += 1
            if actual_price is not None:
                hit = min(low, high) <= actual_price <= max(low, high)
                hit_count += int(hit)
                price_errors.append(abs(target - actual_price))
            if predicted_change is not None and actual_change is not None:
                err = predicted_change - actual_change
                change_errors.append(abs(err))
                signed_errors.append(err)
                rank_pairs.append((predicted_change, actual_change))
        result_rows.append(
            {
                "code": row["code"],
                "name": row.get("name"),
                "listing_date": row.get("listing_date"),
                "actual_price": actual_price,
                "actual_change_pct": actual_change,
                "available": available,
                "reason": "" if available else prediction.get("reason"),
                "source": prediction.get("source"),
                "target_price": target,
                "predicted_change_pct": predicted_change,
                "range_low": low,
                "range_high": high,
                "interval_hit": hit,
                "proxy_score": row.get("proxy_score"),
                "proxy_reasons": row.get("proxy_reasons") or [],
                "category": row.get("category"),
            }
        )
    target_count = len(rows)
    return {
        "candidate": candidate,
        "target_count": target_count,
        "available_count": available_count,
        "unavailable_count": target_count - available_count,
        "hit_count": hit_count,
        "full_hit_rate": hit_count / target_count if target_count else None,
        "available_hit_rate": hit_count / available_count if available_count else None,
        "mae_target_price": _mean(price_errors),
        "mae_change_pct": _mean(change_errors),
        "mean_signed_change_error_pct": _mean(signed_errors),
        "spearman_predicted_vs_actual_change": _spearman_pairs(rank_pairs),
        "rows": result_rows,
        "hit_codes": [row["code"] for row in result_rows if row.get("interval_hit") is True],
        "miss_codes": [row["code"] for row in result_rows if row.get("interval_hit") is False],
        "unavailable_codes": [row["code"] for row in result_rows if not row.get("available")],
    }


def _candidate_sort_key(result: dict[str, Any]) -> tuple[float, float, float, float, str]:
    return (
        -(result.get("full_hit_rate") or 0.0),
        -(result.get("available_hit_rate") or 0.0),
        result.get("mae_change_pct") if result.get("mae_change_pct") is not None else 1e9,
        result.get("mae_target_price") if result.get("mae_target_price") is not None else 1e9,
        str((result.get("candidate") or {}).get("name") or ""),
    )


def _compact_result(result: dict[str, Any], include_rows: bool = False) -> dict[str, Any]:
    payload = {
        "candidate": result["candidate"],
        "target_count": result["target_count"],
        "available_count": result["available_count"],
        "unavailable_count": result["unavailable_count"],
        "hit_count": result["hit_count"],
        "full_hit_rate": result["full_hit_rate"],
        "available_hit_rate": result["available_hit_rate"],
        "mae_target_price": result["mae_target_price"],
        "mae_change_pct": result["mae_change_pct"],
        "mean_signed_change_error_pct": result["mean_signed_change_error_pct"],
        "spearman_predicted_vs_actual_change": result["spearman_predicted_vs_actual_change"],
        "hit_codes": result["hit_codes"],
        "miss_codes": result["miss_codes"],
        "unavailable_codes": result["unavailable_codes"],
    }
    if include_rows:
        payload["rows"] = result["rows"]
    return payload


def _build_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    best = payload["best_proxy_result"]
    lines = [
        "# 雪球作者逻辑蒸馏分析",
        "",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 目标样本：`{payload['target_universe']['name']}`，代码数 `{payload['target_universe']['count']}`",
        f"- author score 报告：`{payload['inputs']['author_score_report']}`",
        "",
        "## 核心结论",
        "",
        f"- 最佳 proxy 候选：`{best['candidate']['name']}`",
        f"- 最佳 proxy 命中：`{best['hit_count']}/{best['target_count']}`，全样本命中率 `{_fmt_rate(best['full_hit_rate'])}`，可用样本命中率 `{_fmt_rate(best['available_hit_rate'])}`",
        f"- 最佳 proxy MAE(涨幅)：`{_fmt_num(best.get('mae_change_pct'), 2)}`，Spearman：`{_fmt_num(best.get('spearman_predicted_vs_actual_change'), 3)}`",
        f"- proxy score 与实际涨幅 Spearman：`{_fmt_num((summary.get('proxy_bucket') or {}).get('spearman_vs_actual_change'), 3)}`",
        "",
        "## 基准对照",
        "",
        "| 方案 | 命中 | 全样本命中率 | 可用样本命中率 | 可用数 | MAE(涨幅) | Spearman |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summary.get("reference_results") or []:
        lines.append(
            "| {name} | {hit}/{target} | {full} | {avail_rate} | {available}/{target} | {mae} | {spear} |".format(
                name=item["candidate"]["name"],
                hit=item["hit_count"],
                target=item["target_count"],
                full=_fmt_rate(item.get("full_hit_rate")),
                avail_rate=_fmt_rate(item.get("available_hit_rate")),
                available=item["available_count"],
                mae=_fmt_num(item.get("mae_change_pct"), 2),
                spear=_fmt_num(item.get("spearman_predicted_vs_actual_change"), 3),
            )
        )
    lines.extend(
        [
            "",
            "## 作者增量归因",
            "",
            "| 类别 | 数量 | 代码 | 平均实际涨幅 | 平均作者-模型差 | 平均实际残差 | 平均proxy分 | 流通市值中位 | 近5中位涨幅 |",
            "|---|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary.get("category_summary") or []:
        lines.append(
            "| {category} | {count} | {codes} | {actual} | {delta} | {residual} | {proxy} | {float_cap} | {recent5} |".format(
                category=row["category"],
                count=row["count"],
                codes=", ".join(row.get("codes") or []),
                actual=_fmt_pct(row.get("avg_actual_change_pct")),
                delta=_fmt_pct(row.get("avg_author_delta_vs_current_pct")),
                residual=_fmt_pct(row.get("avg_actual_residual_vs_current_pct")),
                proxy=_fmt_num(row.get("avg_proxy_score"), 2),
                float_cap=_fmt_num(row.get("median_float_market_cap_yi"), 2),
                recent5=_fmt_pct(row.get("median_recent5_change_pct")),
            )
        )
    lines.extend(
        [
            "",
            "## 字段相关性",
            "",
            "| 字段 | vs 作者-模型差 | vs 实际残差 | vs 实际涨幅 | 对齐分 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for item in (summary.get("feature_correlations") or [])[:10]:
        lines.append(
            "| {label} | {author} | {actual_residual} | {actual} | {align} |".format(
                label=item["label"],
                author=_fmt_num(item.get("spearman_vs_author_delta"), 3),
                actual_residual=_fmt_num(item.get("spearman_vs_actual_residual"), 3),
                actual=_fmt_num(item.get("spearman_vs_actual_change"), 3),
                align=_fmt_num(item.get("alignment_score"), 3),
            )
        )
    bucket = summary.get("proxy_bucket") or {}
    lines.extend(
        [
            "",
            "## Proxy Score 分层",
            "",
            f"- 低分组平均/中位实际涨幅：`{_fmt_pct(bucket.get('low_avg_actual_change_pct'))}` / `{_fmt_pct(bucket.get('low_median_actual_change_pct'))}`",
            f"- 高分组平均/中位实际涨幅：`{_fmt_pct(bucket.get('high_avg_actual_change_pct'))}` / `{_fmt_pct(bucket.get('high_median_actual_change_pct'))}`",
            f"- 低分组代码：`{', '.join(bucket.get('low_codes') or [])}`",
            f"- 高分组代码：`{', '.join(bucket.get('high_codes') or [])}`",
            "",
            "## Top Proxy 候选",
            "",
            "| 排名 | 方案 | 命中 | 全样本命中率 | 可用命中率 | 可用数 | MAE(涨幅) | Spearman |",
            "|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for index, item in enumerate(payload.get("top_proxy_results") or [], start=1):
        lines.append(
            "| {rank} | `{name}` | {hit}/{target} | {full} | {avail_rate} | {available}/{target} | {mae} | {spear} |".format(
                rank=index,
                name=item["candidate"]["name"],
                hit=item["hit_count"],
                target=item["target_count"],
                full=_fmt_rate(item.get("full_hit_rate")),
                avail_rate=_fmt_rate(item.get("available_hit_rate")),
                available=item["available_count"],
                mae=_fmt_num(item.get("mae_change_pct"), 2),
                spear=_fmt_num(item.get("spearman_predicted_vs_actual_change"), 3),
            )
        )
    lines.extend(
        [
            "",
            "## 最佳 Proxy 逐样本",
            "",
            "| 代码 | 简称 | 类别 | 实际涨幅 | proxy分 | 预测涨幅 | 区间 | 命中 | 原因 |",
            "|---|---|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in best.get("rows") or []:
        hit = "" if row.get("interval_hit") is None else ("是" if row.get("interval_hit") else "否")
        lines.append(
            "| {code} | {name} | {category} | {actual} | {score} | {pred} | {low}-{high} | {hit} | {reasons} |".format(
                code=row.get("code"),
                name=row.get("name") or "",
                category=row.get("category") or "",
                actual=_fmt_pct(row.get("actual_change_pct")),
                score=_fmt_num(row.get("proxy_score"), 2),
                pred=_fmt_pct(row.get("predicted_change_pct")),
                low=_fmt_num(row.get("range_low")),
                high=_fmt_num(row.get("range_high")),
                hit=hit,
                reasons="、".join((row.get("proxy_reasons") or [])[:4]),
            )
        )
    lines.extend(
        [
            "",
            "## 口径",
            "",
            "- 作者目标价只作为 teacher label 用于归因和对照，不参与 proxy 预测输入。",
            "- proxy 特征只使用本地 replay、上市日前已知首日历史表现、模型可用性和调参输出。",
            "- 近端情绪只取目标上市日前已经上市的本地样本，避免使用未来样本。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset = param_tuning.load_replay_dataset(args.dataset)
    dataset_items = list(dataset.get("items") or [])
    by_code = _dataset_by_code(dataset)
    params = config_loader.load_params(args.params)
    scan_report = _read_json(Path(args.scan_report))
    author_report_path = Path(args.author_score_report) if args.author_score_report else _latest_author_score_report(Path(args.output_dir))
    author_payload = _read_json(author_report_path)
    author_predictions = blend._index_author_predictions(author_payload)
    target_codes = _target_codes(args.target, scan_report, by_code, author_predictions)

    best_overrides = dict(((scan_report.get("top_candidates") or [{}])[0]).get("overrides") or {})
    current_metrics = param_tuning.evaluate_replay_targets(dataset, params, target_codes=target_codes)
    best_params = dict(params)
    best_params.update(best_overrides)
    scan_best_metrics = param_tuning.evaluate_replay_targets(dataset, best_params, target_codes=target_codes)
    model_predictions = {
        "current_params": blend._index_model_predictions(current_metrics, "current_params"),
        "scan_best": blend._index_model_predictions(scan_best_metrics, "scan_best"),
    }
    teacher_rows = _build_teacher_rows(target_codes, dataset_items, by_code, model_predictions, author_predictions, params)

    reference_candidates = [
        {"name": "current_params", "kind": "model_only", "model": "current_params"},
        {"name": "scan_best", "kind": "model_only", "model": "scan_best"},
        {"name": "author_fixed10", "kind": "author_fixed10"},
        {"name": "author_weighted_interval", "kind": "author_weighted_interval"},
    ]
    reference_results = [
        blend._compact_result(blend._evaluate_candidate(candidate, target_codes, by_code, model_predictions, author_predictions))
        for candidate in reference_candidates
    ]

    proxy_results = [_evaluate_proxy_candidate(candidate, teacher_rows, params) for candidate in _proxy_candidates()]
    proxy_results.sort(key=_candidate_sort_key)
    best_proxy = proxy_results[0]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    payload = {
        "schema": "xueqiu_author_logic_distillation_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {
            "dataset": str(Path(args.dataset)),
            "params": str(Path(args.params)),
            "scan_report": str(Path(args.scan_report)),
            "author_score_report": str(author_report_path),
        },
        "target_universe": {
            "name": args.target,
            "count": len(target_codes),
            "codes": target_codes,
        },
        "summary": {
            "reference_results": reference_results,
            "category_summary": _category_summary(teacher_rows),
            "feature_correlations": _feature_correlations(teacher_rows),
            "proxy_bucket": _bucket_summary(teacher_rows, "proxy_score"),
        },
        "best_proxy_result": _compact_result(best_proxy, include_rows=True),
        "top_proxy_results": [_compact_result(item) for item in proxy_results[:20]],
        "teacher_rows": teacher_rows,
        "all_proxy_results": [_compact_result(item) for item in proxy_results],
    }
    output_dir = Path(args.output_dir)
    json_path = output_dir / f"xueqiu_author_logic_distillation_{args.target}_{timestamp}.json"
    md_path = output_dir / f"xueqiu_author_logic_distillation_{args.target}_{timestamp}.md"
    _write_json(json_path, payload)
    md_path.write_text(_build_markdown(payload), encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "markdown": str(md_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Distill Xueqiu author valuation improvements into local-only proxy features.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--params", default=str(DEFAULT_PARAMS))
    parser.add_argument("--scan-report", default=str(DEFAULT_SCAN_REPORT))
    parser.add_argument("--author-score-report", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--target", choices=["scan_sample", "author_scored", "all_actual"], default="all_actual")
    return parser


def main() -> None:
    payload = run(build_parser().parse_args())
    best = payload["best_proxy_result"]
    print(
        json.dumps(
            {
                "outputs": payload["outputs"],
                "target_universe": payload["target_universe"],
                "best_proxy": {
                    "candidate": best["candidate"],
                    "hit_count": best["hit_count"],
                    "target_count": best["target_count"],
                    "available_count": best["available_count"],
                    "full_hit_rate": best["full_hit_rate"],
                    "available_hit_rate": best["available_hit_rate"],
                    "mae_change_pct": best["mae_change_pct"],
                    "spearman": best["spearman_predicted_vs_actual_change"],
                },
                "proxy_spearman": (payload["summary"].get("proxy_bucket") or {}).get("spearman_vs_actual_change"),
                "category_summary": payload["summary"].get("category_summary"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
