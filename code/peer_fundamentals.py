"""Normalize peer research evidence without silently promoting it into valuation.

Report period, publication date and collection date have distinct meanings.
Current vendor history is not an archived pre-IPO forecast or PIT financial vintage.
"""
from __future__ import annotations

from datetime import date
import json
import math
from pathlib import Path
import re

from ifind_helper import FIELDS

ROOT = Path(__file__).resolve().parents[1]
METRICS = {
    "营业收入": "revenue",
    "归属于母公司所有者的净利润": "net_profit_parent",
    "扣除非经常性损益后的归属母公司股东净利润": "adjusted_net_profit_parent",
    "经营活动产生的现金流量净额": "operating_cash_flow",
    "销售毛利率": "gross_margin_pct",
    "营业收入(同比增长率)": "reported_revenue_yoy_pct",
    "归属母公司股东的净利润(同比增长率)": "reported_profit_yoy_pct",
    "应收账款": "accounts_receivable",
    "存货": "inventory",
    "资产总计": "total_assets",
    "归属于母公司所有者权益合计": "equity_parent",
}

BALANCE_METRICS = {"accounts_receivable", "inventory", "total_assets", "equity_parent"}


def iso_day(value):
    text = str(value or "").strip()[:10].replace("/", "-")
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def parse_number(raw, unit="元"):
    """The cell's 万/亿 suffix overrides the header unit, including negative values."""
    text = str(raw).strip().replace(",", "")
    if text in {"", "None", "null", "--", "-", "—", "N/A", "nan"}:
        return None
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(亿|万)?(?:元|%)?", text)
    if not match:
        return None
    factor = {"亿": 1e8, "万": 1e4}.get(match[2], {"万元": 1e4, "亿元": 1e8}.get(unit, 1))
    value = float(match[1]) * factor
    if not math.isfinite(value):
        return None
    decimals = len(match[1].split(".")[1]) if "." in match[1] else 0
    return {"value": value, "rounding_tolerance": factor * 10 ** -decimals / 2, "raw": text}


def markdown_tables(text):
    header = None
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", c) for c in cells):
            continue
        if header is None:
            header = cells
        elif len(cells) == len(header):
            yield dict(zip(header, cells))


def parse_mcp(envelope, evidence):
    records, profiles, dates, warnings = [], [], [], []
    for block in envelope.get("result", {}).get("content", []):
        if block.get("type") != "text":
            continue
        try:
            response = json.loads(block["text"])
        except (ValueError, KeyError):
            warnings.append("MCP返回非结构化内容，未入库")
            continue
        if response.get("code") != 1:
            warnings.append("MCP查询未成功")
            continue
        data = response.get("data") or {}
        params = data.get("indicators_params") or {}
        publication_period = iso_day((params.get("定期报告实际披露日期") or {}).get("报告期"))
        for row in markdown_tables(data.get("answer") or ""):
            code = row.get("证券代码", "")
            if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ|NQ)", code):
                continue
            base = {"code": code, "name": row.get("证券简称"), "evidence": evidence,
                    "retrieved_at": envelope.get("retrieved_at") or envelope.get("retrieved_date"),
                    "source": "ifind_mcp", "vintage_status": "current_vendor_snapshot"}
            if row.get("主营业务"):
                profiles.append({**base, "business": row["主营业务"], "products": row.get("主营产品名称"),
                                 "available_date": None, "historical_pit_eligible": False})
            if "定期报告实际披露日期" in row:
                publication = iso_day(row["定期报告实际披露日期"])
                if publication_period and publication and publication > publication_period:
                    dates.append({**base, "report_period": publication_period, "published_at": publication})
                else:
                    warnings.append(f"{code}披露日期缺少明确报告期或日期无效，未绑定")
            period = iso_day(row.get("日期"))
            if not period:
                continue
            values, field_warnings = {}, []
            for header, raw in row.items():
                label = re.sub(r"[（(]单位[:：].*?[）)]", "", header).strip()
                metric = METRICS.get(label)
                if not metric:
                    continue
                unit_match = re.search(r"单位[:：]\s*([^）)]+)", header)
                unit = unit_match[1].strip() if unit_match else (params.get(label) or {}).get("单位", "").strip()
                expected_units = {"%"} if metric.endswith("_pct") else {"元", "万元", "亿元"}
                if unit not in expected_units:
                    field_warnings.append(f"{metric}单位不明，未入库")
                    continue
                scope = (params.get(label) or {}).get("报表类型")
                if not metric.endswith("_pct") and scope != "合并报表":
                    field_warnings.append(f"{metric}未确认合并报表，未入库")
                    continue
                parsed = parse_number(raw, unit)
                if parsed:
                    values[metric] = {**parsed, "unit": "%" if metric.endswith("_pct") else "CNY",
                                      "period_basis": "instant" if metric in BALANCE_METRICS else "YTD"}
                parameter_period = iso_day((params.get(label) or {}).get("报告期"))
                if parameter_period and parameter_period != period:
                    values.pop(metric, None)
                    field_warnings.append(f"{metric}行日期与参数报告期冲突，未入库")
            if values:
                records.append({**base, "report_period": period, "statement_scope": "consolidated",
                                "period_basis": "YTD", "values": values, "warnings": field_warnings,
                                "historical_pit_eligible": False})
            warnings.extend(field_warnings)
    return {"financials": records, "profiles": profiles, "publication_dates": dates, "warnings": warnings}


