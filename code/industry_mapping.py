from __future__ import annotations

from dataclasses import dataclass
import json
import hashlib
from pathlib import Path
import re
from typing import Any


INDUSTRY_CLASSIFICATION_VERSION = 4
REFERENCE_PATH = Path(__file__).resolve().parents[1] / "data" / "industry_classification" / "market_reference.json"
REFERENCE_PAYLOAD = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
INDUSTRY_REFERENCE_SIGNATURE = hashlib.sha256(REFERENCE_PATH.read_bytes()).hexdigest()
MARKET_REFERENCE = REFERENCE_PAYLOAD["stocks"]
KNOWN_BSE_INDUSTRY_MAP = {code: (row["primary"], row["secondary"]) for code, row in MARKET_REFERENCE.items()}
# Compatibility exports; the dated reference is a provider cross-check, not a claim of manual review.
MANUALLY_REVIEWED_BSE_CODES = frozenset()
KNOWN_BSE_BUSINESS_TAGS = {
    "920025": ("轧辊", "辊环"),
    "920038": ("贵金属选矿剂", "矿业化学品"),
    "920165": ("天然及发酵原料", "化妆品功效原料"),
    "920176": ("活性多肽", "化妆品功效原料"),
    "920201": ("生物医用材料", "植入耗材"),
    "920202": ("汽车铝合金精密压铸件", "汽车零部件"),
    "920229": ("工业数字印刷", "喷墨打印设备"),
    "920268": ("手术缝线", "介入耗材", "制药设备"),
    "920285": ("净水器件", "冰箱滤水器", "泳池类滤芯"),
}
UNCLASSIFIED = ("未分类", "未分类")

# Legal industry divisions are broad evidence, never an invented fine market group.
CSRC_INDUSTRY_CODE_MAP = {
    "C22": ("轻工制造", "造纸"),
    "C26": ("基础化工", "未分类"),
    "C27": ("未分类", "未分类"),
    "C29": ("基础化工", "未分类"),
    "C30": ("未分类", "未分类"),
    "C32": ("有色金属", "未分类"),
    "C33": ("机械设备", "未分类"),
    "C34": ("机械设备", "通用设备"),
    "C35": ("未分类", "未分类"),
    "C36": ("汽车", "未分类"),
    "C38": ("未分类", "未分类"),
    "C39": ("未分类", "未分类"),
    "C40": ("机械设备", "仪器仪表"),
}
AMBIGUOUS_LEGAL_CANDIDATES = {
    "C30": ("建筑材料", "基础化工"),
    "C35": ("机械设备", "医药生物", "电子"),
    "C38": ("电力设备", "家用电器", "汽车"),
    "C39": ("电子", "计算机", "通信"),
}
LEGACY_GROUP_MAP = {
    "半导体制造": ("电子", "半导体"),
    "电子": ("电子", "其他电子"),
    "技术服务": ("计算机", "IT服务"),
    "通信": ("通信", "通信设备"),
    "化学制品": ("基础化工", "化学制品"),
    "非金属材料": ("基础化工", "非金属材料"),
    "橡胶和塑料制品业": ("基础化工", "未分类"),
    "金属新材料": ("有色金属", "金属新材料"),
    "电池材料": ("电力设备", "电池"),
    "机械设备": ("机械设备", "未分类"),
    "电气设备": ("电力设备", "电网设备"),
    "电力设备": ("电力设备", "未分类"),
    "汽车零部件": ("汽车", "汽车零部件"),
    "国防军工": ("国防军工", "未分类"),
    "电池": ("电力设备", "电池"),
    "金属制品业": ("机械设备", "通用设备"),
    "仪器仪表制造业": ("机械设备", "仪器仪表"),
    "食品饮料": ("食品饮料", "未分类"),
    "消费电子": ("电子", "消费电子"),
    "家用电器": ("家用电器", "未分类"),
    "轻工制造": ("轻工制造", "未分类"),
    "纺织服装": ("纺织服饰", "未分类"),
    "农林牧渔": ("农林牧渔", "未分类"),
    "商贸零售": ("商贸零售", "未分类"),
}
LEGACY_PRIMARIES = frozenset({"信息技术", "化工新材", "高端装备", "消费服务"})
MARKET_TEXT_MAP = {
    "半导体": ("电子", "半导体"),
    "汽车零部件": ("汽车", "汽车零部件"),
    "汽车配件": ("汽车", "汽车零部件"),
    "家电零部件": ("家用电器", "家电零部件"),
    "家用电器": ("家用电器", "未分类"),
    "小家电": ("家用电器", "小家电"),
    "消费电子": ("电子", "消费电子"),
    "计算机设备": ("计算机", "计算机设备"),
    "安防设备": ("计算机", "计算机设备"),
    "软件开发": ("计算机", "软件开发"),
    "通信设备": ("通信", "通信设备"),
    "电机": ("电力设备", "电机"),
    "输配电": ("电力设备", "电网设备"),
    "电网设备": ("电力设备", "电网设备"),
    "风电设备": ("电力设备", "风电设备"),
    "自动化设备": ("机械设备", "自动化设备"),
    "通用设备": ("机械设备", "通用设备"),
    "专用设备": ("机械设备", "未分类"),
    "医疗器械": ("医药生物", "医疗器械"),
    "中药": ("医药生物", "中药"),
    "生物制品": ("医药生物", "生物制品"),
    "化妆品": ("美容护理", "化妆品"),
    "耐火材料": ("建筑材料", "装修建材"),
    "造纸": ("轻工制造", "造纸"),
    "塑料": ("基础化工", "塑料"),
    "橡胶": ("基础化工", "橡胶"),
    "钢铁": ("钢铁", "未分类"),
    "有色金属": ("有色金属", "未分类"),
    "农药化肥": ("基础化工", "农化制品"),
    "互联网电商": ("商贸零售", "互联网电商"),
}
BROAD_LEGAL_NAMES = (
    "电气机械和器材制造业", "计算机、通信和其他电子设备制造业", "专用设备制造业",
    "医药制造业", "化学原料和化学制品制造业", "汽车制造业", "橡胶和塑料制品业",
    "非金属矿物制品业", "金属制品业",
)
VALID_VALUATION_GROUPS = frozenset(
    set(KNOWN_BSE_INDUSTRY_MAP.values())
    | {value for value in MARKET_TEXT_MAP.values() if value[1] != "未分类"}
    | {("钢铁", "普钢"), ("钢铁", "特钢"), ("医药生物", "化学制药"), ("计算机", "IT服务")}
)

