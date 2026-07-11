from __future__ import annotations

import argparse
import importlib.util
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_INDEX = ROOT_DIR / "data" / "xueqiu_corpus" / "index.json"
DEFAULT_ARTICLE_DIR = ROOT_DIR / "data" / "xueqiu_corpus" / "articles"
DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_INTRADAY_DIR = ROOT_DIR / "首日分时走势"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"


_VALIDATION_SPEC = importlib.util.spec_from_file_location(
    "validate_xueqiu_author_ranges",
    ROOT_DIR / "tools" / "validate_xueqiu_author_ranges.py",
)
if _VALIDATION_SPEC is None or _VALIDATION_SPEC.loader is None:
    raise RuntimeError("Cannot load validate_xueqiu_author_ranges.py")
_validation = importlib.util.module_from_spec(_VALIDATION_SPEC)
_VALIDATION_SPEC.loader.exec_module(_validation)


AUTHOR_ORDER = ["兔子兔888", "条条道路通罗马Lee", "无房户小侯", "月半928"]
BLOCK_PATTERNS = ("访问验证", "滑动验证", "安全威胁", "请求ID", "TraceID", "当前网址")
POST_LISTING_TITLE_PATTERNS = re.compile(r"午评|收评|复盘|上市后|格局了|总结")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _safe_float(value: Any) -> float | None:
    return _validation._safe_float(value)


def _date_text(value: Any) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d")


