from __future__ import annotations

from pathlib import Path
import sys

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import pdf_parser


COMPARABLE_CASES = (
    {
        "label": "920156 comparables",
        "path": "公告文件/920156海昌智能招股说明书.pdf",
        "expected_codes": ["837408.NQ", "301128.SZ", "603960.SH"],
    },
    {
        "label": "920177 comparables",
        "path": "公告文件/920177恒道科技招股说明书.pdf",
        "expected_codes": ["874616.NQ", "002859.SZ", "000651.SZ"],
    },
    {
        "label": "920055 comparables",
        "path": "公告文件/920055隆源股份招股说明书.pdf",
        "expected_codes": ["603305.SH", "600933.SH", "603211.SH", "605133.SH"],
    },
    {
        "label": "920119 comparables",
        "path": "公告文件/920119美德乐招股说明书.pdf",
        "expected_codes": ["301029.SZ", "688097.SH", "300450.SZ", "301662.SZ", "300173.SZ"],
    },
    {
        "label": "920011 comparables",
        "path": "公告文件/920011晨光电机招股说明书.pdf",
        "expected_codes": ["603344.SH", "301226.SZ", "300660.SZ", "002892.SZ", "833450.NQ", "920100.BJ"],
    },
    {
        "label": "920181 comparables",
        "path": "公告文件/920181赛英电子招股说明书.pdf",
        "expected_codes": ["301581.SZ", "688103.SH", "873913.NQ"],
    },
    {
        "label": "920186 comparables reasonable empty",
        "path": "公告文件/920186中科仪招股意向书.pdf",
        "expected_codes": [],
    },
)

BUSINESS_CASES = (
    {
        "label": "920156 business",
        "path": "公告文件/920156海昌智能招股说明书.pdf",
        "must_contain": ["高性能线束装备", "智能化解决方案"],
        "must_not_contain": ["招股说明书", "证券代码", "证券简称"],
    },
    {
        "label": "920186 business",
        "path": "公告文件/920186中科仪招股意向书.pdf",
        "must_contain": ["半导体制造设备核心部件提供商", "真空科学仪器设备"],
        "must_not_contain": ["招股意向书", "证券代码", "证券简称"],
    },
    {
        "label": "920029 business",
        "path": "公告文件/920029新恒泰招股说明书.pdf",
        "must_contain": ["功能性高分子发泡材料", "研发、制造和销售"],
        "must_not_contain": ["劳务派遣", "关联关系", "主要责任人"],
    },
    {
        "label": "920119 business",
        "path": "公告文件/920119美德乐招股说明书.pdf",
        "must_contain": ["智能制造装备", "研发、设计、制造和销售业务"],
        "must_not_contain": ["主要责任人", "招股说明书", "证券代码"],
    },
    {
        "label": "920180 business",
        "path": "公告文件/920180爱得科技招股说明书.pdf",
        "must_contain": ["骨科耗材", "医疗器械", "创面修复产品"],
        "must_not_contain": ["行业内其他主要企业情况如下", "挂牌期间", "证券代码"],
    },
)


def main() -> int:
    failures: list[str] = []

    for case in COMPARABLE_CASES:
        file_path = ROOT_DIR / case["path"]
        actual_codes = pdf_parser.extract_comparable_companies(file_path)
        if actual_codes != case["expected_codes"]:
            failures.append(
                f"{case['label']}: expected {case['expected_codes']}, got {actual_codes}"
            )
        print(f"OK {case['label']}: {actual_codes}")

    for case in BUSINESS_CASES:
        file_path = ROOT_DIR / case["path"]
        description = pdf_parser.extract_business_desc(file_path)
        for snippet in case["must_contain"]:
            if snippet not in description:
                failures.append(f"{case['label']}: missing required snippet {snippet}")
        for snippet in case["must_not_contain"]:
            if snippet in description:
                failures.append(f"{case['label']}: unexpected noise snippet {snippet}")
        print(f"OK {case['label']}: {description}")

    if failures:
        print("\nPDF content golden validation failed:")
        for item in failures:
            print(f"- {item}")
        return 1

    print(
        f"\nPDF content golden validation passed: {len(COMPARABLE_CASES)} comparable cases, {len(BUSINESS_CASES)} business cases"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
