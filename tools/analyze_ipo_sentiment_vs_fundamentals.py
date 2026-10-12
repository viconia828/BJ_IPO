from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
import io
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def day(value):
    text = str(value or "").strip().split(" ")[0].replace("/", "-")
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    parts = text.split("-")
    return date(*(int(part) for part in parts))


def read_bars(code):
    path = ROOT / "首日分时走势" / f"{code}.csv"
    if not path.exists():
        return []
    binary = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = binary.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return []
    return [row for row in csv.DictReader(io.StringIO(text))
            if str(row.get("DateTime") or "").startswith(("2025", "2026"))
            and number(row.get("close")) is not None and number(row.get("amount")) is not None]


def canonical_rows(dataset, overrides, cutoff, float_reference=None):
    rows, corrections, exclusions = [], [], []
    for item in dataset["items"]:
        code = str(item["SECURITY_CODE"])
        path = ROOT / "data" / "tushare_db" / "variable_fields" / f"{code}.BJ.json"
        fields = json.loads(path.read_text(encoding="utf-8")).get("fields", {}) if path.exists() else {}
        listing = dict(fields.get("tushare_ipo_listing_day") or {})
        performance = fields.get("tushare_ipo_post_listing_performance") or {}
        bars = read_bars(code)
        trading_rows = [row for row in performance.get("rows", []) if number(row.get("close"))]
        listing_date = day(listing.get("trade_date")) or (day(bars[0]["DateTime"]) if bars else None)
        if not listing_date or listing_date > cutoff:
            exclusions.append({"code": code, "reason": "no verified first trading day"})
            continue
        issue = number(item.get("ISSUE_PRICE"))
        if not issue or issue <= 0:
            exclusions.append({"code": code, "reason": "no issue price"})
            continue
        matching_daily = next((row for row in trading_rows if day(row["trade_date"]) == listing_date), {})
        daily = {**matching_daily, **{k: v for k, v in listing.items() if v is not None}}
        close = number(daily.get("close")) or (number(bars[-1]["close"]) if bars else None)
        if not close:
            exclusions.append({"code": code, "reason": "no first-day close"})
            continue
        vwap = number(daily.get("average_price"))
        if not vwap and number(daily.get("amount")) and number(daily.get("volume")):
            vwap = number(daily["amount"]) * 10.0 / number(daily["volume"])
        if not vwap and bars:
            vwap = sum(float(row["amount"]) for row in bars) / sum(float(row["volume"]) for row in bars)
        high = number(daily.get("high")) or (max(float(row["high"]) for row in bars) if bars else None)
        original_date = day(item.get("LISTING_DATE"))
        if original_date != listing_date:
            corrections.append({"code": code, "field": "LISTING_DATE", "before": str(original_date), "after": str(listing_date)})
        peer_rows = overrides.get(code, item.get("comparable_data") or [])
        valid_peers = [row for row in peer_rows if number(row.get("pe_ttm")) and number(row["pe_ttm"]) > 0
                       and day(row.get("trade_date")) is not None and day(row["trade_date"]) < listing_date]
        issue_pe = number(item.get("AFTER_ISSUE_PE"))
        peers = [float(row["pe_ttm"]) for row in valid_peers]
        peer_ratio = float(np.median(peers)) / issue_pe if len(peers) >= 2 and issue_pe and issue_pe > 0 else None
        peer_ratio_any = float(np.median(peers)) / issue_pe if peers and issue_pe and issue_pe > 0 else None
        legal_pe = number(item.get("INDUSTRY_PE_NEW"))
        legal_ratio = legal_pe / issue_pe if legal_pe and legal_pe > 0 and issue_pe and issue_pe > 0 else None
        cached_float_shares = number(item.get("float_shares")) or number(item.get("TOTAL_ISSUE_NUM"))
        verified_float = (float_reference or {}).get(code, {})
        if verified_float:
            assert day(verified_float["listing_date"]) == listing_date, (code, verified_float, listing_date)
        float_shares = number(verified_float.get("float_shares_wan")) or cached_float_shares
        float_cap = issue * float_shares / 10000 if float_shares and float_shares > 0 else None
        cached_cap = issue * cached_float_shares / 10000 if cached_float_shares and cached_float_shares > 0 else None
        online_shares = number(item.get("ONLINE_ISSUE_NUM"))
        online_cap = issue * online_shares / 100000000 if online_shares and online_shares > 0 else None
        later = sorted([row for row in trading_rows if day(row["trade_date"]) > listing_date], key=lambda row: row["trade_date"])
        next_row = later[0] if later else None
        rows.append({
            "code": code, "name": item.get("SECURITY_NAME_ABBR"), "date": listing_date.isoformat(),
            "issue_price": issue, "issue_pe": issue_pe, "open": number(daily.get("open")) or (number(bars[0]["open"]) if bars else None),
            "high": high, "close": close, "vwap": vwap,
            "vwap_gain_pct": (vwap / issue - 1) * 100 if vwap else None,
            "close_gain_pct": (close / issue - 1) * 100,
            "high_gain_pct": (high / issue - 1) * 100 if high else None,
            "peer_count": len(peers), "peer_pe_median": float(np.median(peers)) if peers else None,
            "peer_ratio": peer_ratio, "fund_log": math.log(peer_ratio) if peer_ratio else None,
            "fund_any_log": math.log(peer_ratio_any) if peer_ratio_any else None,
            "industry_pe_log": math.log(legal_ratio) if legal_ratio else None,
            "float_cap_yi": float_cap, "size_log": math.log(float_cap) if float_cap else None,
            "float_shares_wan": float_shares,
            "float_share_source": verified_float.get("source") or "cached_issuance_plus_old_shares_proxy",
            "cached_float_cap_proxy_yi": cached_cap,
            "size_cached_log": math.log(cached_cap) if cached_cap else None,
            "online_issue_cap_yi": online_cap, "size_online_log": math.log(online_cap) if online_cap else None,
            "y_log": math.log(vwap / issue) if vwap else None,
            "peer_reference_dates": sorted(set(str(row["trade_date"]) for row in valid_peers)),
            "next_date": str(next_row.get("trade_date")) if next_row else None,
            "next_from_close_pct": (float(next_row["close"]) / close - 1) * 100 if next_row else None,
            "daily_source": "tushare_daily_cache" if daily else "intraday_bars",
        })
    rows.sort(key=lambda row: (row["date"], row["code"]))
    for target in rows:
        prior = [row for row in rows if row["date"] < target["date"] and row["y_log"] is not None]
        previous = prior[-3:]
        target["mood3_log"] = float(np.mean([row["y_log"] for row in previous])) if len(previous) == 3 else None
        target["mood3_gain_pct"] = float(np.mean([row["vwap_gain_pct"] for row in previous])) if previous else None
        target["mood3_codes"] = [row["code"] for row in previous]
        visible_next = [row for row in previous if row["next_date"] and day(row["next_date"]) < day(target["date"])]
        target["prior_next_from_close_pct"] = float(np.mean([row["next_from_close_pct"] for row in visible_next])) if visible_next else None
        target["prior_next_codes"] = [row["code"] for row in visible_next]
        target["gap_calendar_days"] = (day(target["date"]) - day(prior[-1]["date"])).days if prior else None
        for row in previous:
            assert row["date"] < target["date"]
        for row in visible_next:
            assert day(row["next_date"]) < day(target["date"])
    return rows, corrections, exclusions


