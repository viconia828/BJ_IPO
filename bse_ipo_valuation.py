from __future__ import annotations

from dataclasses import asdict
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import config_loader
import data_fetcher
import pdf_parser
import report_generator
import valuation_engine
import wind_helper
from industry_mapping import IndustryMapper


CODE_PATTERN = re.compile(r"^\d{6}$")


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _find_pdf(directory: Path, code: str, suffix: str) -> Path | None:
    candidates = _find_pdf_candidates(directory, code, suffix)
    if candidates:
        return candidates[0]
    return None


def _find_pdf_candidates(directory: Path, code: str, suffix: str) -> list[Path]:
    candidate = directory / f"{code}_{suffix}.pdf"
    if candidate.exists():
        return [candidate]

    aliases = {
        "上市公告书": ["上市公告书", "上市公告"],
        "招股说明书摘要": ["招股说明书摘要", "招股说明书", "招股书摘要", "招股书", "招股意向书摘要", "招股意向书"],
    }
    keywords = aliases.get(suffix, [suffix])
    other_keywords = [item for key, values in aliases.items() if key != suffix for item in values]
    if not directory.exists():
        return []

    pdf_files = sorted(directory.glob("*.pdf"))
    prioritized: list[Path] = []
    fallback: list[Path] = []
    for file_path in pdf_files:
        stem = file_path.stem
        if code not in stem:
            continue
        if any(keyword in stem for keyword in keywords):
            prioritized.append(file_path)
        elif not any(keyword in stem for keyword in other_keywords):
            fallback.append(file_path)

    return prioritized + fallback


def _pick_prospectus_pdf(directory: Path, code: str, usage: str) -> Path | None:
    candidates = _find_pdf_candidates(directory, code, "招股说明书摘要")
    if not candidates:
        return None

    def rank(file_path: Path) -> tuple[int, int, str]:
        stem = file_path.stem
        is_summary = "摘要" in stem
        if usage == "business":
            return (0 if is_summary else 1, 0 if "招股说明书" in stem else 1, stem)
        return (0 if not is_summary else 1, 0 if "招股说明书" in stem else 1, stem)

    return sorted(candidates, key=rank)[0]


def _resolve_old_shares(
    params: dict[str, Any],
    listing_pdf: Path | None,
    prospectus_pdf: Path | None = None,
) -> tuple[float, str, dict[str, Any] | None]:
    raw_value = params.get("old_shares_transfer", "auto")
    if raw_value == "auto":
        extracted_result = None
        selected_label = None
        for file_path, label in ((listing_pdf, "上市公告书"), (prospectus_pdf, "招股文件")):
            if not file_path:
                continue
            extracted_result = pdf_parser.extract_old_shares_result(file_path)
            if extracted_result is not None:
                selected_label = label
                break
        if extracted_result is None:
            return 0.0, "待确认（当前按 0 万股计）", None
        extraction_meta = asdict(extracted_result)
        extraction_meta.update(
            {
                "listing_pdf_found": listing_pdf is not None,
                "prospectus_pdf_found": prospectus_pdf is not None,
                "selected_source_label": selected_label or extracted_result.source_file_type,
                "fallback_used": bool(selected_label == "招股文件" and listing_pdf is not None),
            }
        )
        return (
            extracted_result.value_wan_shares,
            f"{extracted_result.value_wan_shares:.2f} 万股（PDF 提取：{extracted_result.source_file_type}）",
            extraction_meta,
        )

    numeric = float(raw_value)
    return numeric, f"{numeric:.2f} 万股（参数指定）", {
        "value_wan_shares": numeric,
        "source_file_type": "参数指定",
        "source_rule": "manual",
        "source_anchor": "old_shares_transfer",
        "raw_snippet": "",
        "confidence": 1.0,
        "unit": "万股",
        "pre_unrestricted_wan_shares": numeric,
        "listing_pdf_found": listing_pdf is not None,
        "prospectus_pdf_found": prospectus_pdf is not None,
        "selected_source_label": "参数指定",
        "fallback_used": False,
    }


def _load_comparable_codes(params: dict[str, Any], prospectus_pdf: Path | None) -> list[str]:
    codes = params.get("comparable_companies") or []
    if codes:
        return list(codes)
    if prospectus_pdf:
        return pdf_parser.extract_comparable_companies(prospectus_pdf)
    return []


def _calc_change_pct(issue_price: float | None, target_price: float | None) -> float | None:
    if not issue_price or not target_price:
        return None
    return (target_price / issue_price - 1) * 100


