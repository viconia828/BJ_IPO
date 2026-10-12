from __future__ import annotations

from pathlib import Path
import json
from tempfile import TemporaryDirectory
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
import report_generator
import reclassify_replay_industries


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    params = config_loader.load_params(ROOT_DIR / "策略参数.txt")
    mapper = IndustryMapper(params)

    for code, group, chain in (
        ("920285", "家用电器 / 家电零部件", "净水产业链"),
        ("920202.BJ", "汽车 / 汽车零部件", "汽车产业链"),
        ("920025", "机械设备 / 专用设备", "钢铁产业链"),
    ):
        info = mapper.resolve_stock_industry(code, {"INDUSTRY": "电气机械和器材制造业", "INDUSTRY_CODE": "C38"})
        _assert(info.display_name == group, f"market taxonomy: {code} mismatch", failures)
        _assert(chain in info.chain_tags, f"chain tag: {code} missing {chain}", failures)
        _assert(info.statutory_industry_code == "C38", "legal evidence must remain separate", failures)

    generic = mapper.resolve_stock_industry("fixture", {"INDUSTRY": "电气机械和器材制造业", "INDUSTRY_CODE": "C38"})
    _assert(generic.primary == "未分类" and generic.status == "needs_review", "C38 alone must not imply power equipment", failures)
    _assert("家用电器" in generic.candidate_groups, "C38 candidates must expose ambiguity", failures)
    paired, _ = valuation_engine._pick_secondary_industry_samples(
        {"primary": "机械设备", "secondary": "专用设备"}, [
            {"SECURITY_CODE": "roll", "industry_primary": "机械设备", "industry_secondary": "专用设备", "LD_AVERAGE_CHANGE": 100.0},
            {"SECURITY_CODE": "other", "industry_primary": "汽车", "industry_secondary": "专用设备", "LD_AVERAGE_CHANGE": 200.0},
        ])
    _assert([row["SECURITY_CODE"] for row in paired] == ["roll"], "same secondary with conflicting parent must not join peers", failures)
    future = mapper.resolve_stock_industry("fixture", {"INDUSTRY_CODE": "C38", "MAIN_BUSINESS": "公司专注于净水器件，主营冰箱滤水器的研发、生产和销售。"})
    _assert(future.display_name == "家用电器 / 家电零部件", "unseen issuer must use products before C38", failures)
    own_code = mapper.resolve_stock_industry("920999", {"MAIN_BUSINESS": "公司（920999.BJ）主营冰箱滤水器的研发、生产和销售。"})
    _assert(own_code.secondary == "家电零部件", "issuer's own code must not be mistaken for a peer", failures)
    future_roll = mapper.resolve_stock_industry("fixture", {"MAIN_BUSINESS": "公司主要从事轧辊、辊环的研发、生产和销售。"})
    _assert(future_roll.display_name == "机械设备 / 专用设备" and "钢铁产业链" in future_roll.chain_tags,
            "rolling equipment industry and steel chain must coexist", failures)
    contradictory = mapper.resolve_stock_industry("fixture", {"SW_INDUSTRY": "电力设备--电网设备--输变电设备",
        "MAIN_BUSINESS": "公司主营净水器件与冰箱滤水器的研发、生产和销售。"})
    _assert(contradictory.source == "evidence_conflict" and contradictory.status == "needs_review",
            "conflicting evidence must enter review", failures)
    hierarchy = mapper.resolve_stock_industry("fixture", {"SW_INDUSTRY": "家用电器--家电零部件Ⅱ--家电零部件Ⅲ"})
    _assert(hierarchy.display_name == "家用电器 / 家电零部件", "structured market hierarchy must parse", failures)
    combined = mapper.resolve_stock_industry("fixture", {"MAIN_BUSINESS": "公司生产汽车零部件和冰箱滤水器。"})
    _assert(combined.status == "needs_review" and len(combined.candidate_groups) == 2, "mixed businesses need review", failures)
    rows = report_generator._build_recent_items(mapper.enrich_recent_ipos([
        {"SECURITY_CODE": "920202"}, {"SECURITY_CODE": "920025"}]))
    _assert("汽车 / 汽车零部件" in rows[0][-1] and "钢铁产业链" in rows[1][-1], "recent table must show fine group and chain", failures)

    temp_root = ROOT_DIR / "tests" / "_tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=temp_root) as temp_dir:
        root = Path(temp_dir)
        cache_dir = root / "cache"
        cache_dir.mkdir()
        raw = {"SECURITY_CODE": "920202", "ISSUE_PRICE": 7.55, "AVERAGE_PRICE": 27.98,
               "LD_AVERAGE_CHANGE": 270.6, "industry_primary": "高端装备", "industry_secondary": "汽车零部件"}
        dataset_path = root / "dataset.json"
        dataset_path.write_text(json.dumps({"items": [raw]}), encoding="utf-8")
        (cache_dir / "920202.json").write_text(json.dumps({"item": raw, "pdf_signature": {"preserve": 1}}), encoding="utf-8")
        result = reclassify_replay_industries.migrate(dataset_path, cache_dir, root / "audit")
        migrated = json.loads(dataset_path.read_text(encoding="utf-8"))["items"][0]
        _assert(result["financial_and_price_fields_preserved"] and migrated["ISSUE_PRICE"] == 7.55
                and migrated["LD_AVERAGE_CHANGE"] == 270.6, "taxonomy migration must preserve financial evidence", failures)
        _assert(migrated["industry_primary"] == "汽车", "taxonomy migration must update stored rows", failures)
        payload = json.loads((cache_dir / "920202.json").read_text(encoding="utf-8"))
        _assert(payload["pdf_signature"] == {"preserve": 1}, "taxonomy migration must preserve PDF evidence", failures)

    expected = {
        "920201": "医药生物 / 医疗器械",
        "920268": "医药生物 / 医疗器械",
        "920176": "美容护理 / 化妆品",
        "920165": "美容护理 / 化妆品",
        "920038": "基础化工 / 化学制品",
        "920298": "机械设备 / 通用设备",
    }
    for code, expected_group in expected.items():
        actual = mapper.resolve_stock_industry(code, {"INDUSTRY": "化学原料和化学制品制造业", "INDUSTRY_CODE": "C26"})
        _assert(actual.display_name == expected_group, f"{code}: expected {expected_group}, got {actual.display_name}", failures)
        _assert(actual.confidence >= 0.95, f"{code}: audited code mapping should be high confidence", failures)

    enriched = mapper.enrich_recent_ipos(
        [{"SECURITY_CODE": "920201", "INDUSTRY": "医药制造业", "INDUSTRY_CODE": "C27"}]
    )[0]
    _assert(enriched.get("statutory_industry") == "医药制造业", "enrich: legal industry missing", failures)
    _assert(enriched.get("valuation_peer_group") == "医药生物 / 医疗器械", "enrich: peer group missing", failures)
    _assert("植入耗材" in (enriched.get("business_tags") or []), "enrich: business tags missing", failures)
    _assert("ifind_sw:2026-10-12" in enriched.get("industry_evidence", []), "enrich: evidence mismatch", failures)

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
    _assert("机械设备 / 自动化设备" in suggested_labels, "audit: issuer equipment signal missing", failures)
    _assert("医药生物 / 医疗器械" not in suggested_labels, "audit: downstream medical application caused false positive", failures)
    _assert("电子 / 半导体" not in suggested_labels, "audit: downstream semiconductor application caused false positive", failures)

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
