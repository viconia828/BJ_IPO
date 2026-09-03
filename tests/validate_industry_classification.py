from __future__ import annotations

from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT_DIR / "code"
TOOLS_DIR = ROOT_DIR / "tools"
for path in (CODE_DIR, TOOLS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import audit_industry_classification
import bse_ipo_valuation
import config_loader
from industry_mapping import IndustryMapper
import param_tuning
import valuation_engine


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    params = config_loader.load_params(ROOT_DIR / "策略参数.txt")
    mapper = IndustryMapper(params)

    expected = {
        "920201": "医药生物 / 医疗器械",
        "920268": "医药生物 / 医疗器械",
        "920176": "医药生物 / 生物制品",
        "920165": "医药生物 / 生物制品",
        "920038": "化工新材 / 金属新材料",
        "920298": "高端装备 / 机械设备",
    }
    for code, expected_group in expected.items():
        actual = mapper.resolve_stock_industry(code, {"INDUSTRY": "化学原料和化学制品制造业", "INDUSTRY_CODE": "C26"})
        _assert(actual.display_name == expected_group, f"{code}: expected {expected_group}, got {actual.display_name}", failures)
        _assert(actual.confidence >= 0.98, f"{code}: audited code mapping should be high confidence", failures)

    enriched = mapper.enrich_recent_ipos(
        [{"SECURITY_CODE": "920201", "INDUSTRY": "医药制造业", "INDUSTRY_CODE": "C27"}]
    )[0]
    _assert(enriched.get("statutory_industry") == "医药制造业", "enrich: legal industry missing", failures)
    _assert(enriched.get("valuation_peer_group") == "医药生物 / 医疗器械", "enrich: peer group missing", failures)
    _assert("植入耗材" in (enriched.get("business_tags") or []), "enrich: business tags missing", failures)
    _assert(enriched.get("industry_evidence") == ["manual_review_2026-09-03"], "enrich: evidence mismatch", failures)

    original_extract = param_tuning.pdf_parser.extract_prospectus_issue_info
    try:
        param_tuning.pdf_parser.extract_prospectus_issue_info = lambda _path: {
            "fields": {"INDUSTRY": "造纸和纸制品业", "INDUSTRY_CODE": "C22"},
            "field_sources": {"INDUSTRY": "fixture", "INDUSTRY_CODE": "fixture"},
        }
        supplemented = param_tuning._supplement_replay_record_industry(
            {"SECURITY_CODE": "fixture"},
            {"old_shares": Path("fixture.pdf"), "comparables": None},
        )
    finally:
        param_tuning.pdf_parser.extract_prospectus_issue_info = original_extract
    _assert(supplemented.get("INDUSTRY") == "造纸和纸制品业", "replay: local legal industry was not supplemented", failures)
    _assert(supplemented.get("INDUSTRY_CODE") == "C22", "replay: local industry code was not supplemented", failures)

    noisy_equipment_text = "公司主要从事智能装备研发、生产与销售，产品下游应用于汽车、医疗器械和半导体行业。"
    suggestions = audit_industry_classification._infer_industries(noisy_equipment_text)
    suggested_labels = {item["label"] for item in suggestions}
    _assert("高端装备 / 机械设备" in suggested_labels, "audit: issuer equipment signal missing", failures)
    _assert("医药生物 / 医疗器械" not in suggested_labels, "audit: downstream medical application caused false positive", failures)
    _assert("信息技术 / 半导体制造" not in suggested_labels, "audit: downstream semiconductor application caused false positive", failures)

    sample_codes = ["920072", "920069", "920166", "920180", "920050"]
    recent_ipos = mapper.enrich_recent_ipos(
        [
            {
                "SECURITY_CODE": code,
                "LISTING_DATE": f"2026-0{index + 1}-15",
                "LD_AVERAGE_CHANGE": change,
            }
            for index, (code, change) in enumerate(zip(sample_codes, (45.0, 260.0, 52.0, 58.0, 61.0)))
        ]
    )
    method2 = valuation_engine.method2_industry_momentum(
        issue_price=11.36,
        issue_pe=18.0,
        industry_pe=30.0,
        float_shares=1800.0,
        industry={"primary": "医药生物", "secondary": "医疗器械"},
        recent_ipos=recent_ipos,
        params=params,
        target_code="920201",
        target_listing_date="2026-09-10",
    )
    _assert(method2.get("available") is True, f"920201 method2 regression failed: {method2.get('reason')}", failures)
    _assert(method2.get("raw_sample_count") == 5, "920201 method2 should see five medical-device samples", failures)

    replay_merged, replay_summary = bse_ipo_valuation._overlay_replay_history_recent_ipos([], target_code="920201")
    replay_codes = {str(item.get("SECURITY_CODE") or "") for item in replay_merged}
    _assert(replay_summary.get("dataset_found") is True, "live flow: local replay dataset not found", failures)
    _assert("920072" in replay_codes, "live flow: year-to-date medical-device sample was not merged", failures)
    pit_merged, _ = bse_ipo_valuation._overlay_replay_history_recent_ipos(
        [],
        target_code="920201",
        target_date="2026-02-01",
    )
    pit_codes = {str(item.get("SECURITY_CODE") or "") for item in pit_merged}
    _assert("920050" in pit_codes, "live flow: visible pre-target replay sample was removed", failures)
    _assert("920180" not in pit_codes, "live flow: future replay sample leaked into target analysis", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("OK industry classification validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