def run(code: str) -> str:
    params = config_loader.load_params("策略参数.txt")
    mapper = IndustryMapper(params)

    ipo_info = data_fetcher.fetch_ipo_info(code)
    industry = mapper.resolve_stock_industry(code, ipo_info)

    pdf_dir = Path("公告文件")
    listing_pdf = _find_pdf(pdf_dir, code, "上市公告书")
    old_shares_fallback_pdf = _pick_prospectus_pdf(pdf_dir, code, "old_shares")
    comparable_pdf = _pick_prospectus_pdf(pdf_dir, code, "comparables")
    business_pdf = _pick_prospectus_pdf(pdf_dir, code, "business")

    old_shares, old_shares_desc, old_shares_meta = _resolve_old_shares(params, listing_pdf, old_shares_fallback_pdf)
    total_issue_num = _safe_float(ipo_info.get("TOTAL_ISSUE_NUM")) or 0.0
    float_shares = total_issue_num + old_shares

    comparable_codes = _load_comparable_codes(params, comparable_pdf)
    comparable_data = []
    wind_summary = {
        "channel": str(params.get("wind_channel", "disabled")),
        "requested_codes": list(comparable_codes),
        "returned_codes": [],
        "fixed_cache_hits": [],
        "variable_cache_hits": [],
        "api_fetched_fixed": [],
        "api_fetched_variable": [],
        "stale_variable_used": [],
        "skipped_due_quota": [],
        "api_calls": 0,
        "quota_limit": int(params.get("wind_daily_request_quota", 20)),
        "quota_used_today": 0,
        "quota_remaining": int(params.get("wind_daily_request_quota", 20)),
        "local_computed_codes": [],
        "eastmoney_api_calls": 0,
        "eastmoney_fetched": [],
        "eastmoney_cache_hits": [],
        "eastmoney_fallback_used": [],
        "cross_validated_codes": [],
        "cross_validation_warnings": [],
        "reason": "",
    }
    if comparable_codes:
        wind_result = wind_helper.get_comparable_valuations(
            comparable_codes,
            str(params.get("wind_channel")),
            params,
        )
        comparable_data = wind_result.get("items") or []
        wind_summary = wind_result.get("summary") or wind_summary

    company_description = (
        pdf_parser.extract_business_desc(business_pdf)
        if business_pdf
        else str(ipo_info.get("MAIN_BUSINESS", "") or "")
    )
    if not company_description:
        company_description = str(ipo_info.get("MAIN_BUSINESS", "") or "")

    recent_ipos = mapper.enrich_recent_ipos(data_fetcher.fetch_recent_ipos(int(params.get("recent_months", 3))))
    recent_ipos = [item for item in recent_ipos if item.get("SECURITY_CODE") != code]

    issue_price = _safe_float(ipo_info.get("ISSUE_PRICE"))
    issue_pe = _safe_float(ipo_info.get("AFTER_ISSUE_PE"))
    industry_pe = _safe_float(ipo_info.get("INDUSTRY_PE_NEW"))

    method1 = valuation_engine.method1_comparable(issue_price, issue_pe, comparable_data, params)
    method2 = valuation_engine.method2_industry_momentum(
        issue_price=issue_price,
        issue_pe=issue_pe,
        industry_pe=industry_pe,
        float_shares=float_shares,
        industry={"primary": industry.primary, "secondary": industry.secondary, "display_name": industry.display_name},
        recent_ipos=recent_ipos,
        params=params,
        target_code=code,
        target_listing_date=ipo_info.get("LISTING_DATE"),
    )
    final = valuation_engine.composite_valuation(method1, method2, params)

    final_change_pct = _calc_change_pct(issue_price, final.get("target_price"))
    range_change_low = _calc_change_pct(issue_price, final.get("range_low"))
    range_change_high = _calc_change_pct(issue_price, final.get("range_high"))

    notes = valuation_engine.generate_notes(
        {
            "ipo_info": ipo_info,
            "float_shares": float_shares,
            "industry": {"primary": industry.primary, "secondary": industry.secondary},
            "method1": method1,
            "method2": method2,
            "old_shares_desc": old_shares_desc,
            "old_shares_meta": old_shares_meta,
            "comparable_codes": comparable_codes,
            "wind_summary": wind_summary,
        },
        params,
    )

    payload = {
        "analysis_date": date.today().isoformat(),
        "params": params,
        "ipo_info": ipo_info,
        "industry": {
            "primary": industry.primary,
            "secondary": industry.secondary,
            "source": industry.source,
            "display_name": industry.display_name,
        },
        "float_shares": float_shares,
        "old_shares_desc": old_shares_desc,
        "old_shares_meta": old_shares_meta,
        "company_description": company_description,
        "comparable_codes": comparable_codes,
        "comparable_data": comparable_data,
        "wind_summary": wind_summary,
        "recent_ipos": recent_ipos,
        "method1": method1,
        "method2": method2,
        "final": final,
        "final_change_pct": final_change_pct,
        "range_change_low": range_change_low,
        "range_change_high": range_change_high,
        "notes": notes,
    }

    return report_generator.generate_report(payload, "输出")


def _prompt_code_interactively() -> str | None:
    while True:
        code = input("请输入 6 位新股代码（例如 920177，直接回车可退出）：").strip()
        if not code:
            print("已取消，本次未生成报告。")
            return None
        if CODE_PATTERN.fullmatch(code):
            return code
        print("输入有误，请输入 6 位数字代码。")


def _normalize_code(code: str) -> str:
    cleaned = str(code or "").strip()
    if not CODE_PATTERN.fullmatch(cleaned):
        raise ValueError("请输入 6 位数字代码，例如 920177。")
    return cleaned


def main() -> int:
    interactive = len(sys.argv) <= 1
    if interactive:
        code = _prompt_code_interactively()
        if code is None:
            return 0
    else:
        code = sys.argv[1].strip()

    try:
        code = _normalize_code(code)
        if interactive:
            print(f"已收到代码 {code}，正在生成报告，请稍候。这一步会读取公告文件并整理估值数据，通常需要几十秒。")
        output_path = run(code)
    except FileNotFoundError as exc:
        print(f"文件缺失：{exc}")
        return 1
    except data_fetcher.DataFetcherError as exc:
        print(f"数据获取失败：{exc}")
        return 1
    except ValueError as exc:
        print(f"参数或输入错误：{exc}")
        return 1
    except Exception as exc:
        print(f"运行失败：{exc}")
        return 1

    report_path = Path(output_path).resolve()
    print(f"报告已生成：{report_path}")
    print(f"报告所在目录：{report_path.parent}")
    if interactive:
        print("可直接打开上面的完整路径，或到“输出”文件夹中查看生成的 PDF 报告。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
