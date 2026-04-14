from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


CODE_PATTERN = re.compile(r"\b\d{6}\.(?:SH|SZ|BJ|NQ)\b", re.IGNORECASE)
CODE_ONLY_PATTERN = re.compile(r"\b\d{6}\b")
SPECIFIC_SECTION_PATTERNS = (
    "可比公司选取标准及基本情况",
    "可比公司基本情况",
    "发行人与同行业可比公司",
)
GENERIC_SECTION_PATTERNS = (
    "同行业可比公司",
    "同行业上市公司",
    "可比公司",
)
BUSINESS_PATTERNS = (
    "主营业务情况",
    "主营业务",
    "公司简介",
    "发行人基本情况",
)
OLD_SHARE_PATTERNS = (
    "老股转让",
    "存量股份发售",
    "原股东公开发售",
    "存量发行",
    "公开发售股份",
)
OLD_SHARE_NEGATIVE_PATTERNS = (
    "本次发行全部为新股发行",
    "全部为新股发行",
    "不涉及原股东公开发售股份",
    "不涉及老股转让",
    "无老股转让",
    "不存在老股转让",
    "不存在存量股份发售",
    "未安排老股转让",
    "本次公开发售股份--",
)
OLD_SHARE_VALUE_PATTERNS = (
    re.compile(
        r"(?:老股转让|存量股份发售|原股东公开发售|存量发行|公开发售股份)"
        r"(?:的?股份)?(?:数量|股数|股份数量|规模)?(?:为|合计|共计|约|拟|不超过|不低于)?"
        r"[^0-9]{0,40}(?P<value>[0-9,]+(?:\.[0-9]+)?)\s*(?P<unit>万股|股)",
        re.IGNORECASE,
    ),
)
NAME_CODE_PATTERN = re.compile(
    r"(?P<name>[\u4e00-\u9fffA-Za-z]{2,24})\s*(?:\d+-\d+-\d+\s*)?[（(]\s*(?P<code>\d{6}\.(?:SH|SZ|BJ|NQ))\s*[）)]",
    re.IGNORECASE,
)
PLAIN_NAME_CODE_PATTERN = re.compile(
    r"(?P<name>[\u4e00-\u9fffA-Za-z]{2,24})\s*(?:\d+-\d+-\d+\s*)?[（(]\s*(?P<code>\d{6})\s*[）)]",
    re.IGNORECASE,
)
GLOSSARY_PATTERN = re.compile(
    r"(?P<name>[\u4e00-\u9fffA-Za-z]{2,24})\s*指[^\n]{0,80}?(?:股票代码|证券代码)\s*(?P<code>\d{6}\.(?:SH|SZ|BJ|NQ))",
    re.IGNORECASE,
)
ROW_NAME_PATTERN = re.compile(
    r"(?:^|\n)\s*(?P<name>[\u4e00-\u9fffA-Za-z]{2,16})\s*(?:\n[（(]|\s+(?:暂未披露|\d{4}\s*年|\d+\.\d+%))",
    re.IGNORECASE,
)
ROW_NAME_STOPWORDS = {
    "发行人",
    "公司名称",
    "项目",
    "区域",
    "主营业务",
    "资产总额",
    "营业收入",
    "毛利率",
    "净利润",
    "公司",
}


def _normalize_text(text: str) -> str:
    text = re.sub(r"\b\d+-\d+-\d+\b", " ", text or "")
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _iter_page_texts(file_path: Path) -> Iterable[str]:
    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                yield page.extract_text() or ""
        return
    except Exception:
        pass

    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(file_path))
        for page in reader.pages:
            yield page.extract_text() or ""
        return
    except Exception:
        pass

    try:
        from PyPDF2 import PdfReader  # type: ignore

        reader = PdfReader(str(file_path))
        for page in reader.pages:
            yield page.extract_text() or ""
    except Exception:
        return


def _read_pdf_text(pdf_path: str | Path) -> str:
    file_path = Path(pdf_path)
    if not file_path.exists():
        return ""
    return "\n".join(page_text for page_text in _iter_page_texts(file_path) if page_text)


def _extract_windows(text: str, keywords: Iterable[str], radius: int = 2000) -> list[str]:
    windows: list[str] = []
    for keyword in keywords:
        start = 0
        while True:
            idx = text.find(keyword, start)
            if idx < 0:
                break
            windows.append(text[max(0, idx - 300) : idx + radius])
            start = idx + len(keyword)
    return windows


def _trim_comparable_window(window: str) -> str:
    stop_markers = (
        "\n发行人 ",
        "\n发行人\n",
        "三、 发行人主营业务情况",
        "三、发行人主营业务情况",
    )
    trimmed = window
    for marker in stop_markers:
        idx = trimmed.find(marker)
        if idx > 200:
            trimmed = trimmed[:idx]
    return trimmed


