from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Any


CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
CODE_DIR = ROOT_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import config_loader
from industry_mapping import IndustryMapper, is_valid_valuation_group
import param_tuning
import pdf_parser


DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_PARAMS = ROOT_DIR / "策略参数.txt"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "输出" / "分类审计"
DEFAULT_JSON = DEFAULT_OUTPUT_DIR / "行业分类审计_latest.json"
DEFAULT_MARKDOWN = DEFAULT_OUTPUT_DIR / "行业分类审计_latest.md"


INFERENCE_RULES: tuple[dict[str, Any], ...] = (
    {
        "label": "高端装备 / 汽车零部件",
        "strong": True,
        "patterns": (r"汽车零部件", r"汽车热管理", r"车载零部件", r"汽车内饰", r"汽车电子", r"汽车饰件"),
    },
    {
        "label": "医药生物 / 医疗器械",
        "strong": True,
        "patterns": (r"手术缝线", r"介入耗材", r"植入耗材", r"医用耗材", r"体外诊断", r"医用影像", r"医疗器械(?:的)?(?:研发|生产|制造|销售)"),
    },
    {
        "label": "医药生物 / 生物制品",
        "strong": True,
        "patterns": (r"活性多肽", r"生物发酵", r"化妆品功效原料", r"重组蛋白", r"生物制品(?:的)?(?:研发|生产|制造|销售)"),
    },
    {
        "label": "信息技术 / 半导体制造",
        "strong": True,
        "patterns": (r"半导体设备", r"集成电路(?:的)?(?:研发|设计|制造|封装)", r"晶圆制造", r"芯片封装", r"光刻"),
    },
    {
        "label": "消费服务 / 消费电子",
        "strong": True,
        "patterns": (r"消费电子", r"智能音箱", r"耳机", r"音响产品", r"智能家居"),
    },
    {
        "label": "消费服务 / 家用电器",
        "strong": True,
        "patterns": (r"除湿机", r"家用空调", r"商用空调", r"空气调节设备"),
    },
    {
        "label": "消费服务 / 农林牧渔",
        "strong": True,
        "patterns": (r"生猪养殖", r"种猪", r"饲料生产", r"水产养殖", r"农产品种植"),
    },
    {
        "label": "高端装备 / 电气设备",
        "strong": False,
        "patterns": (r"电气设备", r"输配电", r"开关柜", r"变压器", r"电机控制", r"变频器"),
    },
    {
        "label": "高端装备 / 机械设备",
        "strong": False,
        "patterns": (r"专用设备", r"智能装备", r"工业机器人", r"数控机床", r"机械设备", r"自动化设备"),
    },
    {
        "label": "化工新材 / 化学制品",
        "strong": False,
        "patterns": (r"化学制品", r"精细化工", r"化工助剂", r"树脂", r"涂料", r"胶黏剂"),
    },
    {
        "label": "化工新材 / 金属新材料",
        "strong": True,
        "patterns": (r"贵金属选矿", r"选矿药剂", r"有色金属冶炼", r"金属新材料"),
    },
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="审计 replay 行业分类，并输出未分类及主营线索冲突样本。")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--params", type=Path, default=DEFAULT_PARAMS)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument(
        "--deep-business-audit",
        action="store_true",
        help="重解析全部样本的主营描述；默认只解析未审定或低置信度样本。",
    )
    return parser.parse_args()


def _business_description(code: str) -> tuple[str, str, bool]:
    paths = param_tuning._resolve_replay_pdf_paths(code)
    path = paths.get("old_shares") or paths.get("comparables") or paths.get("listing")
    if path is None:
        return "", "", False
    try:
        description = pdf_parser.extract_business_desc(path)
        return description, str(path), pdf_parser.business_desc_has_subject_conflict(description, code)
    except Exception as exc:
        return "", f"{path} ({type(exc).__name__}: {exc})", False


def _local_statutory_industry(code: str) -> tuple[str, str, str, str]:
    paths = param_tuning._resolve_replay_pdf_paths(code)
    path = paths.get("old_shares") or paths.get("comparables")
    if path is None:
        return "", "", "", ""
    try:
        result = pdf_parser.extract_prospectus_issue_info(path)
        fields = dict(result.get("fields") or {}) if isinstance(result, dict) else {}
        return (
            str(fields.get("INDUSTRY") or "").strip(),
            str(fields.get("INDUSTRY_CODE") or "").strip(),
            str(path),
            "",
        )
    except Exception as exc:
        return "", "", str(path), f"{type(exc).__name__}: {exc}"