# Only the issuer's products form industry evidence. Customers/applications are tags.
BUSINESS_RULES = (
    ("汽车", "汽车零部件", ("汽车零部件", "汽车配件", "汽车领域铝合金", "汽车用精密", "汽车饰件", "汽车热管理"), ("汽车产业链",)),
    ("家用电器", "家电零部件", ("净水器件", "冰箱滤水器", "家用净水滤芯", "咖啡机滤芯"), ("净水产业链",)),
    ("机械设备", "专用设备", ("轧辊", "辊环"), ("钢铁产业链",)),
    ("医药生物", "医疗器械", ("手术缝线", "医用耗材", "介入耗材", "植入耗材", "医疗器械的研发", "医疗器械产品"), ("医疗器械产业链",)),
    ("电子", "半导体", ("半导体设备", "半导体制造设备", "芯片封装", "电子封装材料", "晶圆制造", "集成电路设计"), ("半导体产业链",)),
    ("美容护理", "化妆品", ("化妆品功效原料", "化妆品活性原料"), ("化妆品产业链",)),
    ("电力设备", "电机", ("微特电机", "小功率电机"), ()),
    ("电力设备", "风电设备", ("风电主轴", "风电零部件"), ("风电产业链",)),
    ("家用电器", "小家电", ("除湿机", "空气调节设备"), ()),
    ("建筑材料", "装修建材", ("耐火材料",), ("高温工业产业链",)),
    ("基础化工", "化学制品", ("贵金属选矿剂", "选矿药剂", "化工助剂", "胶黏剂"), ()),
    ("机械设备", "自动化设备", ("工业机器人控制", "伺服系统", "智能自动化设备", "智能装备"), ()),
)
PRODUCT_KEYWORDS = tuple(dict.fromkeys(keyword for _, _, keywords, _ in BUSINESS_RULES for keyword in keywords))


def _business_text(record: dict[str, Any]) -> str:
    text = next((str(record.get(key) or "").strip() for key in
                 ("ISSUER_BUSINESS", "MAIN_BUSINESS", "company_description", "business_desc", "main_business")
                 if record.get(key)), "")
    if not text:
        return ""
    from pdf_parser import business_desc_has_subject_conflict
    if business_desc_has_subject_conflict(text, str(record.get("SECURITY_CODE") or "").split(".")[0]):
        return ""
    # Keep the issuer clause; incidental downstream markets must not determine its industry.
    return re.split(r"产品下游|下游应用|下游主要|主要客户|客户包括|产品应用于|应用于", text, maxsplit=1)[0]