def _dedupe_codes(codes: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for code in codes:
        current = code.upper().strip()
        if current not in seen:
            seen.append(current)
    return seen


def _normalize_code(code: str) -> str:
    current = code.upper().strip()
    if "." in current:
        return current
    if current.startswith(("600", "601", "603", "605", "688")):
        return f"{current}.SH"
    if current.startswith(("000", "001", "002", "003", "300", "301")):
        return f"{current}.SZ"
    if current.startswith(("830", "831", "832", "833", "834", "835", "836", "837", "838")):
        return f"{current}.NQ"
    if current.startswith("920"):
        return f"{current}.BJ"
    return current


def _search_code_for_name(text: str, name: str) -> str | None:
    escaped = re.escape(name)
    patterns = [
        re.compile(rf"{escaped}[^\n]{{0,60}}?(?P<code>\d{{6}}\.(?:SH|SZ|BJ|NQ))", re.IGNORECASE),
        re.compile(rf"{escaped}\s*[（(]\s*(?P<code>\d{{6}}\.(?:SH|SZ|BJ|NQ))\s*[）)]", re.IGNORECASE),
        re.compile(rf"{escaped}[^\n]{{0,30}}?(?:股票代码|证券代码)\s*(?P<code>\d{{6}}\.(?:SH|SZ|BJ|NQ))", re.IGNORECASE),
    ]
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group("code").upper()
    return None


def _extract_old_shares_from_text(text: str) -> float | None:
    compact_text = _compact_text(text)
    if any(pattern in compact_text for pattern in OLD_SHARE_NEGATIVE_PATTERNS):
        return 0.0

    windows = _extract_windows(text, OLD_SHARE_PATTERNS, radius=800)
    if not windows:
        windows = [text]

    for window in windows:
        compact_window = _compact_text(window)
        if any(pattern in compact_window for pattern in OLD_SHARE_NEGATIVE_PATTERNS):
            return 0.0
        for pattern in OLD_SHARE_VALUE_PATTERNS:
            match = pattern.search(window)
            if not match:
                continue
            value = float(match.group("value").replace(",", ""))
            unit = match.group("unit")
            return value if unit == "万股" else value / 10000
    return None


def extract_old_shares(pdf_path: str | Path) -> float | None:
    text = _read_pdf_text(pdf_path)
    if not text:
        return None
    return _extract_old_shares_from_text(text)


def _extract_named_comparables(text: str) -> list[str]:
    names: list[str] = []
    sentence_patterns = [
        re.compile(r"基于上述标准，公司选取了(?P<names>[^。；\n]+?)作为(?:同行业)?可比公司"),
        re.compile(r"选取了(?P<names>[^。；\n]+?)作为(?:同行业)?可比公司"),
    ]
    for pattern in sentence_patterns:
        for match in pattern.finditer(text):
            raw_names = match.group("names")
            for part in re.split(r"[、，,及和与]", raw_names):
                name = part.strip()
                if 1 < len(name) <= 24 and "可比公司" not in name:
                    names.append(name)
    return names


def _collect_name_code_pairs(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for pattern in (NAME_CODE_PATTERN, GLOSSARY_PATTERN, PLAIN_NAME_CODE_PATTERN):
        for match in pattern.finditer(text):
            name = match.group("name").strip()
            code = _normalize_code(match.group("code"))
            if (name, code) not in pairs:
                pairs.append((name, code))
    return pairs


def _extract_row_company_names(window: str) -> list[str]:
    names: list[str] = []
    for match in ROW_NAME_PATTERN.finditer(window):
        name = match.group("name").strip()
        if name in ROW_NAME_STOPWORDS:
            continue
        if name not in names:
            names.append(name)
    return names


def extract_comparable_companies(pdf_path: str | Path) -> list[str]:
    text = _read_pdf_text(pdf_path)
    if not text:
        return []

    candidate_windows = _extract_windows(text, SPECIFIC_SECTION_PATTERNS, radius=3500)
    if not candidate_windows:
        candidate_windows = _extract_windows(text, GENERIC_SECTION_PATTERNS, radius=2500)
    if not candidate_windows:
        candidate_windows = [text]

    collected_codes: list[str] = []
    name_code_pairs = _collect_name_code_pairs(text)
    loose_match_codes: list[str] = []
    for window in candidate_windows:
        window = _trim_comparable_window(window)
        for pattern in (NAME_CODE_PATTERN, GLOSSARY_PATTERN, PLAIN_NAME_CODE_PATTERN):
            for match in pattern.finditer(window):
                collected_codes.append(_normalize_code(match.group("code")))
        collected_codes.extend(CODE_PATTERN.findall(window))
        for name in _extract_row_company_names(window):
            code = _search_code_for_name(text, name)
            if code:
                collected_codes.append(code)
        for name, code in name_code_pairs:
            if name in window:
                loose_match_codes.append(code)

    if not collected_codes:
        for name in _extract_named_comparables(text):
            code = _search_code_for_name(text, name)
            if code:
                collected_codes.append(code)
    if not collected_codes:
        collected_codes.extend(loose_match_codes)

    return _dedupe_codes(collected_codes)


def _extract_business_desc_from_text(text: str) -> str:
    normalized_text = _normalize_text(text)
    if not normalized_text:
        return ""

    for keyword in BUSINESS_PATTERNS:
        index = normalized_text.find(keyword)
        if index < 0:
            continue
        snippet = normalized_text[index : index + 1200]
        sentence_match = re.search(r"(公司是一家[^。]{20,220}。?)", snippet)
        if sentence_match:
            return sentence_match.group(1).strip()
        sentence_match = re.search(r"(公司主要从事[^。]{20,220}。?)", snippet)
        if sentence_match:
            return sentence_match.group(1).strip()
        return snippet[:240].strip()

    sentence_match = re.search(r"(公司是一家[^。]{20,220}。?)", normalized_text)
    if sentence_match:
        return sentence_match.group(1).strip()
    sentence_match = re.search(r"(公司主要从事[^。]{20,220}。?)", normalized_text)
    if sentence_match:
        return sentence_match.group(1).strip()
    return normalized_text[:240].strip()


def extract_business_desc(pdf_path: str | Path) -> str:
    text = _read_pdf_text(pdf_path)
    if not text:
        return ""
    return _extract_business_desc_from_text(text)