def correlations(frame, recent_codes=None):
    result = {}
    for name, part in {"full": frame, "H1": frame[frame.date < "2026-07-01"],
                       "H2": frame[frame.date >= "2026-07-01"], "last20_complete": frame.tail(20),
                       "last20_listings": frame[frame.code.isin(recent_codes or [])]}.items():
        result[name] = {"n": len(part)}
        for key in ("fund_log", "mood3_log", "size_log"):
            result[name][key] = {
                "pearson": float(part[key].corr(part.y_log)) if len(part) > 3 else None,
                "spearman": float(spearmanr(part[key], part.y_log).statistic) if len(part) > 3 else None,
            }
    return result


def bootstrap_difference(frame, repetitions=2000):
    """Circular block resampling, preserving local time clustering; descriptive CI."""
    rng = np.random.default_rng(20261012)
    values = []
    n, block_size = len(frame), 4
    for _ in range(repetitions):
        starts = rng.integers(0, n, size=math.ceil(n / block_size))
        indices = np.concatenate([(np.arange(start, start + block_size) % n) for start in starts])[:n]
        sample = frame.iloc[indices]
        rho_mood = spearmanr(sample.mood3_log, sample.y_log).statistic
        rho_fund = spearmanr(sample.fund_log, sample.y_log).statistic
        values.append(float(rho_mood - rho_fund))
    return {"definition": "Spearman(sentiment,y) minus Spearman(fundamentals,y)", "block_size": block_size,
            "repetitions": repetitions, "estimate": float(spearmanr(frame.mood3_log, frame.y_log).statistic
                                                             - spearmanr(frame.fund_log, frame.y_log).statistic),
            "ci_95": [float(x) for x in np.quantile(values, [0.025, 0.975])]}


