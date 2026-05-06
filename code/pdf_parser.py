from __future__ import annotations

import json
from dataclasses import asdict, dataclass
import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable


CODE_PATTERN = re.compile(r"\b\d{6}\.(?:SH|SZ|BJ|NQ)\b", re.IGNORECASE)
CODE_ONLY_PATTERN = re.compile(r"\b\d{6}\b")
SPECIFIC_SECTION_PATTERNS = (
    "可比公司选取标准及基本情况",
    "可比公司基本情况",
    "发行人与同行业可比公司",
    "与同行业可比公司的对比分析",
    "发行人与同行业可比公司的对比分析",
)
PROSPECTUS_BUSINESS_CHAPTER_PATTERNS = (
    "第五节业务和技术",
    "第五节业务与技术",
    "第六节业务和技术",
    "第六节业务与技术",
)
GENERIC_SECTION_PATTERNS = (
    "同行业可比公司",
    "同行业上市公司",
    "可比公司",
)
COMPARABLE_SECTION_STOP_PATTERNS = (
    "主营业务情况",
    "公司简介",
    "发行人基本情况",
    "募集资金运用",
    "募集资金投资项目",
    "主要财务数据和财务指标",
    "股票发行情况",
    "风险因素",
)
BUSINESS_PRIMARY_PATTERNS = (
    "发行人主营业务情况",
    "主营业务情况",
    "公司简介",
)
BUSINESS_FALLBACK_PATTERNS = (
    "发行人基本情况",
)
BUSINESS_SECTION_STOP_PATTERNS = (
    "同行业可比公司",
    "同行业上市公司",
    "可比公司选取标准及基本情况",
    "可比公司基本情况",
    "募集资金运用",
    "募集资金投资项目",
    "主要财务数据和财务指标",
    "主要财务数据",
    "股票发行情况",
    "风险因素",
)
BUSINESS_SENTENCE_PATTERNS = (
    re.compile(r"((?!公司|发行人)[\u4e00-\u9fffA-Za-z]{2,24}主要从事[^。]{20,220}。?)"),
    re.compile(r"((?!公司|发行人)[\u4e00-\u9fffA-Za-z]{2,24}是一家[^。]{20,220}。?)"),
    re.compile(r"((?!公司|发行人)[\u4e00-\u9fffA-Za-z]{2,24}主营业务为[^。]{20,220}。?)"),
    re.compile(r"(公司是一家[^。]{20,220}。?)"),
    re.compile(r"(公司是中国领先的[^。]{20,220}。?)"),
    re.compile(r"(公司是[^。]{20,220}。?)"),
    re.compile(r"(公司主要从事[^。]{20,220}。?)"),
    re.compile(r"(公司专业从事[^。]{20,220}。?)"),
    re.compile(r"(公司专注于[^。]{20,220}。?)"),
    re.compile(r"(公司长期专注于[^。]{20,220}。?)"),
    re.compile(r"(公司主营业务为[^。]{20,220}。?)"),
    re.compile(r"(发行人是一家[^。]{20,220}。?)"),
    re.compile(r"(发行人主要从事[^。]{20,220}。?)"),
    re.compile(r"(发行人专业从事[^。]{20,220}。?)"),
    re.compile(r"(发行人主营业务为[^。]{20,220}。?)"),
)
BUSINESS_NOISE_MARKERS = (
    "招股说明书",
    "招股意向书",
    "上市公告书",
    "证券简称",
    "证券代码",
    "子公司",
    "注销",
    "纳入合并",
    "挂牌期间",
    "前五大客户",
    "主要客户",
    "销售情况",
    "产能",
    "产量",
    "销量",
    "产销率",
    "比较情况",
)
BUSINESS_REJECT_MARKERS = (
    "公司是否",
    "发行人是否",
    "主要责任人",
    "关联关系",
    "劳务派遣",
    "发行条件",
    "上市条件",
    "股份回购",
    "回购本次",
    "现金方式分配利润",
)
BUSINESS_REQUIRED_KEYWORDS = (
    "从事",
    "主营业务",
    "研发",
    "生产",
    "制造",
    "销售",
    "服务",
    "解决方案",
    "提供商",
    "供应商",
)
BUSINESS_TRIM_MARKERS = (
    "具体情况如下",
    "主要产品与服务项目",
    "行业内其他主要企业情况如下",
    "二、发行人挂牌期间的基本情况",
    "二、 发行人挂牌期间的基本情况",
    "二、控股股东",
    "二、 控股股东",
    "（一）技术创新",
)
OLD_SHARE_PATTERNS = (
    "老股转让",
    "存量股份发售",
    "原股东公开发售",
    "存量发行",
    "公开发售股份",
)
LISTING_OLD_SHARE_CHAPTER_PATTERNS = (
    "第三节发行人、实际控制人及股东持股情况",
    "第三节发行人、控股股东、实际控制人及股东持股情况",
)
LISTING_OLD_SHARE_SECTION_PATTERNS = (
    "本次发行前后的股本结构变动情况",
)
LISTING_OLD_SHARE_ROW_MARKERS = (
    "无限售流通股小计",
    "无限售条件流通股小计",
    "无限售流通股份小计",
    "无限售条件流通股份小计",
)
LISTING_OLD_SHARE_BLOCK_MARKERS = (
    "无限售流通股",
    "无限售条件流通股",
    "无限售流通股份",
    "无限售条件流通股份",
)
LISTING_OLD_SHARE_SECTION_STOP_PATTERNS = (
    "本次发行后公司前十名股东持股情况",
    "第四节股票发行情况",
    "第四节发行情况",
    "第四节股票公开发行情况",
)
OLD_SHARE_NEGATIVE_PATTERNS = (
    "本次发行全部为新股发行",
    "全部为新股发行",
    "不涉及原股东公开发售股份",
    "原股东不公开发售股份",
    "公司原股东不公开发售股份",
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
GLOSSARY_ENTRY_SPAN = r"(?:(?!\s[\u4e00-\u9fffA-Za-z]{2,24}\s*指).){0,180}?"
GLOSSARY_PATTERN = re.compile(
    rf"(?P<name>[\u4e00-\u9fffA-Za-z]{{2,24}})\s*指{GLOSSARY_ENTRY_SPAN}(?:股票代码|证券代码|挂牌代码)\s*(?P<code>\d{{6}}\.(?:SH|SZ|BJ|NQ))",
    re.IGNORECASE,
)
GLOSSARY_COMPARABLE_PATTERN = re.compile(
    rf"(?P<name>[\u4e00-\u9fffA-Za-z]{{2,24}})\s*指{GLOSSARY_ENTRY_SPAN}(?:同行业可比公司|可比公司)",
    re.IGNORECASE,
)
GLOSSARY_ENTRY_PATTERN = re.compile(r"(?P<name>[\u4e00-\u9fffA-Za-z]{2,24})\s*指", re.IGNORECASE)
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
COMPARABLE_NAME_CODE_FALLBACKS = {
    "旭升集团": "603305.SH",
    "爱柯迪": "600933.SH",
    "晋拓股份": "603211.SH",
    "嵘泰股份": "605133.SH",
    "怡合达": "301029.SZ",
    "博众精工": "688097.SH",
    "先导智能": "300450.SZ",
    "宏工科技": "301662.SZ",
    "福能东方": "300173.SZ",
}
PARSE_CACHE_SCHEMA = "pdf_parse_cache_v1"
PARSE_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "pdf_parse_cache"
_CACHE_MISSING = object()


@dataclass(frozen=True)
class OldSharesExtractionResult:
    value_wan_shares: float
    source_file_type: str
    source_rule: str
    source_anchor: str
    raw_snippet: str
    confidence: float
    unit: str
    pre_unrestricted_wan_shares: float | None = None


def _parse_cache_path(pdf_path: str | Path, kind: str) -> Path:
    resolved = str(Path(pdf_path).resolve())
    digest = hashlib.sha1(f"{kind}|{resolved}".encode("utf-8")).hexdigest()
    return PARSE_CACHE_DIR / f"{digest}.json"


def _load_parse_cache(pdf_path: str | Path, kind: str) -> object:
    file_path = Path(pdf_path)
    if not file_path.exists():
        return _CACHE_MISSING
    cache_path = _parse_cache_path(file_path, kind)
    if not cache_path.exists():
        return _CACHE_MISSING
    try:
        stat = file_path.stat()
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return _CACHE_MISSING
    if not isinstance(payload, dict):
        return _CACHE_MISSING
    if payload.get("schema") != PARSE_CACHE_SCHEMA or payload.get("kind") != kind:
        return _CACHE_MISSING
    if int(payload.get("mtime_ns") or -1) != stat.st_mtime_ns:
        return _CACHE_MISSING
    if int(payload.get("size") or -1) != stat.st_size:
        return _CACHE_MISSING
    return payload.get("value")


def _save_parse_cache(pdf_path: str | Path, kind: str, value: object) -> None:
    file_path = Path(pdf_path)
    if not file_path.exists():
        return
    try:
        stat = file_path.stat()
        PARSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": PARSE_CACHE_SCHEMA,
            "kind": kind,
            "path": str(file_path.resolve()),
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "value": value,
        }
        _parse_cache_path(file_path, kind).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        return


