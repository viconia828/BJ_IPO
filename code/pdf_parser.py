from __future__ import annotations

import json
from dataclasses import asdict, dataclass
import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable


CODE_PATTERN = re.compile(r"\b\d{6}\.(?:SH|SZ|BJ|NQ)\b", re.IGNORECASE)
CODE_ONLY_PATTERN = re.compile(r"\b\d{6}\b")
SPECIFIC_SECTION_PATTERNS = (
    "可比公司选取标准及基本情况",
    "可比公司基本情况",
    "可比上市公司基本情况",
    "发行人与同行业可比公司",
    "发行人与同行业可比上市公司",
    "发行人与同行业可比公众公司",
    "与同行业可比公司的对比分析",
    "发行人与同行业可比公司的对比分析",
    "与同行业可比上市公司的对比分析",
    "发行人与同行业可比上市公司的对比分析",
    "与同行业可比公众公司的比较",
)
PROSPECTUS_BUSINESS_CHAPTER_PATTERNS = (
    "第五节业务和技术",
    "第五节业务与技术",
    "第六节业务和技术",
    "第六节业务与技术",
)
GENERIC_SECTION_PATTERNS = (
    "同行业可比公司",
    "同行业上市公司",
    "可比公司",
)
COMPARABLE_SECTION_STOP_PATTERNS = (
    "主营业务情况",
    "公司简介",
    "发行人基本情况",
    "募集资金运用",
    "募集资金投资项目",
    "主要财务数据和财务指标",
    "股票发行情况",
    "风险因素",
)
BUSINESS_PRIMARY_PATTERNS = (
    "发行人主营业务情况",
    "主营业务情况",
    "公司简介",
)
BUSINESS_FALLBACK_PATTERNS = (
    "发行人基本情况",
)
BUSINESS_SECTION_STOP_PATTERNS = (
    "同行业可比公司",
    "同行业上市公司",
    "可比公司选取标准及基本情况",
    "可比公司基本情况",
    "募集资金运用",
    "募集资金投资项目",
    "主要财务数据和财务指标",
    "主要财务数据",
    "股票发行情况",
    "风险因素",
)
BUSINESS_SENTENCE_PATTERNS = (
    re.compile(r"((?!公司|发行人)[\u4e00-\u9fffA-Za-z]{2,24}主要从事[^。]{20,220}。?)"),
    re.compile(r"((?!公司|发行人)[\u4e00-\u9fffA-Za-z]{2,24}是一家[^。]{20,220}。?)"),
    re.compile(r"((?!公司|发行人)[\u4e00-\u9fffA-Za-z]{2,24}主营业务为[^。]{20,220}。?)"),
    re.compile(r"(公司是一家[^。]{20,220}。?)"),
    re.compile(r"(公司是中国领先的[^。]{20,220}。?)"),
    re.compile(r"(公司是[^。]{20,220}。?)"),
    re.compile(r"(公司主要从事[^。]{20,220}。?)"),
    re.compile(r"(公司专业从事[^。]{20,220}。?)"),
    re.compile(r"(公司专注于[^。]{20,220}。?)"),
    re.compile(r"(公司长期专注于[^。]{20,220}。?)"),
    re.compile(r"(公司主营业务为[^。]{20,220}。?)"),
    re.compile(r"(发行人是一家[^。]{20,220}。?)"),
    re.compile(r"(发行人主要从事[^。]{20,220}。?)"),
    re.compile(r"(发行人专业从事[^。]{20,220}。?)"),
    re.compile(r"(发行人主营业务为[^。]{20,220}。?)"),
)
BUSINESS_NOISE_MARKERS = (
    "招股说明书",
    "招股意向书",
    "上市公告书",
    "证券简称",
    "证券代码",
    "子公司",
    "注销",
    "纳入合并",
    "挂牌期间",
    "前五大客户",
    "主要客户",
    "销售情况",
    "产能",
    "产量",
    "销量",
    "产销率",
    "比较情况",
)
BUSINESS_REJECT_MARKERS = (
    "公司是否",
    "发行人是否",
    "主要责任人",
    "关联关系",
    "劳务派遣",
    "发行条件",
    "上市条件",
    "股份回购",
    "回购本次",
    "现金方式分配利润",
)
BUSINESS_REQUIRED_KEYWORDS = (
    "从事",
    "主营业务",
    "研发",
    "生产",
    "制造",
    "销售",
    "服务",
    "解决方案",
    "提供商",
    "供应商",
)
BUSINESS_TRIM_MARKERS = (
    "具体情况如下",
    "主要产品与服务项目",
    "行业内其他主要企业情况如下",
    "二、发行人挂牌期间的基本情况",
    "二、 发行人挂牌期间的基本情况",
    "二、控股股东",
    "二、 控股股东",
    "（一）技术创新",
)
PROSPECTUS_ISSUE_SECTION_PATTERNS = (
    "本次发行概况",
    "本次发行基本情况",
    "发行概况",
)
PROSPECTUS_ISSUE_SECTION_STOP_PATTERNS = (
    "本次发行的有关机构",
    "本次发行相关机构",
    "风险因素",
    "发行人基本情况",
    "募集资金运用",
    "投资者保护",
)
OLD_SHARE_PATTERNS = (
    "老股转让",
    "存量股份发售",
    "原股东公开发售",
    "存量发行",
    "公开发售股份",
)
LISTING_OLD_SHARE_CHAPTER_PATTERNS = (
    "第三节发行人、实际控制人及股东持股情况",
    "第三节发行人、控股股东、实际控制人及股东持股情况",
)
LISTING_OLD_SHARE_SECTION_PATTERNS = (
    "本次发行前后的股本结构变动情况",
)
LISTING_OLD_SHARE_ROW_MARKERS = (
    "无限售流通股小计",
    "无限售条件流通股小计",
    "无限售流通股份小计",
    "无限售条件流通股份小计",
)
LISTING_OLD_SHARE_BLOCK_MARKERS = (
    "无限售流通股",
    "无限售条件流通股",
    "无限售流通股份",
    "无限售条件流通股份",
)
LISTING_OLD_SHARE_SECTION_STOP_PATTERNS = (
    "本次发行后公司前十名股东持股情况",
    "第四节股票发行情况",
    "第四节发行情况",
    "第四节股票公开发行情况",
)
OLD_SHARE_NEGATIVE_PATTERNS = (
    "本次发行全部为新股发行",
    "全部为新股发行",
    "不涉及原股东公开发售股份",
    "原股东不公开发售股份",
    "公司原股东不公开发售股份",
    "不涉及老股转让",
    "无老股转让",
    "不存在老股转让",
    "不存在存量股份发售",
    "未安排老股转让",
    "本次公开发售股份--",
)
OLD_SHARE_VALUE_PATTERNS = (
    re.compile(
        r"(?:老股转让|存量股份发售|原股东公开发售|存量发行|公开发售股份)"
        r"(?:的?股份)?(?:数量|股数|股份数量|规模)?(?:为|合计|共计|约|拟|不超过|不低于)?"
        r"[^0-9]{0,40}(?P<value>[0-9,]+(?:\.[0-9]+)?)\s*(?P<unit>万股|股)",
        re.IGNORECASE,
    ),
)
NAME_CODE_PATTERN = re.compile(
    r"(?P<name>[\u4e00-\u9fffA-Za-z]{2,24})\s*(?:\d+-\d+-\d+\s*)?[（(]\s*(?P<code>\d{6}\.(?:SH|SZ|BJ|NQ))\s*[）)]",
    re.IGNORECASE,
)
PLAIN_NAME_CODE_PATTERN = re.compile(
    r"(?P<name>[\u4e00-\u9fffA-Za-z]{2,24})\s*(?:\d+-\d+-\d+\s*)?[（(]\s*(?P<code>\d{6})\s*[）)]",
    re.IGNORECASE,
)
GLOSSARY_ENTRY_SPAN = r"(?:(?!\s[\u4e00-\u9fffA-Za-z]{2,24}\s*指).){0,180}?"
GLOSSARY_PATTERN = re.compile(
    rf"(?P<name>[\u4e00-\u9fffA-Za-z]{{2,24}})\s*指{GLOSSARY_ENTRY_SPAN}(?:股票代码|证券代码|挂牌代码)\s*(?P<code>\d{{6}}\.(?:SH|SZ|BJ|NQ))",
    re.IGNORECASE,
)
GLOSSARY_COMPARABLE_PATTERN = re.compile(
    rf"(?P<name>[\u4e00-\u9fffA-Za-z]{{2,24}})\s*指{GLOSSARY_ENTRY_SPAN}(?:同行业可比公司|可比公司)",
    re.IGNORECASE,
)
GLOSSARY_ENTRY_PATTERN = re.compile(r"(?P<name>[\u4e00-\u9fffA-Za-z]{2,24})\s*指", re.IGNORECASE)
ROW_NAME_PATTERN = re.compile(
    r"(?:^|\n)\s*(?P<name>[\u4e00-\u9fffA-Za-z]{2,16})\s*(?:\n[（(]|\s+(?:暂未披露|\d{4}\s*年|\d+\.\d+%))",
    re.IGNORECASE,
)
ROW_NAME_STOPWORDS = {
    "发行人",
    "公司名称",
    "项目",
    "区域",
    "主营业务",
    "资产总额",
    "营业收入",
    "毛利率",
    "净利润",
    "公司",
}
COMPARABLE_NAME_CODE_FALLBACKS = {
    "旭升集团": "603305.SH",
    "爱柯迪": "600933.SH",
    "晋拓股份": "603211.SH",
    "嵘泰股份": "605133.SH",
    "怡合达": "301029.SZ",
    "博众精工": "688097.SH",
    "先导智能": "300450.SZ",
    "宏工科技": "301662.SZ",
    "福能东方": "300173.SZ",
    "壹石通": "688733.SH",
    "万盛股份": "603010.SH",
    "天马新材": "920971.BJ",
    "联瑞新材": "688300.SH",
    "百图股份": "875029.NQ",
    "祥源新材": "300980.SZ",
    "润阳科技": "300920.SZ",
    "海兴电力": "603556.SH",
    "林洋能源": "601222.SH",
    "西力科技": "688616.SH",
    "炬华科技": "300360.SZ",
    "振德医疗": "603301.SH",
    "奥美医疗": "002950.SZ",
    "健尔康": "603205.SH",
    "南微医学": "688029.SH",
    "春立医疗": "688236.SH",
    "康拓医疗": "688314.SH",
    "惠泰医疗": "688617.SH",
    "北京利尔": "002392.SZ",
    "濮耐股份": "002225.SZ",
    "中钢洛耐": "688119.SH",
    "瑞泰科技": "002066.SZ",
    "科创新材": "833580.NQ",
    "坤彩科技": "603826.SH",
    "新威凌": "920634.BJ",
    "有研粉材": "688456.SH",
    "旭阳新材": "874421.NQ",
    "金麒麟": "603586.SH",
    "博云新材": "002297.SZ",
    "天宜新材": "688033.SH",
    "北摩高科": "002985.SZ",
    "林泰新材": "920106.BJ",
    "立万精工": "874389.NQ",
    "华纬科技": "001380.SZ",
    "凯众股份": "603037.SH",
    "铜峰电子": "600237.SH",
    "安徽铜峰电子": "600237.SH",
    "大东南": "002263.SZ",
    "浙江大东南": "002263.SZ",
    "嘉德利": "603435.SH",
    "维力医疗": "603309.SH",
    "三鑫医疗": "300453.SZ",
    "天益医疗": "301097.SZ",
    "华域汽车": "600741.SH",
    "星宇股份": "601799.SH",
    "科博达": "603786.SH",
    "锦波生物": "920982.BJ",
    "芭薇股份": "920123.BJ",
    "漫步者": "002351.SZ",
    "惠威科技": "002888.SZ",
    "先歌国际": "872824.NQ",
    "利通科技": "920225.BJ",
    "中裕科技": "920694.BJ",
    "派特尔": "920871.BJ",
    "三孚新科": "688359.SH",
    "领湃科技": "300530.SZ",
    "华盛锂电": "688353.SH",
    "皇马科技": "603181.SH",
    "松石科技": "870303.NQ",
    "华成工控": "873553.NQ",
    "固高科技": "301510.SZ",
    "信捷电气": "603416.SH",
    "雷赛智能": "002979.SZ",
    "禾川科技": "688320.SH",
    "步科股份": "688160.SH",
    "家联科技": "301193.SZ",
    "富岭股份": "001356.SZ",
    "恒鑫生活": "301501.SZ",
    "海普锐": "837408.NQ",
    "科新机电": "300092.SZ",
    "蓝科高新": "601798.SH",
    "兰石重装": "603169.SH",
    "锡装股份": "001332.SZ",
    "广厦环能": "920703.BJ",
    "德邦科技": "688035.SH",
}
COMPARABLE_CODE_ALIASES = {
    "832522.BJ": "920522.BJ",
    "833284.BJ": "920284.BJ",
    "832982.BJ": "920982.BJ",
    "837023.BJ": "920123.BJ",
    "871694.BJ": "920694.BJ",
    "873703.BJ": "920703.BJ",
}
FULLWIDTH_TRANSLATION = str.maketrans(
    {
        "０": "0",
        "１": "1",
        "２": "2",
        "３": "3",
        "４": "4",
        "５": "5",
        "６": "6",
        "７": "7",
        "８": "8",
        "９": "9",
        "．": ".",
        "，": ",",
        "：": ":",
        "（": "(",
        "）": ")",
        "－": "-",
        "—": "-",
        "／": "/",
    }
)
NUMERIC_TOKEN_PATTERN = r"[0-9][0-9,]*(?:\.[0-9]+)?"
CHINESE_DATE_PATTERN = r"20[0-9]{2}年[0-9]{1,2}月[0-9]{1,2}日"
ISO_DATE_PATTERN = r"20[0-9]{2}[-/][0-9]{1,2}[-/][0-9]{1,2}"
PROSPECTUS_ISSUE_PRICE_PATTERNS = (
    re.compile(rf"(?:每股发行价格|发行价格|发行价)[^0-9]{{0,24}}(?P<value>{NUMERIC_TOKEN_PATTERN})元(?:/股)?"),
    re.compile(rf"(?P<value>{NUMERIC_TOKEN_PATTERN})元/股"),
)
PROSPECTUS_TOTAL_ISSUE_PATTERNS = (
    re.compile(
        rf"(?:本次公开发行股票数量|本次公开发行股份数量|本次发行股票数量|本次发行股份数量|"
        rf"公开发行股票数量|公开发行股份数量|发行股票数量|发行股份数量|发行股数|初始发行数量)"
        rf"[^0-9]{{0,30}}(?P<value>{NUMERIC_TOKEN_PATTERN})(?P<unit>万股|股)"
    ),
    re.compile(
        rf"(?<!网上)(?<!网下)(?<!战略配售)(?:发行数量)"
        rf"[^0-9]{{0,24}}(?P<value>{NUMERIC_TOKEN_PATTERN})(?P<unit>万股|股)"
    ),
)
PROSPECTUS_AFTER_ISSUE_PE_PATTERNS = (
    re.compile(rf"(?:发行市盈率|发行后市盈率|发行后每股收益市盈率)[^0-9]{{0,24}}(?P<value>{NUMERIC_TOKEN_PATTERN})倍"),
    re.compile(rf"(?:发行市盈率|发行后市盈率|发行后每股收益市盈率)[(]?倍[)]?[^0-9]{{0,12}}(?P<value>{NUMERIC_TOKEN_PATTERN})"),
)
PROSPECTUS_INDUSTRY_PE_PATTERNS = (
    re.compile(rf"(?:行业平均静态市盈率|行业平均市盈率|最近一个月平均静态市盈率)[^0-9]{{0,32}}(?P<value>{NUMERIC_TOKEN_PATTERN})倍"),
    re.compile(rf"(?:所属行业|所处行业)[^。；;]{{0,60}}?(?:平均静态市盈率|平均市盈率)[^0-9]{{0,24}}(?P<value>{NUMERIC_TOKEN_PATTERN})倍"),
)
PROSPECTUS_TOTAL_CAPITAL_PATTERNS = (
    re.compile(rf"(?:发行后总股本|本次发行后总股本)[^0-9]{{0,24}}(?P<value>{NUMERIC_TOKEN_PATTERN})(?P<unit>万股|股)"),
)
PROSPECTUS_APPLY_DATE_PATTERNS = (
    re.compile(
        rf"(?:预计发行日期|申购日期|网上申购日期|网上申购日|网上申购时间|申购时间|发行日期)"
        rf"[^0-9]{{0,18}}(?P<date>{CHINESE_DATE_PATTERN}|{ISO_DATE_PATTERN})"
    ),
)
PROSPECTUS_SUBSCRIPTION_LIMIT_PATTERNS = (
    re.compile(
        rf"(?:网上每笔申购数量上限|网上申购数量上限|申购数量上限|申购上限)"
        rf"[(](?P<unit>万股)[)][^0-9]{{0,12}}(?P<value>{NUMERIC_TOKEN_PATTERN})"
    ),
    re.compile(
        rf"(?:申购上限|申购数量上限|网上申购数量上限|网上每笔申购数量上限|每笔申购数量上限)"
        rf"[^。；;]{{0,80}}(?:即|为|不超过)[^0-9]{{0,12}}(?P<value>{NUMERIC_TOKEN_PATTERN})(?P<unit>万股|股)"
    ),
    re.compile(
        rf"(?:申购上限|申购数量上限|网上申购数量上限|网上每笔申购数量上限|每笔申购数量上限)"
        rf"[^0-9]{{0,32}}(?P<value>{NUMERIC_TOKEN_PATTERN})(?P<unit>万股|股)"
    ),
    re.compile(
        rf"(?:申购数量|网上申购数量|每笔申购数量)[^。；;]{{0,48}}(?:不得超过|不超过|上限为)"
        rf"[^0-9]{{0,12}}(?P<value>{NUMERIC_TOKEN_PATTERN})(?P<unit>万股|股)"
    ),
)
PROSPECTUS_ONLINE_ISSUE_PATTERNS = (
    re.compile(
        rf"(?:网上发行数量|网上发行股数|网上初始发行数量|网上初始发行股数|本次网上发行数量|本次网上发行股数)"
        rf"[(](?P<unit>万股|股)[)][^0-9]{{0,12}}(?P<value>{NUMERIC_TOKEN_PATTERN})"
    ),
    re.compile(
        rf"(?:网上发行数量|网上发行股数|网上初始发行数量|网上初始发行股数|本次网上发行数量|本次网上发行股数)"
        rf"[^0-9。；;]{{0,16}}(?:为|:|=)[^0-9]{{0,12}}(?P<value>{NUMERIC_TOKEN_PATTERN})(?P<unit>万股|股)"
    ),
    re.compile(
        rf"(?:网上发行数量|网上发行股数|网上初始发行数量|网上初始发行股数|本次网上发行数量|本次网上发行股数)"
        rf"[^0-9。；;]{{0,24}}(?P<value>{NUMERIC_TOKEN_PATTERN})(?P<unit>万股|股)"
    ),
)
ONLINE_ISSUE_REJECT_MARKERS = ("大于", "小于", "不足", "比例", "规则", "时")
ISSUE_RESULT_VALID_ACCOUNT_PATTERNS = (
    re.compile(rf"(?:有效申购户数|有效申购账户数|网上投资者有效申购户数)[^0-9]{{0,32}}(?P<value>{NUMERIC_TOKEN_PATTERN})户"),
)
ISSUE_RESULT_ALLOCATED_ACCOUNT_PATTERNS = (
    re.compile(rf"(?:网上发行获配户数|网上投资者获配户数|网上获配户数|获配户数)[^0-9]{{0,32}}(?P<value>{NUMERIC_TOKEN_PATTERN})户"),
)
ISSUE_RESULT_VALID_SHARE_PATTERNS = (
    re.compile(
        rf"(?:有效申购数量|有效申购总量|有效申购股数|网上投资者有效申购数量|网上投资者有效申购总量)"
        rf"[^0-9]{{0,32}}(?P<value>{NUMERIC_TOKEN_PATTERN})(?P<unit>万股|股)"
    ),
)
ISSUE_RESULT_FROZEN_FUNDS_PATTERNS = (
    re.compile(rf"(?:冻结资金|冻结资金总额|申购资金总额|有效申购资金总额)[^0-9]{{0,32}}(?P<value>{NUMERIC_TOKEN_PATTERN})(?P<unit>亿元|万元|元)"),
)
ISSUE_RESULT_LWR_PATTERNS = (
    re.compile(rf"(?:网上发行最终中签率|网上发行中签率|中签率|配售比例)[^0-9]{{0,32}}(?P<value>{NUMERIC_TOKEN_PATTERN})%?"),
)
ISSUE_RESULT_MULTIPLE_PATTERNS = (
    re.compile(rf"(?:有效申购倍数|超额认购倍数|认购倍数)[^0-9]{{0,32}}(?P<value>{NUMERIC_TOKEN_PATTERN})倍?"),
)
ISSUE_RESULT_DATE_PATTERNS = (
    re.compile(
        r"(?:日期|公告日期|披露日期)\s*[:：]?\s*"
        r"(?P<date>20[0-9]{2}\s*年\s*[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日)"
    ),
    re.compile(
        r"(?:日期|公告日期|披露日期)\s*[:：]?\s*"
        r"(?P<date>20[0-9]{2}[-/][0-9]{1,2}[-/][0-9]{1,2})"
    ),
)
ISSUE_RESULT_THRESHOLD_PATTERNS = (
    re.compile(
        rf"(?:申购数量|申购股数|申购金额)[^0-9]{{0,16}}(?P<value>{NUMERIC_TOKEN_PATTERN})(?P<unit>万股|股|万元|元)"
        rf"[^。；;]{{0,80}}?(?:获配|配售)100股"
    ),
)
ISSUE_RESULT_TIME_PRIORITY_PATTERNS = (
    re.compile(r"(?:申购时间|同等申购数量).*?(?:优先|先后|排序)"),
    re.compile(r"(?:时间优先|申购时间优先|按申购时间顺序)"),
)
SUBSCRIPTION_DISTRIBUTION_ROW_PATTERN = re.compile(
    rf"(?P<shares>{NUMERIC_TOKEN_PATTERN})\s*(?:股|万股)?\s+"
    rf"(?P<accounts>{NUMERIC_TOKEN_PATTERN})\s*(?:户|个)?"
)
PROSPECTUS_INDUSTRY_PATTERNS = (
    re.compile(r"C制造业(?P<code>[0-9]{2})(?P<industry>[\u4e00-\u9fff、和]{2,36}?业)"),
    re.compile(
        r"(?:所属行业(?:为|:)?|所处行业(?:为|:)?|属于)"
        r"(?P<industry>[\u4e00-\u9fff]{2,24}业)"
        r"[(]行业代码[:：]?(?P<code>[A-Z][0-9]{2})[)]"
    ),
    re.compile(
        r"(?P<industry>[\u4e00-\u9fff]{2,24}业)"
        r"[(]行业代码[:：]?(?P<code>[A-Z][0-9]{2})[)]"
    ),
    re.compile(r"(?P<industry>[\u4e00-\u9fff、和]{2,36}?业)[(](?P<code>C[0-9]{2,4})[)]"),
    re.compile(r"(?P<code>C[0-9]{2,4})(?P<industry>[\u4e00-\u9fff、和]{2,36}?业)"),
)
PARSE_CACHE_SCHEMA = "pdf_parse_cache_v1"
PARSE_CACHE_KIND_VERSIONS = {
    "prospectus_issue_info": 8,
    "issue_announcement_info": 5,
    "issue_result_info": 4,
    "comparable_companies": 22,
    "business_desc": 3,
}
PARSE_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "pdf_parse_cache"
_CACHE_MISSING = object()


