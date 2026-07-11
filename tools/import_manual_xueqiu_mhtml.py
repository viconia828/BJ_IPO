from __future__ import annotations

import argparse
import email
import hashlib
import html
import json
import re
from collections import Counter
from datetime import datetime, timezone
from email import policy
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = ROOT_DIR / "新建文件夹"
DEFAULT_CORPUS_DIR = ROOT_DIR / "data" / "xueqiu_corpus"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs"
DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"

KEYWORDS = ["上市估值", "上市前瞻", "首日价格分析", "首日股价"]

AUTHOR_USER_IDS = {
    "兔子兔888": "8851207271",
    "条条道路通罗马Lee": "8889879564",
    "无房户小侯": "8692639756",
    "月半928": "9833039947",
}

LOCAL_SAMPLE_ALIASES = {
    "爱伦医疗": ["爱舍伦"],
    "欧伦电气": ["欧伦电器"],
    "永励精密": ["永励", "永励精工"],
    "科莱瑞迪": ["科莱瑞迪医疗"],
}


class HtmlTextExtractor(HTMLParser):
    BREAK_TAGS = {"article", "section", "div", "p", "br", "li", "tr", "h1", "h2", "h3", "h4"}
    SKIP_TAGS = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): (value or "") for key, value in attrs}
        style = attrs_dict.get("style", "").replace(" ", "").lower()
        hidden = tag in self.SKIP_TAGS or "display:none" in style
        if self.skip_depth > 0 or hidden:
            self.skip_depth += 1
            return
        if tag in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth > 0:
            self.skip_depth -= 1
            return
        if tag in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth == 0 and data:
            self.parts.append(data)

    def text(self) -> str:
        text = "".join(self.parts)
        text = html.unescape(text).replace("\u00a0", " ")
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def _load_dataset_by_code(path: Path = DEFAULT_DATASET) -> dict[str, dict[str, Any]]:
    payload = _read_json(path)
    items = payload.get("items") or []
    return {str(item.get("SECURITY_CODE")): item for item in items if item.get("SECURITY_CODE")}


def _dataset_aliases(item: dict[str, Any]) -> list[str]:
    name = str(item.get("SECURITY_NAME_ABBR") or "").strip()
    aliases = [name]
    aliases.extend(LOCAL_SAMPLE_ALIASES.get(name, []))
    return [alias for alias in dict.fromkeys(aliases) if alias]


def _target_code_from_text(text: str, filename: str, dataset_by_code: dict[str, dict[str, Any]]) -> str:
    for code in re.findall(r"920\d{3}", f"{filename}\n{text}"):
        if code in dataset_by_code:
            return code

    haystack = f"{filename}\n{text}"
    candidates: list[tuple[int, int, str]] = []
    for code, item in dataset_by_code.items():
        for alias in _dataset_aliases(item):
            pos = haystack.find(alias)
            if pos >= 0:
                candidates.append((pos, -len(alias), code))
    if not candidates:
        return ""
    candidates.sort()
    return candidates[0][2]


def _infer_author_from_text(path: Path, text: str) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    haystack = f"{path.stem}\n{first_line}"
    for author in AUTHOR_USER_IDS:
        if author in haystack:
            return author
    if "-" in path.stem:
        return path.stem.split("-", 1)[0].strip()
    return ""