def _old_shares_result_from_cache(value: object) -> OldSharesExtractionResult | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    try:
        return OldSharesExtractionResult(**value)
    except TypeError:
        return None


def _normalize_text(text: str) -> str:
    text = re.sub(r"\b\d+-\d+-\d+\b", " ", text or "")
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _infer_pdf_file_type(pdf_path: str | Path) -> str:
    stem = Path(pdf_path).stem
    if "上市公告书" in stem or "上市公告" in stem:
        return "上市公告书"
    return "招股文件"


def _find_anchor_positions(text: str, anchors: Iterable[str], start: int = 0) -> list[tuple[int, str]]:
    positions: list[tuple[int, str]] = []
    for anchor in anchors:
        cursor = start
        while True:
            index = text.find(anchor, cursor)
            if index < 0:
                break
            positions.append((index, anchor))
            cursor = index + len(anchor)
    return positions


def _extract_compact_section(
    compact_text: str,
    section_anchors: Iterable[str],
    stop_anchors: Iterable[str],
    *,
    chapter_anchors: Iterable[str] = (),
    fallback_radius: int = 2600,
) -> tuple[str, str]:
    chapter_positions = _find_anchor_positions(compact_text, chapter_anchors) if chapter_anchors else []
    chapter_start = 0
    if chapter_positions:
        chapter_start = max((index for index, _ in chapter_positions), default=0)
        for index, _ in sorted(chapter_positions, key=lambda item: item[0]):
            snippet = compact_text[index : index + 120]
            if "..." in snippet or "……" in snippet:
                continue
            chapter_start = index
            break

    section_positions = [item for item in _find_anchor_positions(compact_text, section_anchors, start=chapter_start) if item[0] >= chapter_start]
    if section_positions:
        section_index, section_anchor = min(section_positions, key=lambda item: item[0])
    else:
        fallback_sections = _find_anchor_positions(compact_text, section_anchors)
        if not fallback_sections:
            return "", ""
        section_index, section_anchor = max(fallback_sections, key=lambda item: item[0])

    stop_positions = [
        item
        for item in _find_anchor_positions(compact_text, stop_anchors, start=section_index + len(section_anchor))
        if item[0] > section_index
    ]
    section_end = min((index for index, _ in stop_positions), default=min(len(compact_text), section_index + fallback_radius))
    return compact_text[section_index:section_end], section_anchor