@dataclass(frozen=True)
class OldSharesExtractionResult:
    value_wan_shares: float
    source_file_type: str
    source_rule: str
    source_anchor: str
    raw_snippet: str
    confidence: float
    unit: str
    pre_unrestricted_wan_shares: float | None = None


def _parse_cache_path(pdf_path: str | Path, kind: str) -> Path:
    resolved = str(Path(pdf_path).resolve())
    digest = hashlib.sha1(f"{kind}|{resolved}".encode("utf-8")).hexdigest()
    return PARSE_CACHE_DIR / f"{digest}.json"


def _load_parse_cache(pdf_path: str | Path, kind: str) -> object:
    file_path = Path(pdf_path)
    if not file_path.exists():
        return _CACHE_MISSING
    cache_path = _parse_cache_path(file_path, kind)
    if not cache_path.exists():
        return _CACHE_MISSING
    try:
        stat = file_path.stat()
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return _CACHE_MISSING
    if not isinstance(payload, dict):
        return _CACHE_MISSING
    if payload.get("schema") != PARSE_CACHE_SCHEMA or payload.get("kind") != kind:
        return _CACHE_MISSING
    expected_version = PARSE_CACHE_KIND_VERSIONS.get(kind)
    if expected_version is not None and int(payload.get("parser_version") or -1) != expected_version:
        return _CACHE_MISSING
    if int(payload.get("mtime_ns") or -1) != stat.st_mtime_ns:
        return _CACHE_MISSING
    if int(payload.get("size") or -1) != stat.st_size:
        return _CACHE_MISSING
    return payload.get("value")