def _infer_industries(text: str) -> list[dict[str, Any]]:
    compact = re.sub(r"\s+", "", str(text or ""))
    suggestions: list[dict[str, Any]] = []
    for rule in INFERENCE_RULES:
        hits = [pattern for pattern in rule["patterns"] if re.search(pattern, compact, flags=re.IGNORECASE)]
        if hits:
            suggestions.append(
                {
                    "label": rule["label"],
                    "strong": bool(rule["strong"]),
                    "evidence_patterns": hits,
                }
            )
    return suggestions


def _is_compatible(current: str, suggestion: str) -> bool:
    return re.sub(r"\s+", "", current) == re.sub(r"\s+", "", suggestion)


def _audit_item(
    item: dict[str, Any],
    mapper: IndustryMapper,
    *,
    deep_business_audit: bool = False,
) -> dict[str, Any]:
    code = str(item.get("SECURITY_CODE") or "").strip()
    name = str(item.get("SECURITY_NAME_ABBR") or "").strip()
    current_primary = str(item.get("industry_primary") or "未分类").strip()
    current_secondary = str(item.get("industry_secondary") or "未分类").strip()
    current = current_primary if current_secondary in {"", "未分类", current_primary} else f"{current_primary} / {current_secondary}"
    record_for_resolution = dict(item)
    preliminary = mapper.resolve_stock_industry(code, record_for_resolution)
    should_parse_statutory = deep_business_audit or preliminary.source not in {"manual", "built_in_sample_map"}
    statutory_pdf = ""
    statutory_error = ""
    if should_parse_statutory:
        local_industry, local_industry_code, statutory_pdf, statutory_error = _local_statutory_industry(code)
        if not str(record_for_resolution.get("INDUSTRY") or "").strip() and local_industry:
            record_for_resolution["INDUSTRY"] = local_industry
        if not str(record_for_resolution.get("INDUSTRY_CODE") or "").strip() and local_industry_code:
            record_for_resolution["INDUSTRY_CODE"] = local_industry_code
    resolved = mapper.resolve_stock_industry(code, record_for_resolution)
    resolved_display = resolved.display_name
    should_audit_business = deep_business_audit or resolved.source not in {"manual", "built_in_sample_map"} or resolved.confidence < 0.9
    if should_audit_business:
        business_desc, pdf_path, subject_conflict = _business_description(code)
    else:
        business_desc, pdf_path, subject_conflict = "", "", False
    inference_text = " ".join(
        str(value or "")
        for value in (
            name,
            record_for_resolution.get("SW_INDUSTRY"),
            record_for_resolution.get("INDUSTRY"),
            business_desc,
        )
    )
    suggestions = [] if subject_conflict else _infer_industries(inference_text)
    reasons: list[str] = []
    if current_primary == "未分类":
        reasons.append("unclassified")
    if current != resolved_display:
        reasons.append("stored_vs_current_mapping_mismatch")
    if resolved.primary != "未分类" and not is_valid_valuation_group(resolved.primary, resolved.secondary):
        reasons.append("invalid_valuation_group")
    if resolved.primary != "未分类" and resolved.confidence < 0.7:
        reasons.append("low_confidence")
    if subject_conflict:
        reasons.append("business_description_subject_conflict")
    strong_suggestions = [suggestion for suggestion in suggestions if suggestion["strong"]]
    if (
        current_primary != "未分类"
        and strong_suggestions
        and not any(_is_compatible(current, suggestion["label"]) for suggestion in strong_suggestions)
    ):
        reasons.append("strong_business_keyword_conflict")
    return {
        "code": code,
        "name": name,
        "listing_date": str(item.get("LISTING_DATE") or ""),
        "current_industry": current,
        "current_source": str(item.get("industry_source") or ""),
        "resolved_industry": resolved_display,
        "resolved_source": resolved.source,
        "statutory_industry": resolved.statutory_industry,
        "statutory_industry_code": resolved.statutory_industry_code,
        "statutory_pdf": statutory_pdf,
        "statutory_parse_error": statutory_error,
        "valuation_peer_group": resolved_display,
        "business_tags": list(resolved.business_tags),
        "confidence": resolved.confidence,
        "evidence": list(resolved.evidence),
        "raw_industry": str(record_for_resolution.get("INDUSTRY") or record_for_resolution.get("SW_INDUSTRY") or ""),
        "raw_industry_code": str(record_for_resolution.get("INDUSTRY_CODE") or ""),
        "suggestions": suggestions,
        "review_reasons": reasons,
        "needs_review": bool(reasons),
        "business_desc": business_desc,
        "business_pdf": pdf_path,
        "business_description_subject_conflict": subject_conflict,
        "business_audit_performed": should_audit_business,
    }


