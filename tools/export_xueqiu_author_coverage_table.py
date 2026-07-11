from __future__ import annotations

import argparse
import csv
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_INDEX = ROOT_DIR / "data" / "xueqiu_corpus" / "index.json"
DEFAULT_ARTICLE_DIR = ROOT_DIR / "data" / "xueqiu_corpus" / "articles"
DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_INTRADAY_DIR = ROOT_DIR / "首日分时走势"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"

AUTHOR_ORDER = ["兔子兔888", "条条道路通罗马Lee", "无房户小侯", "月半928"]
CELL_LEGEND = {
    "R": "可读上市前预测且有可评分区间（显式或折算）",
    "T": "可读上市前预测但无可评分区间",
    "B": "有上市前预测候选但正文缺失/验证页",
    "N": "仅找到非上市前或非预测可读文章",
    "b": "仅阻断/薄缓存",
    "-": "未发现",
}
STATUS_LABELS = {
    "captured_prelisting_readable": "已抓到上市前可读预测",
    "candidate_but_body_missing": "有上市前候选但缺正文",
    "only_blocked_or_thin_cache": "仅阻断/薄缓存",
    "only_non_prelisting_or_non_prediction": "仅非上市前/非预测",
    "not_found": "未发现",
}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = _load_module("audit_xueqiu_local_sample_coverage", ROOT_DIR / "tools" / "audit_xueqiu_local_sample_coverage.py")


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _date_text(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] if text else ""


