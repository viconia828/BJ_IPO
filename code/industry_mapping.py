from __future__ import annotations

from dataclasses import dataclass
from typing import Any


INDUSTRY_CLASSIFICATION_VERSION = 2

KNOWN_BSE_INDUSTRY_MAP: dict[str, tuple[str, str]] = {
    "920011": ("高端装备", "机械设备"),
    "920012": ("信息技术", "半导体制造"),
    "920028": ("化工新材", "橡胶和塑料制品业"),
    "920036": ("消费服务", "消费电子"),
    "920050": ("医药生物", "医疗器械"),
    "920055": ("高端装备", "汽车零部件"),
    "920069": ("医药生物", "医疗器械"),
    "920072": ("医药生物", "医疗器械"),
    "920076": ("化工新材", "非金属材料"),
    "920078": ("化工新材", "金属新材料"),
    "920081": ("消费服务", "家用电器"),
    "920083": ("化工新材", "非金属材料"),
    "920086": ("高端装备", "汽车零部件"),
    "920096": ("高端装备", "电气设备"),
    "920119": ("高端装备", "机械设备"),
    "920125": ("信息技术", "半导体制造"),
    "920126": ("高端装备", "机械设备"),
    "920136": ("高端装备", "汽车零部件"),
    "920156": ("高端装备", "机械设备"),
    "920159": ("化工新材", "化学制品"),
    "920161": ("化工新材", "橡胶和塑料制品业"),
    "920166": ("医药生物", "医疗器械"),
    "920168": ("高端装备", "汽车零部件"),
    "920177": ("高端装备", "机械设备"),
    "920178": ("高端装备", "机械设备"),
    "920180": ("医药生物", "医疗器械"),
    "920181": ("信息技术", "半导体制造"),
    "920183": ("消费服务", "消费电子"),
    "920186": ("信息技术", "半导体制造"),
    "920187": ("高端装备", "汽车零部件"),
    "920188": ("化工新材", "橡胶和塑料制品业"),
    "920189": ("信息技术", "半导体制造"),
    "920191": ("化工新材", "非金属材料"),
    "920193": ("化工新材", "化学制品"),
    "920200": ("高端装备", "机械设备"),
    "920206": ("化工新材", "化学制品"),
    "920211": ("高端装备", "机械设备"),
    "920218": ("化工新材", "橡胶和塑料制品业"),
    "920220": ("高端装备", "汽车零部件"),
    "920222": ("高端装备", "电气设备"),
    # 2026-09-03 classification audit: legal industry is retained separately;
    # these are the peer groups used by valuation method 2.
    "920038": ("化工新材", "金属新材料"),
    "920059": ("高端装备", "汽车零部件"),
    "920065": ("消费服务", "商贸零售"),
    "920071": ("化工新材", "金属新材料"),
    "920079": ("高端装备", "汽车零部件"),
    "920093": ("高端装备", "机械设备"),
    "920107": ("化工新材", "化学制品"),
    "920117": ("高端装备", "机械设备"),
    "920138": ("信息技术", "半导体制造"),
    "920165": ("医药生物", "生物制品"),
    "920176": ("医药生物", "生物制品"),
    "920201": ("医药生物", "医疗器械"),
    "920238": ("高端装备", "金属制品业"),
    "920258": ("化工新材", "化学制品"),
    "920268": ("医药生物", "医疗器械"),
    "920288": ("消费服务", "轻工制造"),
    "920298": ("高端装备", "机械设备"),
}

KNOWN_BSE_BUSINESS_TAGS: dict[str, tuple[str, ...]] = {
    "920038": ("贵金属选矿剂", "矿业化学品"),
    "920165": ("天然及发酵原料", "化妆品功效原料"),
    "920176": ("活性多肽", "化妆品功效原料"),
    "920201": ("生物医用材料", "植入耗材"),
    "920268": ("手术缝线", "介入耗材", "制药设备"),
}

MANUALLY_REVIEWED_BSE_CODES = frozenset(
    {
        "920038",
        "920059",
        "920065",
        "920071",
        "920079",
        "920093",
        "920107",
        "920117",
        "920138",
        "920165",
        "920176",
        "920201",
        "920238",
        "920258",
        "920268",
        "920288",
        "920298",
    }
)

