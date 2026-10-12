"""Collect iFind API data, import saved MCP evidence and build reviewable peer files.

MCP is called by the connected assistant; this CLI imports the saved tool responses.
Normal valuation runs read the cache and do not make natural-language MCP calls.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
from ifind_helper import fetch_financials, now_iso
from peer_fundamentals import derive_ttm, iso_day, merge_records, parse_api, parse_mcp

PERIODS = ["20231231", "20241231", "20250630", "20251231", "20260630"]


def json_file(path):
    return json.loads(path.read_text("utf-8"))


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), "utf-8")


def cases():
    result = {}
    for code in ("920202", "920157", "920025"):
        item = json_file(ROOT / f"data/offline_tuning/replay_items/{code}.json")["item"]
        result[code] = {"name": item["SECURITY_NAME_ABBR"], "candidate_codes": item["comparable_codes"],
                        "status": "candidates_pending_business_review"}
    result["920285"] = {"name": "远大健科", "candidate_codes": ["300272.SZ", "301332.SZ", "603408.SH", "920065.BJ", "300866.SZ"],
                        "status": "candidates_pending_business_review"}
    return result


def crosscheck_tushare(codes, periods, folder, *, refresh=False):
    import tushare_helper
    from local_file_db import LocalFileDB
    settings = tushare_helper._build_settings({})
    db = LocalFileDB(settings["cache_root"])
    checks = []
    for code in codes:
        path = folder / f"{code}_income.json"
        if path.exists() and not refresh:
            saved = json_file(path)
        else:
            rows, error = tushare_helper._call_tushare_api("income", {"ts_code": code, "report_type": "1"},
                "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,revenue,n_income_attr_p,update_flag", settings, db)
            saved = {"source": "tushare_income", "retrieved_at": now_iso(), "code": code,
                     "requested_report_type": "1", "error": error, "rows": rows}
            # Retain older raw snapshots when explicitly refreshing.
            if path.exists():
                archive = folder / "archive" / (path.stem + "_" + hashlib.sha256(path.read_bytes()).hexdigest()[:12] + ".json")
                if not archive.exists():
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    archive.write_bytes(path.read_bytes())
            write_json(path, saved)
        if saved.get("error"):
            checks.append({"code": code, "status": "unavailable", "reason": saved["error"]})
        for row in saved.get("rows", []):
            if row.get("end_date") in periods and str(row.get("report_type")) == "1":
                checks.append({"code": code, "period": row["end_date"], "row": row,
                               "retrieved_at": saved.get("retrieved_at"),
                               "source": "tushare_income", "evidence": str(path.relative_to(ROOT))})
    return checks


def compare_independent(financials, checks, precision_reviews=None):
    comparisons = []
    for financial in financials:
        candidates = [c for c in checks if c["code"] == financial["code"] and
                      c.get("period") == financial["report_period"].replace("-", "")]
        # A later crosscheck can change conflict/date status. Its availability
        # must travel with the resulting record, not inherit an older API date.
        financial["retrieved_at"] = max([str(financial.get("retrieved_at") or "")] +
                                        [str(c.get("retrieved_at") or "") for c in candidates])
        dates = sorted({iso_day(c["row"].get("f_ann_date") or c["row"].get("ann_date"))
                        for c in candidates} - {None})
        financial["tushare_publication_dates"] = dates
        own_date = financial.get("published_at")
        financial["publication_date_needs_review"] = bool(dates and (not own_date or any(d != own_date for d in dates)))
        # A conservative current-research availability gate, not resolution of
        # the original publication / revision conflict or proof of PIT history.
        financial["available_not_before"] = max(dates + ([own_date] if own_date else []), default=None)
        for metric, field in (("revenue", "revenue"), ("net_profit_parent", "n_income_attr_p")):
            value = financial["values"].get(metric)
            if not value:
                continue
            observations = [c for c in candidates if isinstance(c["row"].get(field), (int, float))]
            # Multiple vintages may disagree. Preserve them rather than choosing by value closeness.
            for other in observations:
                delta = other["row"][field] - value["value"]
                review = next((r for r in (precision_reviews or []) if r.get("verified") is True
                    and r.get("unit") in {"元", "万元", "亿元"} and type(r.get("decimals")) is int and 0 <= r["decimals"] <= 6
                    and (r.get("code"), r.get("report_period"), r.get("metric")) ==
                        (financial["code"], financial["report_period"], metric)
                    and abs(other["row"][field] - r.get("display_value_cny", float("inf"))) < .02), None)
                # Only a page-verified unit/precision can increase tolerance.
                # Round integer vendor values alone are not evidence of rounding.
                other_tolerance = 0
                if review:
                    unit_factor = {"元": 1, "万元": 10000, "亿元": 1e8}[review["unit"]]
                    other_tolerance = unit_factor * 10 ** -review["decimals"] / 2
                    financial["retrieved_at"] = max(financial["retrieved_at"], str(review.get("reviewed_at") or ""))
                entry = {"code": financial["code"], "report_period": financial["report_period"], "metric": metric,
                         "ifind": value["value"], "tushare": other["row"][field], "difference": delta,
                         "consistent": abs(delta) <= value["rounding_tolerance"] + other_tolerance + .02,
                         "verified_precision_review": review,
                         "tushare_announcement": other["row"].get("f_ann_date") or other["row"].get("ann_date"),
                         "ifind_evidence": value["evidence"], "tushare_evidence": other["evidence"]}
                comparisons.append(entry)
                if not entry["consistent"]:
                    value["conflict"] = True
                    if metric not in financial["conflicts"]:
                        financial["conflicts"].append(metric)
    return comparisons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="只导入已保存证据，不联网")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--crosscheck-tushare", action="store_true")
    parser.add_argument("--as-of", default=(date.today() + timedelta(days=1)).isoformat(), help="排他日期截断")
    args = parser.parse_args()
    folder = ROOT / "data/peer_fundamentals"
    case_map = cases()
    codes = sorted({c for v in case_map.values() for c in v["candidate_codes"]})
    records, profiles, publications, errors, evidence = [], [], [], [], []
    for path in sorted((folder / "mcp").glob("*.json")):
        # The initial multi-period probe is retained as evidence but never imported.
        if path.stem in {"financials", "dates_20260630"}:
            continue
        reference = str(path.relative_to(ROOT))
        parsed = parse_mcp(json_file(path), reference)
        records.extend(parsed["financials"])
        profiles.extend(parsed["profiles"])
        publications.extend(parsed["publication_dates"])
        errors.extend(parsed["warnings"])
        evidence.append({"path": reference, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    # Query NQ peers explicitly too; keep their trading venue distinct from A shares.
    if args.offline:
        api = [{**json_file(p), "evidence_path": str(p)} for p in sorted((folder / "api").glob("*.json"))]
    else:
        api = fetch_financials(codes, PERIODS, folder / "api", refresh=args.refresh)
    for envelope in api:
        path = Path(envelope["evidence_path"])
        reference = str(path.relative_to(ROOT))
        parsed = parse_api(envelope, reference)
        records.extend(parsed)
        if not parsed:
            errors.append(f"{reference}: API未返回可验证的财务记录")
        evidence.append({"path": reference, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    financials = merge_records(records, publications)
    checks = []
    if args.crosscheck_tushare and not args.offline:
        checks = crosscheck_tushare([c for c in codes if not c.endswith(".NQ")], PERIODS, folder / "tushare", refresh=args.refresh)
    else:
        for path in sorted((folder / "tushare").glob("*_income.json")):
            saved = json_file(path)
            for row in saved.get("rows", []):
                if row.get("end_date") in PERIODS and str(row.get("report_type")) == "1":
                    checks.append({"code": saved["code"], "period": row["end_date"], "row": row,
                                   "retrieved_at": saved.get("retrieved_at"),
                                   "evidence": str(path.relative_to(ROOT))})
    comparisons = compare_independent(financials, checks)
    for path in sorted((folder / "tushare").glob("*_income.json")):
        evidence.append({"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    companies = {}
    for code in codes:
        company_profiles = [p for p in profiles if p["code"] == code]
        company_financials = [f for f in financials if f["code"] == code]
        companies[code] = {"name": company_profiles[0]["name"] if company_profiles else code,
                           "profiles": company_profiles, "financials": company_financials,
                           "diagnostics": derive_ttm(company_financials, args.as_of)}
    summary = {"candidate_companies": len(codes), "profiles": sum(bool(c["profiles"]) for c in companies.values()),
               "financial_records": len(financials), "dated_records": sum(bool(f["published_at"]) for f in financials),
               "records_with_conflicts": sum(bool(f["conflicts"]) for f in financials),
               "independent_comparisons": len(comparisons), "independent_conflicts": sum(not c["consistent"] for c in comparisons),
               "independent_unique_fields": len({(c["code"], c["report_period"], c["metric"]) for c in comparisons}),
               "publication_dates_needing_review": sum(f.get("publication_date_needs_review", False) for f in financials),
               "ttm_available": sum(bool(c["diagnostics"]["available"]) for c in companies.values()),
               "comparative_basis_review": [code for code, c in companies.items() if c["diagnostics"].get("basis_warnings")],
               "mcp_channel_is_independent_vendor": False, "historical_pit_ready": 0}
    catalog = {"schema": "peer_fundamentals_v1", "generated_at": now_iso(), "as_of": args.as_of,
               "status": "research_only", "cases": case_map, "summary": summary, "companies": companies,
               "independent_crosschecks": comparisons, "evidence": evidence, "warnings": sorted(set(errors))}
    write_json(folder / "catalog.json", catalog)
    lines = ["# iFind可比公司资料核验", "", "资料用于盈利与业务复核，尚未改变估值F。",
        "API精确营收/归母利润、MCP主营/扣非/现金流/毛利率/披露日期共同入库；API与MCP是同一供应商，Tushare另作独立核验。",
        "当前查询到的历史财务尚未核验原始公告版本，不能冒充上市前已冻结资料。", "",
        "|代码|名称|财务期数|最新归母同比（%）|最新扣非同比（%）|TTM归母（万元）|冲突字段数|", "|---|---|---:|---:|---:|---:|---:|"]
    for code, company in companies.items():
        d = company["diagnostics"]
        def fmt(value):
            return "待确认" if value is None else f"{value:,.2f}"
        growth = d.get("growth_yoy_pct", {})
        profit = d.get("values", {}).get("net_profit_parent")
        lines.append(f"|{code}|{company['name']}|{len(company['financials'])}|{fmt(growth.get('net_profit_parent'))}|{fmt(growth.get('adjusted_net_profit_parent'))}|{fmt(profit / 10000 if profit is not None else None)}|{sum(len(f['conflicts']) for f in company['financials'])}|")
    lines += ["", "## 同期基数待复核", ""]
    for code, company in companies.items():
        for warning in company["diagnostics"].get("basis_warnings", []):
            label = {"revenue": "营收", "net_profit_parent": "归母利润"}.get(warning["metric"], warning["metric"])
            lines.append(f"- {company['name']}（{code}）{label}：历史金额推算同比 {warning['calculated_yoy_pct']:.2f}%，当前披露同比 {warning['reported_yoy_pct']:.2f}%；{warning['reason']}，暂缓TTM拼接。")
    lines += ["", "## 披露日期差异", "", "以下日期可能分别对应原披露、IPO材料披露或修订版本，仍需核对公告。当前研究采用较晚可得日期，历史回放不使用这些新采集资料。", ""]
    for f in financials:
        if f.get("publication_date_needs_review"):
            lines.append(f"- {companies[f['code']]['name']} {f['report_period']}：iFind {f['published_at']}；Tushare {'、'.join(f['tushare_publication_dates'])}。")
    lines += ["", "## 主营候选资料", ""]
    for case in case_map.values():
        lines += [f"### {case['name']}", ""]
        for code in case["candidate_codes"]:
            company = companies[code]
            description = company["profiles"][0]["business"] if company["profiles"] else "缺少可核验主营及披露日期，暂不加入核心估值池。"
            lines.append(f"- {company['name']}（{code}）：{description}")
        lines.append("")
    lines += ["## 核验摘要", "", "```json", json.dumps(summary, ensure_ascii=False, indent=2), "```", "",
              "冲突保留在catalog.json，受影响字段不进入TTM；暂无完整周期正常化或前瞻盈利预测。"]
    (folder / "review.md").write_text("\n".join(lines) + "\n", "utf-8")
    print(json.dumps({"catalog": str(folder / 'catalog.json'), **summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
