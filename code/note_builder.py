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
    if "待确认" not in desc_text:
        return None

    reason = str(old_shares_meta.get("pending_reason") or "").strip()
    if not reason:
        reason = "公告文件中未提取到有效老股数据"
    return f"首日流通老股数据待确认，原因：{reason}。当前首日流通盘按仅新增发行量估算。"


def generate_notes(data_dict: dict[str, Any], params: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    ipo = data_dict.get("ipo_info", {})
    issue_pe = _safe_float(ipo.get("AFTER_ISSUE_PE"))
    industry_pe = _safe_float(ipo.get("INDUSTRY_PE_NEW"))
    float_shares = _safe_float(data_dict.get("float_shares"))
    industry = data_dict.get("industry", {})
    old_shares_meta = data_dict.get("old_shares_meta", {}) or {}

    if industry.get("primary") == "未分类":
        notes.append("当前标的尚未完成行业映射，方法二已自动回退全市场样本。建议在 `策略参数.txt` 中补充 `stock_industry` 或行业映射。")

    if issue_pe and industry_pe and industry_pe > 0:
        ratio = issue_pe / industry_pe
        if ratio < float(params.get("pe_low_threshold", 0.3)):
            notes.append("发行 PE 显著低于行业 PE，定价具备一定折价优势。")
        elif ratio > float(params.get("pe_high_threshold", 0.6)):
            notes.append("发行 PE 相对行业偏高，需要关注上市首日估值兑现压力。")

    if float_shares is not None and float_shares < float(params.get("float_size_threshold", 2000)):
        notes.append("首日流通盘偏小，历史上这类新股更容易获得情绪溢价。")

    old_shares_note = _build_old_shares_pending_note(
        data_dict.get("old_shares_desc", ""),
        old_shares_meta,
    )
    if old_shares_note:
        notes.append(old_shares_note)

    return notes