def _make_raw_snippet(text: str, start_index: int, length: int = 180) -> str:
    snippet = text[max(0, start_index - 40) : start_index + length]
    return snippet[:length]


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

        with file_path.open("rb") as file_obj:
            reader = PdfReader(file_obj)
            for page in reader.pages:
                yield page.extract_text() or ""
        return
    except Exception:
        pass

    try:
        from PyPDF2 import PdfReader  # type: ignore

        with file_path.open("rb") as file_obj:
            reader = PdfReader(file_obj)
            for page in reader.pages:
                yield page.extract_text() or ""
    except Exception:
        return


@lru_cache(maxsize=32)
def _read_pdf_text_cached(path_text: str) -> str:
    file_path = Path(path_text)
    if not file_path.exists():
        return ""
    return "\n".join(page_text for page_text in _iter_page_texts(file_path) if page_text)


def _read_pdf_text(pdf_path: str | Path) -> str:
    file_path = Path(pdf_path)
    return _read_pdf_text_cached(str(file_path.resolve()))


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
        re.compile(rf"{escaped}[^\n]{{0,120}}?(?P<code>\d{{6}}\.(?:SH|SZ|BJ|NQ))", re.IGNORECASE),
        re.compile(rf"{escaped}\s*[（(]\s*(?P<code>\d{{6}}\.(?:SH|SZ|BJ|NQ))\s*[）)]", re.IGNORECASE),
        re.compile(rf"{escaped}[^\n]{{0,80}}?(?:股票代码|证券代码)\s*(?P<code>\d{{6}}\.(?:SH|SZ|BJ|NQ))", re.IGNORECASE),
        re.compile(rf"{escaped}[^\n]{{0,180}}?(?:股票代码|证券代码)\s*[：:\s]\s*(?P<code>\d{{6}})", re.IGNORECASE),
        re.compile(rf"{escaped}[^\n]{{0,80}}?[（(]\s*(?P<code>\d{{6}})\s*[）)]", re.IGNORECASE),
    ]
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return _normalize_code(match.group("code"))
    return _lookup_comparable_code_by_name(name)


