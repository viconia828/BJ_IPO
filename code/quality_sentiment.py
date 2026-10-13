"""Issuer-relative IPO sentiment. Pure functions; all cutoffs are exclusive dates.

The first release is a shadow model. Reconstructed forecasts are never labelled
as archived forecasts, and missing anchors never fall back to raw IPO gains.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import math
import statistics
from typing import Any

VERSION = "quality_sentiment_v1_structural"


def number(value: Any) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def day(value: Any) -> date | None:
    text = str(value or "").strip()[:10].replace("/", "-")
    try:
        return datetime.strptime(text, "%Y%m%d").date() if len(text) == 8 else date.fromisoformat(text)
    except ValueError:
        return None


def live_cutoff(listing_date: Any = None) -> date:
    now = datetime.now(timezone(timedelta(hours=8)))
    # Today's daily observations are only eligible after the market has closed.
    completed = now.date() + timedelta(days=1) if now.hour >= 18 else now.date()
    listing = day(listing_date)
    return min(listing, completed) if listing else completed


def _positive(value: Any) -> float | None:
    value = number(value)
    return value if value is not None and value > 0 else None


def build_anchor(item: dict, as_of: Any, params: dict) -> dict:
    """An explicit comparable anchor, excluding legacy IPO/float mood factors."""
    cutoff = day(as_of)
    if cutoff is None:
        return {"available": False, "reason": "缺少有效估值时点"}
    frozen = item.get("fundamental_snapshot") or {}
    if (frozen.get("source") and day(frozen.get("available_date")) and day(frozen["available_date"]) < cutoff
            and frozen.get("available") and _positive(frozen.get("center"))):
        return dict(frozen)
    case = item.get('fundamental_case') or {}
    if case:
        from fundamental_anchor import build_reviewed_anchor
        reconstruction = item.get('_fundamental_reconstruction') is True
        available_date = day(case.get('available_not_before'))
        if case.get('integrity_error'):
            return {"available": False, "reason": case['integrity_error']}
        if reconstruction or (available_date and available_date < cutoff):
            return build_reviewed_anchor(case, item.get('comparable_data') or [], as_of, params,
                reconstruction=reconstruction, stage=item.get('_fundamental_stage', 'matched'),
                market_policy=item.get('_fundamental_market_policy', 'per_market'))
    peers = []
    for peer in item.get("comparable_data") or []:
        observed = day(peer.get("trade_date"))
        pe = _positive(peer.get("pe_ttm"))
        if observed and observed < cutoff and (cutoff - observed).days <= 45 and pe:
            peers.append(peer)
    # A duplicated quote does not count as another comparable company.
    peers = list({str(p.get("code") or ""): p for p in sorted(peers, key=lambda p: str(p["trade_date"]))
                  if str(p.get("code") or "").strip()}.values())
    issue = _positive(item.get("ISSUE_PRICE"))
    issue_pe = _positive(item.get("AFTER_ISSUE_PE"))
    review = item.get("fundamental_review") or {}
    reviewed = bool(review.get("source") and day(review.get("available_date"))
                    and day(review["available_date"]) < cutoff)
    explicit = _positive(review.get("center")) if reviewed else None
    width = float(params.get("price_range_width", 0.10))
    if explicit:
        low, high = _positive(review.get("low")), _positive(review.get("high"))
        if not (low and high and low <= explicit <= high and review.get("excludes_ipo_sentiment") is True):
            return {"available": False, "reason": "人工基本面区间缺少上下界或剔除炒新情绪的确认"}
        return {"available": True, "center": explicit, "low": low, "high": high,
                "confidence": 1.0, "basis": "reviewed_fundamental", "review_source": review["source"],
                "reference_dates": [str(review["available_date"])], "limitations": [], "as_of": cutoff.isoformat()}
    if not issue or not issue_pe or len(peers) < 2:
        return {"available": False, "reason": "缺少发行盈利口径或至少两家有事前日期的有效可比公司",
                "peer_count": len(peers), "as_of": cutoff.isoformat()}
    eps = issue / issue_pe
    quality_factor = 1.0
    earnings_basis = "发行静态EPS × 同行TTM PE"
    limitations = ["发行静态盈利与同行TTM尚未完全同口径", "成长性、竞争力等个股差异尚未完成逐股复核"]
    if reviewed and _positive(review.get("eps")) and review.get("earnings_basis"):
        eps = float(review["eps"])
        earnings_basis = str(review["earnings_basis"])
        limitations[0] = "采用有来源的盈利假设，仍需核对可比PE口径"
    if reviewed and _positive(review.get("quality_factor")) and review.get("quality_reason"):
        quality_factor = float(review["quality_factor"])
        limitations[1] = "个股调整：" + str(review["quality_reason"])
    pe_values = [float(p["pe_ttm"]) for p in peers]
    median = statistics.median(pe_values)
    dispersion = math.exp(statistics.median(abs(math.log(pe / median)) for pe in pe_values))
    discount = float(params.get("bse_discount_factor", 0.75))
    center = eps * median * discount * quality_factor
    if not math.isfinite(center) or center <= 0:
        return {"available": False, "reason": "基本面价格无效"}
    confidence = min(len(peers) / 4, 1.0) * 0.7 / max(1.0, dispersion)
    return {"available": True, "center": center, "low": center * (1 - width), "high": center * (1 + width),
            "confidence": confidence, "basis": "comparable_proxy", "peer_count": len(peers),
            "peer_codes": [p["code"] for p in peers], "reference_dates": sorted({str(p["trade_date"]) for p in peers}),
            "eps": eps, "earnings_basis": earnings_basis, "peer_pe": median,
            "bse_discount": discount, "quality_factor": quality_factor, "dispersion_ratio": dispersion,
            "limitations": limitations, "as_of": cutoff.isoformat()}


def visible_post_returns(item: dict, as_of: Any) -> list[dict]:
    """Never infer D2/D3 observation dates by adding calendar days."""
    cutoff, listing = day(as_of), day(item.get("LISTING_DATE"))
    if not cutoff or not listing:
        return []
    output = []
    for prefix in ("NEXT", "THIRD"):
        observed = day(item.get(f"{prefix}_TRADE_DATE"))
        close = _positive(item.get(f"{prefix}_DAY_CLOSE"))
        if observed and listing < observed < cutoff:
            from_close = number(item.get(f"{prefix}_DAY_FROM_LISTING_CLOSE_PCT"))
            base_close, average = _positive(item.get("CLOSE_PRICE")), _positive(item.get("AVERAGE_PRICE"))
            output.append({"date": observed.isoformat(), "from_close_pct":
                           (close / base_close - 1) * 100 if close and base_close else from_close,
                           "from_vwap_pct": (close / average - 1) * 100 if close and average else None})
    return output


def mask_post_listing(item: dict, as_of: Any) -> dict:
    result = dict(item)
    visible = {row["date"]: row for row in visible_post_returns(item, as_of)}
    for prefix in ("NEXT", "THIRD"):
        observed = day(item.get(f"{prefix}_TRADE_DATE"))
        if observed is None or observed.isoformat() not in visible:
            for suffix in ("DAY_CLOSE", "DAY_CLOSE_CHANGE", "DAY_FROM_LISTING_CLOSE_PCT"):
                result[f"{prefix}_{suffix}"] = None
            result[f"{prefix}_DAY_CHANGE"] = None
            result["D2_CLOSE_CHANGE" if prefix == "NEXT" else "D3_CLOSE_CHANGE"] = None
    values = [row["from_close_pct"] for row in visible.values() if row["from_close_pct"] is not None]
    result["POST_LISTING_PROFIT_EFFECT_PCT"] = statistics.mean(values) if values else None
    return result


def _float_cap(item: dict) -> float | None:
    # Legacy float_shares = total issuance + old shares is not accepted.
    shares, issue = _positive(item.get("verified_float_shares")), _positive(item.get("ISSUE_PRICE"))
    return shares * issue / 10000 if shares and issue else None


def _weight(row: dict, target: dict, as_of: date, params: dict, *, time_weight: bool = True) -> float:
    weight = max(row["anchor"]["confidence"], 0.01)
    if time_weight:
        half_life = max(float(params.get("quality_sentiment_half_life_days", 8)), 1.0)
        weight *= 2 ** (-min((as_of - day(row["LISTING_DATE"])).days / half_life, 100))
    source_cap, target_cap = _float_cap(row), _float_cap(target)
    if source_cap and target_cap:
        weight /= 1 + abs(math.log(source_cap / target_cap))
    # A modest similarity weight, not an industry-specific price premium.
    group = str(target.get("industry_secondary") or "")
    if group and group != "未分类" and row.get("industry_secondary") == group:
        weight *= 1.5
    return weight / max(int(row.get("same_date_count", 1)), 1)


def _structural_normalize(rows: list[dict], target: dict) -> tuple[list[dict], dict]:
    """Ridge fit of issuer differences; subsequent time weighting owns market mood.