CSRC_INDUSTRY_CODE_MAP: dict[str, tuple[str, str]] = {
    "C22": ("消费服务", "轻工制造"),
    "C26": ("化工新材", "化学制品"),
    # C27 is too broad to choose among 中药/医疗器械/生物制品.  It must be
    # resolved by a curated code or a more specific industry/business phrase.
    "C29": ("化工新材", "橡胶和塑料制品业"),
    "C30": ("化工新材", "非金属材料"),
    "C32": ("化工新材", "金属新材料"),
    "C33": ("高端装备", "金属制品业"),
    "C34": ("高端装备", "机械设备"),
    "C35": ("高端装备", "机械设备"),
    "C36": ("高端装备", "汽车零部件"),
    "C38": ("高端装备", "电力设备"),
    "C39": ("信息技术", "电子"),
    "C40": ("高端装备", "仪器仪表制造业"),
}

UNCLASSIFIED = ("未分类", "未分类")
VALID_VALUATION_GROUPS = frozenset(
    {
        ("信息技术", "半导体制造"),
        ("信息技术", "电子"),
        ("信息技术", "技术服务"),
        ("信息技术", "通信"),
        ("化工新材", "化学制品"),
        ("化工新材", "非金属材料"),
        ("化工新材", "橡胶和塑料制品业"),
        ("化工新材", "金属新材料"),
        ("化工新材", "轻工制造"),
        ("化工新材", "电池材料"),
        ("高端装备", "机械设备"),
        ("高端装备", "电气设备"),
        ("高端装备", "电力设备"),
        ("高端装备", "汽车零部件"),
        ("高端装备", "国防军工"),
        ("高端装备", "电池"),
        ("高端装备", "金属制品业"),
        ("高端装备", "仪器仪表制造业"),
        ("医药生物", "中药"),
        ("医药生物", "医疗器械"),
        ("医药生物", "生物制品"),
        ("消费服务", "食品饮料"),
        ("消费服务", "消费电子"),
        ("消费服务", "家用电器"),
        ("消费服务", "轻工制造"),
        ("消费服务", "纺织服装"),
        ("消费服务", "农林牧渔"),
        ("消费服务", "商贸零售"),
    }
)


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

    @property
    def display_name(self) -> str:
        if self.primary == "未分类":
            return self.primary
        if self.secondary == "未分类" or self.secondary == self.primary:
            return self.primary
        return f"{self.primary} / {self.secondary}"