def _extract_glossary_labeled_comparable_codes(raw_text: str, lookup_text: str) -> list[str]:
    codes: list[str] = []
    entry_matches = list(GLOSSARY_ENTRY_PATTERN.finditer(raw_text))
    for index, match in enumerate(entry_matches):
        next_start = entry_matches[index + 1].start() if index + 1 < len(entry_matches) else min(len(raw_text), match.end() + 180)
        segment = raw_text[match.start() : next_start]
        compact_segment = _compact_text(segment)
        if "同行业可比公司" not in compact_segment and "可比公司" not in compact_segment:
            continue
        code = _search_code_for_name(lookup_text, match.group("name").strip())
        if code:
            codes.append(code)
    return _dedupe_codes(codes)


@lru_cache(maxsize=1)
def _load_local_comparable_name_code_index() -> dict[str, str]:
    index = dict(COMPARABLE_NAME_CODE_FALLBACKS)
    base_dir = Path(__file__).resolve().parents[1]
    candidate_dirs = (
        base_dir / "data" / "wind_db" / "fixed_fields",
        base_dir / "data" / "tushare_db" / "fixed_fields",
        base_dir / "data" / "temp_validation",
    )
    for candidate_dir in candidate_dirs:
        if not candidate_dir.exists():
            continue
        for file_path in candidate_dir.rglob("*.json"):
            if "fixed_fields" not in file_path.parts:
                continue
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            fields = payload.get("fields") or {}
            if not isinstance(fields, dict):
                continue
            name = str(fields.get("name") or "").strip()
            code = str(payload.get("code") or "").strip()
            if name and code:
                index.setdefault(name, code)
    return index


def _lookup_comparable_code_by_name(name: str) -> str | None:
    code = _load_local_comparable_name_code_index().get(name.strip())
    if not code:
        return None
    return _normalize_code(code)


def _extract_old_shares_result_from_text(text: str, source_file_type: str) -> OldSharesExtractionResult | None:
    listing_result = _extract_old_shares_from_listing_table(text, source_file_type)
    if listing_result is not None:
        return listing_result

    compact_text = _compact_text(text)
    for pattern in OLD_SHARE_NEGATIVE_PATTERNS:
        index = compact_text.find(pattern)
        if index >= 0:
            return OldSharesExtractionResult(
                value_wan_shares=0.0,
                source_file_type=source_file_type,
                source_rule="negative_phrase",
                source_anchor=pattern,
                raw_snippet=_make_raw_snippet(compact_text, index),
                confidence=0.8,
                unit="万股",
                pre_unrestricted_wan_shares=0.0,
            )

    windows = _extract_windows(text, OLD_SHARE_PATTERNS, radius=800)
    if not windows:
        windows = [text]

    for window in windows:
        compact_window = _compact_text(window)
        for pattern in OLD_SHARE_NEGATIVE_PATTERNS:
            index = compact_window.find(pattern)
            if index >= 0:
                return OldSharesExtractionResult(
                    value_wan_shares=0.0,
                    source_file_type=source_file_type,
                    source_rule="negative_phrase",
                    source_anchor=pattern,
                    raw_snippet=_make_raw_snippet(compact_window, index),
                    confidence=0.75,
                    unit="万股",
                    pre_unrestricted_wan_shares=0.0,
                )
        for pattern in OLD_SHARE_VALUE_PATTERNS:
            match = pattern.search(window)
            if not match:
                continue
            value = float(match.group("value").replace(",", ""))
            unit = match.group("unit")
            value_wan_shares = value if unit == "万股" else value / 10000
            anchor = next((keyword for keyword in OLD_SHARE_PATTERNS if keyword in compact_window), "关键词窗口")
            return OldSharesExtractionResult(
                value_wan_shares=value_wan_shares,
                source_file_type=source_file_type,
                source_rule="keyword_value",
                source_anchor=anchor,
                raw_snippet=_make_raw_snippet(_compact_text(match.group(0)), 0),
                confidence=0.7,
                unit=unit,
                pre_unrestricted_wan_shares=value_wan_shares,
            )
    return None


