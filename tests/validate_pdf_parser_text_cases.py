from __future__ import annotations

from pathlib import Path
import sys


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent if CURRENT_DIR.name == "tests" else CURRENT_DIR
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import pdf_parser


COMPARABLE_TEXT = """
第五节业务和技术
5、发行人与同行业可比公司比较
（1）同行业可比公司选取标准
发行人选取可比公司时，主要考虑把主营业务产品和业务模式与公司相似的上市公司作为可比公司，
因此公司选取壹石通、 万盛股份、 天马新材、 联瑞新材和百图股份作为同行业可比公司。
上述可比公司在产品大类、客户大类等方面与公司业务存在一定的可比性。
（2）经营业务、技术情况与市场地位的比较
公司名称主营业务情况专利技术情况主要荣誉称号市场地位
"""

YIKUN_COMPARABLE_TEXT = """
第五节业务和技术
（4）行业内主要企业情况
目前，公司在行业内主要竞争对手包括平高东芝、金冠电气、神马电力等，具体如下：
①平高东芝
平高东芝（廊坊）避雷器有限公司成立于 2002 年 4 月，系河南平高电气股份有限公司（股票代码：600312）
与日本东芝株式会社的合营公司，主要从事避雷器、避雷器用阀片的研发、设计、生产、销售。
②金冠电气（688517.SH）
金冠电气股份有限公司成立于 2005 年 3 月，主要从事输配电及控制设备研发、制造和销售。
③神马电力（603530.SH）
江苏神马电力股份有限公司成立于 1996 年 8 月，主要从事电力系统变电站复合外绝缘等产品的研发、生产与销售。
④中国西电（601179.SH）
中国西电电气股份有限公司主营业务为输配电及控制设备研发、设计、制造、销售等业务。
（5）公司与同行业可比公司的比较情况
公司按照主营业务及产品相似性、客户重叠性、下游应用领域、信息公开化程度等标准选取金冠电气（688517）、
中国西电（601179）、神马电力（603530）、平高东芝作为同行业可比公司作为参考依据。
"""

BUSINESS_TEXT = """
广东金戈新材料股份有限公司招股说明书
三、发行人主营业务情况
公司是一家从事功能性材料研发、生产和销售的国家级专精特新小巨人企业，依托高效的研发创新体系，公司已具备众多成熟的产品系列。
第五节业务和技术
5、发行人与同行业可比公司比较
公司名称主营业务情况专利技术情况主要荣誉称号市场地位
天马新材天马新材主营业务为高性能精细氧化铝粉体的研发、生产和销售。
截至 2025 年 12 月 31 日，已获授权发明专利 8 项。
三、发行人主营业务情况
报告期内，公司始终专注于功能性粉体的研发、生产和销售，导热粉体材料和阻燃粉体材料是收入的主要来源。
"""

COMPARABLE_BUSINESS_TEXT = """
第五节业务和技术
5、同行业可比公司情况
汉邦科技（688755.SH）是一家以色谱技术为核心，主要为制药领域提供分离纯化装备、耗材及技术解决方案。
三、发行人主营业务情况
公司主要从事手术缝线、介入耗材等医疗器械产品的研发、生产和销售。
四、主要产品情况
"""

SOUTH_DAIRY_COMPARABLE_TEXT = """
第五节 业务和技术
2、行业内的主要企业
公司主要从事乳制品及含乳饮料的制造与销售业务，基于所处行业、业务性质及财务数据的可比性，
公司主要选取了伊利股份、蒙牛乳业、光明乳业等十家在中国范围内从事乳制品生产销售的上市企业作为同行业可比公司，具体情况如下：
序号 证券代码 上市公司
1 600887.SH 伊利股份
2 02319.HK 蒙牛乳业
3 600597.SH 光明乳业
4 002946.SZ 新乳业
5 600429.SH 三元股份
6 002329.SZ 皇氏集团
7 600419.SH 天润乳业
8 002732.SZ 燕塘乳业
9 002910.SZ 庄园牧场
10 001318.SZ 阳光乳业
3、公司的市场地位
"""


def main() -> int:
    failures: list[str] = []

    comparable_codes = pdf_parser._extract_comparable_companies_from_text(
        COMPARABLE_TEXT,
        target_code="920083",
    )
    expected_codes = ["688733.SH", "603010.SH", "920971.BJ", "688300.SH", "875029.NQ"]
    if comparable_codes != expected_codes:
        failures.append(f"920083 comparable text: expected {expected_codes}, got {comparable_codes}")

    yikun_codes = pdf_parser._extract_comparable_companies_from_text(
        YIKUN_COMPARABLE_TEXT,
        target_code="920222",
    )
    expected_yikun_codes = ["688517.SH", "601179.SH", "603530.SH"]
    if yikun_codes != expected_yikun_codes:
        failures.append(f"920222 comparable text: expected {expected_yikun_codes}, got {yikun_codes}")

    description = pdf_parser._extract_business_desc_from_text(BUSINESS_TEXT)
    if "功能性材料研发、生产和销售" not in description:
        failures.append(f"920083 business text: missing issuer profile, got {description}")
    if "天马新材" in description:
        failures.append(f"920083 business text: selected comparable-company sentence, got {description}")

    comparable_description = pdf_parser._extract_business_desc_from_text(COMPARABLE_BUSINESS_TEXT)
    if "手术缝线" not in comparable_description:
        failures.append(f"business subject guard: missing issuer description, got {comparable_description}")
    if "汉邦科技" in comparable_description:
        failures.append(f"business subject guard: selected comparable-company sentence, got {comparable_description}")
    if not pdf_parser.business_desc_has_subject_conflict(
        "公司是一家色谱设备供应商汉邦科技（688755.SH），主要从事设备研发和生产。",
        "920268",
    ):
        failures.append("business subject guard: failed to detect another listed-company code")

    south_dairy_codes = pdf_parser._extract_comparable_companies_from_text(
        SOUTH_DAIRY_COMPARABLE_TEXT,
        target_code="920196",
    )
    expected_south_dairy_codes = [
        "600887.SH",
        "600597.SH",
        "002946.SZ",
        "600429.SH",
        "002329.SZ",
        "600419.SH",
        "002732.SZ",
        "002910.SZ",
        "001318.SZ",
    ]
    if south_dairy_codes != expected_south_dairy_codes:
        failures.append(
            f"920196 comparable table: expected {expected_south_dairy_codes}, got {south_dairy_codes}"
        )

    south_dairy_description = pdf_parser._extract_business_desc_from_text(SOUTH_DAIRY_COMPARABLE_TEXT)
    if south_dairy_description != "公司主要从事乳制品及含乳饮料的制造与销售业务":
        failures.append(f"920196 business text: trailing peer-selection text was not trimmed, got {south_dairy_description}")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("OK pdf parser text cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
