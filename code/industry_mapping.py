from __future__ import annotations

from dataclasses import dataclass
from typing import Any


KNOWN_BSE_INDUSTRY_MAP: dict[str, tuple[str, str]] = {
    "920011": ("高端装备", "机械设备"),
    "920012": ("信息技术", "半导体制造"),
    "920028": ("化工新材", "橡胶和塑料制品业"),
    "920036": ("消费服务", "消费电子"),
    "920050": ("医药生物", "医疗器械"),
    "920055": ("高端装备", "汽车零部件"),
    "920069": ("医药生物", "医疗器械"),
    "920076": ("化工新材", "金属新材料"),
    "920078": ("化工新材", "金属新材料"),
    "920086": ("高端装备", "机械设备"),
    "920119": ("高端装备", "仪器仪表制造业"),
    "920159": ("消费服务", "农林牧渔"),
    "920166": ("医药生物", "医疗器械"),
    "920168": ("高端装备", "汽车零部件"),
    "920177": ("高端装备", "机械设备"),
    "920180": ("医药生物", "医疗器械"),
    "920181": ("信息技术", "半导体制造"),
    "920183": ("消费服务", "消费电子"),
    "920187": ("高端装备", "机械设备"),
    "920188": ("高端装备", "机械设备"),
    "920206": ("化工新材", "化学制品"),
}

UNCLASSIFIED = ("未分类", "未分类")


@dataclass(frozen=True)
class IndustryInfo:
    primary: str
    secondary: str
    source: str

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
                if len(key) >= 3 and key in industry_name:
                    raw_value = self.mapping.get(key)
                    break
        if not raw_value:
            return UNCLASSIFIED
        return self._parse_mapping_value(raw_value)

    def _resolve_from_record(self, record: dict[str, Any]) -> tuple[str, str]:
        for key in ("SW_INDUSTRY", "INDUSTRY_SW", "INDUSTRY_NAME", "INDUSTRY"):
            primary, secondary = self.get_industry(record.get(key))
            if primary != "未分类":
                return primary, secondary
        return UNCLASSIFIED

    def resolve_stock_industry(self, code: str, record: dict[str, Any] | None = None) -> IndustryInfo:
        manual_industry = str(self.params.get("stock_industry", "auto")).strip()
        if manual_industry and manual_industry.lower() != "auto":
            primary, secondary = self._parse_mapping_value(manual_industry)
            return IndustryInfo(primary, secondary, "manual")

        if record:
            primary, secondary = self._resolve_from_record(record)
            if primary != "未分类":
                return IndustryInfo(primary, secondary, "record_mapping")

        if code in KNOWN_BSE_INDUSTRY_MAP:
            primary, secondary = KNOWN_BSE_INDUSTRY_MAP[code]
            return IndustryInfo(primary, secondary, "built_in_sample_map")

        return IndustryInfo(*UNCLASSIFIED, source="unclassified")

    def enrich_recent_ipos(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for record in records:
            code = str(record.get("SECURITY_CODE", "")).strip()
            industry = self.resolve_stock_industry(code, record)
            current = dict(record)
            current["industry_primary"] = industry.primary
            current["industry_secondary"] = industry.secondary
            current["industry_source"] = industry.source
            enriched.append(current)
        return enriched