def _infer_listing_share_unit_from_payload(payload: str) -> str:
    leading_match = re.match(
        r"^-{0,4}(?P<share>[0-9,]+(?:\.[0-9]{1,4})?)(?P<pct>[0-9]{1,3}\.[0-9]{2,4}%?)",
        payload,
        re.IGNORECASE,
    )
    if not leading_match:
        return ""
    share_token = leading_match.group("share")
    if "," in share_token and "." not in share_token:
        return "股"
    if "." in share_token:
        return "万股"
    return ""


def _detect_listing_share_unit(compact_text: str, marker_index: int, payload: str) -> str:
    window = compact_text[max(0, marker_index - 10000) : marker_index]
    if any(marker in window for marker in ("数量（万股）", "持股数量（万股）", "股份数量（万股）")):
        return "万股"
    if any(marker in window for marker in ("数量（股）", "持股数量（股）", "股份数量（股）")):
        return "股"
    return _infer_listing_share_unit_from_payload(payload)


def _find_listing_table_row(search_text: str) -> tuple[str, int, int] | None:
    for marker in LISTING_OLD_SHARE_ROW_MARKERS:
        marker_index = search_text.find(marker)
        if marker_index >= 0:
            return marker, marker_index, marker_index + len(marker)

    for block_marker in LISTING_OLD_SHARE_BLOCK_MARKERS:
        block_index = search_text.find(block_marker)
        if block_index < 0:
            continue
        window = search_text[block_index + len(block_marker) : block_index + len(block_marker) + 220]
        for summary_marker in ("小计", "合计"):
            relative_index = window.find(summary_marker)
            if relative_index >= 0:
                marker_index = block_index + len(block_marker) + relative_index
                payload_start = marker_index + len(summary_marker)
                return f"{block_marker} -> {summary_marker}", marker_index, payload_start
    return None


def _extract_old_shares_from_listing_table(text: str, source_file_type: str) -> OldSharesExtractionResult | None:
    compact_text = _compact_text(text)
    section_text, section_anchor = _extract_compact_section(
        compact_text,
        LISTING_OLD_SHARE_SECTION_PATTERNS,
        LISTING_OLD_SHARE_SECTION_STOP_PATTERNS,
        chapter_anchors=LISTING_OLD_SHARE_CHAPTER_PATTERNS,
        fallback_radius=2600,
    )
    search_targets: list[tuple[str, str, bool]] = []
    if section_text:
        search_targets.append((section_text, section_anchor, True))
    if not section_text or section_text != compact_text:
        search_targets.append((compact_text, section_anchor, False))

    for search_text, anchor_prefix, used_section in search_targets:
        row_info = _find_listing_table_row(search_text)
        if row_info is None:
            continue
        marker, marker_index, payload_start = row_info
        payload = search_text[payload_start : payload_start + 260]
        unit = _detect_listing_share_unit(search_text, marker_index, payload)
        if not unit:
            unit = "万股" if "." in payload[:40] and "," not in payload[:40] else "股"

        percent_pattern = r"[0-9]{1,3}\.[0-9]{2,4}%?"
        if unit == "股":
            share_count_pattern = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)"
            patterns = (
                re.compile(
                    rf"^(?P<pre>{share_count_pattern})(?P<pre_pct>{percent_pattern})"
                    rf"(?P<post>{share_count_pattern})(?P<post_pct>{percent_pattern})",
                    re.IGNORECASE,
                ),
                re.compile(
                    rf"^(?P<pre>-{{2,}})(?P<post>{share_count_pattern})(?P<post_pct>{percent_pattern})",
                    re.IGNORECASE,
                ),
            )
        else:
            share_count_pattern = r"[0-9,]+(?:\.[0-9]{1,4})?"
            patterns = (
                re.compile(
                    rf"^(?P<pre>{share_count_pattern})(?P<pre_pct>{percent_pattern})"
                    rf"(?P<post>{share_count_pattern})(?P<post_pct>{percent_pattern})",
                    re.IGNORECASE,
                ),
                re.compile(
                    rf"^(?P<pre>-{{2,}})(?P<post>{share_count_pattern})(?P<post_pct>{percent_pattern})",
                    re.IGNORECASE,
                ),
            )

        match = None
        for current_pattern in patterns:
            match = current_pattern.search(payload)
            if match:
                break
        if not match:
            continue

        pre_value = match.group("pre").strip("-")
        source_anchor = f"{anchor_prefix or '股本结构表'} -> {marker}"
        if not pre_value:
            return OldSharesExtractionResult(
                value_wan_shares=0.0,
                source_file_type=source_file_type,
                source_rule="listing_table",
                source_anchor=source_anchor,
                raw_snippet=_make_raw_snippet(search_text, marker_index),
                confidence=0.95 if used_section else 0.88,
                unit=unit,
                pre_unrestricted_wan_shares=0.0,
            )
        value = float(pre_value.replace(",", ""))
        value_wan_shares = value if unit == "万股" else value / 10000
        return OldSharesExtractionResult(
            value_wan_shares=value_wan_shares,
            source_file_type=source_file_type,
            source_rule="listing_table",
            source_anchor=source_anchor,
            raw_snippet=_make_raw_snippet(search_text, marker_index),
            confidence=0.98 if used_section else 0.9,
            unit=unit,
            pre_unrestricted_wan_shares=value_wan_shares,
        )

    return None


