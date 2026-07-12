from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Any


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import pdf_parser


DEFAULT_INPUT_DIR = ROOT_DIR / "公告文件"
DEFAULT_JSON = ROOT_DIR / "outputs" / "comparable_parser_coverage_audit_latest.json"
DEFAULT_MARKDOWN = ROOT_DIR / "outputs" / "comparable_parser_coverage_audit_latest.md"
VALID_CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ|BJ|NQ)$", re.IGNORECASE)
SELECTION_SENTENCE_PATTERN = re.compile(
    r"[^。；\n]{0,100}(?:选取|选择|确定)[^。；\n]{0,260}(?:作为|确定为)[^。；\n]{0,30}(?:同行业)?可比公司[^。；\n]{0,80}",
)
REVIEW_DISPOSITIONS = {
    "920177": {
        "disposition": "confirmed_no_comparable_companies",
        "note": "人工复核招股说明书：正文明确无可比公司，空结果符合原文。",
    },
    "920186": {
        "disposition": "confirmed_no_comparable_companies",
        "note": "人工复核招股说明书：正文明确无可比公司，空结果符合原文。",
    },
    **{
        code: {
            "disposition": "reviewed_audit_false_positive",
            "note": "人工复核确认当前解析结果可接受，审计启发式标记为误报。",
        }
        for code in (
            "920011",
            "920028",
            "920076",
            "920078",
            "920083",
            "920117",
            "920126",
            "920176",
            "920183",
            "920191",
            "920200",
        )
    },
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="审计本地招股文件可比公司解析覆盖与异常代码。")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--codes", help="仅审计指定代码，逗号分隔。")
    parser.add_argument("--workers", type=int, default=1, help="PDF 并行读取进程数，默认 1。")
    return parser.parse_args()


def _resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT_DIR / path


def _normalize_snippet(text: str, limit: int = 520) -> str:
    compact = re.sub(r"\s+", "", str(text or "")).strip()
    return compact[:limit]


def _document_kind(path: Path) -> str:
    if "招股意向书" in path.stem:
        return "intent"
    return "prospectus"


def _selection_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for match in SELECTION_SENTENCE_PATTERN.finditer(text):
        snippet = _normalize_snippet(match.group(0))
        if snippet and snippet not in sentences:
            sentences.append(snippet)
    return sentences[:8]


def _specific_section(text: str) -> tuple[str, str]:
    normalized = pdf_parser._normalize_text(text)
    section, anchor = pdf_parser._extract_compact_section(
        normalized,
        pdf_parser.SPECIFIC_SECTION_PATTERNS,
        pdf_parser.COMPARABLE_SECTION_STOP_PATTERNS,
        chapter_anchors=pdf_parser.PROSPECTUS_BUSINESS_CHAPTER_PATTERNS,
        fallback_radius=6000,
    )
    if section:
        return section, anchor
    return pdf_parser._extract_compact_section(
        normalized,
        pdf_parser.GENERIC_SECTION_PATTERNS,
        pdf_parser.COMPARABLE_SECTION_STOP_PATTERNS,
        chapter_anchors=pdf_parser.PROSPECTUS_BUSINESS_CHAPTER_PATTERNS,
        fallback_radius=6000,
    )


def _audit_file(path: Path) -> dict[str, Any]:
    text = pdf_parser._read_pdf_text(path)
    normalized_text = pdf_parser._normalize_text(text)
    section, section_anchor = _specific_section(text)
    parsed_codes = pdf_parser.extract_comparable_companies(path)
    named_comparables = pdf_parser._extract_named_comparables(section or normalized_text)
    resolved_names: dict[str, str] = {}
    unresolved_names: list[str] = []
    for name in named_comparables:
        code = pdf_parser._search_direct_or_known_code_for_name(normalized_text, name)
        if code:
            resolved_names[name] = code
        else:
            unresolved_names.append(name)

    section_pairs = pdf_parser._collect_name_code_pairs(section)
    section_codes = []
    for _, code in section_pairs:
        if code not in section_codes and code.split(".", 1)[0] != path.stem[:6]:
            section_codes.append(code)

    flags: list[str] = []
    invalid_codes = [code for code in parsed_codes if not VALID_CODE_PATTERN.fullmatch(str(code))]
    if invalid_codes:
        flags.append("invalid_parsed_code")
    if not parsed_codes and ("可比公司" in normalized_text or "可比企业" in normalized_text):
        flags.append("zero_with_comparable_markers")
    if 0 < len(parsed_codes) <= 2:
        flags.append("low_parsed_count_review")
    if unresolved_names:
        flags.append("unresolved_explicit_names")
    independent_missing = [code for code in section_codes if code not in parsed_codes]
    if independent_missing:
        flags.append("section_codes_not_in_parser_result")

    return {
        "file_name": path.name,
        "path": str(path),
        "code": path.stem[:6],
        "document_kind": _document_kind(path),
        "parsed_codes": parsed_codes,
        "parsed_count": len(parsed_codes),
        "invalid_codes": invalid_codes,
        "section_anchor": section_anchor,
        "section_codes": section_codes,
        "section_codes_not_in_parser_result": independent_missing,
        "named_comparables": named_comparables,
        "resolved_names": resolved_names,
        "unresolved_names": unresolved_names,
        "selection_sentences": _selection_sentences(normalized_text),
        "section_snippet": _normalize_snippet(section, limit=1200),
        "flags": flags,
    }


