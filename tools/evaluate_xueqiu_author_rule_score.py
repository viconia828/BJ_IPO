from __future__ import annotations

import argparse
import importlib.util
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_INDEX = ROOT_DIR / "data" / "xueqiu_corpus" / "index.json"
DEFAULT_ARTICLE_DIR = ROOT_DIR / "data" / "xueqiu_corpus" / "articles"
DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_SCAN_REPORT = ROOT_DIR / "调参" / "valuation_hit_rate_scan_202603plus_20260710_001437.json"
DEFAULT_INTRADAY_DIR = ROOT_DIR / "首日分时走势"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"

AUTHOR_WEIGHTS = {
    "兔子兔888": 1.0,
    "条条道路通罗马Lee": 0.9,
    "无房户小侯": 0.7,
    "月半928": 0.35,
}

POSITIVE_TERMS = (
    "小盘",
    "低价",
    "热点",
    "国产替代",
    "机器人",
    "半导体",
    "新能源",
    "临停",
    "超顶",
    "修复",
    "优质",
    "龙头",
)
NEGATIVE_TERMS = (
    "高价",
    "老股",
    "业绩下滑",
    "业绩承压",
    "增速放缓",
    "不受市场待见",
    "折价",
    "破发",
    "出口",
    "汇率",
    "客户集中",
    "周期",
    "风险",
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validation = _load_module("validate_xueqiu_author_ranges", ROOT_DIR / "tools" / "validate_xueqiu_author_ranges.py")
coverage = _load_module("audit_xueqiu_local_sample_coverage", ROOT_DIR / "tools" / "audit_xueqiu_local_sample_coverage.py")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _safe_float(value: Any) -> float | None:
    return validation._safe_float(value)


def _fmt_num(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:.{digits}f}"


def _fmt_pct(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:.1f}%"


def _calc_change_pct(issue_price: Any, price: Any) -> float | None:
    issue = _safe_float(issue_price)
    target = _safe_float(price)
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


def _spearman(rows: list[dict[str, Any]]) -> float | None:
    pairs = [
        (_safe_float(row.get("author_score_pct")), _safe_float(row.get("actual_average_change_pct")))
        for row in rows
    ]
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    xs = [x for x, _ in pairs]
    ys = [y for _, y in pairs]
    return _pearson(_rank(xs), _rank(ys))


def _count_terms(text: str, terms: tuple[str, ...]) -> int:
    return sum(1 for term in terms if term in text)


def _load_scan_context(path: Path) -> dict[str, Any]:
    return validation._load_scan_context(path)


def _article_text_window(article: dict[str, Any], match: dict[str, Any], matches: list[dict[str, Any]]) -> str:
    windows = validation._candidate_windows(article, match, matches)
    if windows:
        return windows[0]
    return validation._compact_text(f"{article.get('title') or ''}{article.get('text') or ''}")


def _evidence_from_articles(
    dataset_items: list[dict[str, Any]],
    by_code: dict[str, dict[str, Any]],
    articles: list[dict[str, Any]],
    intraday_dir: Path,
) -> dict[str, list[dict[str, Any]]]:
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for article in articles:
        matches = validation._find_local_matches(article, dataset_items)
        if not matches:
            continue
        for match in matches:
            code = match["code"]
            dataset_item = by_code.get(code)
            if not dataset_item:
                continue
            listing_date, listing_date_source = validation._resolve_listing_date(dataset_item, intraday_dir)
            timing = coverage._classify_timing(article, listing_date)
            if timing != "pre_listing":
                continue
            if not coverage._is_prediction_like(article):
                continue
            if not coverage._is_readable_body(article):
                continue
            price_range, all_ranges, single_prices = validation._select_range_for_match(article, match, matches, dataset_item)
            forecast_kind = str((price_range or {}).get("kind") or "")
            range_weight_multiplier = 1.0
            target_pe = validation._extract_target_pe(str(article.get("text") or ""))
            window = _article_text_window(article, match, matches)
            positive_count = _count_terms(window, POSITIVE_TERMS)
            negative_count = _count_terms(window, NEGATIVE_TERMS)
            evidence[code].append(
                {
                    "author_name": article.get("author_name"),
                    "user_id": article.get("user_id"),
                    "status_id": article.get("status_id"),
                    "title": article.get("title"),
                    "url": article.get("url"),
                    "created_at_text": article.get("created_at_text"),
                    "created_at_iso": article.get("created_at_iso"),
                    "listing_date": listing_date.strftime("%Y-%m-%d") if listing_date else "",
                    "listing_date_source": listing_date_source,
                    "weight": AUTHOR_WEIGHTS.get(str(article.get("author_name") or ""), 0.5) * range_weight_multiplier,
                    "author_base_weight": AUTHOR_WEIGHTS.get(str(article.get("author_name") or ""), 0.5),
                    "range_weight_multiplier": range_weight_multiplier,
                    "has_explicit_range": price_range is not None,
                    "forecast_kind": forecast_kind,
                    "forecast_low": price_range.get("low") if price_range else None,
                    "forecast_high": price_range.get("high") if price_range else None,
                    "forecast_mid": price_range.get("mid") if price_range else None,
                    "forecast_text": price_range.get("text") if price_range else "",
                    "single_prices": single_prices,
                    "target_pe_mentions": target_pe,
                    "positive_count": positive_count,
                    "negative_count": negative_count,
                    "net_phrase_score": positive_count - negative_count,
                }
            )
    return evidence


def _weighted_average(items: list[tuple[float, float]]) -> float | None:
    total_weight = sum(weight for _, weight in items if weight > 0)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in items if weight > 0) / total_weight