def extract_old_shares_result(pdf_path: str | Path) -> OldSharesExtractionResult | None:
    cached = _load_parse_cache(pdf_path, "old_shares_result")
    if cached is not _CACHE_MISSING:
        if cached is None:
            return None
        cached_result = _old_shares_result_from_cache(cached)
        if cached_result is not None:
            return cached_result

    text = _read_pdf_text(pdf_path)
    if not text:
        _save_parse_cache(pdf_path, "old_shares_result", None)
        return None
    result = _extract_old_shares_result_from_text(text, _infer_pdf_file_type(pdf_path))
    _save_parse_cache(pdf_path, "old_shares_result", asdict(result) if result is not None else None)
    return result


def extract_old_shares(pdf_path: str | Path) -> float | None:
    result = extract_old_shares_result(pdf_path)
    if result is None:
        return None
    return result.value_wan_shares


def _extract_named_comparables(text: str) -> list[str]:
    names: list[str] = []
    sentence_patterns = [
        re.compile(r"基于上述标准，公司选取了(?P<names>[^。；\n]+?)作为(?:同行业)?可比公司"),
        re.compile(r"选取(?:了)?(?P<names>[^。；\n]+?)作为(?:同行业)?可比公司"),
        re.compile(r"(?:基于上述标准，公司|公司)?选取(?:了)?(?:国内)?上市公司(?P<names>[^。；\n]+?)作为(?:同行业)?可比公司"),
    ]
    for pattern in sentence_patterns:
        for match in pattern.finditer(text):
            raw_names = match.group("names")
            for part in re.split(r"[、，,及和与]", raw_names):
                name = part.strip()
                name = re.sub(r"^(?:国内)?上市公司", "", name).strip()
                if "的" in name:
                    candidate = name.rsplit("的", 1)[-1].strip()
                    if 1 < len(candidate) <= 24:
                        name = candidate
                if 1 < len(name) <= 24 and "可比公司" not in name:
                    names.append(name)
    return names


def _extract_known_comparable_names(text: str) -> list[str]:
    positions: list[tuple[int, str]] = []
    for name in COMPARABLE_NAME_CODE_FALLBACKS:
        index = text.find(name)
        if index >= 0:
            positions.append((index, name))
    return [name for _, name in sorted(positions, key=lambda item: item[0])]


def _extract_comparable_codes_from_section(section_text: str, full_text: str) -> list[str]:
    collected_codes: list[str] = []
    name_code_pairs = _collect_name_code_pairs(full_text)

    for pattern in (NAME_CODE_PATTERN, GLOSSARY_PATTERN, PLAIN_NAME_CODE_PATTERN):
        for match in pattern.finditer(section_text):
            collected_codes.append(_normalize_code(match.group("code")))
    collected_codes.extend(CODE_PATTERN.findall(section_text))

    named_codes: list[str] = []
    for name in _extract_named_comparables(section_text):
        code = _search_code_for_name(full_text, name)
        if code:
            named_codes.append(code)

    if named_codes:
        return _dedupe_codes(collected_codes + named_codes)

    for name in _extract_row_company_names(section_text):
        code = _search_code_for_name(full_text, name)
        if code:
            collected_codes.append(code)

    for name in _extract_known_comparable_names(section_text):
        code = _search_code_for_name(full_text, name)
        if code:
            collected_codes.append(code)

    for name, code in name_code_pairs:
        if name in section_text:
            collected_codes.append(code)

    return _dedupe_codes(collected_codes)


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


