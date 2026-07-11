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
        reason = "公告中未提取到有效老股转让数据"
    return f"首日流通老股数据待确认，原因：{reason}。当前首日流通盘按仅新增发行量估算。"


def generate_notes(data_dict: dict[str, Any], params: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    ipo = data_dict.get("ipo_info", {})
    issue_pe = _safe_float(ipo.get("AFTER_ISSUE_PE"))
    industry_pe = _safe_float(ipo.get("INDUSTRY_PE_NEW"))
    float_shares = _safe_float(data_dict.get("float_shares"))
    industry = data_dict.get("industry", {})
    method1 = data_dict.get("method1", {}) or {}
    method3 = data_dict.get("method3", {}) or {}
    old_shares_meta = data_dict.get("old_shares_meta", {}) or {}

    if industry.get("primary") == "\u672a\u5206\u7c7b" or industry.get("secondary") == "\u672a\u5206\u7c7b":
        notes.append("当前标的尚未完成二级行业映射，方法二不再回退一级行业或全市场样本。")

    if issue_pe and industry_pe and industry_pe > 0:
        ratio = issue_pe / industry_pe
        if ratio < float(params.get("pe_low_threshold", 0.3)):
            notes.append("发行 PE 明显低于披露的行业 PE，方法一已计入有限的低估修正。")
        elif ratio > float(params.get("pe_high_threshold", 0.6)):
            notes.append("发行 PE 相对行业 PE 偏高；EPS 已反映该影响，当前不再重复施加高 PE 惩罚。")

    if float_shares is not None and float_shares < float(params.get("float_size_threshold", 2000)):
        notes.append("首日流通盘偏小，方法一已连续计入流通盘溢价；方法二仍保持纯行业口径。")

    if method1.get("anchor_source") == "industry_pe_fallback":
        notes.append("有效上市可比 PE 缺失，方法一使用披露的行业 PE 低置信度兜底。")

    if method3.get("available"):
        premium_pct = _safe_float(method3.get("sentiment_premium_pct"))
        if premium_pct is not None and premium_pct >= 20:
            notes.append("近期新股情绪溢价较高，综合估值已计入方法三的正向加成。")
        elif premium_pct is not None and premium_pct < 0:
            notes.append("近期新股情绪偏弱，方法三已从基础估值中扣除情绪折价。")

    old_shares_note = _build_old_shares_pending_note(
        data_dict.get("old_shares_desc", ""),
        old_shares_meta,
    )
    if old_shares_note:
        notes.append(old_shares_note)

    return notes