def parse_api(envelope, evidence):
    response = envelope.get("response") or {}
    if envelope.get("http_status") != 200 or response.get("errorcode") != 0:
        return []
    request = envelope.get("request") or {}
    expected = {p["indicator"]: p.get("indiparams", []) for p in request.get("indipara", [])}
    echoed = {p.get("function"): {a.get("name"): a.get("value") for a in p.get("params", [])}
              for p in response.get("inputParams", {}).get("params", [])}
    records = []
    for table in response.get("tables", []):
        code = table.get("thscode")
        if code not in request.get("codes", "").split(","):
            continue
        values, periods = {}, set()
        for indicator, metric in FIELDS.items():
            pars = expected.get(indicator, [])
            echo = echoed.get(indicator, {})
            if (len(pars) != 2 or pars[1] != "100" or echo.get("FL") != "100"
                    or echo.get("FDREPORT") != pars[0]):
                continue
            period = iso_day(pars[0])
            cell = (table.get("table") or {}).get(indicator)
            if not period or not isinstance(cell, list) or len(cell) != 1:
                continue
            parsed = parse_number(cell[0])
            if parsed:
                values[metric] = {**parsed, "unit": "CNY"}
                periods.add(period)
        if values and len(periods) == 1:
            records.append({"code": code, "source": "ifind_api", "report_period": periods.pop(),
                            "statement_scope": "consolidated", "period_basis": "YTD", "values": values,
                            "retrieved_at": envelope.get("retrieved_at"), "evidence": evidence,
                            "vintage_status": "current_vendor_snapshot", "historical_pit_eligible": False})
    return records


def merge_records(records, publications):
    """Keep disagreements visible; API and MCP are two channels of the SAME vendor."""
    grouped = {}
    for record in records:
        grouped.setdefault((record["code"], record["report_period"]), []).append(record)
    merged = []
    for (code, period), candidates in sorted(grouped.items()):
        values, conflicts = {}, []
        all_dates = sorted({p["published_at"] for p in publications
                            if p["code"] == code and p["report_period"] == period})
        for metric in set().union(*(r["values"] for r in candidates)):
            options = [{**r["values"][metric], "source": r["source"], "evidence": r["evidence"]}
                       for r in candidates if metric in r["values"]]
            options.sort(key=lambda r: (r["source"] != "ifind_api", r["rounding_tolerance"]))
            chosen = options[0]
            consistent = all(abs(v["value"] - chosen["value"]) <=
                             v["rounding_tolerance"] + chosen["rounding_tolerance"] + .02 for v in options)
            values[metric] = {**chosen, "observations": options, "conflict": not consistent}
            if not consistent:
                conflicts.append(metric)
        if len(all_dates) > 1:
            conflicts.append("published_at")
        merged.append({"code": code, "report_period": period, "values": values,
                       "statement_scope": "consolidated", "period_basis": "YTD",
                       "published_at": all_dates[0] if len(all_dates) == 1 else None,
                       "publication_evidence": [p for p in publications if p["code"] == code and p["report_period"] == period],
                       "retrieved_at": max(str(r.get("retrieved_at") or "") for r in candidates),
                       "conflicts": conflicts, "historical_pit_eligible": False,
                       "warnings": sorted({w for r in candidates for w in r.get("warnings", [])}),
                       "source_family": "ifind", "channel_agreement_is_independent_validation": False})
    return merged


def visible_record(record, as_of, *, historical=False):
    cutoff = iso_day(as_of)
    published = iso_day(record.get("published_at"))
    collected = iso_day(record.get("retrieved_at"))
    if not cutoff or not published or published >= cutoff or not collected or collected >= cutoff:
        return False
    conservative_date = iso_day(record.get("available_not_before"))
    if conservative_date and conservative_date >= cutoff:
        return False
    if historical and not record.get("historical_pit_eligible"):
        return False
    return record.get("statement_scope") == "consolidated" and record.get("period_basis") == "YTD"