def _fmt_price(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{number:.2f}"


def _cell_detail(row: dict[str, Any], author: str) -> str:
    evs = [ev for ev in row.get("evidence") or [] if str(ev.get("author_name") or "") == author]
    if not evs:
        return ""
    parts: list[str] = []
    for ev in evs:
        timing = ev.get("timing") or ""
        title = ev.get("title") or ""
        status_id = ev.get("status_id") or ""
        created = ev.get("created_at_text") or ev.get("created_at_iso") or ""
        flags = []
        if ev.get("prediction_like"):
            flags.append("预测")
        if ev.get("readable_body"):
            flags.append("可读")
        if ev.get("has_explicit_range"):
            flags.append("区间")
            if ev.get("range_is_derived"):
                flags.append("折算")
        if ev.get("blocked"):
            flags.append("阻断")
        part = f"{timing}|{'/'.join(flags) or '-'}|{created}|{status_id}|{title}"
        parts.append(part)
    return "；".join(parts)


def _blocked_links(row: dict[str, Any]) -> str:
    links = []
    for ev in row.get("blocked_prelisting_candidates") or []:
        title = ev.get("title") or ""
        author = ev.get("author_name") or ""
        url = ev.get("url") or ""
        created = ev.get("created_at_text") or ev.get("created_at_iso") or ""
        links.append(f"{author}《{title}》{created} {url}".strip())
    return "；".join(links)


def _missing_hint(row: dict[str, Any]) -> str:
    cells = row.get("author_cells") or {}
    missing_authors = [author for author in AUTHOR_ORDER if cells.get(author) in {"-", "B", "b"}]
    if row.get("status") == "candidate_but_body_missing":
        return "优先补正文：" + _blocked_links(row)
    if row.get("status") == "only_blocked_or_thin_cache":
        return "检查是否存在上市前预测；当前只有阻断/薄缓存"
    if row.get("status") == "only_non_prelisting_or_non_prediction":
        return "检查是否存在上市前估值/前瞻；当前仅非预测证据"
    if row.get("status") == "not_found":
        return "四作者均未发现；建议按别名/上市前名称搜索"
    if missing_authors:
        return "未覆盖作者：" + "、".join(missing_authors)
    return ""


def _flatten_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for row in rows:
        cells = row.get("author_cells") or {}
        item = {
            "code": row.get("code"),
            "name": row.get("name"),
            "listing_date": _date_text(row.get("listing_date")),
            "issue_price": _fmt_price(row.get("issue_price")),
            "status": row.get("status"),
            "status_label": STATUS_LABELS.get(str(row.get("status") or ""), str(row.get("status") or "")),
            "prelisting_candidate_count": row.get("prelisting_candidate_count"),
            "prelisting_readable_count": row.get("prelisting_readable_count"),
            "explicit_range_count": row.get("explicit_range_count"),
            "readable_authors": "、".join(row.get("prelisting_readable_authors") or []),
            "explicit_range_authors": "、".join(row.get("explicit_range_authors") or []),
            "blocked_prelisting_candidates": _blocked_links(row),
            "missing_hint": _missing_hint(row),
        }
        for author in AUTHOR_ORDER:
            item[author] = cells.get(author, "-")
            item[f"{author}_detail"] = _cell_detail(row, author)
        flattened.append(item)
    return flattened


def _build_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# 雪球四作者本地样本覆盖表",
        "",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- 本地样本：`{summary['local_sample_count']}`",
        f"- 已发现上市前预测样本：`{summary['known_prelisting_prediction_code_count']}`",
        f"- 已抓到上市前可读正文：`{summary['captured_prelisting_readable_code_count']}`",
        f"- 有可评分区间样本：`{summary['explicit_range_code_count']}`",
        "",
        "## 单元格说明",
        "",
    ]
    for key, value in CELL_LEGEND.items():
        lines.append(f"- `{key}`：{value}")
    lines.extend(
        [
            "",
            "## 总表",
            "",
            "| 代码 | 简称 | 上市日 | 状态 | 兔子兔888 | 条条道路通罗马Lee | 无房户小侯 | 月半928 | 可读作者 | 区间作者 | 查漏提示 |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {code} | {name} | {listing_date} | {status} | {a1} | {a2} | {a3} | {a4} | {readable} | {range_authors} | {hint} |".format(
                code=row["code"],
                name=row["name"],
                listing_date=row["listing_date"],
                status=row["status_label"],
                a1=row["兔子兔888"],
                a2=row["条条道路通罗马Lee"],
                a3=row["无房户小侯"],
                a4=row["月半928"],
                readable=row["readable_authors"],
                range_authors=row["explicit_range_authors"],
                hint=(row["missing_hint"] or "").replace("|", "/"),
            )
        )
    lines.extend(
        [
            "",
            "## 缺正文/薄缓存重点",
            "",
            "| 代码 | 简称 | 状态 | 候选 |",
            "|---|---|---|---|",
        ]
    )
    focus = [
        row for row in payload["rows"]
        if row["status"] in {"candidate_but_body_missing", "only_blocked_or_thin_cache", "not_found", "only_non_prelisting_or_non_prediction"}
    ]
    for row in focus:
        lines.append(
            "| {code} | {name} | {status} | {hint} |".format(
                code=row["code"],
                name=row["name"],
                status=row["status_label"],
                hint=(row["missing_hint"] or "").replace("|", "/"),
            )
        )
    lines.extend(
        [
            "",
            "## 逐作者明细",
            "",
        ]
    )
    for author in AUTHOR_ORDER:
        lines.extend(
            [
                f"### {author}",
                "",
                "| 代码 | 简称 | 单元格 | 证据明细 |",
                "|---|---|---|---|",
            ]
        )
        for row in payload["rows"]:
            detail = row.get(f"{author}_detail") or ""
            if row.get(author) == "-" and not detail:
                continue
            lines.append(
                "| {code} | {name} | {cell} | {detail} |".format(
                    code=row["code"],
                    name=row["name"],
                    cell=row.get(author),
                    detail=detail.replace("|", "/"),
                )
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def export_table(args: argparse.Namespace) -> dict[str, Any]:
    dataset_items, by_code = audit._load_dataset(Path(args.dataset))
    articles = audit._load_all_articles(Path(args.corpus_index), Path(args.article_dir))
    rows = audit._build_rows(dataset_items, by_code, articles, Path(args.intraday_dir))
    summary = audit._summarize(rows, articles)
    flattened = _flatten_rows(rows)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    payload = {
        "schema": "xueqiu_author_coverage_table_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {
            "corpus_index": str(Path(args.corpus_index)),
            "article_dir": str(Path(args.article_dir)),
            "dataset": str(Path(args.dataset)),
            "intraday_dir": str(Path(args.intraday_dir)),
        },
        "authors": AUTHOR_ORDER,
        "legend": CELL_LEGEND,
        "summary": summary,
        "rows": flattened,
    }
    json_path = output_dir / f"xueqiu_author_coverage_table_{timestamp}.json"
    md_path = output_dir / f"xueqiu_author_coverage_table_{timestamp}.md"
    csv_path = output_dir / f"xueqiu_author_coverage_table_{timestamp}.csv"
    _write_json(json_path, payload)
    md_path.write_text(_build_markdown(payload), encoding="utf-8")
    fieldnames = [
        "code",
        "name",
        "listing_date",
        "issue_price",
        "status",
        "status_label",
        *AUTHOR_ORDER,
        "prelisting_candidate_count",
        "prelisting_readable_count",
        "explicit_range_count",
        "readable_authors",
        "explicit_range_authors",
        "blocked_prelisting_candidates",
        "missing_hint",
        *[f"{author}_detail" for author in AUTHOR_ORDER],
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(flattened)
    payload["outputs"] = {"json": str(json_path), "markdown": str(md_path), "csv": str(csv_path)}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export local sample coverage by Xueqiu author.")
    parser.add_argument("--corpus-index", default=str(DEFAULT_CORPUS_INDEX))
    parser.add_argument("--article-dir", default=str(DEFAULT_ARTICLE_DIR))
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--intraday-dir", default=str(DEFAULT_INTRADAY_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> None:
    payload = export_table(build_parser().parse_args())
    print(json.dumps({"outputs": payload["outputs"], "summary": payload["summary"], "legend": payload["legend"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