def _load_dataset(path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    return _validation._load_dataset(path)


def _load_index_article_keys(index_path: Path) -> set[tuple[str, str]]:
    payload = _read_json(index_path)
    keys: set[tuple[str, str]] = set()
    for article in payload.get("articles") or []:
        keys.add((str(article.get("user_id") or ""), str(article.get("status_id") or "")))
    return keys


def _load_all_articles(index_path: Path, article_dir: Path) -> list[dict[str, Any]]:
    latest_index_keys = _load_index_article_keys(index_path)
    merged: dict[tuple[str, str], dict[str, Any]] = {}

    for article in _validation._load_articles(index_path):
        key = (str(article.get("user_id") or ""), str(article.get("status_id") or ""))
        article["in_latest_index"] = True
        article["record_source"] = "index"
        merged[key] = article

    for path in sorted(article_dir.glob("*.json")):
        try:
            article = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        key = (str(article.get("user_id") or ""), str(article.get("status_id") or ""))
        if not key[0] or not key[1]:
            continue
        if key in merged:
            continue
        article["in_latest_index"] = key in latest_index_keys
        article["record_source"] = "article_cache"
        article["file"] = str(path.relative_to(ROOT_DIR))
        merged[key] = article

    return list(merged.values())


def _is_blocked(article: dict[str, Any]) -> bool:
    text = f"{article.get('page_title') or ''}\n{article.get('text') or ''}"
    quality = article.get("quality") if isinstance(article.get("quality"), dict) else {}
    if quality.get("blocked_by_verification") is True:
        return True
    return any(pattern in text for pattern in BLOCK_PATTERNS)


def _is_readable_body(article: dict[str, Any]) -> bool:
    if _is_blocked(article):
        return False
    quality = article.get("quality") if isinstance(article.get("quality"), dict) else {}
    readable = bool(article.get("readable") or quality.get("readable"))
    text = str(article.get("text") or "")
    return readable and len(text.strip()) >= 200


def _classify_timing(article: dict[str, Any], listing_date: Any) -> str:
    created_at = (
        _validation._parse_dt(article.get("created_at_ms"))
        or _validation._parse_dt(article.get("created_at_iso"))
        or _validation._parse_dt(article.get("created_at_text"))
    )
    if created_at is None or listing_date is None:
        return "unknown"
    if created_at.date() < listing_date.date():
        return "pre_listing"
    if created_at.date() == listing_date.date():
        return "listing_day"
    return "after_listing"


def _is_prediction_like(article: dict[str, Any]) -> bool:
    title = str(article.get("title") or "")
    article_type = str(article.get("article_type") or "")
    if POST_LISTING_TITLE_PATTERNS.search(title):
        return False
    return article_type in {"listing_valuation", "listing_preview", "first_day_price_analysis", "other"}


def _article_summary(article: dict[str, Any], match: dict[str, Any], all_matches: list[dict[str, Any]], dataset_item: dict[str, Any], intraday_dir: Path) -> dict[str, Any]:
    listing_date, listing_date_source = _validation._resolve_listing_date(dataset_item, intraday_dir)
    price_range, extracted_ranges, single_prices = _validation._select_range_for_match(article, match, all_matches, dataset_item)
    text = str(article.get("text") or "")
    target_pe_mentions = _validation._extract_target_pe(text)
    readable_body = _is_readable_body(article)
    blocked = _is_blocked(article)
    forecast_kind = str(price_range.get("kind") or "") if price_range else ""
    range_is_derived = any(marker in forecast_kind for marker in ("implied", "fixed10", "lower_bound"))
    return {
        "author_name": article.get("author_name"),
        "user_id": article.get("user_id"),
        "status_id": article.get("status_id"),
        "title": article.get("title"),
        "url": article.get("url"),
        "created_at_text": article.get("created_at_text"),
        "created_at_iso": article.get("created_at_iso"),
        "article_type": article.get("article_type"),
        "record_source": article.get("record_source"),
        "in_latest_index": bool(article.get("in_latest_index")),
        "file": article.get("file"),
        "listing_date": _date_text(listing_date),
        "listing_date_source": listing_date_source,
        "timing": _classify_timing(article, listing_date),
        "prediction_like": _is_prediction_like(article),
        "readable_body": readable_body,
        "blocked": blocked,
        "text_length": len(text.strip()),
        "has_explicit_range": price_range is not None,
        "has_scorable_range": price_range is not None,
        "forecast_kind": forecast_kind,
        "range_is_derived": range_is_derived,
        "forecast_low": price_range.get("low") if price_range else None,
        "forecast_high": price_range.get("high") if price_range else None,
        "forecast_text": price_range.get("text") if price_range else "",
        "extracted_range_count": len(extracted_ranges),
        "single_price_count": len(single_prices),
        "target_pe_count": len(target_pe_mentions),
    }


def _status_for_evidence(evidence: list[dict[str, Any]]) -> str:
    pre = [item for item in evidence if item["timing"] == "pre_listing" and item["prediction_like"]]
    if any(item["readable_body"] for item in pre):
        return "captured_prelisting_readable"
    if pre:
        return "candidate_but_body_missing"
    if any(item["readable_body"] for item in evidence):
        return "only_non_prelisting_or_non_prediction"
    if evidence:
        return "only_blocked_or_thin_cache"
    return "not_found"


def _author_cells(evidence: list[dict[str, Any]]) -> dict[str, str]:
    by_author: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in evidence:
        by_author[str(item.get("author_name") or "")].append(item)

    cells: dict[str, str] = {}
    for author in AUTHOR_ORDER:
        items = by_author.get(author, [])
        if not items:
            cells[author] = "-"
            continue
        pre = [item for item in items if item["timing"] == "pre_listing" and item["prediction_like"]]
        if any(item["readable_body"] and item["has_explicit_range"] for item in pre):
            cells[author] = "R"
        elif any(item["readable_body"] for item in pre):
            cells[author] = "T"
        elif pre:
            cells[author] = "B"
        elif any(item["readable_body"] for item in items):
            cells[author] = "N"
        else:
            cells[author] = "b"
    return cells


def _build_rows(dataset_items: list[dict[str, Any]], by_code: dict[str, dict[str, Any]], articles: list[dict[str, Any]], intraday_dir: Path) -> list[dict[str, Any]]:
    evidence_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for article in articles:
        matches = _validation._find_local_matches(article, dataset_items)
        if not matches:
            continue
        for match in matches:
            code = match["code"]
            dataset_item = by_code.get(code)
            if not dataset_item:
                continue
            evidence_by_code[code].append(_article_summary(article, match, matches, dataset_item, intraday_dir))

    rows: list[dict[str, Any]] = []
    for item in dataset_items:
        code = str(item.get("SECURITY_CODE") or "")
        evidence = sorted(
            evidence_by_code.get(code, []),
            key=lambda row: (row.get("created_at_iso") or row.get("created_at_text") or "", row.get("author_name") or ""),
        )
        pre_readable = [
            ev for ev in evidence
            if ev["timing"] == "pre_listing" and ev["prediction_like"] and ev["readable_body"]
        ]
        pre_candidate = [
            ev for ev in evidence
            if ev["timing"] == "pre_listing" and ev["prediction_like"]
        ]
        explicit_range = [ev for ev in pre_readable if ev["has_explicit_range"]]
        cells = _author_cells(evidence)
        rows.append(
            {
                "code": code,
                "name": item.get("SECURITY_NAME_ABBR"),
                "listing_date": item.get("LISTING_DATE"),
                "issue_price": _safe_float(item.get("ISSUE_PRICE")),
                "status": _status_for_evidence(evidence),
                "author_cells": cells,
                "evidence_count": len(evidence),
                "prelisting_candidate_count": len(pre_candidate),
                "prelisting_readable_count": len(pre_readable),
                "explicit_range_count": len(explicit_range),
                "prelisting_readable_authors": sorted({str(ev["author_name"]) for ev in pre_readable}),
                "explicit_range_authors": sorted({str(ev["author_name"]) for ev in explicit_range}),
                "blocked_prelisting_candidates": [
                    ev for ev in pre_candidate if not ev["readable_body"]
                ],
                "evidence": evidence,
            }
        )

    return rows


def _summarize(rows: list[dict[str, Any]], articles: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        status_counts[row["status"]] += 1

    known_predicted = [
        row for row in rows
        if row["prelisting_candidate_count"] > 0
    ]
    captured_predicted = [
        row for row in known_predicted
        if row["prelisting_readable_count"] > 0
    ]
    missing_body = [
        row for row in known_predicted
        if row["prelisting_readable_count"] == 0
    ]
    explicit_range_rows = [row for row in rows if row["explicit_range_count"] > 0]

    author_known: dict[str, dict[str, Any]] = {}
    for author in AUTHOR_ORDER:
        known = [
            row for row in rows
            if row["author_cells"].get(author) in {"R", "T", "B"}
        ]
        readable = [
            row for row in rows
            if row["author_cells"].get(author) in {"R", "T"}
        ]
        explicit = [
            row for row in rows
            if row["author_cells"].get(author) == "R"
        ]
        blocked = [
            row for row in rows
            if row["author_cells"].get(author) == "B"
        ]
        author_known[author] = {
            "known_prelisting_prediction_codes": len(known),
            "readable_prelisting_prediction_codes": len(readable),
            "explicit_range_codes": len(explicit),
            "blocked_or_missing_body_codes": len(blocked),
            "blocked_or_missing_body": [f"{row['code']} {row['name']}" for row in blocked],
        }

    return {
        "local_sample_count": len(rows),
        "article_record_count_all_cache_and_index": len(articles),
        "status_counts": dict(status_counts),
        "known_prelisting_prediction_code_count": len(known_predicted),
        "captured_prelisting_readable_code_count": len(captured_predicted),
        "candidate_but_body_missing_code_count": len(missing_body),
        "candidate_but_body_missing": [f"{row['code']} {row['name']}" for row in missing_body],
        "explicit_range_code_count": len(explicit_range_rows),
        "not_found_codes": [f"{row['code']} {row['name']}" for row in rows if row["status"] == "not_found"],
        "only_non_prelisting_or_non_prediction_codes": [
            f"{row['code']} {row['name']}" for row in rows if row["status"] == "only_non_prelisting_or_non_prediction"
        ],
        "by_author": author_known,
    }


def _fmt_cell(value: str) -> str:
    return value or "-"


def _build_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# 雪球四作者本地样本覆盖审计",
        "",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 本地样本数：`{summary['local_sample_count']}`",
        f"- 当前语料/缓存文章记录：`{summary['article_record_count_all_cache_and_index']}`",
        f"- 已发现四作者上市前预测的本地样本：`{summary['known_prelisting_prediction_code_count']}`",
        f"- 其中已抓到上市前可读正文：`{summary['captured_prelisting_readable_code_count']}`",
        f"- 只有候选/验证页但正文缺失：`{summary['candidate_but_body_missing_code_count']}`",
        f"- 有可评分价格区间的本地样本：`{summary['explicit_range_code_count']}`",
        "",
        "## 结论",
        "",
    ]
    if summary["candidate_but_body_missing_code_count"] == 0:
        lines.append("- 对当前已发现的四作者上市前预测本地样本，正文已经全部抓到。")
    else:
        lines.append("- 仍有已发现预测但正文未抓到的本地样本：`{}`。".format("，".join(summary["candidate_but_body_missing"])))
    lines.extend(
        [
            "- 未在当前四作者语料和缓存中发现的本地样本，不等于作者一定没预测；只表示当前关键词/时间线采集未发现。",
            "",
            "## 标记说明",
            "",
            "- `R`：上市前可读正文，且有显式价格区间。",
            "- `T`：上市前可读正文，但未抽到显式价格区间。",
            "- `B`：上市前候选存在，但正文为验证页/阻断页或过薄缓存。",
            "- `N`：只找到非上市前或非预测类可读文章。",
            "- `b`：只找到阻断页/过薄缓存。",
            "- `-`：未发现该作者覆盖。",
            "",
            "## 覆盖矩阵",
            "",
            "| 代码 | 简称 | 状态 | 兔子兔888 | 罗马Lee | 小侯 | 月半928 | 可读预测 | 可评分区间 |",
            "|---|---|---|---|---|---|---|---:|---:|",
        ]
    )
    for row in payload["rows"]:
        cells = row["author_cells"]
        lines.append(
            "| {code} | {name} | {status} | {rabbit} | {lee} | {hou} | {yue} | {readable} | {ranges} |".format(
                code=row["code"],
                name=row["name"],
                status=row["status"],
                rabbit=_fmt_cell(cells.get("兔子兔888", "-")),
                lee=_fmt_cell(cells.get("条条道路通罗马Lee", "-")),
                hou=_fmt_cell(cells.get("无房户小侯", "-")),
                yue=_fmt_cell(cells.get("月半928", "-")),
                readable=row["prelisting_readable_count"],
                ranges=row["explicit_range_count"],
            )
        )

    lines.extend(["", "## 分作者", "", "| 作者 | 已发现预测代码 | 可读正文代码 | 可评分区间代码 | 正文缺失 |", "|---|---:|---:|---:|---|"])
    for author in AUTHOR_ORDER:
        item = summary["by_author"][author]
        lines.append(
            "| {author} | {known} | {readable} | {explicit} | {missing} |".format(
                author=author,
                known=item["known_prelisting_prediction_codes"],
                readable=item["readable_prelisting_prediction_codes"],
                explicit=item["explicit_range_codes"],
                missing="，".join(item["blocked_or_missing_body"]) or "-",
            )
        )

    lines.extend(
        [
            "",
            "## 未发现",
            "",
            "当前未在四作者语料/缓存中发现的本地样本：",
            "",
            "`{}`".format("，".join(summary["not_found_codes"]) or "-"),
            "",
        ]
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset_items, by_code = _load_dataset(Path(args.dataset))
    articles = _load_all_articles(Path(args.corpus_index), Path(args.article_dir))
    rows = _build_rows(dataset_items, by_code, articles, Path(args.intraday_dir))
    payload = {
        "schema": "xueqiu_local_sample_coverage_audit_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {
            "corpus_index": str(Path(args.corpus_index)),
            "article_dir": str(Path(args.article_dir)),
            "dataset": str(Path(args.dataset)),
            "intraday_dir": str(Path(args.intraday_dir)),
        },
        "summary": _summarize(rows, articles),
        "rows": rows,
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    json_path = output_dir / f"xueqiu_local_sample_coverage_{timestamp}.json"
    md_path = output_dir / f"xueqiu_local_sample_coverage_{timestamp}.md"
    _write_json(json_path, payload)
    md_path.write_text(_build_markdown(payload), encoding="utf-8")
    payload["outputs"] = {"json": str(json_path), "markdown": str(md_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit whether Xueqiu corpus covers local IPO replay samples.")
    parser.add_argument("--corpus-index", default=str(DEFAULT_CORPUS_INDEX))
    parser.add_argument("--article-dir", default=str(DEFAULT_ARTICLE_DIR))
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--intraday-dir", default=str(DEFAULT_INTRADAY_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> None:
    payload = run(build_parser().parse_args())
    print(json.dumps({"outputs": payload["outputs"], "summary": payload["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
