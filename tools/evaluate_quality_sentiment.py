"""Fixed-spec walk-forward shadow comparison; no parameter search or promotion."""
from __future__ import annotations
import csv
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
import config_loader
import param_tuning
import quality_sentiment as qs
import quality_sentiment_data as data
import report_generator


def metrics(rows, key):
    errors = [(r[key] - r["actual"]) / r["issue"] * 100 for r in rows]
    price_errors = [(r[key] / r["actual"] - 1) * 100 for r in rows]
    return {"n": len(rows), "gain_mae_pp": statistics.mean(abs(x) for x in errors) if errors else None,
            "price_mae_pct": statistics.mean(abs(x) for x in price_errors) if errors else None,
            "log_mae": statistics.mean(abs(math.log(r[key] / r["actual"])) for r in rows) if rows else None,
            "overprice_mean_pct": statistics.mean(max(x, 0) for x in price_errors) if rows else None,
            "below_lower_rate": statistics.mean(r["actual"] < r[key] * .9 for r in rows) if rows else None,
            "hit_rate": statistics.mean(r[key] * .9 <= r["actual"] <= r[key] * 1.1 for r in rows) if rows else None}


def main():
    params = config_loader.load_params()
    history, audit = data.load_history()
    # Same corrected structure for both models, while retaining the raw cache.
    corrected = [{**r, "float_shares": r.get("verified_float_shares") or r.get("float_shares")} for r in history]
    pool = param_tuning._build_recent_pool(corrected)
    rows, predictions, unavailable = [], [], []
    output = ROOT / "outputs/研究产物/最新/个股资质情绪模型_20261012"
    output.mkdir(parents=True, exist_ok=True)
    for item in corrected:
        actual, issue = qs.number(item.get("AVERAGE_PRICE")), qs.number(item.get("ISSUE_PRICE"))
        if not actual or not issue or not qs.day(item.get("LISTING_DATE")):
            continue
        result = qs.evaluate(item, corrected, params, as_of=item["LISTING_DATE"])
        final, m1, m2, m3 = param_tuning._evaluate_replay_prediction(item, params, pool, param_tuning.COMPOSITE_EVALUATION_SCOPE)
        rolling = qs.number(final.get("local_center_rolling_change_pct"))
        if not result["available"] or not final.get("available") or rolling is None:
            unavailable.append({"code": item["SECURITY_CODE"], "reason": result["sentiment"].get("reason") or final.get("reason") or "旧情绪线不可用"})
            continue
        row = {"code": item["SECURITY_CODE"], "name": item.get("SECURITY_NAME_ABBR"),
               "date": str(qs.day(item["LISTING_DATE"])), "actual": actual, "issue": issue,
               "anchor": result["anchor"]["center"], "new_sentiment": result["sentiment"]["center"],
               "legacy_sentiment": issue * (1 + rolling / 100), "legacy_formal": final["target_price"],
               "state": result["state"]["label"], "effective_n": result["sentiment"]["effective_n"],
               "actual_premium_pct": (actual / result["anchor"]["center"] - 1) * 100,
               "expectation_error_pct": (actual / result["sentiment"]["center"] - 1) * 100}
        rows.append(row)
        predictions.append({"code": row["code"], "result": result})
        if row["code"] in {"920202", "920157"}:
            payload = {"analysis_date": row["date"], "ipo_info": item, "industry": {"display_name": item.get("industry_secondary")},
                       "params": params, "comparable_data": item.get("comparable_data", []),
                       "method1": m1, "method2": m2, "method3": m3, "final": qs.attach(final, result),
                       "final_change_pct": (final["target_price"] / issue - 1) * 100,
                       "range_change_low": (final["range_low"] / issue - 1) * 100,
                       "range_change_high": (final["range_high"] / issue - 1) * 100,
                       "recent_ipos": [r for r in corrected if qs.day(r["LISTING_DATE"]) and qs.day(r["LISTING_DATE"]) < qs.day(row["date"])],
                       "notes": ["历史模拟报告：不是当时已经发布的预测。"], "float_shares": item.get("verified_float_shares"),
                       "company_description": "历史模拟；用于检查个股基础、情绪区间与转向提示。"}
            context = report_generator._prepare_report_context(payload)
            (output / f"{row['code']}_历史模拟.html").write_text(report_generator._build_report_html(context), encoding="utf-8")
            (output / f"{row['code']}_历史模拟.md").write_text(report_generator.build_report_markdown(payload), encoding="utf-8")
    summary = {"version": qs.VERSION, "mode": "shadow", "parameter_search": False,
               "same_sample_n": len(rows), "unavailable": unavailable,
               "metrics": {name: metrics(rows, name) for name in ("legacy_formal", "legacy_sentiment", "new_sentiment")},
               "recent10": {name: metrics(rows[-10:], name) for name in ("legacy_formal", "legacy_sentiment", "new_sentiment")},
               "cooling_watch_cases": {name: metrics([r for r in rows if r["state"] == "降温观察"], name)
                                       for name in ("legacy_sentiment", "new_sentiment")},
               "examples": [r for r in rows if r["code"] in {"920202", "920157"}],
               "limitations": ["同样本历史重建，不是独立留出验证", "代理基础未充分表达盈利增长和竞争地位",
                               "上市固定流通股数经事后结构核验", "状态阈值为固定实验设定，未校准预警误报率"],
               "promotion": "保持影子模式；须进一步复核基本面与真实前向结果"}
    for name, obj in (("summary.json", summary), ("predictions.json", predictions), ("input_corrections.json", audit)):
        (output / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    if rows:
        with (output / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