def _save_parse_cache(pdf_path: str | Path, kind: str, value: object) -> None:
    file_path = Path(pdf_path)
    if not file_path.exists():
        return
    try:
        stat = file_path.stat()
        PARSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": PARSE_CACHE_SCHEMA,
            "kind": kind,
            "path": str(file_path.resolve()),
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "value": value,
        }
        if kind in PARSE_CACHE_KIND_VERSIONS:
            payload["parser_version"] = PARSE_CACHE_KIND_VERSIONS[kind]
        _parse_cache_path(file_path, kind).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        return


def _old_shares_result_from_cache(value: object) -> OldSharesExtractionResult | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    try:
        return OldSharesExtractionResult(**value)
    except TypeError:
        return None


def _normalize_text(text: str) -> str:
    text = re.sub(r"\b\d+-\d+-\d+\b", " ", text or "")
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _infer_pdf_file_type(pdf_path: str | Path) -> str:
    stem = Path(pdf_path).stem
    if "上市公告书" in stem or "上市公告" in stem:
        return "上市公告书"
    return "招股文件"


def _find_anchor_positions(text: str, anchors: Iterable[str], start: int = 0) -> list[tuple[int, str]]:
    positions: list[tuple[int, str]] = []
    for anchor in anchors:
        cursor = start
        while True:
            index = text.find(anchor, cursor)
            if index < 0:
                break
            positions.append((index, anchor))
            cursor = index + len(anchor)
    return positions


def _extract_compact_section(
    compact_text: str,
    section_anchors: Iterable[str],
    stop_anchors: Iterable[str],
    *,
    chapter_anchors: Iterable[str] = (),
    fallback_radius: int = 2600,
) -> tuple[str, str]:
    chapter_positions = _find_anchor_positions(compact_text, chapter_anchors) if chapter_anchors else []
    chapter_start = 0
    if chapter_positions:
        chapter_start = max((index for index, _ in chapter_positions), default=0)
        for index, _ in sorted(chapter_positions, key=lambda item: item[0]):
            snippet = compact_text[index : index + 120]
            if "..." in snippet or "……" in snippet:
                continue
            chapter_start = index
            break

    section_positions = [item for item in _find_anchor_positions(compact_text, section_anchors, start=chapter_start) if item[0] >= chapter_start]
    if section_positions:
        section_index, section_anchor = min(section_positions, key=lambda item: item[0])
    else:
        fallback_sections = _find_anchor_positions(compact_text, section_anchors)
        if not fallback_sections:
            return "", ""
        section_index, section_anchor = max(fallback_sections, key=lambda item: item[0])

    stop_positions = [
        item
        for item in _find_anchor_positions(compact_text, stop_anchors, start=section_index + len(section_anchor))
        if item[0] > section_index
    ]
    section_end = min((index for index, _ in stop_positions), default=min(len(compact_text), section_index + fallback_radius))
    return compact_text[section_index:section_end], section_anchor


def _make_raw_snippet(text: str, start_index: int, length: int = 180) -> str:
    snippet = text[max(0, start_index - 40) : start_index + length]
    return snippet[:length]


def _iter_page_texts(file_path: Path) -> Iterable[str]:
    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                yield page.extract_text() or ""
        return
    except Exception:
        pass

    try:
        from pypdf import PdfReader  # type: ignore

        with file_path.open("rb") as file_obj:
            reader = PdfReader(file_obj)
            for page in reader.pages:
                yield page.extract_text() or ""
        return
    except Exception:
        pass

    try:
        from PyPDF2 import PdfReader  # type: ignore

        with file_path.open("rb") as file_obj:
            reader = PdfReader(file_obj)
            for page in reader.pages:
                yield page.extract_text() or ""
    except Exception:
        return


@lru_cache(maxsize=32)
def _read_pdf_text_cached(path_text: str) -> str:
    file_path = Path(path_text)
    if not file_path.exists():
        return ""
    return "\n".join(page_text for page_text in _iter_page_texts(file_path) if page_text)


def _read_pdf_text(pdf_path: str | Path) -> str:
    file_path = Path(pdf_path)
    return _read_pdf_text_cached(str(file_path.resolve()))


def _normalize_fullwidth_text(text: str) -> str:
    return (text or "").translate(FULLWIDTH_TRANSLATION)