def walk_forward(frame, size_column="size_log"):
    models = {"size_only": [size_column], "fundamentals": ["fund_log", size_column],
              "sentiment": ["mood3_log", size_column], "both": ["fund_log", "mood3_log", size_column]}
    predictions = []
    for _, target in frame.iterrows():
        training = frame[frame.date < target.date]
        if len(training) < 15:
            continue
        row = {"code": target.code, "date": target.date, "actual_log": target.y_log,
               "actual_gain_pct": target.vwap_gain_pct, "train_n": len(training)}
        for name, columns in models.items():
            x = training[columns].to_numpy(float)
            means, scales = x.mean(axis=0), x.std(axis=0)
            scales[scales < 1e-9] = 1.0
            design = np.column_stack([np.ones(len(x)), (x - means) / scales])
            beta = np.linalg.lstsq(design, training.y_log.to_numpy(float), rcond=None)[0]
            target_x = np.r_[1.0, (target[columns].to_numpy(float) - means) / scales]
            estimate = float(target_x @ beta)
            row[name + "_log"] = estimate
            row[name + "_gain_pct"] = (math.exp(estimate) - 1) * 100
        predictions.append(row)
    p = pd.DataFrame(predictions)
    summary = {}
    for scope, part in {"all": p, "H2": p[p.date >= "2026-07-01"], "last20": p.tail(20)}.items():
        summary[scope] = {"n": len(part)}
        for model in models:
            delta = part[model + "_log"] - part.actual_log
            summary[scope][model] = {"log_mae": float(abs(delta).mean()),
                                     "log_rmse": float(np.sqrt((delta ** 2).mean())),
                                     "gain_mae_pct": float(abs(part[model + "_gain_pct"] - part.actual_gain_pct).mean())}
    return summary, predictions