u = intercept + b1*log(F/issue) + b2*log(float cap) + market residual.
The first slope permits a smaller extra premium for a stock already priced high
by fundamentals. Shrinkage is fixed, not selected against these replay outcomes.
"""
    weights = [max(r["anchor"]["confidence"], .01) / max(r.get("same_date_count", 1), 1) for r in rows]
    norm = len(rows) / sum(weights)
    weights = [w * norm for w in weights]
    caps = [math.log(_float_cap(r)) for r in rows if _float_cap(r)]
    fill = statistics.median(caps) if caps else 0.0
    def features(r):
        return [math.log(r["anchor"]["center"] / float(r["ISSUE_PRICE"])), math.log(_float_cap(r)) if _float_cap(r) else fill]
    xs = [features(r) for r in rows]
    means = [sum(w * x[j] for w, x in zip(weights, xs)) / sum(weights) for j in range(2)]
    ymean = sum(w * r["u"] for w, r in zip(weights, rows)) / sum(weights)
    aa = sum(w * (x[0] - means[0]) ** 2 for w, x in zip(weights, xs)) + 2.0
    bb = sum(w * (x[1] - means[1]) ** 2 for w, x in zip(weights, xs)) + 2.0
    ab = sum(w * (x[0] - means[0]) * (x[1] - means[1]) for w, x in zip(weights, xs))
    ay = sum(w * (x[0] - means[0]) * (r["u"] - ymean) for w, x, r in zip(weights, xs, rows))
    by = sum(w * (x[1] - means[1]) * (r["u"] - ymean) for w, x, r in zip(weights, xs, rows))
    determinant = aa * bb - ab * ab
    b1, b2 = (ay * bb - by * ab) / determinant, (by * aa - ay * ab) / determinant
    # A higher fundamental anchor must not mechanically lower the price.
    b1 = max(b1, -1.0)
    target_features = features(target)
    normalized = []
    for row, x in zip(rows, xs):
        adjustment = b1 * (target_features[0] - x[0]) + b2 * (target_features[1] - x[1])
        normalized.append({**row, "u": row["u"] + adjustment})
    return normalized, {"fundamental_extra_premium_slope": b1, "float_cap_slope": b2,
                        "ridge_penalty": 2.0, "float_missing_imputation": fill}


def _estimate(rows: list[dict], target: dict, as_of: date, params: dict, *, minimum: int = 8,
              time_weight: bool = True, structural: bool = True) -> dict:
    rows = rows[-40:]
    if len(rows) < minimum:
        return {"available": False, "reason": f"有效历史不足{minimum}只", "sample_count": len(rows)}
    coefficients = {}
    if structural:
        rows, coefficients = _structural_normalize(rows, target)
    weights = [_weight(r, target, as_of, params, time_weight=time_weight) for r in rows]
    total = sum(weights)
    mean = sum(w * r["u"] for w, r in zip(weights, rows)) / total
    return {"available": True, "log_premium": mean, "premium_pct": math.expm1(mean) * 100,
            "sample_count": len(rows), "effective_n": total ** 2 / sum(w * w for w in weights),
            "sample_codes": [r["SECURITY_CODE"] for r in rows], "structural_model": coefficients}


def _state(rows: list[dict], target: dict, cutoff: date, params: dict, all_prior: list[dict]) -> dict:
    dates = sorted({r["LISTING_DATE"] for r in rows})
    all_dates = sorted({str(day(r["LISTING_DATE"])) for r in all_prior})
    latest_all_dates = all_dates[-3:]
    latest_all = [r for r in all_prior if str(day(r["LISTING_DATE"])) in latest_all_dates]
    ready_codes = {r["SECURITY_CODE"] for r in rows}
    coverage = sum(r["SECURITY_CODE"] in ready_codes for r in latest_all) / len(latest_all) if latest_all else 0
    post_by_ipo = []
    post_count = 0
    for item in latest_all:
        observed = [r["from_vwap_pct"] for r in visible_post_returns(item, cutoff) if r["from_vwap_pct"] is not None]
        if observed:
            post_by_ipo.append(statistics.mean(observed))
            post_count += len(observed)
    post_mean = statistics.mean(post_by_ipo) if post_by_ipo else None
    base = {"label": "无法判断", "code": "uncertain", "recent_coverage": coverage,
            "last_feedback_date": dates[-1] if dates else None,
            "days_since_feedback": (cutoff - day(dates[-1])).days if dates else None,
            "post_vwap_mean_pct": post_mean, "post_count": post_count, "post_ipo_count": len(post_by_ipo),
            "note": "个股基础或连续反馈不足；原始首日涨幅不用于替代判断"}
    if len(dates) < 6 or coverage < 2 / 3:
        if len(post_by_ipo) >= 2 and all(value < 0 for value in post_by_ipo):
            base.update(code="weak_aftermarket", label="承接偏弱，净情绪待确认",
                        note="近期多只新股首日均价至后续收盘亏损；个股基础覆盖不足，不能确认净情绪转向")
        return base
    adjusted, _ = _structural_normalize(rows[-40:], target)
    current = [r for r in adjusted if r["LISTING_DATE"] in latest_all_dates]
    previous = [r for r in adjusted if r["LISTING_DATE"] in all_dates[-6:-3]]
    if len({r["LISTING_DATE"] for r in current}) < 2 or len({r["LISTING_DATE"] for r in previous}) < 2:
        base["note"] = "相邻窗口个股基础覆盖不足，不用更早股票补成完整窗口"
        return base
    current_signal = _estimate(current, target, cutoff, params, minimum=1, time_weight=False, structural=False)
    previous_signal = _estimate(previous, target, cutoff, params, minimum=1, time_weight=False, structural=False)
    delta = current_signal["log_premium"] - previous_signal["log_premium"]
    # One fixed material-change threshold for the first shadow experiment.
    material, decline = math.log(1.10), math.log(0.90)
    errors = [r["expectation_error"] for r in current if r.get("expectation_error") is not None]
    miss_share = sum(e < decline for e in errors) / len(errors) if errors else None
    confirmed_inputs = all(r["anchor"]["basis"] == "reviewed_fundamental" and
                           r.get("expectation_origin") == "archived" for r in current)
    if delta < decline or (miss_share is not None and miss_share >= 2 / 3):
        confirmation = delta < decline and miss_share is not None and miss_share >= 2 / 3 and post_mean is not None and post_mean < 0
        code, label = ("cooling_confirmed", "降温确认") if confirmation and confirmed_inputs else ("cooling_watch", "降温观察")
    elif delta > material:
        code, label = ("divergent", "溢价与承接分化") if post_mean is not None and post_mean < 0 else ("recovery_watch", "修复待确认")
    else:
        code, label = "stable", "暂稳观察"
    stale = (cutoff - day(dates[-1])).days > 14
    if stale:
        code, label = "stale", "反馈过期，待更新"
    return {**base, "code": code, "label": label, "delta_log": delta,
            "premium_multiplier_change_pct": math.expm1(delta) * 100,
            "recent_premium_pct": current_signal["premium_pct"], "previous_premium_pct": previous_signal["premium_pct"],
            "miss_share": miss_share, "expectation_count": len(errors), "post_vwap_mean_pct": post_mean,
            "post_count": post_count, "recent_codes": [r["SECURITY_CODE"] for r in current],
            "industry_count": len({r.get("industry_secondary") for r in current}),
            "evidence_mode": "archived" if confirmed_inputs else "historical_simulation",
            "note": "比较相对各自基本面代理的额外定价；兑现采用逐时点历史模拟，尚不支持强确认"
                    if not confirmed_inputs else "比较个股调整后的溢价方向、事前预期兑现与已知后续反馈"}


def evaluate(target: dict, history: list[dict], params: dict, *, as_of: Any) -> dict:
    cutoff = day(as_of)
    if cutoff is None:
        raise ValueError("quality sentiment requires an explicit as_of date")
    prior = [dict(r) for r in history if day(r.get("LISTING_DATE")) and day(r["LISTING_DATE"]) < cutoff
             and (_positive(r.get("AVERAGE_PRICE")) or _positive(r.get("CLOSE_PRICE")))
             and str(r.get("SECURITY_CODE")) != str(target.get("SECURITY_CODE"))]
    prior.sort(key=lambda r: (day(r["LISTING_DATE"]), str(r.get("SECURITY_CODE"))))
    completed, excluded = [], []
    for listing in sorted({day(r["LISTING_DATE"]) for r in prior}):
        group = []
        for item in [r for r in prior if day(r["LISTING_DATE"]) == listing]:
            anchor = build_anchor(item, listing, params)
            actual = _positive(item.get("AVERAGE_PRICE"))
            if not anchor.get("available") or actual is None or not _positive(item.get("ISSUE_PRICE")):
                excluded.append({"code": item.get("SECURITY_CODE"), "reason": anchor.get("reason") or "首日均价缺失"})
                continue
            model_item = {**item, "anchor": anchor}
            estimate = _estimate(completed, model_item, listing, params)
            center = anchor["center"] * math.exp(estimate["log_premium"]) if estimate["available"] else None
            origin = "historical_simulation"
            snapshot = item.get("sentiment_snapshot") or {}
            if (snapshot.get("origin") == "archived" and snapshot.get("source") and
                    day(snapshot.get("as_of")) and day(snapshot["as_of"]) < listing and _positive(snapshot.get("center"))):
                center, origin = float(snapshot["center"]), "archived"
            group.append({**item, "LISTING_DATE": listing.isoformat(), "anchor": anchor,
                          "u": math.log(actual / anchor["center"]), "expected_center": center,
                          "expectation_error": math.log(actual / center) if center else None,
                          "expectation_origin": origin,
                          "premium_pct": (actual / anchor["center"] - 1) * 100})
        for row in group:
            row["same_date_count"] = len(group)
        completed.extend(group)
    anchor = build_anchor(target, cutoff, params)
    model_target = {**target, "anchor": anchor}
    prediction = (_estimate(completed, model_target, cutoff, params) if anchor.get("available") and _positive(target.get("ISSUE_PRICE")) else
                  {"available": False, "reason": anchor.get("reason")})
    available = bool(anchor.get("available") and prediction.get("available"))
    width = float(params.get("price_range_width", 0.10))
    sentiment = dict(prediction)
    if available:
        center = anchor["center"] * math.exp(prediction["log_premium"])
        sentiment.update(center=center, low=center * (1 - width), high=center * (1 + width))
    else:
        sentiment.update(available=False, reason=anchor.get("reason") or prediction.get("reason"))
    details = [{"code": r["SECURITY_CODE"], "name": r.get("SECURITY_NAME_ABBR"), "date": r["LISTING_DATE"],
                "anchor": r["anchor"], "actual": r["AVERAGE_PRICE"], "expected_center": r["expected_center"],
                "premium_pct": r["premium_pct"], "expectation_error": r["expectation_error"],
                "expectation_origin": r["expectation_origin"], "post_returns": visible_post_returns(r, cutoff)}
               for r in completed[-6:]]
    return {"version": VERSION, "mode": str(params.get("quality_sentiment_mode", "shadow")),
            "available": available, "as_of_exclusive": cutoff.isoformat(), "anchor": anchor, "sentiment": sentiment,
            "state": (_state(completed, model_target, cutoff, params, prior) if anchor.get("available") else
                      {"code": "uncertain", "label": "无法判断", "note": "目标缺少可靠PE基础"}), "recent_details": details,
            "history_count": len(completed), "excluded": excluded,
            "limitations": ["历史为按事前日期截断的重建，非原始事前预测档案", "基本面代理不能替代完整公司资质研究",
                            "同宽区间用于公平对照，尚非经过覆盖率校准的预测区间"],
            "target_float_source": target.get("verified_float_source") or "缺失，未使用旧流通盘代理"}


def attach(final: dict, result: dict) -> dict:
    """Shadow by default; explicit active mode owns the price once, without blends."""
    output = {**final, "quality_sentiment": result}
    if result.get("mode") == "active":
        output["legacy_valuation"] = {k: v for k, v in final.items() if k != "quality_sentiment"}
        sentiment = result["sentiment"]
        output.update(available=result["available"], target_price=sentiment.get("center"),
                      range_low=sentiment.get("low"), range_high=sentiment.get("high"),
                      reason=sentiment.get("reason", ""), quality_sentiment_applied=result["available"])
    return output
