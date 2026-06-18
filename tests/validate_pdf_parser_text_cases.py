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

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("OK pdf parser text cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