def _build_code_groups(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in files:
        grouped.setdefault(str(item["code"]), []).append(item)

    rows: list[dict[str, Any]] = []
    for code, documents in sorted(grouped.items()):
        result_sets = {tuple(item["parsed_codes"]) for item in documents}
        flags = sorted({flag for item in documents for flag in item["flags"]})
        if len(result_sets) > 1:
            flags.append("document_stage_result_mismatch")
        raw_flags = sorted(set(flags))
        review = dict(REVIEW_DISPOSITIONS.get(code) or {})
        rows.append(
            {
                "code": code,
                "document_count": len(documents),
                "documents": [item["file_name"] for item in documents],
                "parsed_results": {item["document_kind"]: item["parsed_codes"] for item in documents},
                "flags": raw_flags,
                "unresolved_flags": [] if review else raw_flags,
                "review_disposition": review.get("disposition", ""),
                "review_note": review.get("note", ""),
            }
        )
    return rows


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# 本地招股文件可比公司解析完整性审计",
        "",
        f"生成时间：{payload['generated_at']}",
        "",
        "## 汇总",
        "",
        f"- 招股文件：{summary['file_count']} 份。",
        f"- 股票代码：{summary['code_count']} 个。",
        f"- 当前零结果文件：{summary['zero_result_file_count']} 份。",
        f"- 异常格式代码文件：{summary['invalid_code_file_count']} 份。",
        f"- 启发式原始标记：{summary['raw_flagged_code_count']} 个。",
        f"- 已人工关闭：{summary['reviewed_code_count']} 个。",
        f"- 尚待人工复核：{summary['flagged_code_count']} 个。",
        f"- 确认原文无可比公司：{summary['confirmed_no_comparable_count']} 个。",
        "",
        "## 逐代码",
        "",
        "| 代码 | 文件数 | 解析结果 | 原始标记 | 人工处置 | 未关闭标记 |",
        "|---|---:|---|---|---|---|",
    ]
    for item in payload["codes"]:
        rendered_results = "；".join(
            f"{kind}:{','.join(codes) if codes else '空'}"
            for kind, codes in item["parsed_results"].items()
        )
        flags = ", ".join(item["flags"]) or "-"
        unresolved = ", ".join(item["unresolved_flags"]) or "-"
        disposition = item["review_disposition"] or "-"
        lines.append(f"| {item['code']} | {item['document_count']} | {rendered_results} | {flags} | {disposition} | {unresolved} |")

    lines.extend(["", "## 标记文件证据", ""])
    for item in payload["files"]:
        if not item["flags"]:
            continue
        lines.extend(
            [
                f"### {item['file_name']}",
                "",
                f"- 当前结果：`{item['parsed_codes']}`",
                f"- 章节独立代码：`{item['section_codes']}`",
                f"- 未解析显式名称：`{item['unresolved_names']}`",
                f"- 标记：`{item['flags']}`",
            ]
        )
        if item["selection_sentences"]:
            lines.append("- 选取句：" + "；".join(item["selection_sentences"]))
        if item["section_snippet"]:
            lines.append("- 章节片段：" + item["section_snippet"])
        lines.append("")
    lines.extend(["", "## 人工复核处置", ""])
    for item in payload["codes"]:
        if not item["review_disposition"]:
            continue
        lines.append(
            f"- {item['code']}：`{item['review_disposition']}`；{item['review_note']}"
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = _parse_args()
    input_dir = _resolve_path(args.input_dir)
    selected_codes = {item.strip() for item in str(args.codes or "").split(",") if item.strip()}
    paths = sorted(
        path
        for path in input_dir.glob("*.pdf")
        if ("招股说明书" in path.stem or "招股意向书" in path.stem)
        and (not selected_codes or path.stem[:6] in selected_codes)
    )
    worker_count = max(int(args.workers), 1)
    if worker_count > 1 and len(paths) > 1:
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            files = list(executor.map(_audit_file, paths))
    else:
        files = [_audit_file(path) for path in paths]
    codes = _build_code_groups(files)
    reviewed_codes = [item for item in codes if item["review_disposition"]]
    payload = {
        "schema": "comparable_parser_coverage_audit_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "file_count": len(files),
            "code_count": len(codes),
            "zero_result_file_count": sum(1 for item in files if not item["parsed_codes"]),
            "invalid_code_file_count": sum(1 for item in files if item["invalid_codes"]),
            "raw_flagged_code_count": sum(1 for item in codes if item["flags"]),
            "reviewed_code_count": len(reviewed_codes),
            "flagged_code_count": sum(1 for item in codes if item["unresolved_flags"]),
            "confirmed_no_comparable_count": sum(
                item["review_disposition"] == "confirmed_no_comparable_companies"
                for item in codes
            ),
        },
        "codes": codes,
        "files": files,
    }
    json_path = _resolve_path(args.json_output)
    markdown_path = _resolve_path(args.markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_render_markdown(payload), encoding="utf-8")
    print(json.dumps({"summary": payload["summary"], "json": str(json_path), "markdown": str(markdown_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
