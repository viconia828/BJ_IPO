from __future__ import annotations

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


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _find_pdf(directory: Path, code: str, suffix: str) -> Path | None:
    candidate = directory / f"{code}_{suffix}.pdf"
    if candidate.exists():
        return candidate

    aliases = {
        "上市公告书": ["上市公告书", "上市公告"],
        "招股说明书摘要": ["招股说明书摘要", "招股说明书", "招股书摘要", "招股书", "招股意向书摘要", "招股意向书"],
    }
    keywords = aliases.get(suffix, [suffix])
    other_keywords = [item for key, values in aliases.items() if key != suffix for item in values]
    if not directory.exists():
        return None

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

    if prioritized:
        return prioritized[0]
    if fallback:
        return fallback[0]
    return None


def _resolve_old_shares(
    params: dict[str, Any],
    listing_pdf: Path | None,
    prospectus_pdf: Path | None = None,
) -> tuple[float, str]:
    raw_value = params.get("old_shares_transfer", "auto")
    if raw_value == "auto":
        extracted = None
        extracted_from = ""
        for file_path, label in ((listing_pdf, "上市公告书"), (prospectus_pdf, "招股文件")):
            if not file_path:
                continue
            extracted = pdf_parser.extract_old_shares(file_path)
            if extracted is not None:
                extracted_from = label
                break
        if extracted is None:
            return 0.0, "待确认（当前按 0 万股计）"
        return extracted, f"{extracted:.2f} 万股（PDF 提取：{extracted_from}）"

    numeric = float(raw_value)
    return numeric, f"{numeric:.2f} 万股（参数指定）"


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
    prospectus_pdf = _find_pdf(pdf_dir, code, "招股说明书摘要")

    old_shares, old_shares_desc = _resolve_old_shares(params, listing_pdf, prospectus_pdf)
    total_issue_num = _safe_float(ipo_info.get("TOTAL_ISSUE_NUM")) or 0.0
    float_shares = total_issue_num + old_shares

    comparable_codes = _load_comparable_codes(params, prospectus_pdf)
    comparable_data = []
    if comparable_codes and params.get("wind_channel", "disabled") != "disabled":
        comparable_data = wind_helper.get_comparable_valuations(comparable_codes, str(params.get("wind_channel")))

    company_description = (
        pdf_parser.extract_business_desc(prospectus_pdf)
        if prospectus_pdf
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
            "comparable_codes": comparable_codes,
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
        "company_description": company_description,
        "comparable_codes": comparable_codes,
        "comparable_data": comparable_data,
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


def main() -> int:
    code = sys.argv[1].strip() if len(sys.argv) > 1 else input("请输入新股代码: ").strip()
    if not code:
        print("请输入有效的新股代码，例如 920012")
        return 1

    try:
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

    print(f"报告已生成：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
