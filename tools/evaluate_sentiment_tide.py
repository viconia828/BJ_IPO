"""Reproduce the frozen 2026-10-17 tide comparison without network or parameter writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
import param_tuning as pt
import sentiment_tide as tide
import valuation_engine as ve

TRIPLES = ((0., .5, 1.), (.25, .5, 1.), (.25, .75, 1.), (.5, .75, 1.),
           (.5, .75, 1.1), (.5, .75, 1.25), (.75, .875, 1.))


def metrics(rows, model):
    selected = [r for r in rows if r["prices"].get(model) is not None]
    if not selected:
        return {"n": 0}
    errors = [(r["prices"][model] / r["actual"] - 1) * 100 for r in selected]
    change_errors = [abs(r["prices"][model] - r["actual"]) / r["issue_price"] * 100 for r in selected]
    return {"n": len(selected), "mape_pct": statistics.mean(map(abs, errors)),
            "overestimate_component_pp": statistics.mean(max(e, 0) for e in errors),
            "underestimate_component_pp": statistics.mean(max(-e, 0) for e in errors),
            "mae_change_pp": statistics.mean(change_errors),
            "p90_change_error_pp": ve._linear_quantile(change_errors, .9),
            "interval_hit_rate": statistics.mean(r["prices"][model] * .9 <= r["actual"] <= r["prices"][model] * 1.1 for r in selected)}


def replay(payload):
    params = payload["baseline_params"]
    items = sorted(payload["items"], key=lambda r: (r["LISTING_DATE"], r["SECURITY_CODE"]))
    rows = []
    for item in items:
        cutoff = tide.parse_date(item["LISTING_DATE"])
        prior = [r for r in items if tide.parse_date(r["LISTING_DATE"]) < cutoff]
        recent = tide.visible_history(prior, cutoff, int(params["recent_days"]), item["SECURITY_CODE"])
        offline, m1, m2, m3 = pt._evaluate_replay_prediction(item, params, prior, pt.COMPOSITE_EVALUATION_SCOPE)
        composite = ve.composite_valuation(m1, m2, params, method3=m3)
        kwargs = dict(issue_price=item["ISSUE_PRICE"], issue_pe=item["AFTER_ISSUE_PE"],
                      industry_pe=item["INDUSTRY_PE_NEW"], float_shares=item["float_shares"],
                      old_shares=item["old_shares"],
                      industry={"primary": item["industry_primary"], "secondary": item["industry_secondary"]},
                      target_code=item["SECURITY_CODE"], target_listing_date=cutoff,
                      online_issue_num=item["ONLINE_ISSUE_NUM"], top_apply_marketcap=item["TOP_APPLY_MARKETCAP"])
        legacy = ve.apply_local_center_overlay(composite, recent_ipos=recent, params=params, **kwargs)
        state = tide.classify(prior, cutoff, int(params["recent_days"]))
        prices = {"legacy_live": legacy.get("target_price"), "legacy_offline_full": offline.get("target_price"),
                  "no_overlay": composite.get("target_price")}
        base = composite.get("base_target_price")
        for triple in ((1, 1.25, 1.5), (1, 1.5, 2), (1.25, 1.5, 2), (1.5, 2, 2.5), (2, 2.5, 3)):
            prices[f"whole_price_{triple}"] = base * dict(zip(tide.FACTOR_DEFAULTS, triple))[state["tier"]] if base else None
        for smooth in (False, True):
            for triple in TRIPLES:
                candidate_params = {**params, "sentiment_tide_enabled": True,
                                    "local_center_smooth_warmup": smooth,
                                    **{f"sentiment_tide_{k}_factor": v for k, v in zip(tide.FACTOR_DEFAULTS, triple)}}
                candidate = ve.apply_local_center_overlay(composite, recent_ipos=prior, params=candidate_params, **kwargs)
                prices[f"positive_gate_{smooth}_{triple}"] = candidate.get("target_price")
        warm = ve.apply_local_center_overlay(composite, recent_ipos=recent,
                    params={**params, "local_center_smooth_warmup": True}, **kwargs)
        prices["warmup_only"] = warm.get("target_price")
        rows.append({"code": item["SECURITY_CODE"], "name": item["SECURITY_NAME_ABBR"],
                     "date": cutoff.isoformat(), "issue_price": item["ISSUE_PRICE"], "actual": item["AVERAGE_PRICE"],
                     "base_price": base, "prices": prices, "tide": state})
    # Compare exactly the same eligible targets; missing anchors are explicit.
    common = [r for r in rows if r["prices"]["legacy_live"] is not None]
    calibration = [r for r in common if r["date"] < "2026-07-01"]
    candidates = [f"positive_gate_True_{t}" for t in TRIPLES]
    def guard_metrics(model):
        m = metrics(common, model)
        return {"interval_hit_rate": m["interval_hit_rate"], "mae_change_pct": m["mae_change_pp"],
                "p90_change_abs_error_pct": m["p90_change_error_pp"], "available_rate": len(common) / len(rows)}
    guards = {model: pt._formal_acceptance_guard(guard_metrics(model), guard_metrics("legacy_live")) for model in candidates}
    eligible = [model for model in candidates if guards[model]["passed"]]
    if not eligible:
        raise ValueError("没有三档候选通过原正式验收，停止选择")
    selected = min(eligible, key=lambda model: metrics(calibration, model)["mape_pct"])
    for row in rows:
        row["prices"]["selected"] = row["prices"][selected]
    cohorts = {"all": common, "h1_calibration": calibration,
               "q3": [r for r in common if "2026-07-01" <= r["date"] < "2026-10-01"],
               "recent10": common[-10:], "october": [r for r in common if r["date"] >= "2026-10-01"],
               "without_archived_peer_recovery": [r for r in common if r["code"] != "920196"]}
    selected_factors = dict(zip(tide.FACTOR_DEFAULTS, next(t for t in TRIPLES if selected == f"positive_gate_True_{t}")))
    guard = pt._formal_acceptance_guard(guard_metrics("selected"), guard_metrics("legacy_live"))
    return {"schema": "sentiment_tide_comparison_v1", "selected": selected, "selected_factors": selected_factors,
            "unchanged_auto_acceptance_guard": guard,
            "candidate_acceptance_guards": guards,
            "selection": "初始四组仅削减加成的方案P90退化；增补高档1.10/1.25及更温和削减方案。先用全样本原验收门槛筛选，再按上半年MAPE选优；全历史参与选择，非独立样本外检验。",
            "limitations": payload["limitations"], "archived_peer_recovery": payload["archived_peer_recovery"],
            "excluded": [{"code": r["code"], "name": r["name"], "reason": "无可用方法一/二基础"} for r in rows if r not in common],
            "metrics": {name: {model: metrics(cohort, model) for model in rows[0]["prices"]} for name, cohort in cohorts.items()},
            "rows": rows}


def report_text(result):
    factors = result["selected_factors"]
    lines = ["情绪潮汐三档对照（2026-10-17冻结输入）", "",
             "采用高/中/低档正向加成倍数：" + " / ".join(f"{factors[k]:.2f}" for k in ("high", "medium", "low")) + "。",
             "基础 B = 方法一/二综合；原中枢 F = 方法三叠加滚动中枢。",
             "新中枢 = B + min(F-B,0) + 档位倍数 × max(F-B,0)。",
             "滚动权重 = 原配置权重 × min(有效历史只数/5,1)，小样本沿用同一有界线性拟合，消除5只启用开关。",
             "正式与离线共用近30天情绪/滚动窗口；方法二仍用当年已有同二级行业历史。", "",
             "固定规则（首日成交均价涨幅；同日取中位数，一批只更新一次）：",
             "1. >=300%，且较前三批中位数高至少25%，或高档内突破本轮峰值15%，启动/重启高档。",
             "2. 高档最多延续三批后续新股；跌至峰值一半以下先转中档。",
             "3. <=100%，或同时低于200%和本轮峰值一半，直接转低档；>=150%的普通表现可回中档。",
             "4. 窗口无样本默认中档；仅使用目标日期之前已上市结果，不使用目标自身或同日结果。",
             "以上阈值固定后只比较倍数；未按单只股票写例外。", "",
             "共同样本 MAPE（估价相对实际首日均价的平均绝对误差）："]
    for name, values in result["metrics"].items():
        old, new = values["legacy_live"], values["selected"]
        lines.append(f"{name}: n={old['n']}, {old['mape_pct']:.2f}% -> {new['mape_pct']:.2f}%；"
                     f"高估分量 {old['overestimate_component_pp']:.2f}->{new['overestimate_component_pp']:.2f}，"
                     f"低估分量 {old['underestimate_component_pp']:.2f}->{new['underestimate_component_pp']:.2f} 个百分点")
    lines += ["", "代价与原自动验收："]
    for name in ("all", "q3", "recent10"):
        old, new = result["metrics"][name]["legacy_live"], result["metrics"][name]["selected"]
        lines.append(f"{name}: P90涨幅误差 {old['p90_change_error_pp']:.2f}->{new['p90_change_error_pp']:.2f} 个百分点；"
                     f"区间命中率 {old['interval_hit_rate']:.2%}->{new['interval_hit_rate']:.2%}")
    lines += ["现有自动调参验收结果：" + str(result["unchanged_auto_acceptance_guard"]),
              "最终选择通过共同样本的原全样本验收（以正式30天输入为旧基准）；Q3和最近样本的局部退化仍单列，不宣称所有指标改善。",
              "", "最近三只（相同当前参数的历史重估，不等同当时已发布报告）："]
    for row in result["rows"][-3:]:
        p = row["prices"]
        lines.append(f"{row['date']} {row['name']}：{row['tide']['label']}档；原 {p['legacy_live']:.2f} -> 新 {p['selected']:.2f}，实际 {row['actual']:.2f} 元")
    lines += ["", "整价倍数候选全历史MAPE："]
    lines += [f"{key}: {value['mape_pct']:.2f}%" for key, value in result["metrics"]["all"].items() if key.startswith("whole_price_")]
    lines += ["", result["selection"], "南方补充来源：2026-09-24已保存报告第2页的5家正PE可比；报告日期不冒充行情日期。",
              "剔除这只补录样本的结果单列，生产数据库未因实验被覆盖。",
              "莫森上市后的低档只能用于南方及后续预测；莫森自身仍明显高估，宇特低估略加重。",
              "新规则压低过热尾部，但不能解决个股盈利、估值及盘子差异。", *result["limitations"], "",
              "复现：python tools/evaluate_sentiment_tide.py", "详细逐股及三档轨迹见 results.json。"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/sentiment_tide/20261017/inputs.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/sentiment_tide/20261017")
    args = parser.parse_args()
    raw = args.input.read_bytes()
    result = replay(json.loads(raw))
    result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = report_text(result)
    (args.output_dir / "对照说明.txt").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