class IndustryMapper:
    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params
        self.mapping = params.get("industry_mapping", {})

    def _parse_mapping_value(self, raw_value: str) -> tuple[str, str]:
        if not raw_value:
            return UNCLASSIFIED
        parts = [part.strip() for part in raw_value.split("/", 1)]
        if len(parts) == 1:
            return parts[0] or "未分类", "未分类"
        return parts[0] or "未分类", parts[1] or "未分类"

    def get_industry(self, sw_industry_name: str | None) -> tuple[str, str]:
        if not sw_industry_name:
            return UNCLASSIFIED
        industry_name = str(sw_industry_name).strip()
        raw_value = self.mapping.get(industry_name)
        if not raw_value:
            for key in sorted((str(item).strip() for item in self.mapping), key=len, reverse=True):
                if len(key) >= 2 and key in industry_name:
                    raw_value = self.mapping.get(key)
                    break
        if not raw_value:
            return UNCLASSIFIED
        return self._parse_mapping_value(raw_value)

    def _resolve_from_record(self, record: dict[str, Any]) -> tuple[str, str, str, str]:
        for key in ("SW_INDUSTRY", "INDUSTRY_SW", "INDUSTRY_NAME", "INDUSTRY"):
            raw_value = str(record.get(key) or "").strip()
            primary, secondary = self.get_industry(raw_value)
            if primary != "未分类":
                return primary, secondary, key, raw_value
        return *UNCLASSIFIED, "", ""

    def _resolve_from_industry_code(self, record: dict[str, Any]) -> tuple[str, str]:
        raw_code = str(record.get("INDUSTRY_CODE") or record.get("CSRC_INDUSTRY_CODE") or "").strip().upper()
        if raw_code and raw_code[0].isdigit():
            raw_code = f"C{raw_code}"
        industry_code = raw_code[:3]
        return CSRC_INDUSTRY_CODE_MAP.get(industry_code, UNCLASSIFIED)

    def _statutory_fields(self, record: dict[str, Any] | None) -> tuple[str, str]:
        if not record:
            return "", ""
        statutory_industry = str(
            record.get("statutory_industry")
            or record.get("INDUSTRY")
            or record.get("INDUSTRY_NAME")
            or record.get("SW_INDUSTRY")
            or record.get("INDUSTRY_SW")
            or ""
        ).strip()
        statutory_code = str(
            record.get("statutory_industry_code")
            or record.get("INDUSTRY_CODE")
            or record.get("CSRC_INDUSTRY_CODE")
            or ""
        ).strip().upper()
        return statutory_industry, statutory_code

    def resolve_stock_industry(self, code: str, record: dict[str, Any] | None = None) -> IndustryInfo:
        normalized_code = str(code or "").strip()
        statutory_industry, statutory_code = self._statutory_fields(record)
        manual_industry = str(self.params.get("stock_industry", "auto")).strip()
        if manual_industry and manual_industry.lower() != "auto":
            primary, secondary = self._parse_mapping_value(manual_industry)
            return IndustryInfo(
                primary,
                secondary,
                "manual",
                statutory_industry,
                statutory_code,
                confidence=1.0,
                evidence=("stock_industry",),
            )

        if normalized_code in KNOWN_BSE_INDUSTRY_MAP:
            primary, secondary = KNOWN_BSE_INDUSTRY_MAP[normalized_code]
            manually_reviewed = normalized_code in MANUALLY_REVIEWED_BSE_CODES
            evidence = "manual_review_2026-09-03" if manually_reviewed else "curated_code_mapping"
            return IndustryInfo(
                primary,
                secondary,
                "built_in_sample_map",
                statutory_industry,
                statutory_code,
                KNOWN_BSE_BUSINESS_TAGS.get(normalized_code, ()),
                1.0 if manually_reviewed else 0.98,
                (evidence,),
            )

        if record:
            primary, secondary, field_name, raw_value = self._resolve_from_record(record)
            if primary != "未分类":
                return IndustryInfo(
                    primary,
                    secondary,
                    "record_mapping",
                    statutory_industry,
                    statutory_code,
                    confidence=0.8,
                    evidence=(f"{field_name}:{raw_value}",),
                )

            primary, secondary = self._resolve_from_industry_code(record)
            if primary != "未分类":
                return IndustryInfo(
                    primary,
                    secondary,
                    "industry_code_mapping",
                    statutory_industry,
                    statutory_code,
                    confidence=0.6,
                    evidence=(f"INDUSTRY_CODE:{statutory_code}",),
                )

        return IndustryInfo(
            *UNCLASSIFIED,
            source="unclassified",
            statutory_industry=statutory_industry,
            statutory_industry_code=statutory_code,
        )

    def enrich_recent_ipos(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for record in records:
            code = str(record.get("SECURITY_CODE", "")).strip()
            industry = self.resolve_stock_industry(code, record)
            current = dict(record)
            current["industry_primary"] = industry.primary
            current["industry_secondary"] = industry.secondary
            current["industry_source"] = industry.source
            current["statutory_industry"] = industry.statutory_industry
            current["statutory_industry_code"] = industry.statutory_industry_code
            current["valuation_peer_group"] = industry.display_name
            current["business_tags"] = list(industry.business_tags)
            current["industry_confidence"] = industry.confidence
            current["industry_evidence"] = list(industry.evidence)
            enriched.append(current)
        return enriched


def is_valid_valuation_group(primary: str, secondary: str) -> bool:
    return (str(primary or "").strip(), str(secondary or "").strip()) in VALID_VALUATION_GROUPS
