from __future__ import annotations

import argparse
from pathlib import Path
import sys

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tools" else CURRENT_DIR
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from bse_official_helper import BSEOfficialClient, BSEOfficialError


DEFAULT_OUTPUT_DIR = ROOT_DIR / "公告文件"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按北交所官网映射链下载招股说明书 PDF。",
    )
    parser.add_argument(
        "code",
        help="上市后 6 位代码，例如 920177。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="PDF 输出目录，默认写入 公告文件/。",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="若目标文件已存在则覆盖。",
    )
    parser.add_argument(
        "--resolve-only",
        action="store_true",
        help="只输出映射与 PDF 链接，不实际下载。",
    )
    parser.add_argument(
        "--document",
        choices=("prospectus", "intent", "issue", "result", "listing", "all"),
        default="prospectus",
        help="下载文件类型：招股说明书、招股意向书、发行公告、发行结果公告、上市公告书，或全部下载。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = BSEOfficialClient(status_callback=print)
    output_path = args.output_dir if args.output_dir.is_absolute() else (ROOT_DIR / args.output_dir)
    has_error = False

    def _handle_resolution(
        label: str,
        resolution: object,
        target_name: str,
    ) -> bool:
        mapping = resolution.mapping
        disclosure = resolution.disclosure
        print(f"[{label}]")
        print(
            f"代码映射：{mapping.listed_company.post_listing_code} {mapping.listed_company.short_name} "
            f"-> {mapping.project.pre_listing_code}"
        )
        print(f"公司全称：{mapping.listed_company.company_name}")
        print(f"项目 ID：{mapping.project.project_id}")
        print(f"命中文件：{disclosure.title}")
        if disclosure.publish_date:
            print(f"披露日期：{disclosure.publish_date}")
        print(f"下载链接：{disclosure.full_url}")
        if disclosure.detail_url:
            print(f"详情页：{disclosure.detail_url}")
        print(f"目标文件名：{target_name}")
        if args.resolve_only:
            return True
        downloaded_path = client.download_disclosure_file(
            disclosure,
            output_path / target_name,
            overwrite=args.overwrite,
        )
        print(f"已下载到：{downloaded_path}")
        return True

    if args.document in {"prospectus", "intent", "all"}:
        try:
            if args.document in {"prospectus", "all"}:
                prospectus_documents = client.resolve_prospectus_documents_by_post_listing_code(args.code)
            else:
                prospectus_documents = [
                    client.resolve_prospectus_by_post_listing_code(args.code, preferred_kind="intent")
                ]
            for prospectus in prospectus_documents:
                title = prospectus.disclosure.title
                label = "招股意向书" if "招股意向书" in title else "招股说明书"
                _handle_resolution(label, prospectus, client.build_prospectus_filename(prospectus))
        except BSEOfficialError as exc:
            label = "招股意向书" if args.document == "intent" else "招股文件"
            print(f"[{label}] 失败：{exc}")
            has_error = True

    if args.document in {"issue", "all"}:
        try:
            issue_announcement = client.resolve_issue_announcement_by_post_listing_code(args.code)
            _handle_resolution("发行公告", issue_announcement, client.build_issue_announcement_filename(issue_announcement))
        except BSEOfficialError as exc:
            print(f"[发行公告] 失败：{exc}")
            has_error = True

    if args.document in {"result", "all"}:
        try:
            issue_result = client.resolve_issue_result_announcement_by_post_listing_code(args.code)
            _handle_resolution("发行结果公告", issue_result, client.build_issue_result_announcement_filename(issue_result))
        except BSEOfficialError as exc:
            print(f"[发行结果公告] 失败：{exc}")
            has_error = True

    if args.document in {"listing", "all"}:
        try:
            listing = client.resolve_listing_announcement_by_post_listing_code(args.code)
            _handle_resolution("上市公告书", listing, client.build_listing_announcement_filename(listing))
        except BSEOfficialError as exc:
            print(f"[上市公告书] 失败：{exc}")
            has_error = True

    return 1 if has_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
