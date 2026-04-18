from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import re
import sys

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tools" else CURRENT_DIR
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import pdf_parser


DEFAULT_INPUT_DIR = ROOT_DIR / "公告文件"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "输出"
STOCK_CODE_PATTERN = re.compile(r"(?P<code>\d{6})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="批量扫描公告文件 PDF，输出 old_shares / comparables / business_desc 结果。"
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="待扫描 PDF 目录，默认是公告文件目录。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="输出 JSON 路径；不传则只打印汇总。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="最多扫描多少个 PDF，便于先做小样本验证。",
    )
    parser.add_argument(
        "--contains",
        type=str,
        default=None,
        help="只扫描文件名包含指定文本的 PDF。",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="逐个打印样本结果摘要。",
    )
    return parser.parse_args()


def resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else (ROOT_DIR / path)


def iter_pdf_files(input_dir: Path, contains: str | None = None) -> list[Path]:
    pdf_files = sorted(input_dir.rglob("*.pdf"), key=lambda item: item.name)
    if contains:
        pdf_files = [path for path in pdf_files if contains in path.name]
    return pdf_files


def infer_stock_code(file_name: str) -> str:
    match = STOCK_CODE_PATTERN.search(file_name)
    return match.group("code") if match else ""


def infer_document_type(file_name: str) -> str:
    if "上市公告书" in file_name:
        return "上市公告书"
    if "招股说明书" in file_name:
        return "招股说明书"
    if "招股意向书" in file_name:
        return "招股意向书"
    return "未知"


def serialize_old_shares(pdf_path: Path) -> tuple[dict[str, object], str | None]:
    try:
        result = pdf_parser.extract_old_shares_result(pdf_path)
    except Exception as exc:  # pragma: no cover - scanning script should keep going
        return {"has_result": False}, f"old_shares: {type(exc).__name__}: {exc}"

    if result is None:
        return {"has_result": False}, None

    payload = asdict(result)
    payload["has_result"] = True
    return payload, None


def serialize_comparables(pdf_path: Path) -> tuple[dict[str, object], str | None]:
    try:
        codes = pdf_parser.extract_comparable_companies(pdf_path)
    except Exception as exc:  # pragma: no cover - scanning script should keep going
        return {"has_result": False, "codes": [], "count": 0}, f"comparables: {type(exc).__name__}: {exc}"

    return {
        "has_result": bool(codes),
        "codes": codes,
        "count": len(codes),
    }, None


def serialize_business_desc(pdf_path: Path) -> tuple[dict[str, object], str | None]:
    try:
        text = pdf_parser.extract_business_desc(pdf_path)
    except Exception as exc:  # pragma: no cover - scanning script should keep going
        return {"has_result": False, "text": "", "length": 0}, f"business_desc: {type(exc).__name__}: {exc}"

    return {
        "has_result": bool(text),
        "text": text,
        "length": len(text),
    }, None


def build_record(pdf_path: Path) -> dict[str, object]:
    old_shares, old_shares_error = serialize_old_shares(pdf_path)
    comparables, comparables_error = serialize_comparables(pdf_path)
    business_desc, business_desc_error = serialize_business_desc(pdf_path)

    errors = [
        error
        for error in (old_shares_error, comparables_error, business_desc_error)
        if error
    ]

    return {
        "stock_code": infer_stock_code(pdf_path.name),
        "document_type": infer_document_type(pdf_path.name),
        "file_name": pdf_path.name,
        "relative_path": str(pdf_path.relative_to(ROOT_DIR)),
        "old_shares": old_shares,
        "comparables": comparables,
        "business_desc": business_desc,
        "errors": errors,
    }


def summarize(records: list[dict[str, object]]) -> dict[str, int]:
    return {
        "total_files": len(records),
        "old_shares_hits": sum(1 for item in records if item["old_shares"]["has_result"]),
        "comparables_hits": sum(1 for item in records if item["comparables"]["has_result"]),
        "business_desc_hits": sum(1 for item in records if item["business_desc"]["has_result"]),
        "error_files": sum(1 for item in records if item["errors"]),
    }


def print_record(record: dict[str, object]) -> None:
    old_flag = "Y" if record["old_shares"]["has_result"] else "N"
    comparable_count = record["comparables"]["count"]
    business_flag = "Y" if record["business_desc"]["has_result"] else "N"
    error_count = len(record["errors"])
    print(
        f"{record['file_name']} | old_shares={old_flag} | comparables={comparable_count} | "
        f"business_desc={business_flag} | errors={error_count}"
    )


def write_output(output_path: Path, records: list[dict[str, object]], summary: dict[str, int]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": summary,
        "records": records,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    input_dir = resolve_path(args.input_dir)
    if not input_dir.exists():
        print(f"输入目录不存在: {input_dir}")
        return 1

    pdf_files = iter_pdf_files(input_dir, args.contains)
    if args.limit is not None:
        pdf_files = pdf_files[: args.limit]

    if not pdf_files:
        print("没有匹配到待扫描的 PDF。")
        return 1

    records = [build_record(pdf_path) for pdf_path in pdf_files]
    summary = summarize(records)

    if args.verbose:
        for record in records:
            print_record(record)

    print(
        "Scan summary: total={total_files}, old_shares_hits={old_shares_hits}, "
        "comparables_hits={comparables_hits}, business_desc_hits={business_desc_hits}, "
        "error_files={error_files}".format(**summary)
    )

    if args.output:
        output_path = resolve_path(args.output)
        write_output(output_path, records, summary)
        print(f"JSON written to: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