def plot_diagnostic(result, output_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    font = FontProperties(fname="C:/Windows/Fonts/msyh.ttc")
    plt.rcParams["font.family"] = font.get_name()
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    c = result["correlations"]
    scopes = [("full", f"全年可用\nn={c['full']['n']}"), ("H1", f"上半年\nn={c['H1']['n']}"),
              ("H2", f"下半年\nn={c['H2']['n']}"), ("last20_listings", f"最近20只中的\n{c['last20_listings']['n']}只有完整快照")]
    x = np.arange(len(scopes))
    for offset, signal, label, color in ((-0.18, "fund_log", "同业估值", "#577A9E"),
                                         (0.18, "mood3_log", "此前3只原始涨幅代理", "#D78A40")):
        values = [result["correlations"][key][signal]["spearman"] for key, _ in scopes]
        bars = axes[0].bar(x + offset, values, width=0.34, color=color, label=label)
        axes[0].bar_label(bars, fmt="%.2f", padding=3)
    axes[0].set_xticks(x, [label for _, label in scopes])
    axes[0].set_ylim(0, 0.75)
    axes[0].set_ylabel("与首日均价溢价的 Spearman 相关系数")
    axes[0].set_title("原始涨幅代理：近期较强，全年非单调")
    axes[0].legend(frameon=False, fontsize=9)
    models = [("fundamentals", "同业＋流通盘"), ("sentiment", "涨幅代理＋流通盘"), ("both", "同业＋涨幅代理＋流通盘")]
    values = [result["walk_forward"]["all"][name]["gain_mae_pct"] for name, _ in models]
    bars = axes[1].barh([label for _, label in models], values, color=["#577A9E", "#D78A40", "#438B76"])
    axes[1].bar_label(bars, fmt="%.1f", padding=4)
    axes[1].set_xlim(0, max(values) * 1.16)
    axes[1].invert_yaxis()
    axes[1].set_xlabel("首日均价溢价预测 MAE（百分点，越低越好）")
    axes[1].set_title(f"{result['walk_forward']['all']['n']}次滚动预测：联合模型误差较低")
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y" if ax == axes[0] else "x", alpha=0.15)
        ax.set_axisbelow(True)
    fig.suptitle("先验证观察，再决定估值调参方向", fontsize=16, fontweight="bold")
    fig.text(0.03, 0.01, "截至2026-10-12；同业PE、历史IPO收益只取上市前；上市时固定流通股数经事后结构核验。小样本相关不代表因果。", fontsize=9, color="#555555")
    fig.tight_layout(rect=[0, 0.04, 1, 0.92])
    fig.savefig(output_path, dpi=180, facecolor="white")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Read-only diagnostic of pre-listing fundamentals and recent IPO sentiment.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "研究产物" / "最新" / "新股基本面与情绪检验_20261012")
    parser.add_argument("--peer-override", type=Path, default=ROOT / "outputs" / "研究产物" / "最新" / "新股基本面与情绪检验_20261012" / "sources" / "yute_historical_comparables.json")
    parser.add_argument("--float-reference", type=Path, default=ROOT / "outputs" / "研究产物" / "最新" / "新股基本面与情绪检验_20261012" / "sources" / "ifind_float_reference.json")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    dataset = json.loads((ROOT / "data" / "offline_tuning" / "replay_dataset.json").read_text(encoding="utf-8"))
    overrides = {"920157": json.loads(args.peer_override.read_text(encoding="utf-8"))["data"]} if args.peer_override.exists() else {}
    float_reference = json.loads(args.float_reference.read_text(encoding="utf-8"))["data"] if args.float_reference.exists() else {}
    rows, corrections, exclusions = canonical_rows(dataset, overrides, date(2026, 10, 12), float_reference)
    df = pd.DataFrame(rows)
    complete = df.dropna(subset=["y_log", "fund_log", "mood3_log", "size_log"]).copy()
    result = {"definitions": {"outcome": "log(first-day VWAP / issue price)",
              "fundamental": "log(median positive pre-listing comparable PE_TTM / issue PE); at least 2 comparables",
              "sentiment": "mean log(first-day VWAP / issue price) of the 3 IPOs strictly preceding the target",
              "control": "log(issue-price initial float market cap), fixed listing share count retrospectively crosschecked by volume/turnover; not using first-day volume or turnover as a predictor",
              "prediction": "expanding OLS, at least 15 strictly earlier eligible listings; no parameter search; historical structural reconstruction rather than a proven original-vintage PIT dataset"},
              "canonical_n": len(df), "same_sample_n": len(complete), "date_corrections": corrections,
              "exclusions": exclusions, "correlations": correlations(complete, df.tail(20).code.tolist()),
              "correlation_difference_block_bootstrap": bootstrap_difference(complete)}
    result["sensitivity"] = {}
    for key in ("fund_any_log", "industry_pe_log"):
        alternative = df.dropna(subset=[key, "mood3_log", "y_log", "size_log"]).copy()
        alternative["fund_log"] = alternative[key]
        result["sensitivity"][key] = correlations(alternative, df.tail(20).code.tolist())
    result["walk_forward"], predictions = walk_forward(complete)
    result["size_control_sensitivity"] = {}
    for column in ("size_cached_log", "size_online_log"):
        # Hold the evaluated samples fixed while auditing the share-count definition.
        same = complete.dropna(subset=[column])
        result["size_control_sensitivity"][column], _ = walk_forward(same, column)
    result["input_sources"] = {"peer_override": str(args.peer_override), "float_reference": str(args.float_reference),
                               "float_verified_n": len(float_reference)}
    recent = complete[complete.code.isin(df.tail(20).code.tolist())]
    result["recent20_correlation_difference_bootstrap"] = bootstrap_difference(recent)
    result["recent20_coverage"] = {"listing_count": 20, "matched_count": len(recent),
        "missing_fundamental_codes": df.tail(20)[df.tail(20).fund_log.isna()].code.tolist(),
        "matched_start": recent.date.min(), "matched_end": recent.date.max()}
    result["examples"] = [row for row in rows if row["code"] in {"920202", "920157", "920025", "920229", "920201"}]
    result["missing_fundamental_codes"] = df[df.fund_log.isna()].code.tolist()
    (args.output_dir / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    df.to_csv(args.output_dir / "canonical_samples.csv", index=False, encoding="utf-8-sig")
    complete.to_csv(args.output_dir / "same_sample_comparison.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(predictions).to_csv(args.output_dir / "walk_forward_predictions.csv", index=False, encoding="utf-8-sig")
    plot_diagnostic(result, args.output_dir / "同业估值与炒新情绪_初步检验.png")
    print(json.dumps({key: result[key] for key in ("canonical_n", "same_sample_n", "correlations", "walk_forward")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