def _extract_comparable_companies_legacy(text: str) -> list[str]:
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


def extract_comparable_companies(pdf_path: str | Path) -> list[str]:
    cached = _load_parse_cache(pdf_path, "comparable_companies")
    if cached is not _CACHE_MISSING and isinstance(cached, list):
        return [str(code) for code in cached]

    text = _read_pdf_text(pdf_path)
    if not text:
        _save_parse_cache(pdf_path, "comparable_companies", [])
        return []

    normalized_text = _normalize_text(text)
    glossary_codes = _extract_glossary_labeled_comparable_codes(text, normalized_text)
    specific_section_text, specific_section_anchor = _extract_compact_section(
        normalized_text,
        SPECIFIC_SECTION_PATTERNS,
        COMPARABLE_SECTION_STOP_PATTERNS,
        chapter_anchors=PROSPECTUS_BUSINESS_CHAPTER_PATTERNS,
        fallback_radius=3600,
    )
    if specific_section_text:
        specific_codes = _extract_comparable_codes_from_section(specific_section_text, normalized_text)
        if specific_codes:
            result_codes = _dedupe_codes(specific_codes + glossary_codes)
            target_code = Path(pdf_path).stem[:6]
            if len(result_codes) > 1:
                result_codes = [code for code in result_codes if code.split(".", 1)[0] != target_code]
            _save_parse_cache(pdf_path, "comparable_companies", result_codes)
            return result_codes

    generic_section_text, generic_section_anchor = _extract_compact_section(
        normalized_text,
        GENERIC_SECTION_PATTERNS,
        COMPARABLE_SECTION_STOP_PATTERNS,
        chapter_anchors=PROSPECTUS_BUSINESS_CHAPTER_PATTERNS,
        fallback_radius=4200,
    )
    if generic_section_text:
        generic_codes = _extract_comparable_codes_from_section(generic_section_text, normalized_text)
        if generic_codes and (
            generic_section_anchor not in GENERIC_SECTION_PATTERNS
            or len(generic_codes) <= 4
            or any(marker in generic_section_text for marker in ("选取", "作为同行业可比公司", "可比公司基本情况", "可比公司选取标准"))
        ):
            result_codes = _dedupe_codes(generic_codes + glossary_codes)
            target_code = Path(pdf_path).stem[:6]
            if len(result_codes) > 1:
                result_codes = [code for code in result_codes if code.split(".", 1)[0] != target_code]
            _save_parse_cache(pdf_path, "comparable_companies", result_codes)
            return result_codes

    if glossary_codes:
        result_codes = list(glossary_codes)
        target_code = Path(pdf_path).stem[:6]
        if len(result_codes) > 1:
            result_codes = [code for code in result_codes if code.split(".", 1)[0] != target_code]
        _save_parse_cache(pdf_path, "comparable_companies", result_codes)
        return result_codes

    result_codes = _extract_comparable_companies_legacy(text)
    target_code = Path(pdf_path).stem[:6]
    if len(result_codes) > 1:
        result_codes = [code for code in result_codes if code.split(".", 1)[0] != target_code]
    _save_parse_cache(pdf_path, "comparable_companies", result_codes)
    return result_codes


def _clean_business_desc(text: str) -> str:
    cleaned = re.sub(r"^(?:主营业务情况|主营业务|公司简介|发行人基本情况)\s*", "", text or "")
    cleaned = re.sub(
        r"^(?!公司|发行人)(?:[\u4e00-\u9fffA-Za-z]{2,24})(?=(?:主要从事|是一家|主营业务为|是中国领先的|是国内领先的))",
        "公司",
        cleaned,
    )
    for marker in BUSINESS_TRIM_MARKERS:
        idx = cleaned.find(marker)
        if idx > 20:
            cleaned = cleaned[:idx]
    cleaned = re.sub(r"[，,]\s*除[\u4e00-\u9fffA-Za-z0-9（）()&\s]{1,40}外\s*$", "", cleaned)
    return cleaned.strip(" ：:;，,")