def infer_business_groups(text: str, target_code: str = "") -> list[dict[str, Any]]:
    trusted = _business_text({"MAIN_BUSINESS": text, "SECURITY_CODE": target_code})
    suggestions = []
    for primary, secondary, keywords, chains in BUSINESS_RULES:
        matches = [keyword for keyword in keywords if keyword in re.sub(r"\s+", "", trusted)]
        if matches:
            suggestions.append({"label": f"{primary} / {secondary}", "primary": primary, "secondary": secondary,
                                "strong": True, "matched_patterns": matches, "chain_tags": list(chains)})
    return suggestions


@dataclass(frozen=True)
class IndustryInfo:
    primary: str
    secondary: str
    source: str
    statutory_industry: str = ""
    statutory_industry_code: str = ""
    business_tags: tuple[str, ...] = ()
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()
    chain_tags: tuple[str, ...] = ()
    tertiary: str = ""
    status: str = "confirmed"
    candidate_groups: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()

    @property
    def display_name(self) -> str:
        if self.primary == "未分类":
            return self.primary
        if self.secondary in {"未分类", self.primary, ""}:
            return self.primary
        return f"{self.primary} / {self.secondary}"


class IndustryMapper:
    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params
        self.mapping = params.get("industry_mapping", {})

    def _parse_mapping_value(self, raw_value: str) -> tuple[str, str]:
        parts = [re.sub(r"[ⅠⅡⅢ]+$", "", part.strip()) for part in raw_value.split("/", 1)]
        primary = parts[0] if parts and parts[0] else "未分类"
        secondary = parts[1] if len(parts) > 1 and parts[1] else "未分类"
        if primary in LEGACY_PRIMARIES:
            return LEGACY_GROUP_MAP.get(secondary, UNCLASSIFIED)
        return primary, secondary

    def get_industry(self, name: str | None) -> tuple[str, str]:
        text = str(name or "").strip()
        if not text:
            return UNCLASSIFIED
        if any(name in text for name in BROAD_LEGAL_NAMES):
            return UNCLASSIFIED
        hierarchy = re.split(r"--|/|>", text)
        if len(hierarchy) >= 2:
            pair = self._parse_mapping_value("/".join(hierarchy[:2]))
            if is_valid_valuation_group(*pair):
                return pair
        for key in sorted(MARKET_TEXT_MAP, key=len, reverse=True):
            if key in text:
                return MARKET_TEXT_MAP[key]
        raw = self.mapping.get(text)
        if not raw:
            for key in sorted(self.mapping, key=len, reverse=True):
                if len(key) >= 2 and key in text:
                    raw = self.mapping[key]
                    break
        return self._parse_mapping_value(str(raw)) if raw else UNCLASSIFIED

    def _statutory_fields(self, record: dict[str, Any]) -> tuple[str, str]:
        # SW market categories must never masquerade as statutory evidence.
        name = str(record.get("statutory_industry") or record.get("INDUSTRY") or "").strip()
        code = str(record.get("statutory_industry_code") or record.get("INDUSTRY_CODE")
                   or record.get("CSRC_INDUSTRY_CODE") or "").strip().upper()
        return name, code

    def resolve_stock_industry(self, code: str, record: dict[str, Any] | None = None) -> IndustryInfo:
        record = dict(record or {})
        normalized_code = str(code or record.get("SECURITY_CODE") or "").strip().split(".")[0]
        record.setdefault("SECURITY_CODE", normalized_code)
        statutory_name, statutory_code = self._statutory_fields(record)
        common = {"statutory_industry": statutory_name, "statutory_industry_code": statutory_code}
        manual = str(self.params.get("stock_industry", "auto")).strip()
        if manual and manual.lower() != "auto":
            return IndustryInfo(*self._parse_mapping_value(manual), "manual", **common,
                                confidence=1.0, evidence=("stock_industry",))

        reference = MARKET_REFERENCE.get(normalized_code)
        if reference:
            main = reference.get("main_business", "")
            tags = KNOWN_BSE_BUSINESS_TAGS.get(normalized_code, ())
            if not tags:
                tags = tuple(keyword for keyword in PRODUCT_KEYWORDS if keyword in main)
            signals = infer_business_groups(_business_text(record), normalized_code)
            conflicts = [item["label"] for item in signals
                         if (item["primary"], item["secondary"]) != (reference["primary"], reference["secondary"])]
            return IndustryInfo(reference["primary"], reference["secondary"], "market_reference", **common,
                                business_tags=tags, chain_tags=tuple(reference.get("chain_tags") or ()),
                                tertiary=reference.get("tertiary", ""), confidence=0.65 if conflicts else 0.95,
                                status="needs_review" if conflicts else "confirmed",
                                candidate_groups=tuple(dict.fromkeys(conflicts)),
                                review_reasons=("issuer_business_conflicts_with_dated_reference",) if conflicts else (),
                                evidence=(f"ifind_sw:{reference['reference_date']}", f"issuer_business:{main}"))

        structured = None
        for key in ("SW_INDUSTRY", "INDUSTRY_SW", "INDUSTRY_NAME", "INDUSTRY"):
            value = str(record.get(key) or "").strip()
            pair = self.get_industry(value)
            if pair[0] != "未分类":
                structured = (pair, key, value)
                break
        business = infer_business_groups(_business_text(record), normalized_code)
        business_pairs = list(dict.fromkeys((item["primary"], item["secondary"]) for item in business))
        if len(business_pairs) > 1 or (
            business_pairs and structured and structured[0][1] != "未分类" and structured[0] != business_pairs[0]
        ):
            candidates = [f"{a} / {b}" for a, b in business_pairs]
            if structured:
                candidates.append(" / ".join(structured[0]))
            return IndustryInfo(*UNCLASSIFIED, "evidence_conflict", **common, confidence=0.3,
                                status="needs_review", candidate_groups=tuple(dict.fromkeys(candidates)),
                                review_reasons=("conflicting_business_or_market_evidence",))
        if business_pairs:
            suggestion = business[0]
            return IndustryInfo(*business_pairs[0], "issuer_business_rule", **common, confidence=0.88,
                                business_tags=tuple(suggestion["matched_patterns"]),
                                chain_tags=tuple(suggestion["chain_tags"]),
                                evidence=tuple(f"issuer_product:{item}" for item in suggestion["matched_patterns"]))
        if structured:
            pair, key, value = structured
            fine = pair[1] != "未分类"
            return IndustryInfo(*pair, "record_mapping", **common, confidence=0.85 if fine else 0.5,
                                status="confirmed" if fine else "needs_review", evidence=(f"{key}:{value}",),
                                review_reasons=() if fine else ("broad_market_industry",))

        legal_code = re.sub(r"[-\s]", "", statutory_code)
        if legal_code and legal_code[0].isdigit():
            legal_code = "C" + legal_code
        if legal_code.startswith("C385"):
            return IndustryInfo("家用电器", "未分类", "industry_subcode_mapping", **common, confidence=0.6,
                                status="needs_review", evidence=(f"INDUSTRY_CODE:{legal_code}",),
                                review_reasons=("broad_market_industry",))
        pair = CSRC_INDUSTRY_CODE_MAP.get(legal_code[:3], UNCLASSIFIED)
        return IndustryInfo(*pair, "industry_code_mapping" if legal_code else "unclassified", **common,
                            confidence=0.45 if pair[0] != "未分类" else 0.0, status="needs_review",
                            evidence=(f"INDUSTRY_CODE:{legal_code}",) if legal_code else (),
                            candidate_groups=AMBIGUOUS_LEGAL_CANDIDATES.get(legal_code[:3], ()),
                            review_reasons=("insufficient_fine_industry_evidence",))

    def enrich_recent_ipos(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched = []
        for record in records:
            info = self.resolve_stock_industry(str(record.get("SECURITY_CODE") or ""), record)
            enriched.append({
                **record, "industry_primary": info.primary, "industry_secondary": info.secondary,
                "industry_tertiary": info.tertiary, "industry_source": info.source,
                "statutory_industry": info.statutory_industry, "statutory_industry_code": info.statutory_industry_code,
                "valuation_peer_group": info.display_name, "business_tags": list(info.business_tags),
                "chain_tags": list(info.chain_tags), "industry_confidence": info.confidence,
                "industry_evidence": list(info.evidence), "industry_status": info.status,
                "industry_candidates": list(info.candidate_groups), "industry_review_reasons": list(info.review_reasons),
                "industry_classification_version": INDUSTRY_CLASSIFICATION_VERSION,
            })
        return enriched


def is_valid_valuation_group(primary: str, secondary: str) -> bool:
    return (str(primary or "").strip(), str(secondary or "").strip()) in VALID_VALUATION_GROUPS