def _write_markdown(payload: dict[str, Any], path: Path) -> None:
    summary = payload["summary"]
    lines = [
        "# 行业分类审计",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 样本总数：{summary['sample_count']}",
        f"- 主营审计模式：{'全量深度审计' if payload['deep_business_audit'] else '风险样本定向审计'}",
        f"- 待复核：{summary['review_count']}",
        f"- 未分类：{summary['unclassified_count']}",
        f"- 映射未同步：{summary['mapping_mismatch_count']}",
        f"- 强主营关键词冲突：{summary['keyword_conflict_count']}",
        f"- 主营主体疑似错误：{summary['subject_conflict_count']}",
        f"- 低置信度：{summary['low_confidence_count']}",
        f"- 非法估值组：{summary['invalid_group_count']}",
        "",
        "## 待复核清单",
        "",
        "| 代码 | 名称 | 法定行业 | 当前分类 | 最新估值组 | 标签 | 置信度 | 触发原因 |",
        "|---|---|---|---|---|---|---:|---|",
    ]
    for item in payload["review_items"]:
        suggestions = "；".join(
            f"{entry['label']}（{','.join(entry['evidence_patterns'])}）"
            for entry in item["suggestions"]
        ) or "—"
        reasons = "；".join(item["review_reasons"])
        lines.append(
            f"| {item['code']} | {item['name']} | {item['statutory_industry']} ({item['statutory_industry_code']}) | "
            f"{item['current_industry']} | {item['valuation_peer_group']} | {','.join(item['business_tags']) or '—'} | "
            f"{item['confidence']:.2f} | {reasons} |"
        )
        desc = re.sub(r"\s+", " ", item["business_desc"]).strip()[:360]
        lines.extend(("", f"> {item['code']} {item['name']}：{desc or '未提取到主营描述。'}", ""))
        if suggestions:
            lines.extend((f"> 主营线索建议：{suggestions}", ""))
    lines.extend(
        (
            "## 全样本分类",
            "",
            "| 代码 | 名称 | 法定行业 | 估值同类组 | 标签 | 置信度 | 来源 |",
            "|---|---|---|---|---|---:|---|",
        )
    )
    for item in payload["items"]:
        lines.append(
            f"| {item['code']} | {item['name']} | {item['statutory_industry']} ({item['statutory_industry_code']}) | "
            f"{item['valuation_peer_group']} | {','.join(item['business_tags']) or '—'} | {item['confidence']:.2f} | "
            f"{item['resolved_source']} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    dataset_path = args.dataset if args.dataset.is_absolute() else ROOT_DIR / args.dataset
    params_path = args.params if args.params.is_absolute() else ROOT_DIR / args.params
    json_path = args.json_output if args.json_output.is_absolute() else ROOT_DIR / args.json_output
    markdown_path = args.markdown_output if args.markdown_output.is_absolute() else ROOT_DIR / args.markdown_output
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    mapper = IndustryMapper(config_loader.load_params(params_path))
    items = [
        _audit_item(item, mapper, deep_business_audit=args.deep_business_audit)
        for item in dataset.get("items") or []
    ]
    review_items = [item for item in items if item["needs_review"]]
    payload = {
        "schema": "industry_classification_audit_v2",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": str(dataset_path),
        "deep_business_audit": bool(args.deep_business_audit),
        "summary": {
            "sample_count": len(items),
            "review_count": len(review_items),
            "unclassified_count": sum("unclassified" in item["review_reasons"] for item in items),
            "mapping_mismatch_count": sum("stored_vs_current_mapping_mismatch" in item["review_reasons"] for item in items),
            "keyword_conflict_count": sum("strong_business_keyword_conflict" in item["review_reasons"] for item in items),
            "subject_conflict_count": sum("business_description_subject_conflict" in item["review_reasons"] for item in items),
            "low_confidence_count": sum("low_confidence" in item["review_reasons"] for item in items),
            "invalid_group_count": sum("invalid_valuation_group" in item["review_reasons"] for item in items),
        },
        "review_items": review_items,
        "items": items,
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(payload, markdown_path)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    print(markdown_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