def derive_ttm(records, as_of):
    """Research diagnostics only; require all components and their publication dates."""
    by_period = {r["report_period"]: r for r in records if visible_record(r, as_of)}
    if not by_period:
        return {"available": False, "reason": "缺少已披露且已采集的财务记录"}
    latest = max(by_period)
    year = int(latest[:4])
    periods = [latest] if latest.endswith("12-31") else [f"{year-1}-12-31", latest, f"{year-1}{latest[4:]}"]
    if any(p not in by_period for p in periods):
        return {"available": False, "reason": "TTM所需年度和同期财务不齐"}
    # Current comparative figures can have been restated even when two vendors
    # agree on the original previous-period report. Do not silently combine them.
    basis_warnings = []
    previous_record = by_period.get(f"{year-1}{latest[4:]}", {})
    for metric, growth_metric in (("revenue", "reported_revenue_yoy_pct"),
                                 ("net_profit_parent", "reported_profit_yoy_pct")):
        current = by_period[latest]["values"].get(metric)
        prior = previous_record.get("values", {}).get(metric)
        reported = by_period[latest]["values"].get(growth_metric)
        if current and prior and reported and prior["value"] > 0 and not any(v.get("conflict") for v in (current, prior, reported)):
            calculated = (current["value"] / prior["value"] - 1) * 100
            if abs(calculated - reported["value"]) > .10:
                basis_warnings.append({"metric": metric, "calculated_yoy_pct": calculated,
                    "reported_yoy_pct": reported["value"], "reason": "同期基数/合并范围/修订版本待核验"})
    if basis_warnings:
        return {"available": False, "end_date": latest, "components": periods,
                "reason": "当前披露同比与历史基数不一致，暂缓TTM拼接", "basis_warnings": basis_warnings}
    result = {"available": True, "end_date": latest, "components": periods, "values": {}, "growth_yoy_pct": {},
              "historical_pit_eligible": False, "status": "research_only"}
    for metric in ["revenue", "net_profit_parent", "adjusted_net_profit_parent", "operating_cash_flow"]:
        cells = [by_period[p]["values"].get(metric) for p in periods]
        if any(c is None or c.get("conflict") for c in cells):
            continue
        result["values"][metric] = cells[0]["value"] if len(cells) == 1 else cells[0]["value"] + cells[1]["value"] - cells[2]["value"]
        previous = by_period.get(f"{year-1}{latest[4:]}", {}).get("values", {}).get(metric)
        current = by_period[latest]["values"].get(metric)
        if previous and current and not previous.get("conflict") and not current.get("conflict") and previous["value"] > 0:
            result["growth_yoy_pct"][metric] = (current["value"] / previous["value"] - 1) * 100
    result["available"] = bool(result["values"])
    return result


def attach_cached_fundamentals(items, root: Path = ROOT, as_of=None):
    """Read-only supplement; no vendor calls, price replacement or historical backfill."""
    cutoff = iso_day(as_of) or date.fromordinal(date.today().toordinal() + 1).isoformat()
    path = root / "data/peer_fundamentals/catalog.json"
    try:
        catalog = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        catalog = {}
    broad_path = root / "data/peer_research/catalog.json"
    try:
        broad = json.loads(broad_path.read_text("utf-8"))
    except (OSError, ValueError):
        broad = {}
    if not catalog and not broad:
        return items, {"available": False, "reason": "尚无可比公司基本面资料库"}
    output, attached = [], 0
    for original in items:
        row = dict(original)
        code = str(row.get("code") or "")
        peer = catalog.get("companies", {}).get(code, {})
        evidence_catalog = path
        if code in broad.get("companies", {}) and re.fullmatch(r"\d{6}\.(SH|SZ|BJ|NQ)", code):
            try:
                peer = json.loads((root / f"data/peer_research/workpapers/companies/{code}.json").read_text("utf-8"))
                evidence_catalog = broad_path
            except (OSError, ValueError):
                pass
        financials = [r for r in peer.get("financials", []) if visible_record(r, cutoff)]
        profiles = [p for p in peer.get("profiles", []) if iso_day(p.get("retrieved_at")) and iso_day(p["retrieved_at"]) < cutoff]
        if financials or profiles:
            row["fundamental_evidence"] = {"source": "ifind", "status": "research_only", "as_of": cutoff,
                "profiles": profiles, "financials": financials, "ttm": derive_ttm(financials, cutoff),
                    "historical_pit_eligible": False, "catalog": str(evidence_catalog),
                    "review_checks": peer.get("diagnostics", {}).get("checks", [])
                        if str(peer.get("diagnostics", {}).get("as_of") or "9999") <= cutoff else [],
                "note": "当前供应商历史快照；待主营匹配与原始财报版本复核，不直接改变估值"}
            attached += 1
        output.append(row)
    return output, {"available": attached > 0, "source": "ifind", "attached": attached, "requested": len(items),
                    "status": "research_only", "as_of": cutoff, "catalog": str(broad_path if broad else path)}
