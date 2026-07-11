from __future__ import annotations

from pathlib import Path
import re
import sys

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import pdf_parser


PDF_NAME_PATTERN = re.compile(
    r"(?P<code>\d{6})(?P<name>.+?)(?P<doc_type>上市公告书|招股说明书|招股意向书)\.pdf$"
)


def resolve_pdf_path(relative_path: str) -> Path:
    file_path = ROOT_DIR / relative_path
    if file_path.exists():
        return file_path

    match = PDF_NAME_PATTERN.match(file_path.name)
    if not match:
        return file_path

    doc_types = [match.group("doc_type")]
    if match.group("doc_type") == "招股意向书":
        doc_types.append("招股说明书")
    elif match.group("doc_type") == "招股说明书":
        doc_types.append("招股意向书")

    for doc_type in doc_types:
        candidates = sorted(file_path.parent.glob(f"{match.group('code')}_*_{doc_type}.pdf"))
        if candidates:
            return candidates[0]
    return file_path


COMPARABLE_CASES = (
    {
        "label": "920028 comparables",
        "path": "公告文件/920028_新恒泰_招股说明书.pdf",
        "expected_codes": ["300980.SZ", "300920.SZ"],
    },
    {
        "label": "920029 comparables",
        "path": "公告文件/920029_开发科技_招股说明书.pdf",
        "expected_codes": ["603556.SH", "601222.SH", "688616.SH", "300360.SZ"],
    },
    {
        "label": "920050 comparables",
        "path": "公告文件/920050_爱舍伦_招股说明书.pdf",
        "expected_codes": ["603301.SH", "002950.SZ", "603205.SH"],
    },
    {
        "label": "920072 intent comparables",
        "path": "公告文件/920072_科莱瑞迪_招股意向书.pdf",
        "expected_codes": ["688029.SH", "688236.SH", "688314.SH", "688617.SH"],
    },
    {
        "label": "920072 prospectus comparables",
        "path": "公告文件/920072_科莱瑞迪_招股说明书.pdf",
        "expected_codes": ["688029.SH", "688236.SH", "688314.SH", "688617.SH"],
    },
    {
        "label": "920076 comparables",
        "path": "公告文件/920076_国亮新材_招股说明书.pdf",
        "expected_codes": ["002392.SZ", "002225.SZ", "688119.SH", "002066.SZ", "833580.NQ"],
    },
    {
        "label": "920078 comparables",
        "path": "公告文件/920078_族兴新材_招股说明书.pdf",
        "expected_codes": ["874421.NQ", "688456.SH", "920634.BJ", "603826.SH"],
    },
    {
        "label": "920086 comparables",
        "path": "公告文件/920086_科马材料_招股说明书.pdf",
        "expected_codes": ["603586.SH", "002297.SZ", "688033.SH", "002985.SZ", "920106.BJ"],
    },
    {
        "label": "920117 comparables",
        "path": "公告文件/920117_龙鑫智能_招股说明书.pdf",
        "expected_codes": ["301662.SZ", "920522.BJ", "920284.BJ", "300619.SZ", "874378.NQ", "874312.NQ"],
    },
    {
        "label": "920125 comparables",
        "path": "公告文件/920125_鸿仕达_招股说明书.pdf",
        "expected_codes": ["688097.SH", "603283.SH", "300836.SZ", "301128.SZ"],
    },
    {
        "label": "920126 comparables",
        "path": "公告文件/920126_永大股份_招股说明书.pdf",
        "expected_codes": ["300092.SZ", "601798.SH", "603169.SH", "001332.SZ", "920703.BJ"],
    },
    {
        "label": "920136 comparables",
        "path": "公告文件/920136_永励精密_招股说明书.pdf",
        "expected_codes": ["874389.NQ", "001380.SZ", "603037.SH"],
    },
    {
        "label": "920161 comparables",
        "path": "公告文件/920161_龙辰科技_招股说明书.pdf",
        "expected_codes": ["600237.SH", "002263.SZ", "603435.SH"],
    },
    {
        "label": "920166 comparables",
        "path": "公告文件/920166_海圣医疗_招股说明书.pdf",
        "expected_codes": ["603309.SH", "300453.SZ", "301097.SZ"],
    },
    {
        "label": "920168 comparables",
        "path": "公告文件/920168_通宝光电_招股说明书.pdf",
        "expected_codes": ["600741.SH", "601799.SH", "603786.SH"],
    },
    {
        "label": "920176 comparables",
        "path": "公告文件/920176_维琪科技_招股说明书.pdf",
        "expected_codes": ["920982.BJ", "920123.BJ"],
    },
    {
        "label": "920183 comparables",
        "path": "公告文件/920183_海菲曼_招股说明书.pdf",
        "expected_codes": ["002351.SZ", "002888.SZ", "872824.NQ"],
    },
    {
        "label": "920188 comparables",
        "path": "公告文件/920188_悦龙科技_招股说明书.pdf",
        "expected_codes": ["920225.BJ", "920694.BJ", "920871.BJ"],
    },
    {
        "label": "920189 comparables with name-code conflict correction",
        "path": "公告文件/920189_康美特_招股说明书.pdf",
        "expected_codes": ["300041.SZ", "688535.SH", "688035.SH", "688093.SH", "688019.SH", "688219.SH", "300644.SZ", "300221.SZ"],
    },
    {
        "label": "920193 comparables",
        "path": "公告文件/920193_吉和昌_向不特定合格投资者公开发行股票并在北京证券交易所上市招股说明书.pdf",
        "expected_codes": ["688359.SH", "300530.SZ", "688353.SH", "870303.NQ", "603181.SH"],
    },
    {
        "label": "920211 comparables",
        "path": "公告文件/920211_新睿电子_招股说明书.pdf",
        "expected_codes": ["873553.NQ", "301510.SZ", "603416.SH", "002979.SZ", "688320.SH", "688160.SH"],
    },
    {
        "label": "920218 comparables",
        "path": "公告文件/920218_新天力_招股说明书.pdf",
        "expected_codes": ["301193.SZ", "001356.SZ", "301501.SZ"],
    },
    {
        "label": "920220 comparables",
        "path": "公告文件/920220_朗信电气_招股说明书.pdf",
        "expected_codes": ["603305.SH", "300969.SZ", "002536.SZ", "603348.SH", "603266.SH"],
    },
    {
        "label": "920156 comparables",
        "path": "公告文件/920156海昌智能招股说明书.pdf",
        "expected_codes": ["837408.NQ", "301128.SZ", "603960.SH"],
    },
    {
        "label": "920177 comparables reasonable empty",
        "path": "公告文件/920177恒道科技招股说明书.pdf",
        "expected_codes": [],
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
        "label": "920028 business",
        "path": "公告文件/920028新恒泰招股说明书.pdf",
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
        file_path = resolve_pdf_path(case["path"])
        actual_codes = pdf_parser.extract_comparable_companies(file_path)
        if actual_codes != case["expected_codes"]:
            failures.append(
                f"{case['label']}: expected {case['expected_codes']}, got {actual_codes}"
            )
        print(f"OK {case['label']}: {actual_codes}")

    for case in BUSINESS_CASES:
        file_path = resolve_pdf_path(case["path"])
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
