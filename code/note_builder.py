from __future__ import annotations

from typing import Any


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_old_shares_pending_note(old_shares_desc: Any, old_shares_meta: dict[str, Any]) -> str | None:
    desc_text = str(old_shares_desc or "").strip()
    if "pending" not in desc_text.lower() and "\u5f85\u786e\u8ba4" not in desc_text:
        return None

    reason = str(old_shares_meta.get("pending_reason") or "").strip()
    if not reason:
        reason = "no valid old-share transfer data was extracted from announcements"
    return f"Old-share transfer data is still pending: {reason}. Float shares are currently estimated from new issue shares only."


def generate_notes(data_dict: dict[str, Any], params: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    ipo = data_dict.get("ipo_info", {})
    issue_pe = _safe_float(ipo.get("AFTER_ISSUE_PE"))
    industry_pe = _safe_float(ipo.get("INDUSTRY_PE_NEW"))
    float_shares = _safe_float(data_dict.get("float_shares"))
    industry = data_dict.get("industry", {})
    method3 = data_dict.get("method3", {}) or {}
    old_shares_meta = data_dict.get("old_shares_meta", {}) or {}

    if industry.get("primary") == "\u672a\u5206\u7c7b" or industry.get("secondary") == "\u672a\u5206\u7c7b":
        notes.append("Industry mapping is incomplete. Method2 now skips instead of falling back to primary industry or full-market samples.")

    if issue_pe and industry_pe and industry_pe > 0:
        ratio = issue_pe / industry_pe
        if ratio < float(params.get("pe_low_threshold", 0.3)):
            notes.append("Issue PE is materially below the disclosed industry PE; method1 may capture part of this valuation discount.")
        elif ratio > float(params.get("pe_high_threshold", 0.6)):
            notes.append("Issue PE is high relative to the disclosed industry PE; watch for first-day valuation pressure.")

    if float_shares is not None and float_shares < float(params.get("float_size_threshold", 2000)):
        notes.append("First-day float is small, which can still amplify trading volatility even though method2 no longer applies a float-size adjustment.")

    if method3.get("available"):
        premium_pct = _safe_float(method3.get("sentiment_premium_pct"))
        if premium_pct is not None and premium_pct >= 20:
            notes.append("Recent IPO sentiment premium is high; the final valuation includes a positive method3 add-on.")
        elif premium_pct is not None and premium_pct < 0:
            notes.append("Recent IPO sentiment is a drag; method3 subtracts a sentiment discount from the base valuation.")

    old_shares_note = _build_old_shares_pending_note(
        data_dict.get("old_shares_desc", ""),
        old_shares_meta,
    )
    if old_shares_note:
        notes.append(old_shares_note)

    return notes