def _build_score_rows(
    dataset_items: list[dict[str, Any]],
    evidence_by_code: dict[str, list[dict[str, Any]]],
    intraday_dir: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in dataset_items:
        code = str(item.get("SECURITY_CODE") or "")
        issue_price = _safe_float(item.get("ISSUE_PRICE"))
        evidence = evidence_by_code.get(code, [])
        explicit = [ev for ev in evidence if ev.get("has_explicit_range") and _safe_float(ev.get("forecast_mid")) is not None]
        if not explicit or not issue_price:
            continue

        average_price, average_price_source, average_price_reason = validation._resolve_average_price(item, intraday_dir)
        if average_price is None:
            actual_change = None
        else:
            actual_change = _calc_change_pct(issue_price, average_price)

        low = _weighted_average([(_safe_float(ev["forecast_low"]) or 0.0, float(ev["weight"])) for ev in explicit])
        high = _weighted_average([(_safe_float(ev["forecast_high"]) or 0.0, float(ev["weight"])) for ev in explicit])
        mid = _weighted_average([(_safe_float(ev["forecast_mid"]) or 0.0, float(ev["weight"])) for ev in explicit])
        if low is None or high is None or mid is None:
            continue
        fixed_low = mid * 0.9
        fixed_high = mid * 1.1
        author_score_pct = _calc_change_pct(issue_price, mid)
        phrase_net = sum(float(ev["weight"]) * float(ev["net_phrase_score"]) for ev in evidence) / max(
            sum(float(ev["weight"]) for ev in evidence),
            1e-9,
        )
        score_with_phrases_pct = (author_score_pct or 0.0) + phrase_net * 5.0
        rows.append(
            {
                "code": code,
                "name": item.get("SECURITY_NAME_ABBR"),
                "listing_date": item.get("LISTING_DATE"),
                "issue_price": issue_price,
                "evidence_count": len(evidence),
                "explicit_evidence_count": len(explicit),
                "authors": sorted({str(ev["author_name"]) for ev in evidence}),
                "explicit_authors": sorted({str(ev["author_name"]) for ev in explicit}),
                "weighted_low": low,
                "weighted_high": high,
                "weighted_mid": mid,
                "fixed10_low": fixed_low,
                "fixed10_high": fixed_high,
                "author_score_pct": author_score_pct,
                "phrase_net_score": phrase_net,
                "score_with_phrases_pct": score_with_phrases_pct,
                "actual_average_price": average_price,
                "actual_average_price_source": average_price_source,
                "actual_average_price_reason": average_price_reason,
                "actual_average_change_pct": actual_change,
                "weighted_interval_hit": (low <= average_price <= high) if average_price is not None else None,
                "fixed10_interval_hit": (fixed_low <= average_price <= fixed_high) if average_price is not None else None,
                "abs_price_error_to_mid": abs(average_price - mid) if average_price is not None else None,
                "evidence": explicit,
            }
        )
    rows.sort(key=lambda row: (str(row.get("listing_date") or ""), row["code"]))
    return rows


def _interval_metrics(rows: list[dict[str, Any]], hit_key: str) -> dict[str, Any]:
    evaluated = [row for row in rows if row.get(hit_key) is not None]
    hits = [row for row in evaluated if row.get(hit_key)]
    errors = [_safe_float(row.get("abs_price_error_to_mid")) for row in evaluated]
    errors = [x for x in errors if x is not None]
    return {
        "eligible_count": len(rows),
        "evaluated_count": len(evaluated),
        "hit_count": len(hits),
        "hit_rate": len(hits) / len(evaluated) if evaluated else None,
        "price_mae_to_mid": _mean(errors),
        "hit_codes": [row["code"] for row in hits],
        "miss_codes": [row["code"] for row in evaluated if not row.get(hit_key)],
    }


def _bucket_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [
        row for row in rows
        if _safe_float(row.get("author_score_pct")) is not None and _safe_float(row.get("actual_average_change_pct")) is not None
    ]
    if not evaluated:
        return {}
    ordered = sorted(evaluated, key=lambda row: _safe_float(row["author_score_pct"]) or 0.0)
    n = len(ordered)
    bucket_size = max(1, math.ceil(n / 3))
    low_bucket = ordered[:bucket_size]
    high_bucket = ordered[-bucket_size:]
    return {
        "spearman_score_vs_actual_return": _spearman(evaluated),
        "low_bucket_count": len(low_bucket),
        "low_bucket_avg_actual_return_pct": _mean([row["actual_average_change_pct"] for row in low_bucket]),
        "low_bucket_median_actual_return_pct": _median([row["actual_average_change_pct"] for row in low_bucket]),
        "high_bucket_count": len(high_bucket),
        "high_bucket_avg_actual_return_pct": _mean([row["actual_average_change_pct"] for row in high_bucket]),
        "high_bucket_median_actual_return_pct": _median([row["actual_average_change_pct"] for row in high_bucket]),
        "low_bucket_codes": [row["code"] for row in low_bucket],
        "high_bucket_codes": [row["code"] for row in high_bucket],
    }


def _baseline_overlap(rows: list[dict[str, Any]], scan_context: dict[str, Any]) -> dict[str, Any]:
    sample_codes = set(scan_context.get("sample_codes") or [])
    baseline_hits = set(scan_context.get("baseline_hit_codes") or [])
    best_hits = set(scan_context.get("best_hit_codes") or [])
    overlap = [row for row in rows if row["code"] in sample_codes and row.get("fixed10_interval_hit") is not None]
    codes = [row["code"] for row in overlap]
    author_fixed_hits = [row for row in overlap if row.get("fixed10_interval_hit")]
    author_weighted_hits = [row for row in overlap if row.get("weighted_interval_hit")]
    return {
        "overlap_count": len(codes),
        "codes": codes,
        "author_fixed10_hit_count": len(author_fixed_hits),
        "author_fixed10_hit_rate": len(author_fixed_hits) / len(codes) if codes else None,
        "author_weighted_interval_hit_count": len(author_weighted_hits),
        "author_weighted_interval_hit_rate": len(author_weighted_hits) / len(codes) if codes else None,
        "baseline_hit_count": len([code for code in codes if code in baseline_hits]),
        "baseline_hit_rate": len([code for code in codes if code in baseline_hits]) / len(codes) if codes else None,
        "best_scan_hit_count": len([code for code in codes if code in best_hits]),
        "best_scan_hit_rate": len([code for code in codes if code in best_hits]) / len(codes) if codes else None,
    }


def _summarize(rows: list[dict[str, Any]], scan_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "score_code_count": len(rows),
        "weighted_author_interval": _interval_metrics(rows, "weighted_interval_hit"),
        "fixed10_author_mid_interval": _interval_metrics(rows, "fixed10_interval_hit"),
        "rank_buckets": _bucket_metrics(rows),
        "baseline_overlap": _baseline_overlap(rows, scan_context),
        "by_author_presence": {
            author: len([row for row in rows if author in row.get("authors", [])])
            for author in AUTHOR_WEIGHTS
        },
    }


def _build_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    weighted = summary["weighted_author_interval"]
    fixed = summary["fixed10_author_mid_interval"]
    overlap = summary["baseline_overlap"]
    buckets = summary["rank_buckets"]
    lines = [
        "# 雪球 Author-Rule Score 首版验证",
        "",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 可评分本地代码：`{summary['score_code_count']}`",
        f"- 作者原始加权区间命中：`{weighted['hit_count']}/{weighted['evaluated_count']}`，命中率 `{_fmt_pct((weighted['hit_rate'] or 0) * 100)}`",
        f"- 作者中枢 ±10% 命中：`{fixed['hit_count']}/{fixed['evaluated_count']}`，命中率 `{_fmt_pct((fixed['hit_rate'] or 0) * 100)}`",
        f"- 同代码作者中枢 ±10% 命中：`{overlap['author_fixed10_hit_count']}/{overlap['overlap_count']}`，命中率 `{_fmt_pct((overlap['author_fixed10_hit_rate'] or 0) * 100)}`",
        f"- 同代码 baseline 命中：`{overlap['baseline_hit_count']}/{overlap['overlap_count']}`，命中率 `{_fmt_pct((overlap['baseline_hit_rate'] or 0) * 100)}`",
        f"- 同代码 2026-07-10 扫描最优命中：`{overlap['best_scan_hit_count']}/{overlap['overlap_count']}`，命中率 `{_fmt_pct((overlap['best_scan_hit_rate'] or 0) * 100)}`",
        "",
        "## 排名观察",
        "",
        f"- score 与实际首日均价涨幅 Spearman：`{_fmt_num(buckets.get('spearman_score_vs_actual_return'), 3)}`",
        f"- 低分组平均/中位实际涨幅：`{_fmt_pct(buckets.get('low_bucket_avg_actual_return_pct'))}` / `{_fmt_pct(buckets.get('low_bucket_median_actual_return_pct'))}`",
        f"- 高分组平均/中位实际涨幅：`{_fmt_pct(buckets.get('high_bucket_avg_actual_return_pct'))}` / `{_fmt_pct(buckets.get('high_bucket_median_actual_return_pct'))}`",
        f"- 低分组代码：`{', '.join(buckets.get('low_bucket_codes') or [])}`",
        f"- 高分组代码：`{', '.join(buckets.get('high_bucket_codes') or [])}`",
        "",
        "## 明细",
        "",
        "| 代码 | 简称 | 作者 | 发行价 | 作者中枢 | 作者区间 | ±10%命中 | 加权区间命中 | 实际均价 | 实际涨幅 | score |",
        "|---|---|---|---:|---:|---:|---|---|---:|---:|---:|",
    ]
    for row in payload["rows"]:
        lines.append(
            "| {code} | {name} | {authors} | {issue} | {mid} | {low}-{high} | {fixed_hit} | {weighted_hit} | {actual} | {actual_chg} | {score} |".format(
                code=row["code"],
                name=row["name"],
                authors="、".join(row.get("explicit_authors") or []),
                issue=_fmt_num(row.get("issue_price")),
                mid=_fmt_num(row.get("weighted_mid")),
                low=_fmt_num(row.get("weighted_low")),
                high=_fmt_num(row.get("weighted_high")),
                fixed_hit="是" if row.get("fixed10_interval_hit") else "否",
                weighted_hit="是" if row.get("weighted_interval_hit") else "否",
                actual=_fmt_num(row.get("actual_average_price")),
                actual_chg=_fmt_pct(row.get("actual_average_change_pct")),
                score=_fmt_pct(row.get("author_score_pct")),
            )
        )
    lines.extend(
        [
            "",
            "## 口径",
            "",
            "- 仅使用上市前、预测类、正文可读且能抽到显式价格区间的作者证据。",
            "- 作者权重：兔子兔888 1.0，条条道路通罗马Lee 0.9，无房户小侯 0.7，月半928 0.35。",
            "- `作者原始加权区间` 使用作者 low/high 的加权平均；`作者中枢 ±10%` 与现有固定宽度评估更可比。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset_items, by_code = validation._load_dataset(Path(args.dataset))
    articles = coverage._load_all_articles(Path(args.corpus_index), Path(args.article_dir))
    evidence_by_code = _evidence_from_articles(dataset_items, by_code, articles, Path(args.intraday_dir))
    rows = _build_score_rows(dataset_items, evidence_by_code, Path(args.intraday_dir))
    scan_context = _load_scan_context(Path(args.scan_report))
    payload = {
        "schema": "xueqiu_author_rule_score_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {
            "corpus_index": str(Path(args.corpus_index)),
            "article_dir": str(Path(args.article_dir)),
            "dataset": str(Path(args.dataset)),
            "scan_report": str(Path(args.scan_report)),
            "intraday_dir": str(Path(args.intraday_dir)),
        },
        "author_weights": AUTHOR_WEIGHTS,
        "summary": _summarize(rows, scan_context),
        "rows": rows,
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    json_path = output_dir / f"xueqiu_author_rule_score_{timestamp}.json"
    md_path = output_dir / f"xueqiu_author_rule_score_{timestamp}.md"
    _write_json(json_path, payload)
    md_path.write_text(_build_markdown(payload), encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "markdown": str(md_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate first-pass Xueqiu author-rule score.")
    parser.add_argument("--corpus-index", default=str(DEFAULT_CORPUS_INDEX))
    parser.add_argument("--article-dir", default=str(DEFAULT_ARTICLE_DIR))
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--scan-report", default=str(DEFAULT_SCAN_REPORT))
    parser.add_argument("--intraday-dir", default=str(DEFAULT_INTRADAY_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> None:
    payload = run(build_parser().parse_args())
    print(json.dumps({"outputs": payload["outputs"], "summary": payload["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