def _is_plausible_business_desc(text: str) -> bool:
    current = _clean_business_desc(text)
    if not current:
        return False
    if any(marker in current for marker in BUSINESS_REJECT_MARKERS):
        return False
    if re.search(r"除[\u4e00-\u9fffA-Za-z0-9（）()&\s]{1,30}外", current):
        return False
    if not any(keyword in current for keyword in BUSINESS_REQUIRED_KEYWORDS):
        return False
    if (current.startswith("公司是") or current.startswith("发行人是")) and not any(
        keyword in current
        for keyword in ("一家", "中国领先", "国内领先", "从事", "主营业务", "提供商", "供应商", "解决方案")
    ):
        return False
    return True


def _score_business_desc(text: str) -> tuple[int, int]:
    score = 0
    current = text or ""
    if current.startswith("公司是"):
        score += 3
    if current.startswith("公司是一家"):
        score += 4
    if current.startswith("公司是中国领先的"):
        score += 4
    if current.startswith("公司主要从事"):
        score += 3
    if current.startswith("公司主营业务为"):
        score += 3
    if current.startswith("公司专业从事") or current.startswith("公司专注于") or current.startswith("公司长期专注于"):
        score += 3
    if current.startswith("发行人是一家") or current.startswith("发行人主要从事") or current.startswith("发行人主营业务为"):
        score += 2
    if "领先" in current or "提供商" in current:
        score += 2
    if 30 <= len(current) <= 180:
        score += 1
    if re.search(r"20\d{2}\s*年", current):
        score -= 2
    for marker in BUSINESS_NOISE_MARKERS:
        if marker in current:
            score -= 3 if marker in ("产能", "产量", "销量", "产销率") else 5
    return score, -len(current)


def _pick_best_business_sentence(text: str) -> str:
    candidates: list[str] = []
    for pattern in BUSINESS_SENTENCE_PATTERNS:
        for match in pattern.finditer(text):
            raw_candidate = match.group(1).strip()
            if "行业内其他主要企业情况如下" in raw_candidate:
                continue
            candidate = _clean_business_desc(raw_candidate)
            if candidate and _is_plausible_business_desc(candidate):
                candidates.append(candidate)
    if not candidates:
        return ""
    return max(candidates, key=_score_business_desc)


def _extract_business_desc_from_text(text: str) -> str:
    normalized_text = _normalize_text(text)
    if not normalized_text:
        return ""

    section_text, _ = _extract_compact_section(
        normalized_text,
        BUSINESS_PRIMARY_PATTERNS,
        BUSINESS_SECTION_STOP_PATTERNS,
        chapter_anchors=PROSPECTUS_BUSINESS_CHAPTER_PATTERNS,
        fallback_radius=1800,
    )
    if section_text:
        candidate = _pick_best_business_sentence(section_text)
        if candidate:
            return candidate

    fallback_section_text, _ = _extract_compact_section(
        normalized_text,
        BUSINESS_FALLBACK_PATTERNS,
        BUSINESS_SECTION_STOP_PATTERNS,
        chapter_anchors=PROSPECTUS_BUSINESS_CHAPTER_PATTERNS,
        fallback_radius=1800,
    )
    if fallback_section_text and fallback_section_text != section_text:
        candidate = _pick_best_business_sentence(fallback_section_text)
        if candidate:
            return candidate

    candidate = _pick_best_business_sentence(normalized_text)
    if candidate:
        return candidate

    preferred_section = fallback_section_text or section_text
    if preferred_section:
        fallback_section = _clean_business_desc(preferred_section[:240].strip())
        if fallback_section:
            return fallback_section

    return _clean_business_desc(normalized_text[:240].strip())


def extract_business_desc(pdf_path: str | Path) -> str:
    cached = _load_parse_cache(pdf_path, "business_desc")
    if cached is not _CACHE_MISSING and isinstance(cached, str):
        return cached

    text = _read_pdf_text(pdf_path)
    if not text:
        _save_parse_cache(pdf_path, "business_desc", "")
        return ""
    result = _extract_business_desc_from_text(text)
    _save_parse_cache(pdf_path, "business_desc", result)
    return result