def _parse_numeric_token(value: str) -> float | None:
    try:
        return float(str(value or "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _normalize_issue_date(value: str) -> str:
    text = str(value or "").strip().replace("/", "-")
    chinese_match = re.fullmatch(r"(?P<year>20[0-9]{2})年(?P<month>[0-9]{1,2})月(?P<day>[0-9]{1,2})日", text)
    if chinese_match:
        return "{year}-{month:02d}-{day:02d}".format(
            year=chinese_match.group("year"),
            month=int(chinese_match.group("month")),
            day=int(chinese_match.group("day")),
        )
    iso_match = re.fullmatch(r"(?P<year>20[0-9]{2})-(?P<month>[0-9]{1,2})-(?P<day>[0-9]{1,2})", text)
    if iso_match:
        return "{year}-{month:02d}-{day:02d}".format(
            year=iso_match.group("year"),
            month=int(iso_match.group("month")),
            day=int(iso_match.group("day")),
        )
    return text


def _shares_to_wan(value: float, unit: str) -> float:
    return value if unit == "万股" else value / 10000


def _shares_to_raw(value: float, unit: str) -> float:
    return value * 10000 if unit == "万股" else value


def _funds_to_yi(value: float, unit: str) -> float:
    if unit == "亿元":
        return value
    if unit == "万元":
        return value / 10000
    return value / 100000000


def _money_to_wan(value: float, unit: str) -> float:
    if unit == "万元":
        return value
    if unit == "亿元":
        return value * 10000
    return value / 10000


def _clean_prospectus_industry_name(value: str) -> str:
    current = str(value or "").strip(" ：:，,。；;")
    for marker in ("所属行业为", "所处行业为", "所属行业:", "所处行业:", "行业为", "属于"):
        if marker in current:
            current = current.split(marker, 1)[-1]
    for marker in ("指", "管理型行业分类", "上市公司行业分类"):
        index = current.find(marker)
        if index > 0:
            current = current[:index]
    return current.strip(" ：:，,。；;")


def _valid_issue_field_value(value: object) -> bool:
    return value not in (None, "", "--")


def _set_prospectus_issue_field(
    result: dict[str, dict[str, object]],
    field_name: str,
    value: object,
    rule: str,
    source_text: str,
    start_index: int,
) -> None:
    if not _valid_issue_field_value(value) or field_name in result["fields"]:
        return
    result["fields"][field_name] = value
    result["field_sources"][field_name] = f"prospectus:{rule}"
    result["raw_snippets"][field_name] = _make_raw_snippet(source_text, start_index)


def _extract_price_way_from_prospectus_text(result: dict[str, dict[str, object]], search_text: str, rule: str) -> None:
    if "PRICE_WAY" in result["fields"]:
        return
    for keyword, label in (("直接定价", "直接定价"), ("询价", "询价"), ("竞价", "竞价")):
        index = search_text.find(keyword)
        if index < 0:
            continue
        context = search_text[max(0, index - 80) : index + 120]
        if keyword == "直接定价" or any(marker in context for marker in ("定价方式", "发行价格", "确定发行价格", "发行方式")):
            _set_prospectus_issue_field(result, "PRICE_WAY", label, rule, search_text, index)
            return


def _extract_industry_from_prospectus_text(result: dict[str, dict[str, object]], search_text: str, rule: str) -> None:
    if "INDUSTRY" in result["fields"] and "INDUSTRY_CODE" in result["fields"]:
        return
    anchors = (
        "公司所属行业",
        "公司所处行业",
        "公司属于",
        "所属行业",
        "行业代码",
        "国民经济行业分类",
        "上市公司行业统计分类",
        "上市公司行业分类",
        "行业分类",
    )
    windows: list[tuple[str, int]] = []
    for anchor in anchors:
        start = 0
        while True:
            index = search_text.find(anchor, start)
            if index < 0:
                break
            windows.append((search_text[max(0, index - 80) : index + 420], max(0, index - 80)))
            start = index + len(anchor)
    if not windows:
        windows = [(search_text, 0)]

    for window, offset in windows:
        for pattern in PROSPECTUS_INDUSTRY_PATTERNS:
            match = pattern.search(window)
            if not match:
                continue
            industry = _clean_prospectus_industry_name(match.group("industry"))
            industry_code = str(match.group("code") or "").strip().upper()
            if industry_code and industry_code[0].isdigit():
                industry_code = f"C{industry_code}"
            if not industry or not industry_code.startswith("C") or not industry.endswith("业"):
                continue
            if industry == "制造业":
                continue
            if any(
                marker in industry
                for marker in (
                    "合伙企业",
                    "执行事务",
                    "股权投资",
                    "科技产业",
                    "上市公司行业",
                    "管理型行业",
                )
            ):
                continue
            if industry_code.startswith("C") and len(industry_code) > 3:
                industry_code = industry_code[:3]
            _set_prospectus_issue_field(result, "INDUSTRY", industry, rule, search_text, offset + match.start())
            _set_prospectus_issue_field(result, "INDUSTRY_CODE", industry_code, rule, search_text, offset + match.start())
            return


def _extract_numeric_patterns(
    result: dict[str, dict[str, object]],
    search_text: str,
    rule: str,
    field_name: str,
    patterns: Iterable[re.Pattern[str]],
) -> None:
    if field_name in result["fields"]:
        return
    for pattern in patterns:
        match = pattern.search(search_text)
        if not match:
            continue
        value = _parse_numeric_token(match.group("value"))
        if value is None:
            continue
        _set_prospectus_issue_field(result, field_name, value, rule, search_text, match.start())
        return


def _extract_wan_share_patterns(
    result: dict[str, dict[str, object]],
    search_text: str,
    rule: str,
    field_name: str,
    patterns: Iterable[re.Pattern[str]],
) -> None:
    if field_name in result["fields"]:
        return
    for pattern in patterns:
        match = pattern.search(search_text)
        if not match:
            continue
        value = _parse_numeric_token(match.group("value"))
        if value is None:
            continue
        unit = str(match.group("unit") or "").strip()
        _set_prospectus_issue_field(result, field_name, _shares_to_wan(value, unit), rule, search_text, match.start())
        return


def _extract_raw_share_patterns(
    result: dict[str, dict[str, object]],
    search_text: str,
    rule: str,
    field_name: str,
    patterns: Iterable[re.Pattern[str]],
) -> None:
    if field_name in result["fields"]:
        return
    for pattern in patterns:
        match = pattern.search(search_text)
        if not match:
            continue
        value = _parse_numeric_token(match.group("value"))
        if value is None:
            continue
        unit = str(match.group("unit") or "").strip()
        _set_prospectus_issue_field(result, field_name, _shares_to_raw(value, unit), rule, search_text, match.start())
        return


def _extract_online_issue_patterns(
    result: dict[str, dict[str, object]],
    search_text: str,
    rule: str,
    field_name: str,
) -> None:
    if field_name in result["fields"]:
        return
    for pattern in PROSPECTUS_ONLINE_ISSUE_PATTERNS:
        for match in pattern.finditer(search_text):
            lead_text = search_text[match.start() : match.start("value")]
            if any(marker in lead_text for marker in ONLINE_ISSUE_REJECT_MARKERS):
                continue
            value = _parse_numeric_token(match.group("value"))
            if value is None:
                continue
            unit = str(match.group("unit") or "").strip()
            _set_prospectus_issue_field(result, field_name, _shares_to_raw(value, unit), rule, search_text, match.start())
            return


def _extract_funds_patterns(
    result: dict[str, dict[str, object]],
    search_text: str,
    rule: str,
    field_name: str,
    patterns: Iterable[re.Pattern[str]],
) -> None:
    if field_name in result["fields"]:
        return
    for pattern in patterns:
        match = pattern.search(search_text)
        if not match:
            continue
        value = _parse_numeric_token(match.group("value"))
        if value is None:
            continue
        unit = str(match.group("unit") or "").strip()
        _set_prospectus_issue_field(result, field_name, _funds_to_yi(value, unit), rule, search_text, match.start())
        return


def _extract_apply_date_from_prospectus_text(result: dict[str, dict[str, object]], search_text: str, rule: str) -> None:
    if "APPLY_DATE" in result["fields"]:
        return
    for pattern in PROSPECTUS_APPLY_DATE_PATTERNS:
        match = pattern.search(search_text)
        if not match:
            continue
        _set_prospectus_issue_field(
            result,
            "APPLY_DATE",
            _normalize_issue_date(match.group("date")),
            rule,
            search_text,
            match.start(),
        )
        return


def _extract_issue_result_date_from_text(text: str) -> str:
    normalized_text = _normalize_fullwidth_text(text)
    matches: list[tuple[int, str]] = []
    for pattern in ISSUE_RESULT_DATE_PATTERNS:
        for match in pattern.finditer(normalized_text):
            raw_date = re.sub(r"\s+", "", match.group("date"))
            matches.append((match.start(), _normalize_issue_date(raw_date)))
    if not matches:
        return ""
    return max(matches, key=lambda item: item[0])[1]


def _build_prospectus_issue_search_targets(text: str) -> list[tuple[str, str]]:
    compact_text = _compact_text(_normalize_fullwidth_text(text))
    if not compact_text:
        return []
    section_text, section_anchor = _extract_compact_section(
        compact_text,
        PROSPECTUS_ISSUE_SECTION_PATTERNS,
        PROSPECTUS_ISSUE_SECTION_STOP_PATTERNS,
        fallback_radius=3600,
    )
    targets: list[tuple[str, str]] = []
    if section_text:
        targets.append((section_text, section_anchor or "本次发行概况"))
    if not section_text or section_text != compact_text:
        targets.append((compact_text, "全文"))
    return targets


def _extract_prospectus_issue_info_from_text(text: str) -> dict[str, object]:
    result: dict[str, dict[str, object]] = {
        "fields": {},
        "field_sources": {},
        "raw_snippets": {},
    }
    for search_text, rule in _build_prospectus_issue_search_targets(text):
        _extract_price_way_from_prospectus_text(result, search_text, rule)
        _extract_industry_from_prospectus_text(result, search_text, rule)
        _extract_numeric_patterns(result, search_text, rule, "ISSUE_PRICE", PROSPECTUS_ISSUE_PRICE_PATTERNS)
        _extract_numeric_patterns(result, search_text, rule, "AFTER_ISSUE_PE", PROSPECTUS_AFTER_ISSUE_PE_PATTERNS)
        _extract_numeric_patterns(result, search_text, rule, "INDUSTRY_PE_NEW", PROSPECTUS_INDUSTRY_PE_PATTERNS)
        _extract_wan_share_patterns(result, search_text, rule, "TOTAL_ISSUE_NUM", PROSPECTUS_TOTAL_ISSUE_PATTERNS)
        _extract_wan_share_patterns(result, search_text, rule, "TOTAL_SHARE_CAPITAL_AFTER_ISSUE", PROSPECTUS_TOTAL_CAPITAL_PATTERNS)
        _extract_wan_share_patterns(result, search_text, rule, "SUBSCRIPTION_LIMIT_WAN_SHARES", PROSPECTUS_SUBSCRIPTION_LIMIT_PATTERNS)
        _extract_online_issue_patterns(result, search_text, rule, "ONLINE_ISSUE_NUM")
        _extract_apply_date_from_prospectus_text(result, search_text, rule)

    limit_wan_shares = _parse_numeric_token(str(result["fields"].get("SUBSCRIPTION_LIMIT_WAN_SHARES") or ""))
    issue_price = _parse_numeric_token(str(result["fields"].get("ISSUE_PRICE") or ""))
    if (
        limit_wan_shares is not None
        and limit_wan_shares > 0
        and issue_price is not None
        and issue_price > 0
        and "TOP_APPLY_MARKETCAP" not in result["fields"]
    ):
        result["fields"]["TOP_APPLY_MARKETCAP"] = limit_wan_shares * issue_price
        result["field_sources"]["TOP_APPLY_MARKETCAP"] = result["field_sources"].get(
            "SUBSCRIPTION_LIMIT_WAN_SHARES",
            "prospectus:subscription_limit",
        )
        result["raw_snippets"]["TOP_APPLY_MARKETCAP"] = result["raw_snippets"].get("SUBSCRIPTION_LIMIT_WAN_SHARES", "")

    return result


def _rewrite_issue_info_sources(result: dict[str, object], source_prefix: str) -> dict[str, object]:
    rewritten = {
        "fields": dict(result.get("fields") or {}),
        "field_sources": dict(result.get("field_sources") or {}),
        "raw_snippets": dict(result.get("raw_snippets") or {}),
    }
    field_sources = rewritten["field_sources"]
    if isinstance(field_sources, dict):
        for field_name, source in list(field_sources.items()):
            source_text = str(source or "")
            if source_text.startswith("prospectus:"):
                field_sources[field_name] = f"{source_prefix}:{source_text.split(':', 1)[1]}"
            elif source_text:
                field_sources[field_name] = f"{source_prefix}:{source_text}"
    return rewritten


def _extract_issue_announcement_info_from_text(text: str) -> dict[str, object]:
    result = _extract_prospectus_issue_info_from_text(text)
    return _rewrite_issue_info_sources(result, "issue_announcement")


def _extract_subscription_distribution_from_text(text: str) -> list[dict[str, float]]:
    normalized = _normalize_fullwidth_text(text)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    rows: list[dict[str, float]] = []
    capture_remaining = 0
    for line in lines:
        compact_line = _compact_text(line)
        if "申购数量" in compact_line and ("户数" in compact_line or "账户数" in compact_line):
            capture_remaining = 60
            continue
        if capture_remaining <= 0:
            continue
        capture_remaining -= 1
        if any(marker in compact_line for marker in ("合计", "总计", "发行结果", "配售结果")):
            break
        match = SUBSCRIPTION_DISTRIBUTION_ROW_PATTERN.search(line.replace(",", ""))
        if not match:
            continue
        shares = _parse_numeric_token(match.group("shares"))
        accounts = _parse_numeric_token(match.group("accounts"))
        if shares is None or accounts is None or shares <= 0 or accounts <= 0:
            continue
        if "万股" in line[: max(line.find(str(match.group("shares"))), 0) + 20]:
            shares *= 10000
        item = {"apply_shares": float(shares), "accounts": float(accounts)}
        if item not in rows:
            rows.append(item)
    return rows


def _extract_issue_result_info_from_text(text: str) -> dict[str, object]:
    result = _rewrite_issue_info_sources(_extract_prospectus_issue_info_from_text(text), "issue_result")
    fields = result["fields"]
    field_sources = result["field_sources"]
    raw_snippets = result["raw_snippets"]
    compact_text = _compact_text(_normalize_fullwidth_text(text))

    typed_result: dict[str, dict[str, object]] = {
        "fields": fields if isinstance(fields, dict) else {},
        "field_sources": field_sources if isinstance(field_sources, dict) else {},
        "raw_snippets": raw_snippets if isinstance(raw_snippets, dict) else {},
    }
    result_date = _extract_issue_result_date_from_text(text)
    if result_date and "ISSUE_RESULT_DATE" not in typed_result["fields"]:
        typed_result["fields"]["ISSUE_RESULT_DATE"] = result_date
        typed_result["field_sources"]["ISSUE_RESULT_DATE"] = "issue_result:document_date"
        typed_result["raw_snippets"]["ISSUE_RESULT_DATE"] = ""

    _extract_numeric_patterns(typed_result, compact_text, "result", "ONLINE_VA_NUM", ISSUE_RESULT_VALID_ACCOUNT_PATTERNS)
    _extract_numeric_patterns(
        typed_result,
        compact_text,
        "result",
        "ONLINE_ALLOCATED_ACCOUNTS",
        ISSUE_RESULT_ALLOCATED_ACCOUNT_PATTERNS,
    )
    _extract_raw_share_patterns(typed_result, compact_text, "result", "ONLINE_VA_SHARES", ISSUE_RESULT_VALID_SHARE_PATTERNS)
    _extract_online_issue_patterns(typed_result, compact_text, "result", "ONLINE_ISSUE_NUM")
    _extract_funds_patterns(typed_result, compact_text, "result", "FROZEN_FUNDS_YI", ISSUE_RESULT_FROZEN_FUNDS_PATTERNS)
    _extract_numeric_patterns(typed_result, compact_text, "result", "ONLINE_ISSUE_LWR", ISSUE_RESULT_LWR_PATTERNS)
    _extract_numeric_patterns(typed_result, compact_text, "result", "ONLINE_ES_MULTIPLE", ISSUE_RESULT_MULTIPLE_PATTERNS)

    if "FRACTIONAL_THRESHOLD_SHARES" not in typed_result["fields"]:
        for pattern in ISSUE_RESULT_THRESHOLD_PATTERNS:
            match = pattern.search(compact_text)
            if not match:
                continue
            value = _parse_numeric_token(match.group("value"))
            if value is None:
                continue
            unit = str(match.group("unit") or "")
            if unit in {"万元", "元", "亿元"}:
                issue_price = _parse_numeric_token(str(typed_result["fields"].get("ISSUE_PRICE") or ""))
                if not issue_price:
                    continue
                shares = _money_to_wan(value, unit) * 10000 / issue_price
            else:
                shares = _shares_to_raw(value, unit)
            _set_prospectus_issue_field(
                typed_result,
                "FRACTIONAL_THRESHOLD_SHARES",
                shares,
                "result_threshold",
                compact_text,
                match.start(),
            )
            break

    if ISSUE_RESULT_TIME_PRIORITY_PATTERNS[0].search(compact_text) or ISSUE_RESULT_TIME_PRIORITY_PATTERNS[1].search(compact_text):
        typed_result["fields"].setdefault("FRACTIONAL_TIME_PRIORITY_REQUIRED", True)
        typed_result["field_sources"].setdefault("FRACTIONAL_TIME_PRIORITY_REQUIRED", "issue_result:time_priority")
        typed_result["raw_snippets"].setdefault("FRACTIONAL_TIME_PRIORITY_REQUIRED", _make_raw_snippet(compact_text, 0))

    distribution = _extract_subscription_distribution_from_text(text)
    if distribution:
        typed_result["fields"]["SUBSCRIPTION_AMOUNT_DISTRIBUTION"] = distribution
        typed_result["field_sources"]["SUBSCRIPTION_AMOUNT_DISTRIBUTION"] = "issue_result:distribution_table"
        typed_result["raw_snippets"]["SUBSCRIPTION_AMOUNT_DISTRIBUTION"] = ""

    for field_name, source in list(typed_result["field_sources"].items()):
        source_text = str(source or "")
        if source_text.startswith("prospectus:"):
            typed_result["field_sources"][field_name] = f"issue_result:{source_text.split(':', 1)[1]}"

    return typed_result


def extract_prospectus_issue_info(pdf_path: str | Path) -> dict[str, object]:
    cached = _load_parse_cache(pdf_path, "prospectus_issue_info")
    if cached is not _CACHE_MISSING and isinstance(cached, dict):
        return cached

    text = _read_pdf_text(pdf_path)
    if not text:
        result: dict[str, object] = {"fields": {}, "field_sources": {}, "raw_snippets": {}}
        return result
    result = _extract_prospectus_issue_info_from_text(text)
    _save_parse_cache(pdf_path, "prospectus_issue_info", result)
    return result


def extract_issue_announcement_info(pdf_path: str | Path) -> dict[str, object]:
    cached = _load_parse_cache(pdf_path, "issue_announcement_info")
    if cached is not _CACHE_MISSING and isinstance(cached, dict):
        return cached

    text = _read_pdf_text(pdf_path)
    if not text:
        result: dict[str, object] = {"fields": {}, "field_sources": {}, "raw_snippets": {}}
        return result
    result = _extract_issue_announcement_info_from_text(text)
    _save_parse_cache(pdf_path, "issue_announcement_info", result)
    return result


def extract_issue_result_info(pdf_path: str | Path) -> dict[str, object]:
    cached = _load_parse_cache(pdf_path, "issue_result_info")
    if cached is not _CACHE_MISSING and isinstance(cached, dict):
        return cached

    text = _read_pdf_text(pdf_path)
    if not text:
        result: dict[str, object] = {"fields": {}, "field_sources": {}, "raw_snippets": {}}
        return result
    result = _extract_issue_result_info_from_text(text)
    _save_parse_cache(pdf_path, "issue_result_info", result)
    return result


def _extract_windows(text: str, keywords: Iterable[str], radius: int = 2000) -> list[str]:
    windows: list[str] = []
    for keyword in keywords:
        start = 0
        while True:
            idx = text.find(keyword, start)
            if idx < 0:
                break
            windows.append(text[max(0, idx - 300) : idx + radius])
            start = idx + len(keyword)
    return windows


def _trim_comparable_window(window: str) -> str:
    stop_markers = (
        "\n发行人 ",
        "\n发行人\n",
        "三、 发行人主营业务情况",
        "三、发行人主营业务情况",
    )
    trimmed = window
    for marker in stop_markers:
        idx = trimmed.find(marker)
        if idx > 200:
            trimmed = trimmed[:idx]
    return trimmed


def _dedupe_codes(codes: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for code in codes:
        current = code.upper().strip()
        if current not in seen:
            seen.append(current)
    return seen


def _normalize_code(code: str) -> str:
    current = code.upper().strip()
    if "." in current:
        return current
    if current.startswith(("600", "601", "603", "605", "688")):
        return f"{current}.SH"
    if current.startswith(("000", "001", "002", "003", "300", "301")):
        return f"{current}.SZ"
    if current.startswith(("830", "831", "832", "833", "834", "835", "836", "837", "838")):
        return f"{current}.NQ"
    if current.startswith("920"):
        return f"{current}.BJ"
    return current


def _search_code_for_name(text: str, name: str) -> str | None:
    escaped = re.escape(name)
    patterns = [
        re.compile(rf"{escaped}\s*[（(]\s*(?P<code>\d{{6}}\.(?:SH|SZ|BJ|NQ))\s*[）)]", re.IGNORECASE),
        re.compile(rf"{escaped}[^\n]{{0,80}}?(?:股票代码|证券代码)\s*(?P<code>\d{{6}}\.(?:SH|SZ|BJ|NQ))", re.IGNORECASE),
        re.compile(rf"{escaped}[^\n]{{0,180}}?(?:股票代码|证券代码)\s*[：:\s]\s*(?P<code>\d{{6}})", re.IGNORECASE),
        re.compile(rf"{escaped}[^\n]{{0,80}}?[（(]\s*(?P<code>\d{{6}})\s*[）)]", re.IGNORECASE),
        re.compile(rf"{escaped}[^\n]{{0,120}}?(?P<code>\d{{6}}\.(?:SH|SZ|BJ|NQ))", re.IGNORECASE),
    ]
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return _normalize_code(match.group("code"))
    return _lookup_comparable_code_by_name(name)


def _search_direct_or_known_code_for_name(text: str, name: str) -> str | None:
    escaped = re.escape(name)
    patterns = [
        re.compile(rf"{escaped}\s*[（(]\s*(?P<code>\d{{6}}\.(?:SH|SZ|BJ|NQ))\s*[）)]", re.IGNORECASE),
        re.compile(rf"{escaped}\s*[（(]\s*(?P<code>\d{{6}})\s*[）)]", re.IGNORECASE),
        re.compile(rf"{escaped}\s*指\s*(?P<code>\d{{6}}\.(?:SH|SZ|BJ|NQ))", re.IGNORECASE),
        re.compile(rf"{escaped}\s*指\s*(?P<code>\d{{6}})", re.IGNORECASE),
        re.compile(
            rf"{escaped}[\u4e00-\u9fffA-Za-z]{{0,24}}\s*[（(]\s*(?P<code>\d{{6}}\.(?:SH|SZ|BJ|NQ))\s*[）)]",
            re.IGNORECASE,
        ),
        re.compile(
            rf"{escaped}[\u4e00-\u9fffA-Za-z]{{0,24}}\s*[（(]\s*(?P<code>\d{{6}})\s*[）)]",
            re.IGNORECASE,
        ),
    ]
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return _normalize_code(match.group("code"))
    return _lookup_comparable_code_by_name(name)


def _extract_glossary_labeled_comparable_codes(raw_text: str, lookup_text: str) -> list[str]:
    codes: list[str] = []
    entry_matches = list(GLOSSARY_ENTRY_PATTERN.finditer(raw_text))
    for index, match in enumerate(entry_matches):
        next_start = entry_matches[index + 1].start() if index + 1 < len(entry_matches) else min(len(raw_text), match.end() + 180)
        segment = raw_text[match.start() : next_start]
        compact_segment = _compact_text(segment)
        if "同行业可比公司" not in compact_segment and "可比公司" not in compact_segment:
            continue
        normalized_segment = _normalize_text(segment)
        code = _search_code_for_name(lookup_text, match.group("name").strip())
        if code:
            codes.append(code)
        for code_match in re.finditer(
            r"同行业可比公司[\u4e00-\u9fffA-Za-z]{2,60}?[（(]\s*(?P<code>\d{6}\.(?:SH|SZ|BJ|NQ))\s*[）)]",
            normalized_segment,
            re.IGNORECASE,
        ):
            codes.append(_normalize_code(code_match.group("code")))
    return _dedupe_codes(codes)


@lru_cache(maxsize=1)
def _load_local_comparable_name_code_index() -> dict[str, str]:
    index = dict(COMPARABLE_NAME_CODE_FALLBACKS)
    base_dir = Path(__file__).resolve().parents[1]
    candidate_dirs = (
        base_dir / "data" / "wind_db" / "fixed_fields",
        base_dir / "data" / "tushare_db" / "fixed_fields",
        base_dir / "data" / "temp_validation",
    )
    for candidate_dir in candidate_dirs:
        if not candidate_dir.exists():
            continue
        for file_path in candidate_dir.rglob("*.json"):
            if "fixed_fields" not in file_path.parts:
                continue
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            fields = payload.get("fields") or {}
            if not isinstance(fields, dict):
                continue
            name = str(fields.get("name") or "").strip()
            code = str(payload.get("code") or "").strip()
            if name and code:
                index.setdefault(name, code)
    return index


def _lookup_comparable_code_by_name(name: str) -> str | None:
    code = _load_local_comparable_name_code_index().get(name.strip())
    if not code:
        return None
    return _normalize_code(code)


def _extract_old_shares_result_from_text(text: str, source_file_type: str) -> OldSharesExtractionResult | None:
    listing_result = _extract_old_shares_from_listing_table(text, source_file_type)
    if listing_result is not None:
        return listing_result

    compact_text = _compact_text(text)
    for pattern in OLD_SHARE_NEGATIVE_PATTERNS:
        index = compact_text.find(pattern)
        if index >= 0:
            return OldSharesExtractionResult(
                value_wan_shares=0.0,
                source_file_type=source_file_type,
                source_rule="negative_phrase",
                source_anchor=pattern,
                raw_snippet=_make_raw_snippet(compact_text, index),
                confidence=0.8,
                unit="万股",
                pre_unrestricted_wan_shares=0.0,
            )

    windows = _extract_windows(text, OLD_SHARE_PATTERNS, radius=800)
    if not windows:
        windows = [text]

    for window in windows:
        compact_window = _compact_text(window)
        for pattern in OLD_SHARE_NEGATIVE_PATTERNS:
            index = compact_window.find(pattern)
            if index >= 0:
                return OldSharesExtractionResult(
                    value_wan_shares=0.0,
                    source_file_type=source_file_type,
                    source_rule="negative_phrase",
                    source_anchor=pattern,
                    raw_snippet=_make_raw_snippet(compact_window, index),
                    confidence=0.75,
                    unit="万股",
                    pre_unrestricted_wan_shares=0.0,
                )
        for pattern in OLD_SHARE_VALUE_PATTERNS:
            match = pattern.search(window)
            if not match:
                continue
            value = float(match.group("value").replace(",", ""))
            unit = match.group("unit")
            value_wan_shares = value if unit == "万股" else value / 10000
            anchor = next((keyword for keyword in OLD_SHARE_PATTERNS if keyword in compact_window), "关键词窗口")
            return OldSharesExtractionResult(
                value_wan_shares=value_wan_shares,
                source_file_type=source_file_type,
                source_rule="keyword_value",
                source_anchor=anchor,
                raw_snippet=_make_raw_snippet(_compact_text(match.group(0)), 0),
                confidence=0.7,
                unit=unit,
                pre_unrestricted_wan_shares=value_wan_shares,
            )
    return None


def _infer_listing_share_unit_from_payload(payload: str) -> str:
    leading_match = re.match(
        r"^-{0,4}(?P<share>[0-9,]+(?:\.[0-9]{1,4})?)(?P<pct>[0-9]{1,3}\.[0-9]{2,4}%?)",
        payload,
        re.IGNORECASE,
    )
    if not leading_match:
        return ""
    share_token = leading_match.group("share")
    if "," in share_token and "." not in share_token:
        return "股"
    if "." in share_token:
        return "万股"
    return ""


def _detect_listing_share_unit(compact_text: str, marker_index: int, payload: str) -> str:
    window = compact_text[max(0, marker_index - 10000) : marker_index]
    if any(marker in window for marker in ("数量（万股）", "持股数量（万股）", "股份数量（万股）")):
        return "万股"
    if any(marker in window for marker in ("数量（股）", "持股数量（股）", "股份数量（股）")):
        return "股"
    return _infer_listing_share_unit_from_payload(payload)


def _find_listing_table_row(search_text: str) -> tuple[str, int, int] | None:
    for marker in LISTING_OLD_SHARE_ROW_MARKERS:
        marker_index = search_text.find(marker)
        if marker_index >= 0:
            return marker, marker_index, marker_index + len(marker)

    for block_marker in LISTING_OLD_SHARE_BLOCK_MARKERS:
        block_index = search_text.find(block_marker)
        if block_index < 0:
            continue
        window = search_text[block_index + len(block_marker) : block_index + len(block_marker) + 220]
        for summary_marker in ("小计", "合计"):
            relative_index = window.find(summary_marker)
            if relative_index >= 0:
                marker_index = block_index + len(block_marker) + relative_index
                payload_start = marker_index + len(summary_marker)
                return f"{block_marker} -> {summary_marker}", marker_index, payload_start
    return None


def _extract_old_shares_from_listing_table(text: str, source_file_type: str) -> OldSharesExtractionResult | None:
    compact_text = _compact_text(text)
    section_text, section_anchor = _extract_compact_section(
        compact_text,
        LISTING_OLD_SHARE_SECTION_PATTERNS,
        LISTING_OLD_SHARE_SECTION_STOP_PATTERNS,
        chapter_anchors=LISTING_OLD_SHARE_CHAPTER_PATTERNS,
        fallback_radius=2600,
    )
    search_targets: list[tuple[str, str, bool]] = []
    if section_text:
        search_targets.append((section_text, section_anchor, True))
    if not section_text or section_text != compact_text:
        search_targets.append((compact_text, section_anchor, False))

    for search_text, anchor_prefix, used_section in search_targets:
        row_info = _find_listing_table_row(search_text)
        if row_info is None:
            continue
        marker, marker_index, payload_start = row_info
        payload = search_text[payload_start : payload_start + 260]
        unit = _detect_listing_share_unit(search_text, marker_index, payload)
        if not unit:
            unit = "万股" if "." in payload[:40] and "," not in payload[:40] else "股"

        percent_pattern = r"[0-9]{1,3}\.[0-9]{2,4}%?"
        if unit == "股":
            share_count_pattern = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)"
            patterns = (
                re.compile(
                    rf"^(?P<pre>{share_count_pattern})(?P<pre_pct>{percent_pattern})"
                    rf"(?P<post>{share_count_pattern})(?P<post_pct>{percent_pattern})",
                    re.IGNORECASE,
                ),
                re.compile(
                    rf"^(?P<pre>-{{2,}})(?P<post>{share_count_pattern})(?P<post_pct>{percent_pattern})",
                    re.IGNORECASE,
                ),
            )
        else:
            share_count_pattern = r"[0-9,]+(?:\.[0-9]{1,4})?"
            patterns = (
                re.compile(
                    rf"^(?P<pre>{share_count_pattern})(?P<pre_pct>{percent_pattern})"
                    rf"(?P<post>{share_count_pattern})(?P<post_pct>{percent_pattern})",
                    re.IGNORECASE,
                ),
                re.compile(
                    rf"^(?P<pre>-{{2,}})(?P<post>{share_count_pattern})(?P<post_pct>{percent_pattern})",
                    re.IGNORECASE,
                ),
            )

        match = None
        for current_pattern in patterns:
            match = current_pattern.search(payload)
            if match:
                break
        if not match:
            continue

        pre_value = match.group("pre").strip("-")
        source_anchor = f"{anchor_prefix or '股本结构表'} -> {marker}"
        if not pre_value:
            return OldSharesExtractionResult(
                value_wan_shares=0.0,
                source_file_type=source_file_type,
                source_rule="listing_table",
                source_anchor=source_anchor,
                raw_snippet=_make_raw_snippet(search_text, marker_index),
                confidence=0.95 if used_section else 0.88,
                unit=unit,
                pre_unrestricted_wan_shares=0.0,
            )
        value = float(pre_value.replace(",", ""))
        value_wan_shares = value if unit == "万股" else value / 10000
        return OldSharesExtractionResult(
            value_wan_shares=value_wan_shares,
            source_file_type=source_file_type,
            source_rule="listing_table",
            source_anchor=source_anchor,
            raw_snippet=_make_raw_snippet(search_text, marker_index),
            confidence=0.98 if used_section else 0.9,
            unit=unit,
            pre_unrestricted_wan_shares=value_wan_shares,
        )

    return None


def extract_old_shares_result(pdf_path: str | Path) -> OldSharesExtractionResult | None:
    cached = _load_parse_cache(pdf_path, "old_shares_result")
    if cached is not _CACHE_MISSING:
        if cached is None:
            return None
        cached_result = _old_shares_result_from_cache(cached)
        if cached_result is not None:
            return cached_result

    text = _read_pdf_text(pdf_path)
    if not text:
        _save_parse_cache(pdf_path, "old_shares_result", None)
        return None
    result = _extract_old_shares_result_from_text(text, _infer_pdf_file_type(pdf_path))
    _save_parse_cache(pdf_path, "old_shares_result", asdict(result) if result is not None else None)
    return result


def extract_old_shares(pdf_path: str | Path) -> float | None:
    result = extract_old_shares_result(pdf_path)
    if result is None:
        return None
    return result.value_wan_shares


def _extract_named_comparables(text: str) -> list[str]:
    names: list[str] = []
    sentence_patterns = [
        re.compile(r"(?:因此|故|基于上述标准[，,]?)?公司(?:选取|选择)(?:了)?(?P<names>[\u4e00-\u9fffA-Za-z、，,\s及和与]{2,180}?)作为(?:发行人的?|公司的?|同行业)?可比(?:上市|公众)?公司"),
        re.compile(r"基于上述标准，公司(?:选取|选择)了(?P<names>[^。；\n]+?)作为(?:发行人的?|公司的?|同行业)?可比(?:上市|公众)?公司"),
        re.compile(r"(?:选取|选择)(?!可比公司时)(?:了)?(?P<names>[^。；\n]+?)作为(?:发行人的?|公司的?|同行业)?可比(?:上市|公众)?公司"),
        re.compile(r"(?:基于上述标准，公司|公司)?(?:选取|选择)(?:了)?(?:国内)?上市公司(?P<names>[^。；\n]+?)作为(?:发行人的?|公司的?|同行业)?可比(?:上市|公众)?公司"),
    ]
    for pattern in sentence_patterns:
        for match in pattern.finditer(text):
            raw_names = re.sub(r"\s+", "", match.group("names"))
            for part in re.split(r"(?:以及|[、，,及和与])", raw_names):
                name = part.strip()
                name = re.sub(r"^(?:国内)?上市公司", "", name).strip()
                name = re.sub(r"(?:等|等公司|股份有限公司)$", "", name).strip()
                name = re.sub(r"[（(]\s*\d{6}(?:\.(?:SH|SZ|BJ|NQ))?\s*[）)]", "", name, flags=re.IGNORECASE).strip()
                if "的" in name:
                    candidate = name.rsplit("的", 1)[-1].strip()
                    if 1 < len(candidate) <= 24:
                        name = candidate
                if (
                    1 < len(name) <= 24
                    and "可比公司" not in name
                    and name not in {"公司", "上市公司", "公众公司", "同行业公司"}
                    and name not in names
                ):
                    names.append(name)
    return names


def _extract_explicit_selection_codes(text: str) -> list[str]:
    """Extract peers from sentences/tables that explicitly describe peer selection."""
    codes: list[str] = []
    selection_marker = re.compile(r"选取|选择|选为")
    for match in selection_marker.finditer(text):
        left = max(
            text.rfind("。", max(0, match.start() - 900), match.start()),
            text.rfind("；", max(0, match.start() - 900), match.start()),
        )
        left = max(left + 1, match.start() - 700)
        right_periods = [
            index
            for index in (
                text.find("。", match.end()),
                text.find("；", match.end()),
            )
            if index >= 0
        ]
        right = min(right_periods) + 1 if right_periods else min(len(text), match.end() + 900)
        sentence = text[left:right]
        if "可比公司" not in sentence and "可比上市公司" not in sentence and "可比公众公司" not in sentence:
            continue

        direct_codes = list(CODE_PATTERN.findall(sentence))
        if len(_dedupe_codes(direct_codes)) < 2:
            direct_codes = []
            for pattern in (NAME_CODE_PATTERN, GLOSSARY_PATTERN, PLAIN_NAME_CODE_PATTERN):
                direct_codes.extend(_normalize_code(item.group("code")) for item in pattern.finditer(sentence))
        if len(_dedupe_codes(direct_codes)) >= 2:
            corrections: dict[str, str] = {}
            for name, canonical_code in COMPARABLE_NAME_CODE_FALLBACKS.items():
                match_with_code = re.search(
                    rf"{re.escape(name)}\s*[（(]\s*(?P<code>\d{{6}}(?:\.(?:SH|SZ|BJ|NQ))?)\s*[）)]",
                    sentence,
                    re.IGNORECASE,
                )
                if match_with_code:
                    corrections[_normalize_code(match_with_code.group("code"))] = canonical_code
            direct_codes = [corrections.get(code, code) for code in direct_codes]
            codes.extend(direct_codes)
            continue

        sentence_names = _extract_named_comparables(sentence)
        sentence_names.extend(_extract_known_comparable_names(sentence))
        sentence_names = sorted(
            dict.fromkeys(sentence_names),
            key=lambda name: sentence.find(name) if sentence.find(name) >= 0 else len(sentence),
        )
        sentence_codes: list[str] = []
        for name in sentence_names:
            code = _search_direct_or_known_code_for_name(text, name)
            if code:
                sentence_codes.append(code)
        if sentence_codes:
            codes.extend(sentence_codes)
            continue

        # "选择如下公司" leaves the names to a multi-row table after the sentence.
        evidence = text[left : min(len(text), right + 2400)]
        for name in _extract_known_comparable_names(evidence):
            code = _search_direct_or_known_code_for_name(text, name)
            if code:
                codes.append(code)
    return _dedupe_codes(codes)


def _extract_best_known_comparable_table_codes(text: str) -> list[str]:
    """Choose the peer table/window containing the strongest known-name evidence."""
    best_codes: list[str] = []
    for anchor in ("可比上市公司", "可比公众公司", "同行业可比公司"):
        cursor = 0
        while True:
            index = text.find(anchor, cursor)
            if index < 0:
                break
            window = text[index : min(len(text), index + 4200)]
            current_codes: list[str] = []
            for name in _extract_known_comparable_names(window):
                code = _lookup_comparable_code_by_name(name) or _search_direct_or_known_code_for_name(text, name)
                if code:
                    current_codes.append(code)
            current_codes = _dedupe_codes(current_codes)
            if len(current_codes) > len(best_codes):
                best_codes = current_codes
            cursor = index + len(anchor)
    return best_codes


def _extract_known_comparable_names(text: str) -> list[str]:
    positions: list[tuple[int, str]] = []
    for name in COMPARABLE_NAME_CODE_FALLBACKS:
        index = text.find(name)
        if index >= 0:
            positions.append((index, name))
    return [name for _, name in sorted(positions, key=lambda item: item[0])]


def _extract_comparable_codes_from_section(section_text: str, full_text: str) -> list[str]:
    collected_codes: list[str] = []
    name_code_pairs = _collect_name_code_pairs(full_text)

    for pattern in (NAME_CODE_PATTERN, GLOSSARY_PATTERN, PLAIN_NAME_CODE_PATTERN):
        for match in pattern.finditer(section_text):
            collected_codes.append(_normalize_code(match.group("code")))
    collected_codes.extend(CODE_PATTERN.findall(section_text))

    named_codes: list[str] = []
    for name in _extract_named_comparables(section_text):
        code = _search_direct_or_known_code_for_name(full_text, name)
        if code:
            named_codes.append(code)

    if named_codes:
        return _dedupe_codes(collected_codes + named_codes)

    for name in _extract_row_company_names(section_text):
        code = _search_direct_or_known_code_for_name(full_text, name)
        if code:
            collected_codes.append(code)

    for name in _extract_known_comparable_names(section_text):
        code = _search_direct_or_known_code_for_name(full_text, name)
        if code:
            collected_codes.append(code)

    for name, code in name_code_pairs:
        if name in section_text:
            collected_codes.append(code)

    return _dedupe_codes(collected_codes)


def _collect_name_code_pairs(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for pattern in (NAME_CODE_PATTERN, GLOSSARY_PATTERN, PLAIN_NAME_CODE_PATTERN):
        for match in pattern.finditer(text):
            name = match.group("name").strip()
            code = _normalize_code(match.group("code"))
            if (name, code) not in pairs:
                pairs.append((name, code))
    return pairs


def _extract_row_company_names(window: str) -> list[str]:
    names: list[str] = []
    for match in ROW_NAME_PATTERN.finditer(window):
        name = match.group("name").strip()
        if name in ROW_NAME_STOPWORDS:
            continue
        if name not in names:
            names.append(name)
    return names


def _extract_comparable_companies_legacy(text: str) -> list[str]:
    lookup_text = _normalize_text(text)
    candidate_windows = _extract_windows(text, SPECIFIC_SECTION_PATTERNS, radius=3500)
    if not candidate_windows:
        candidate_windows = _extract_windows(text, GENERIC_SECTION_PATTERNS, radius=2500)
    if not candidate_windows:
        candidate_windows = [text]

    collected_codes: list[str] = []
    name_code_pairs = _collect_name_code_pairs(lookup_text)
    loose_match_codes: list[str] = []
    for window in candidate_windows:
        window = _trim_comparable_window(window)
        for pattern in (NAME_CODE_PATTERN, GLOSSARY_PATTERN, PLAIN_NAME_CODE_PATTERN):
            for match in pattern.finditer(window):
                collected_codes.append(_normalize_code(match.group("code")))
        collected_codes.extend(CODE_PATTERN.findall(window))
        for name in _extract_row_company_names(window):
            code = _search_direct_or_known_code_for_name(lookup_text, name)
            if code:
                collected_codes.append(code)
        for name in _extract_named_comparables(window):
            code = _search_direct_or_known_code_for_name(lookup_text, name)
            if code:
                collected_codes.append(code)
        for name, code in name_code_pairs:
            if name in window:
                loose_match_codes.append(code)

    if not collected_codes:
        for name in _extract_named_comparables(text):
            code = _search_direct_or_known_code_for_name(lookup_text, name)
            if code:
                collected_codes.append(code)
    if not collected_codes:
        collected_codes.extend(loose_match_codes)

    return _dedupe_codes(collected_codes)


def _filter_target_code(codes: list[str], target_code: str) -> list[str]:
    valid_codes = _dedupe_codes(
        COMPARABLE_CODE_ALIASES.get(code.upper().strip(), code.upper().strip())
        for code in codes
        if CODE_PATTERN.fullmatch(code)
    )
    if not target_code:
        return valid_codes
    return [code for code in valid_codes if code.split(".", 1)[0] != target_code]


def _extract_comparable_companies_from_text(text: str, target_code: str = "") -> list[str]:
    if not text:
        return []

    normalized_text = _normalize_text(text)
    explicit_selection_codes = _extract_explicit_selection_codes(normalized_text)
    if explicit_selection_codes:
        filtered_codes = _filter_target_code(explicit_selection_codes, target_code)
        if filtered_codes:
            return filtered_codes

    table_codes = _extract_best_known_comparable_table_codes(normalized_text)
    if len(table_codes) >= 3:
        filtered_codes = _filter_target_code(table_codes, target_code)
        if filtered_codes:
            return filtered_codes

    glossary_codes = _extract_glossary_labeled_comparable_codes(text, normalized_text)
    rejected_broad_generic_section = False
    specific_section_text, _ = _extract_compact_section(
        normalized_text,
        SPECIFIC_SECTION_PATTERNS,
        COMPARABLE_SECTION_STOP_PATTERNS,
        chapter_anchors=PROSPECTUS_BUSINESS_CHAPTER_PATTERNS,
        fallback_radius=3600,
    )
    if specific_section_text:
        specific_codes = _extract_comparable_codes_from_section(specific_section_text, normalized_text)
        if specific_codes:
            result_codes = _dedupe_codes(specific_codes + glossary_codes)
            filtered_codes = _filter_target_code(result_codes, target_code)
            if filtered_codes:
                return filtered_codes

    generic_section_text, generic_section_anchor = _extract_compact_section(
        normalized_text,
        GENERIC_SECTION_PATTERNS,
        COMPARABLE_SECTION_STOP_PATTERNS,
        chapter_anchors=PROSPECTUS_BUSINESS_CHAPTER_PATTERNS,
        fallback_radius=4200,
    )
    if generic_section_text:
        generic_codes = _extract_comparable_codes_from_section(generic_section_text, normalized_text)
        generic_has_selection_marker = any(
            marker in generic_section_text
            for marker in ("选取", "作为同行业可比公司", "可比公司基本情况", "可比公司选取标准")
        )
        if generic_codes and (
            generic_section_anchor not in GENERIC_SECTION_PATTERNS
            or generic_has_selection_marker
        ):
            result_codes = _dedupe_codes(generic_codes + glossary_codes)
            filtered_codes = _filter_target_code(result_codes, target_code)
            if filtered_codes:
                return filtered_codes
        elif generic_codes:
            rejected_broad_generic_section = True

    if glossary_codes:
        filtered_codes = _filter_target_code(list(glossary_codes), target_code)
        if filtered_codes:
            return filtered_codes

    if rejected_broad_generic_section:
        return []

    result_codes = _extract_comparable_companies_legacy(text)
    return _filter_target_code(result_codes, target_code)


def extract_comparable_companies(pdf_path: str | Path) -> list[str]:
    cached = _load_parse_cache(pdf_path, "comparable_companies")
    if cached is not _CACHE_MISSING and isinstance(cached, list):
        return [str(code) for code in cached]

    text = _read_pdf_text(pdf_path)
    if not text:
        _save_parse_cache(pdf_path, "comparable_companies", [])
        return []

    result_codes = _extract_comparable_companies_from_text(text, target_code=Path(pdf_path).stem[:6])
    _save_parse_cache(pdf_path, "comparable_companies", result_codes)
    return result_codes


def _clean_business_desc(text: str) -> str:
    cleaned = re.sub(r"^(?:主营业务情况|主营业务|公司简介|发行人基本情况)\s*", "", text or "")
    cleaned = re.sub(
        r"^(?!公司|发行人)(?:[\u4e00-\u9fffA-Za-z]{2,24})(?=(?:主要从事|是一家|主营业务为|是中国领先的|是国内领先的))",
        "公司",
        cleaned,
    )
    for marker in BUSINESS_TRIM_MARKERS:
        idx = cleaned.find(marker)
        if idx > 20:
            cleaned = cleaned[:idx]
    cleaned = re.sub(r"[，,]\s*除[\u4e00-\u9fffA-Za-z0-9（）()&\s]{1,40}外\s*$", "", cleaned)
    return cleaned.strip(" ：:;，,")


def _is_plausible_business_desc(text: str) -> bool:
    current = _clean_business_desc(text)
    if not current:
        return False
    if any(marker in current for marker in BUSINESS_REJECT_MARKERS):
        return False
    if re.search(r"除[\u4e00-\u9fffA-Za-z0-9（）()&\s]{1,30}外", current):
        return False
    if not any(keyword in current for keyword in BUSINESS_REQUIRED_KEYWORDS):
        return False
    if (current.startswith("公司是") or current.startswith("发行人是")) and not any(
        keyword in current
        for keyword in ("一家", "中国领先", "国内领先", "从事", "主营业务", "提供商", "供应商", "解决方案")
    ):
        return False
    return True


def _score_business_desc(text: str) -> tuple[int, int]:
    score = 0
    current = text or ""
    if current.startswith("公司是"):
        score += 3
    if current.startswith("公司是一家"):
        score += 4
    if current.startswith("公司是中国领先的"):
        score += 4
    if current.startswith("公司主要从事"):
        score += 3
    if current.startswith("公司主营业务为"):
        score += 3
    if current.startswith("公司专业从事") or current.startswith("公司专注于") or current.startswith("公司长期专注于"):
        score += 3
    if current.startswith("发行人是一家") or current.startswith("发行人主要从事") or current.startswith("发行人主营业务为"):
        score += 2
    if "领先" in current or "提供商" in current:
        score += 2
    if "解决方案" in current:
        score += 2
    if "研发、生产和销售" in current or "研发、制造和销售" in current:
        score += 1
    if 30 <= len(current) <= 180:
        score += 1
    if re.search(r"20\d{2}\s*年", current):
        score -= 2
    if any(name in current for name in COMPARABLE_NAME_CODE_FALLBACKS):
        score -= 4
    for marker in BUSINESS_NOISE_MARKERS:
        if marker in current:
            score -= 3 if marker in ("产能", "产量", "销量", "产销率") else 5
    return score, -len(current)


def _collect_business_sentence_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for pattern in BUSINESS_SENTENCE_PATTERNS:
        for match in pattern.finditer(text):
            raw_candidate = match.group(1).strip()
            if "行业内其他主要企业情况如下" in raw_candidate:
                continue
            candidate = _clean_business_desc(raw_candidate)
            if candidate and _is_plausible_business_desc(candidate):
                candidates.append(candidate)
    return candidates


def _pick_best_business_sentence(text: str) -> str:
    candidates = _collect_business_sentence_candidates(text)
    if not candidates:
        return ""
    return max(candidates, key=_score_business_desc)


def _extract_business_desc_from_text(text: str) -> str:
    normalized_text = _normalize_text(text)
    if not normalized_text:
        return ""

    candidate_sentences: list[str] = []
    preferred_sections: list[str] = []
    exact_section_text, _ = _extract_compact_section(
        normalized_text,
        ("发行人主营业务情况",),
        BUSINESS_SECTION_STOP_PATTERNS,
        chapter_anchors=PROSPECTUS_BUSINESS_CHAPTER_PATTERNS,
        fallback_radius=1800,
    )
    if exact_section_text:
        preferred_sections.append(exact_section_text)

    section_text, _ = _extract_compact_section(
        normalized_text,
        BUSINESS_PRIMARY_PATTERNS,
        BUSINESS_SECTION_STOP_PATTERNS,
        chapter_anchors=PROSPECTUS_BUSINESS_CHAPTER_PATTERNS,
        fallback_radius=1800,
    )
    if section_text:
        preferred_sections.append(section_text)

    fallback_section_text, _ = _extract_compact_section(
        normalized_text,
        BUSINESS_FALLBACK_PATTERNS,
        BUSINESS_SECTION_STOP_PATTERNS,
        chapter_anchors=PROSPECTUS_BUSINESS_CHAPTER_PATTERNS,
        fallback_radius=1800,
    )
    if fallback_section_text and fallback_section_text != section_text:
        preferred_sections.append(fallback_section_text)

    for current_section in preferred_sections:
        candidate_sentences.extend(_collect_business_sentence_candidates(current_section))
    candidate_sentences.extend(_collect_business_sentence_candidates(normalized_text))

    if candidate_sentences:
        return max(candidate_sentences, key=_score_business_desc)

    preferred_section = fallback_section_text or exact_section_text or section_text
    if preferred_section:
        fallback_section = _clean_business_desc(preferred_section[:240].strip())
        if fallback_section:
            return fallback_section

    return _clean_business_desc(normalized_text[:240].strip())


def extract_business_desc(pdf_path: str | Path) -> str:
    cached = _load_parse_cache(pdf_path, "business_desc")
    if cached is not _CACHE_MISSING and isinstance(cached, str):
        return cached

    text = _read_pdf_text(pdf_path)
    if not text:
        _save_parse_cache(pdf_path, "business_desc", "")
        return ""
    result = _extract_business_desc_from_text(text)
    _save_parse_cache(pdf_path, "business_desc", result)
    return result
