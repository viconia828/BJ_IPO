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
from industry_mapping import IndustryMapper
import param_tuning
import pdf_parser


DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_PARAMS = ROOT_DIR / "策略参数.txt"
DEFAULT_JSON = ROOT_DIR / "outputs" / "industry_classification_audit_latest.json"
DEFAULT_MARKDOWN = ROOT_DIR / "outputs" / "industry_classification_audit_latest.md"


INFERENCE_RULES: tuple[dict[str, Any], ...] = (
    {
        "label": "高端装备 / 汽车零部件",
        "strong": True,
        "patterns": (r"汽车零部件", r"汽车热管理", r"车载零部件", r"汽车内饰", r"汽车电子", r"汽车饰件"),
    },
    {
        "label": "医药生物 / 医疗器械",
        "strong": True,
        "patterns": (r"医疗器械", r"医用耗材", r"体外诊断", r"医用影像"),
    },
    {
        "label": "信息技术 / 半导体制造",
        "strong": True,
        "patterns": (r"半导体", r"集成电路", r"晶圆", r"芯片封装", r"光刻"),
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
)


MANUAL_REVIEW_NOTES: dict[str, dict[str, str]] = {}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="审计 replay 行业分类，并输出未分类及主营线索冲突样本。")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--params", type=Path, default=DEFAULT_PARAMS)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args()


def _business_description(code: str) -> tuple[str, str]:
    paths = param_tuning._resolve_replay_pdf_paths(code)
    path = paths.get("comparables") or paths.get("old_shares") or paths.get("listing")
    if path is None:
        return "", ""
    try:
        return pdf_parser.extract_business_desc(path), str(path)
    except Exception as exc:
        return "", f"{path} ({type(exc).__name__}: {exc})"


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
    current_parts = {part.strip() for part in current.split("/") if part.strip()}
    suggestion_parts = {part.strip() for part in suggestion.split("/") if part.strip()}
    return bool(current_parts & suggestion_parts)


def _audit_item(item: dict[str, Any], mapper: IndustryMapper) -> dict[str, Any]:
    code = str(item.get("SECURITY_CODE") or "").strip()
    name = str(item.get("SECURITY_NAME_ABBR") or "").strip()
    current_primary = str(item.get("industry_primary") or "未分类").strip()
    current_secondary = str(item.get("industry_secondary") or "未分类").strip()
    current = current_primary if current_secondary in {"", "未分类", current_primary} else f"{current_primary} / {current_secondary}"
    resolved = mapper.resolve_stock_industry(code, item)
    resolved_display = resolved.display_name
    business_desc, pdf_path = _business_description(code)
    inference_text = " ".join(
        str(value or "")
        for value in (
            name,
            item.get("SW_INDUSTRY"),
            item.get("INDUSTRY"),
            business_desc,
        )
    )
    suggestions = _infer_industries(inference_text)
    manual_review = dict(MANUAL_REVIEW_NOTES.get(code) or {})
    if manual_review:
        suggestions = [
            {
                "label": manual_review["suggested"],
                "strong": True,
                "evidence_patterns": ["主营业务人工语义复核"],
            }
        ]
    reasons: list[str] = []
    if current_primary == "未分类":
        reasons.append("unclassified")
    if current != resolved_display:
        reasons.append("stored_vs_current_mapping_mismatch")
    strong_suggestions = [suggestion for suggestion in suggestions if suggestion["strong"]]
    if (
        current_primary != "未分类"
        and strong_suggestions
        and not any(_is_compatible(current, suggestion["label"]) for suggestion in strong_suggestions)
    ):
        reasons.append("strong_business_keyword_conflict")
    if manual_review and current_primary != "未分类":
        reasons.append("manual_semantic_review")
    return {
        "code": code,
        "name": name,
        "listing_date": str(item.get("LISTING_DATE") or ""),
        "current_industry": current,
        "current_source": str(item.get("industry_source") or ""),
        "resolved_industry": resolved_display,
        "resolved_source": resolved.source,
        "raw_industry": str(item.get("INDUSTRY") or item.get("SW_INDUSTRY") or ""),
        "raw_industry_code": str(item.get("INDUSTRY_CODE") or ""),
        "suggestions": suggestions,
        "review_reasons": reasons,
        "review_note": manual_review.get("note", ""),
        "needs_review": bool(reasons),
        "business_desc": business_desc,
        "business_pdf": pdf_path,
    }


def _write_markdown(payload: dict[str, Any], path: Path) -> None:
    summary = payload["summary"]
    lines = [
        "# 行业分类审计",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 样本总数：{summary['sample_count']}",
        f"- 待复核：{summary['review_count']}",
        f"- 未分类：{summary['unclassified_count']}",
        f"- 映射未同步：{summary['mapping_mismatch_count']}",
        f"- 强主营关键词冲突：{summary['keyword_conflict_count']}",
        f"- 已分类但语义待复核：{summary['manual_semantic_review_count']}",
        "",
        "## 待复核清单",
        "",
        "| 代码 | 名称 | 当前分类 | 主营线索建议 | 触发原因 |",
        "|---|---|---|---|---|",
    ]
    for item in payload["review_items"]:
        suggestions = "；".join(
            f"{entry['label']}（{','.join(entry['evidence_patterns'])}）"
            for entry in item["suggestions"]
        ) or "—"
        reasons = "；".join(item["review_reasons"])
        lines.append(
            f"| {item['code']} | {item['name']} | {item['current_industry']} | {suggestions} | {reasons} |"
        )
        desc = re.sub(r"\s+", " ", item["business_desc"]).strip()[:360]
        lines.extend(("", f"> {item['code']} {item['name']}：{desc or '未提取到主营描述。'}", ""))
        if item.get("review_note"):
            lines.extend((f"> 复核意见：{item['review_note']}", ""))
    lines.extend(("## 全样本分类", "", "| 代码 | 名称 | 当前分类 | 来源 |", "|---|---|---|---|"))
    for item in payload["items"]:
        lines.append(f"| {item['code']} | {item['name']} | {item['current_industry']} | {item['current_source']} |")
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
    items = [_audit_item(item, mapper) for item in dataset.get("items") or []]
    review_items = [item for item in items if item["needs_review"]]
    payload = {
        "schema": "industry_classification_audit_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": str(dataset_path),
        "summary": {
            "sample_count": len(items),
            "review_count": len(review_items),
            "unclassified_count": sum("unclassified" in item["review_reasons"] for item in items),
            "mapping_mismatch_count": sum("stored_vs_current_mapping_mismatch" in item["review_reasons"] for item in items),
            "keyword_conflict_count": sum("strong_business_keyword_conflict" in item["review_reasons"] for item in items),
            "manual_semantic_review_count": sum("manual_semantic_review" in item["review_reasons"] for item in items),
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