def _parse_listing_dt(item: dict[str, Any] | None) -> datetime | None:
    if not item:
        return None
    raw = str(item.get("LISTING_DATE") or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _infer_text_created_at(author: str, text: str, target_code: str, dataset_by_code: dict[str, dict[str, Any]]) -> tuple[str, str]:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    match = re.search(r"(\d{1,2})-(\d{1,2})\s+(\d{1,2}:\d{2})", first_line)
    if not match:
        return "", ""
    month = int(match.group(1))
    day = int(match.group(2))
    hour, minute = [int(part) for part in match.group(3).split(":", 1)]
    listing_dt = _parse_listing_dt(dataset_by_code.get(target_code))
    year = listing_dt.year if listing_dt else datetime.now().year
    created = datetime(year, month, day, hour, minute)
    if listing_dt and created.date() > listing_dt.date() and month > listing_dt.month:
        created = created.replace(year=year - 1)
    return created.strftime("%Y-%m-%d %H:%M:%S"), first_line


def _clean_text_article_text(text: str, title: str) -> str:
    lines: list[str] = []
    for idx, raw_line in enumerate(text.splitlines()):
        line = raw_line.strip()
        if idx == 0 and "来自" in line and re.search(r"\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}", line):
            continue
        if line == title:
            continue
        lines.append(raw_line.rstrip())
    return "\n".join(lines).strip()


def _strip_text(fragment: str) -> str:
    parser = HtmlTextExtractor()
    parser.feed(fragment or "")
    parser.close()
    return parser.text()


def _first_match(pattern: str, text: str, flags: int = re.S) -> str:
    match = re.search(pattern, text or "", flags)
    return match.group(1).strip() if match else ""


def _extract_html_part(path: Path) -> tuple[str, dict[str, str]]:
    with path.open("rb") as handle:
        msg = email.message_from_binary_file(handle, policy=policy.default)

    headers = {key.lower(): str(value) for key, value in msg.items()}
    for part in msg.walk():
        if part.get_content_type() != "text/html":
            continue
        raw = part.get_payload(decode=True)
        if raw is None:
            content = part.get_content()
            if isinstance(content, bytes):
                raw = content
            else:
                return str(content), {key.lower(): str(value) for key, value in part.items()} | headers
        charset = part.get_content_charset() or "utf-8"
        try:
            html_text = raw.decode(charset, errors="replace")
        except LookupError:
            html_text = raw.decode("utf-8", errors="replace")
        part_headers = {key.lower(): str(value) for key, value in part.items()}
        return html_text, part_headers | headers
    raise ValueError(f"no text/html part found: {path}")


def _extract_url(html_text: str, headers: dict[str, str]) -> str:
    for key in ("content-location", "location"):
        value = headers.get(key)
        if value and "xueqiu.com" in value:
            return value.strip()
    match = re.search(r"https://xueqiu\.com/\d+/\d+", html_text)
    return match.group(0) if match else ""


def _extract_article_html(html_text: str) -> str:
    match = re.search(
        r"(<article\b[^>]*class=[\"'][^\"']*article__bd[^\"']*[\"'][^>]*>.*?</article>)",
        html_text,
        re.S | re.I,
    )
    if match:
        return match.group(1)
    detail = re.search(
        r"(<div\b[^>]*class=[\"'][^\"']*article__bd__detail[^\"']*[\"'][^>]*>.*?</div>)",
        html_text,
        re.S | re.I,
    )
    return detail.group(1) if detail else html_text


def _extract_author_name(html_text: str) -> str:
    patterns = [
        r'data-screenname="([^"]+)"',
        r'<a\b[^>]*class="[^"]*\bname\b[^"]*"[^>]*>(.*?)</a>',
        r'<img\b[^>]*class="[^"]*\bavatar\b[^"]*"[^>]*alt="([^"]+)"',
    ]
    for pattern in patterns:
        value = _first_match(pattern, html_text)
        value = _strip_text(value)
        if value:
            return value
    return ""


def _extract_time(html_text: str) -> tuple[int | None, str, str]:
    created_at = _first_match(r'data-created_at="(\d{10,})"', html_text)
    created_at_ms = int(created_at) if created_at else None
    time_match = re.search(r"<time\b([^>]*)>(.*?)</time>", html_text, re.S | re.I)
    created_at_iso = ""
    created_at_text = ""
    if time_match:
        attrs = time_match.group(1)
        created_at_iso = _first_match(r'datetime="([^"]+)"', attrs, flags=0)
        created_at_text = _first_match(r'title="([^"]+)"', attrs, flags=0) or _strip_text(time_match.group(2))
    return created_at_ms, created_at_iso, created_at_text


def _extract_title(html_text: str, article_html: str, path: Path) -> str:
    for pattern in [
        r"<h1\b[^>]*class=[\"'][^\"']*article__bd__title[^\"']*[\"'][^>]*>(.*?)</h1>",
        r"<title\b[^>]*>(.*?)</title>",
    ]:
        title = _strip_text(_first_match(pattern, html_text, flags=re.S | re.I))
        if title:
            title = re.sub(r"\s+-\s+雪球\s*$", "", title).strip()
            return title
    first_line = _strip_text(article_html).splitlines()[0:1]
    return first_line[0].strip() if first_line else path.stem.split(" - ")[0].strip()


def _clean_article_text(text: str, title: str) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        if line == title:
            continue
        if line.startswith("来自") and "雪球" in line:
            continue
        if line.startswith("来源：雪球"):
            continue
        if line in {"雪球", "雪球专栏"}:
            continue
        lines.append(line)
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def _classify_article(title: str, text: str) -> tuple[str, list[str]]:
    haystack = f"{title}\n{text}"
    matched = [keyword for keyword in KEYWORDS if keyword in haystack]
    if "上市估值" in title or "上市估值" in text[:200]:
        article_type = "listing_valuation"
    elif "上市前瞻" in title or "上市前瞻" in text[:200]:
        article_type = "listing_preview"
    elif "首日价格分析" in title or "首日股价" in title or "首日股价" in text[:200]:
        article_type = "first_day_price_analysis"
    elif "申购" in title and ("策略" in title or "分析" in title):
        article_type = "subscription_strategy"
    else:
        article_type = "other"
    return article_type, matched


def _extract_issue_price(text: str) -> float | None:
    match = re.search(r"发行价(?:为|：|:)?\s*(\d+(?:\.\d+)?)\s*元", text)
    return float(match.group(1)) if match else None


def _extract_price_range(text: str) -> dict[str, Any] | None:
    compact = re.sub(r"\s+", "", text or "")
    patterns = [
        r"(对应价格区间|对应上市首日价格|上市首日价格|首日价格区间|首日股价|对应股价)[：:为约大概]*"
        r"(?P<low>\d+(?:\.\d+)?)元?[，,、~\-至到]+(?P<high>\d+(?:\.\d+)?)元",
        r"(对应价格区间|对应股价)[：:为约大概]*(?P<low>\d+(?:\.\d+)?)元?(?:.*?)(?P<high>\d+(?:\.\d+)?)元",
    ]
    for pattern in patterns:
        match = re.search(pattern, compact)
        if not match:
            continue
        low = float(match.group("low"))
        high = float(match.group("high"))
        if low > high:
            low, high = high, low
        return {
            "low": low,
            "high": high,
            "mid": (low + high) / 2,
            "text": match.group(0),
        }
    return None


def _extract_target_pe(text: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for match in re.finditer(r"(?P<low>\d+(?:\.\d+)?)\s*[-~至到]\s*(?P<high>\d+(?:\.\d+)?)\s*(?:倍|X|x)", text):
        low = float(match.group("low"))
        high = float(match.group("high"))
        if 5 <= low <= 120 and 5 <= high <= 120:
            results.append({"low": min(low, high), "high": max(low, high), "text": match.group(0)})
    for match in re.finditer(r"(?P<pe>\d+(?:\.\d+)?)\s*(?:倍|X|x)(?:PE|市盈率|估值)?", text, re.I):
        pe = float(match.group("pe"))
        if 5 <= pe <= 120:
            results.append({"value": pe, "text": match.group(0)})
    return results[:8]


def _extract_stock_mentions(text: str) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    mentions: list[dict[str, str]] = []
    for name, code in re.findall(r"\$([^$(]+)\((?:BJ|NQ)?(920\d{3}|\d{6})\)\$", text):
        key = (name.strip(), code.strip())
        if key not in seen:
            seen.add(key)
            mentions.append({"name": key[0], "code": key[1]})
    return mentions


def _build_article(path: Path, corpus_dir: Path) -> dict[str, Any]:
    html_text, headers = _extract_html_part(path)
    article_html = _extract_article_html(html_text)
    url = _extract_url(html_text, headers)
    url_match = re.search(r"xueqiu\.com/(\d+)/(\d+)", url)
    if not url_match:
        raise ValueError(f"cannot resolve xueqiu user/status id: {path}")
    user_id, status_id = url_match.group(1), url_match.group(2)

    title = _extract_title(html_text, article_html, path)
    page_title = _strip_text(_first_match(r"<title\b[^>]*>(.*?)</title>", html_text, flags=re.S | re.I))
    text = _clean_article_text(_strip_text(article_html), title)
    author_name = _extract_author_name(html_text) or "兔子兔888"
    created_at_ms, created_at_iso, created_at_text = _extract_time(html_text)
    article_type, matched_keywords = _classify_article(title, text)
    issue_price = _extract_issue_price(text)
    price_range = _extract_price_range(text)

    article_file = corpus_dir / "articles" / f"{user_id}_{status_id}.json"
    collected_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {
        "user_id": user_id,
        "status_id": status_id,
        "author_name": author_name,
        "created_at_ms": created_at_ms,
        "created_at_iso": created_at_iso,
        "created_at_text": created_at_text,
        "title": title,
        "page_title": page_title,
        "url": url,
        "article_type": article_type,
        "matched_keywords": matched_keywords,
        "stock_mentions": _extract_stock_mentions(text),
        "text": text,
        "html_text_length": len(html_text),
        "manual_import": True,
        "manual_source_file": _relative(path),
        "collected_at": collected_at,
        "extracted": {
            "issue_price": issue_price,
            "target_pe": _extract_target_pe(text),
            "float_market_cap": None,
            "total_market_cap": None,
            "market_cap_range_text": re.findall(r"估值市值区间[^\n。；;]{0,120}", text)[:5],
            "price_range": price_range,
            "comparable_companies": [],
            "listing_date_hints": re.findall(r"\d{1,2}\.\d{1,2}(?:周[一二三四五六日天])?(?:上市|申购)?", text)[:5],
            "first_day_view": [line for line in text.splitlines() if "首日" in line][:8],
            "risk_phrases": [line for line in text.splitlines() if "风险" in line][:8],
            "author_rule_phrases": [],
        },
        "quality": {
            "readable": True,
            "blocked_by_verification": False,
            "text_length": len(text),
            "has_issue_price": issue_price is not None,
            "has_valuation": "估值" in text or bool(price_range),
            "has_first_day": "首日" in text or "上市" in text,
            "suspected_truncated": len(text) < 500,
        },
        "file": _relative(article_file),
    }


def _build_text_article(path: Path, corpus_dir: Path, dataset_by_code: dict[str, dict[str, Any]]) -> dict[str, Any]:
    raw_text = path.read_text(encoding="utf-8-sig", errors="replace")
    author_name = _infer_author_from_text(path, raw_text)
    user_id = AUTHOR_USER_IDS.get(author_name, f"manual_{hashlib.md5(author_name.encode('utf-8')).hexdigest()[:8]}")
    status_id = f"manualtxt_{hashlib.md5((path.name + raw_text).encode('utf-8')).hexdigest()[:16]}"
    target_code = _target_code_from_text(raw_text, path.stem, dataset_by_code)
    target_item = dataset_by_code.get(target_code) if target_code else None
    created_at_text, header_text = _infer_text_created_at(author_name, raw_text, target_code, dataset_by_code)

    title_part = path.stem.split("-", 1)[1].strip() if "-" in path.stem else path.stem.strip()
    target_name = str((target_item or {}).get("SECURITY_NAME_ABBR") or title_part).strip()
    title = title_part if title_part else f"{target_name} 手工文本预测"
    text = _clean_text_article_text(raw_text, title)
    article_type, matched_keywords = _classify_article(title, text)
    issue_price = _extract_issue_price(text)
    price_range = _extract_price_range(text)
    stock_mentions = _extract_stock_mentions(text)
    if target_code and not any(item.get("code") == target_code for item in stock_mentions):
        stock_mentions.insert(0, {"name": target_name, "code": target_code})

    article_file = corpus_dir / "articles" / f"{user_id}_{status_id}.json"
    manual_url = "manual://" + _relative(path).replace("\\", "/")
    collected_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {
        "user_id": user_id,
        "status_id": status_id,
        "author_name": author_name,
        "created_at_ms": None,
        "created_at_iso": created_at_text,
        "created_at_text": created_at_text or header_text,
        "title": title,
        "page_title": path.name,
        "url": manual_url,
        "article_type": article_type,
        "matched_keywords": matched_keywords,
        "stock_mentions": stock_mentions,
        "text": text,
        "html_text_length": 0,
        "manual_import": True,
        "manual_text_import": True,
        "manual_source_file": _relative(path),
        "collected_at": collected_at,
        "extracted": {
            "issue_price": issue_price,
            "target_pe": _extract_target_pe(text),
            "float_market_cap": None,
            "total_market_cap": None,
            "market_cap_range_text": re.findall(r"估值市值区间[^\n。；;]{0,120}", text)[:5],
            "price_range": price_range,
            "comparable_companies": [],
            "listing_date_hints": re.findall(r"\d{1,2}\.\d{1,2}(?:周[一二三四五六日天])?(?:上市|申购)?", text)[:5],
            "first_day_view": [line for line in text.splitlines() if "首日" in line][:8],
            "risk_phrases": [line for line in text.splitlines() if "风险" in line][:8],
            "author_rule_phrases": [],
        },
        "quality": {
            "readable": True,
            "blocked_by_verification": False,
            "text_length": len(text),
            "has_issue_price": issue_price is not None,
            "has_valuation": "估值" in text or bool(price_range),
            "has_first_day": "首日" in text or "上市" in text,
            "suspected_truncated": len(text) < 500,
        },
        "file": _relative(article_file),
    }


def _article_summary(article: dict[str, Any]) -> dict[str, Any]:
    quality = article.get("quality") if isinstance(article.get("quality"), dict) else {}
    return {
        "user_id": article.get("user_id"),
        "author_name": article.get("author_name"),
        "status_id": article.get("status_id"),
        "title": article.get("title"),
        "url": article.get("url"),
        "created_at_text": article.get("created_at_text"),
        "article_type": article.get("article_type"),
        "matched_keywords": article.get("matched_keywords") or [],
        "text_length": quality.get("text_length", len(str(article.get("text") or ""))),
        "readable": bool(quality.get("readable")),
        "file": article.get("file"),
    }


def _rebuild_stats(index: dict[str, Any]) -> dict[str, Any]:
    articles = index.get("articles") or []
    by_type = Counter(str(article.get("article_type") or "other") for article in articles)
    by_author = Counter(str(article.get("author_name") or article.get("user_id") or "") for article in articles)
    article_payloads: list[dict[str, Any]] = []
    for summary in articles:
        path_value = summary.get("file")
        path = ROOT_DIR / str(path_value) if path_value else None
        if path and path.exists():
            try:
                article_payloads.append(_read_json(path))
            except json.JSONDecodeError:
                article_payloads.append(summary)
        else:
            article_payloads.append(summary)

    def has_extracted(article: dict[str, Any], key: str) -> bool:
        extracted = article.get("extracted") if isinstance(article.get("extracted"), dict) else {}
        value = extracted.get(key)
        return bool(value)

    def blocked(article: dict[str, Any]) -> bool:
        quality = article.get("quality") if isinstance(article.get("quality"), dict) else {}
        return bool(quality.get("blocked_by_verification"))

    return {
        "author_count": len({article.get("user_id") for article in articles if article.get("user_id")}),
        "article_count": len(articles),
        "readable_count": sum(1 for article in articles if article.get("readable")),
        "blocked_by_verification_count": sum(1 for article in article_payloads if blocked(article)),
        "with_issue_price_count": sum(
            1
            for article in article_payloads
            if has_extracted(article, "issue_price")
            or bool((article.get("quality") if isinstance(article.get("quality"), dict) else {}).get("has_issue_price"))
        ),
        "with_price_range_count": sum(1 for article in article_payloads if has_extracted(article, "price_range")),
        "with_target_pe_count": sum(1 for article in article_payloads if has_extracted(article, "target_pe")),
        "by_type": dict(by_type),
        "by_author": dict(by_author),
    }


def _load_index(corpus_dir: Path) -> dict[str, Any]:
    index_path = corpus_dir / "index.json"
    index = _read_json(index_path)
    if not index:
        index = {
            "generated_at": "",
            "started_at": "",
            "options": {},
            "stats": {},
            "authors": [],
            "articles": [],
        }
    index.setdefault("articles", [])
    return index


def import_mhtml(input_dir: Path, corpus_dir: Path, output_dir: Path) -> Path:
    files = sorted(input_dir.glob("*.mhtml")) + sorted(input_dir.glob("*.txt"))
    if not files:
        raise FileNotFoundError(f"no .mhtml or .txt files found in {input_dir}")

    dataset_by_code = _load_dataset_by_code()
    index = _load_index(corpus_dir)
    articles_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for summary in index.get("articles") or []:
        key = (str(summary.get("user_id") or ""), str(summary.get("status_id") or ""))
        if not all(key):
            continue
        if key not in articles_by_key:
            order.append(key)
        articles_by_key[key] = dict(summary)

    imported: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path in files:
        try:
            if path.suffix.lower() == ".txt":
                article = _build_text_article(path, corpus_dir, dataset_by_code)
            else:
                article = _build_article(path, corpus_dir)
            article_path = corpus_dir / "articles" / f"{article['user_id']}_{article['status_id']}.json"
            _write_json(article_path, article)
            key = (str(article["user_id"]), str(article["status_id"]))
            if key not in articles_by_key:
                order.append(key)
            articles_by_key[key] = _article_summary(article)
            imported.append(
                {
                    "source_file": _relative(path),
                    "source_type": path.suffix.lower().lstrip("."),
                    "article_file": _relative(article_path),
                    "user_id": article["user_id"],
                    "status_id": article["status_id"],
                    "author_name": article["author_name"],
                    "created_at_text": article["created_at_text"],
                    "title": article["title"],
                    "text_length": article["quality"]["text_length"],
                    "price_range": article["extracted"]["price_range"],
                }
            )
        except Exception as exc:  # noqa: BLE001 - import report should keep all per-file failures.
            errors.append({"source_file": _relative(path), "error": str(exc)})

    index["articles"] = [articles_by_key[key] for key in order]
    index["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    index["manual_imported_at"] = index["generated_at"]
    index["manual_source_dir"] = _relative(input_dir)
    index["stats"] = _rebuild_stats(index)
    _write_json(corpus_dir / "index.json", index)

    jsonl_path = corpus_dir / "articles.jsonl"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as handle:
        for summary in index["articles"]:
            path_value = summary.get("file")
            article_path = ROOT_DIR / str(path_value) if path_value else None
            if article_path and article_path.exists():
                article = _read_json(article_path)
            else:
                article = summary
            handle.write(json.dumps(article, ensure_ascii=False) + "\n")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_dir": _relative(input_dir),
        "corpus_dir": _relative(corpus_dir),
        "mhtml_count": sum(1 for path in files if path.suffix.lower() == ".mhtml"),
        "text_count": sum(1 for path in files if path.suffix.lower() == ".txt"),
        "file_count": len(files),
        "imported_count": len(imported),
        "error_count": len(errors),
        "imported": imported,
        "errors": errors,
        "index_stats": index["stats"],
    }
    output_path = output_dir / f"xueqiu_manual_article_import_{timestamp}.json"
    _write_json(output_path, report)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Import manually saved Xueqiu MHTML/TXT pages into the local corpus cache.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    report_path = import_mhtml(args.input_dir, args.corpus_dir, args.output_dir)
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
