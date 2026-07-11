const fs = require("fs");
const path = require("path");
const { chromium } = require(
  "C:\\Users\\Ai\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\node\\node_modules\\.pnpm\\playwright-core@1.61.1\\node_modules\\playwright-core"
);

const ROOT_DIR = path.resolve(__dirname, "..");

const DEFAULT_USERS = [
  "8889879564",
  "8692639756",
  "9833039947",
  "8851207271",
];

const DEFAULT_KEYWORDS = ["上市估值", "上市前瞻", "首日价格分析", "首日股价"];

const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36";

function parseArgs(argv) {
  const args = {
    users: DEFAULT_USERS,
    keywords: DEFAULT_KEYWORDS,
    maxPages: 20,
    maxArticlesPerAuthor: 200,
    stopEmptyPages: 3,
    delayMs: 1500,
    outDir: path.join(ROOT_DIR, "data", "xueqiu_corpus"),
    sinceDate: "",
    sinceMs: null,
    noKeywordFilter: false,
    refresh: false,
    refreshUnreadable: false,
    headful: false,
  };

  for (let i = 2; i < argv.length; i += 1) {
    const key = argv[i];
    const value = argv[i + 1];
    if (key === "--users" && value) {
      args.users = value.split(",").map((x) => x.trim()).filter(Boolean);
      i += 1;
    } else if (key === "--keywords" && value) {
      args.keywords = value.split(",").map((x) => x.trim()).filter(Boolean);
      i += 1;
    } else if (key === "--max-pages" && value) {
      args.maxPages = Number(value);
      i += 1;
    } else if (key === "--max-articles-per-author" && value) {
      args.maxArticlesPerAuthor = Number(value);
      i += 1;
    } else if (key === "--stop-empty-pages" && value) {
      args.stopEmptyPages = Number(value);
      i += 1;
    } else if (key === "--delay-ms" && value) {
      args.delayMs = Number(value);
      i += 1;
    } else if (key === "--out-dir" && value) {
      args.outDir = path.resolve(value);
      i += 1;
    } else if (key === "--since-date" && value) {
      args.sinceDate = value;
      args.sinceMs = Date.parse(`${value}T00:00:00+08:00`);
      if (Number.isNaN(args.sinceMs)) throw new Error(`Invalid --since-date: ${value}`);
      i += 1;
    } else if (key === "--no-keyword-filter") {
      args.noKeywordFilter = true;
    } else if (key === "--refresh") {
      args.refresh = true;
    } else if (key === "--refresh-unreadable") {
      args.refreshUnreadable = true;
    } else if (key === "--headful") {
      args.headful = true;
    } else if (key === "--help" || key === "-h") {
      args.help = true;
    }
  }
  return args;
}

function usage() {
  return [
    "Usage: node tools/xueqiu_corpus_collect.cjs [options]",
    "",
    "Options:",
    "  --users <ids>                   Comma-separated Xueqiu user IDs.",
    "  --keywords <words>              Comma-separated match keywords.",
    "  --max-pages <n>                 Max timeline pages per author. Default 20.",
    "  --max-articles-per-author <n>   Max detail pages per author. Default 200.",
    "  --stop-empty-pages <n>          Stop after consecutive no-hit pages. Default 3.",
    "  --delay-ms <n>                  Delay between page/detail requests. Default 1500.",
    "  --out-dir <path>                Corpus output dir. Default data/xueqiu_corpus.",
    "  --since-date <YYYY-MM-DD>       Keep timeline articles on or after this date.",
    "  --no-keyword-filter             Collect all timeline articles in date range.",
    "  --refresh                       Re-fetch article details even if JSON exists.",
    "  --refresh-unreadable            Re-fetch cached unreadable or blocked articles.",
    "  --headful                       Launch visible Chrome.",
  ].join("\n");
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function normalizeWhitespace(text) {
  return String(text || "")
    .replace(/\r/g, "\n")
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function stripHtml(value) {
  return normalizeWhitespace(
    String(value || "")
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<\/p>/gi, "\n")
      .replace(/<[^>]+>/g, "")
      .replace(/&nbsp;/g, " ")
      .replace(/&amp;/g, "&")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
  );
}

function unique(items) {
  return Array.from(new Set(items.filter((x) => x !== undefined && x !== null && String(x).trim() !== "")));
}

function matchedKeywords(text, keywords) {
  return keywords.filter((kw) => text.includes(kw));
}

function timelineCreatedAtMs(item) {
  return Number(item?.created_at || item?.createdAt || 0) || null;
}

function articleCreatedAtMs(article) {
  return Number(article?.created_at_ms || 0) || null;
}

function compactTitle(text) {
  return normalizeWhitespace(text).replace(/\s+/g, " ").slice(0, 200);
}

function articleType(title, text) {
  const haystack = `${title} ${text}`;
  if (haystack.includes("首日价格分析")) return "first_day_price_analysis";
  if (haystack.includes("上市前瞻")) return "listing_preview";
  if (haystack.includes("上市估值") || haystack.includes("估值分析")) return "listing_valuation";
  if (haystack.includes("申购策略")) return "subscription_strategy";
  return "other";
}

function linesOf(text) {
  return normalizeWhitespace(text)
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function extractMainText(rawText, title, authorName) {
  const lines = linesOf(rawText);
  if (!lines.length) return "";

  let start = -1;
  const titleNeedle = compactTitle(title);
  for (let i = 0; i < lines.length; i += 1) {
    if (compactTitle(lines[i]) === titleNeedle || compactTitle(lines[i]).includes(titleNeedle.slice(0, 30))) {
      start = i + 1;
      break;
    }
  }

  for (let i = 0; i < lines.length; i += 1) {
    if (/来自.+的雪球专栏/.test(lines[i])) {
      start = i + 1;
      break;
    }
  }

  if (start < 0) start = 0;

  const endPatterns = [
    /^风险提示：用户发表/,
    /^风险提示:用户发表/,
    /^♦/,
    /^我是.+/,
    new RegExp(`^${escapeRegExp(authorName || "")}的专栏`),
    /^全部讨论/,
    /^最热最新最早$/,
    /^投诉$/,
    /^回复@/,
  ].filter((pattern) => String(pattern).length > 4);

  let end = lines.length;
  for (let i = start; i < lines.length; i += 1) {
    if (endPatterns.some((pattern) => pattern.test(lines[i]))) {
      end = i;
      break;
    }
  }

  let mainText = lines.slice(start, end).join("\n");
  const footerIndex = mainText.search(/\n?[♦◆]+/);
  if (footerIndex >= 0) mainText = mainText.slice(0, footerIndex);
  return normalizeWhitespace(mainText);
}

function escapeRegExp(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function parseFirstNumber(pattern, text) {
  const match = text.match(pattern);
  return match ? Number(match[1]) : null;
}

function extractLineAfter(labelPattern, text) {
  const lines = linesOf(text);
  const out = [];
  for (const line of lines) {
    if (labelPattern.test(line)) out.push(line.slice(0, 240));
  }
  return unique(out).slice(0, 8);
}

function extractStockMentions(title, text) {
  const mentions = [];
  const stockPattern = /\$?([\u4e00-\u9fa5A-Za-z0-9]{1,30})[（(]((?:BJ|SZ|SH|NQ)?\d{3,6}(?:\.[A-Z]{2})?)[）)]\$?/g;
  let match;
  while ((match = stockPattern.exec(text)) !== null) {
    mentions.push({ name: match[1], code: match[2] });
  }

  const codePattern = /\b(920\d{3})\b/g;
  while ((match = codePattern.exec(`${title}\n${text}`)) !== null) {
    mentions.push({ name: "", code: match[1] });
  }

  const titleName = title
    .replace(/北交新股/g, "")
    .match(/^([\u4e00-\u9fa5A-Za-z0-9]+?)(?:\d+月\d+日)?(?:上市估值|上市首日价格分析|上市前瞻|上市估值分析|上市)/);
  if (titleName) mentions.push({ name: titleName[1], code: "" });

  const seen = new Set();
  return mentions.filter((item) => {
    const key = `${item.name}|${item.code}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function extractComparableCompanies(text) {
  const lines = extractLineAfter(/可比上市公司|可比公司/, text);
  const names = [];
  for (const line of lines) {
    const cleaned = line.replace(/可比上市公司[^：:]*[：:]/, "").replace(/可比公司[^：:]*[：:]/, "");
    for (const part of cleaned.split(/[、,，；;]/)) {
      const name = part.replace(/[（(].*?[）)]/g, "").replace(/\s+/g, "").trim();
      if (name && !/PETTM|PE|亿元|倍/.test(name)) names.push(name);
    }
  }
  return unique(names).slice(0, 20);
}

function extractListingDateHints(text) {
  const hints = [];
  const patterns = [
    /(?:将于|于)?(\d{1,2}月\d{1,2}日)(?:本周[一二三四五六日天])?(?:上市|申购)/g,
    /(\d{4}[-/]\d{1,2}[-/]\d{1,2})(?:上市|申购)?/g,
  ];
  for (const pattern of patterns) {
    let match;
    while ((match = pattern.exec(text)) !== null) hints.push(match[1]);
  }
  return unique(hints).slice(0, 12);
}

function extractMoneyRange(label, text) {
  const pattern = new RegExp(`${label}[^\\n。；;]*`, "g");
  const matches = text.match(pattern) || [];
  return matches.map((x) => x.slice(0, 240));
}

function extractViewPhrases(text) {
  const sentences = normalizeWhitespace(text)
    .replace(/([。！？；;])/g, "$1\n")
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);
  const patterns = /首日|竞价|预期|股价|涨幅|高开|破发|超顶|修复|情绪|看点|低价|小盘|流通盘|不受市场待见|表现疲软/;
  return unique(sentences.filter((s) => patterns.test(s)).map((s) => s.slice(0, 220))).slice(0, 20);
}

function extractRiskPhrases(text) {
  const sentences = normalizeWhitespace(text)
    .replace(/([。！？；;])/g, "$1\n")
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);
  const patterns = /风险|下滑|集中|依赖|周期|汇率|竞争|原材料|毛利率|高价|高估|业绩|出口|不确定/;
  return unique(sentences.filter((s) => patterns.test(s)).map((s) => s.slice(0, 220))).slice(0, 24);
}

function extractArticleFields(title, text) {
  const issuePrice =
    parseFirstNumber(/发行价(?:格)?[：:\s]*([0-9]+(?:\.[0-9]+)?)/, text) ??
    parseFirstNumber(/公司发行价\s*([0-9]+(?:\.[0-9]+)?)元/, text) ??
    parseFirstNumber(/首日价格为\s*([0-9]+(?:\.[0-9]+)?)\s*\*/, text);
  const targetPe =
    parseFirstNumber(/(?:暂给予公司|给予公司|给予)\s*([0-9]+(?:\.[0-9]+)?)\s*倍估值/, text) ??
    parseFirstNumber(/([0-9]+(?:\.[0-9]+)?)\s*倍估值/, text) ??
    parseFirstNumber(/我认为\s*([0-9]+(?:\.[0-9]+)?)\s*X\s*相对合理/i, text) ??
    parseFirstNumber(/([0-9]+(?:\.[0-9]+)?)\s*X\s*相对合理/i, text);
  const floatMarketCap =
    parseFirstNumber(/流通市值[：:\s]*([0-9]+(?:\.[0-9]+)?)\s*亿元/, text) ??
    parseFirstNumber(/初始流通市值为\s*([0-9]+(?:\.[0-9]+)?)\s*亿元/, text);
  const totalMarketCap = parseFirstNumber(/总市值[：:\s]*([0-9]+(?:\.[0-9]+)?)\s*亿元/, text);

  const priceRangeMatch =
    text.match(/对应价格区间[：:\s]*([0-9]+(?:\.[0-9]+)?)元?[，,、~\-至\s]+([0-9]+(?:\.[0-9]+)?)元/) ||
    text.match(/(?:首日)?预判股价[：:\s]*([0-9]+(?:\.[0-9]+)?)元?[，,、~\-至\s]+([0-9]+(?:\.[0-9]+)?)元/) ||
    text.match(/首日价格区间为\s*([0-9]+(?:\.[0-9]+)?)元?[，,、~\-至\s]+([0-9]+(?:\.[0-9]+)?)元/);
  const marketCapRangeLine = extractMoneyRange("市值区间", text).slice(0, 3);

  return {
    issue_price: issuePrice,
    target_pe: targetPe,
    float_market_cap: floatMarketCap,
    total_market_cap: totalMarketCap,
    market_cap_range_text: marketCapRangeLine,
    price_range: priceRangeMatch
      ? { low: Number(priceRangeMatch[1]), high: Number(priceRangeMatch[2]), text: priceRangeMatch[0].slice(0, 160) }
      : null,
    comparable_companies: extractComparableCompanies(text),
    listing_date_hints: extractListingDateHints(`${title}\n${text}`),
    first_day_view: extractViewPhrases(text),
    risk_phrases: extractRiskPhrases(text),
    author_rule_phrases: extractViewPhrases(text).filter((s) => /给予|估值|预期|情绪|看点|小盘|低价|高价|行业/.test(s)),
  };
}

function qualityFlags(title, text, rawText) {
  const blockedByVerification = /访问验证|请按住滑块|TraceID|aliyun_waf|验证码|被禁止访问互联网|ERR_NETWORK_ACCESS_DENIED|安全威胁|访问被阻断|请求ID|当前网址/.test(rawText);
  const readable = text.length >= 120 && !blockedByVerification;
  return {
    readable,
    blocked_by_verification: blockedByVerification,
    text_length: text.length,
    has_issue_price: /发行价|发行价格/.test(text),
    has_valuation: /估值|市值区间|价格区间|可比/.test(text),
    has_first_day: /首日|上市首日|竞价|股价/.test(`${title}\n${text}`),
    suspected_truncated: /\.\.\.$/.test(text) || /全文|展开/.test(rawText.slice(0, 3000)),
  };
}

function existingArticlePath(outDir, userId, statusId) {
  return path.join(outDir, "articles", `${userId}_${statusId}.json`);
}

function readExistingArticle(outDir, userId, statusId) {
  const filePath = existingArticlePath(outDir, userId, statusId);
  if (!fs.existsSync(filePath)) return null;
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function isVerificationBlockedArticle(article) {
  return !!(
    article?.quality?.blocked_by_verification ||
    /访问验证|请按住滑块|TraceID|aliyun_waf|验证码|ERR_NETWORK_ACCESS_DENIED|安全威胁|访问被阻断|请求ID|当前网址/.test(article?.text || "")
  );
}

function writeArticle(outDir, article) {
  const filePath = existingArticlePath(outDir, article.user_id, article.status_id);
  fs.writeFileSync(filePath, `${JSON.stringify(article, null, 2)}\n`, "utf8");
  return filePath;
}

async function wait(delayMs) {
  if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs));
}

async function readHome(page, userId) {
  await page.goto(`https://xueqiu.com/u/${userId}`, { waitUntil: "domcontentloaded", timeout: 25000 }).catch(() => {});
  await page.waitForTimeout(4000);
  return page.evaluate(() => {
    const text = document.body?.innerText || document.body?.textContent || "";
    const profileName = (text.match(/\n([^\n]+)\n\d+ 关注/) || [])[1] || "";
    return {
      url: location.href,
      title: document.title,
      profile_name: profileName,
      login_visible: text.includes("登录"),
      text_preview: text.slice(0, 1000),
    };
  });
}

async function fetchTimelinePage(page, userId, pageNo) {
  const api = `https://xueqiu.com/statuses/original/timeline.json?user_id=${userId}&page=${pageNo}`;
  return page.evaluate(async (apiUrl) => {
    const response = await fetch(apiUrl, {
      credentials: "include",
      headers: {
        accept: "application/json, text/plain, */*",
        "x-requested-with": "XMLHttpRequest",
      },
    });
    const contentType = response.headers.get("content-type") || "";
    const body = contentType.includes("json") ? await response.json() : await response.text();
    return { ok: response.ok, status: response.status, contentType, body };
  }, api);
}

function normalizeTimelineItems(payload) {
  if (!payload || typeof payload.body === "string") return [];
  if (Array.isArray(payload.body?.list)) return payload.body.list;
  if (Array.isArray(payload.body?.statuses)) return payload.body.statuses;
  if (Array.isArray(payload.body)) return payload.body;
  return [];
}

function candidateFromTimelineItem(userId, item, keywords, options) {
  const id = item.id || item.status_id;
  if (!id) return null;
  const title = stripHtml(item.title || "");
  const text = stripHtml(`${item.title || ""} ${item.description || ""} ${item.text || ""}`);
  const hits = matchedKeywords(text, keywords);
  if (!options.noKeywordFilter && !hits.length) return null;
  const createdAtMs = timelineCreatedAtMs(item);
  return {
    user_id: String(userId),
    status_id: String(id),
    url: `https://xueqiu.com/${userId}/${id}`,
    title: title || compactTitle(text).slice(0, 80),
    timeline_text: text.slice(0, 800),
    created_at_ms: createdAtMs,
    created_at_text: item.timeBefore || "",
    matched_keywords: hits,
  };
}

async function collectCandidates(page, userId, keywords, options) {
  const pages = [];
  const candidates = new Map();
  let emptyHitPages = 0;

  for (let pageNo = 1; pageNo <= options.maxPages; pageNo += 1) {
    const payload = await fetchTimelinePage(page, userId, pageNo).catch((error) => ({ ok: false, error: String(error) }));
    const items = normalizeTimelineItems(payload);
    let hitCount = 0;

    let beforeSinceCount = 0;
    for (const item of items) {
      const createdAtMs = timelineCreatedAtMs(item);
      if (options.sinceMs && createdAtMs && createdAtMs < options.sinceMs) {
        beforeSinceCount += 1;
        continue;
      }
      const candidate = candidateFromTimelineItem(userId, item, keywords, options);
      if (!candidate) continue;
      hitCount += 1;
      if (!candidates.has(candidate.status_id)) candidates.set(candidate.status_id, candidate);
    }

    pages.push({
      page: pageNo,
      ok: !!payload.ok,
      status: payload.status,
      content_type: payload.contentType,
      count: items.length,
      hit_count: hitCount,
      before_since_count: beforeSinceCount,
      error: payload.error,
    });

    emptyHitPages = hitCount ? 0 : emptyHitPages + 1;
    if (options.sinceMs && items.length && beforeSinceCount === items.length) break;
    if (!items.length || emptyHitPages >= options.stopEmptyPages) break;
    await wait(options.delayMs);
  }

  return {
    pages,
    candidates: Array.from(candidates.values()).slice(0, options.maxArticlesPerAuthor),
  };
}

async function readDetail(page, candidate, authorName, keywords) {
  await page.goto(candidate.url, { waitUntil: "domcontentloaded", timeout: 25000 }).catch(() => {});
  await page.waitForTimeout(4000);
  const detail = await page.evaluate(() => ({
    url: location.href,
    title: document.title,
    raw_text: document.body?.innerText || document.body?.textContent || "",
  }));
  const rawText = normalizeWhitespace(detail.raw_text);
  const title = candidate.title || compactTitle(detail.title.replace(/\s+-\s+雪球$/, ""));
  const text = extractMainText(rawText, title, authorName);
  const extracted = extractArticleFields(title, text);
  const quality = qualityFlags(title, text, rawText);
  const createdAtText = (rawText.match(/发布于([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2})/) || [])[1] || candidate.created_at_text || "";

  return {
    source: "xueqiu",
    user_id: candidate.user_id,
    author_name: authorName || "",
    status_id: candidate.status_id,
    url: candidate.url,
    canonical_url: detail.url,
    title,
    page_title: detail.title,
    created_at_ms: candidate.created_at_ms,
    created_at_iso: candidate.created_at_ms ? new Date(candidate.created_at_ms).toISOString() : null,
    created_at_text: createdAtText,
    collected_at: new Date().toISOString(),
    matched_keywords: unique([...candidate.matched_keywords, ...matchedKeywords(`${title}\n${text}`, keywords)]),
    article_type: articleType(title, text),
    text,
    text_length: text.length,
    stock_mentions: extractStockMentions(title, text),
    extracted,
    quality,
  };
}

function writeAggregate(outDir, articles, index) {
  const articlesDir = path.join(outDir, "articles");
  ensureDir(articlesDir);
  const jsonlPath = path.join(outDir, "articles.jsonl");
  fs.writeFileSync(jsonlPath, articles.map((article) => JSON.stringify(article)).join("\n") + (articles.length ? "\n" : ""), "utf8");
  const indexPath = path.join(outDir, "index.json");
  fs.writeFileSync(indexPath, `${JSON.stringify(index, null, 2)}\n`, "utf8");
  return { jsonlPath, indexPath };
}

function mergeReadableCachedArticles(outDir, articles, options) {
  const articlesDir = path.join(outDir, "articles");
  if (!fs.existsSync(articlesDir)) return 0;
  const byKey = new Map(articles.map((article) => [`${article.user_id}_${article.status_id}`, article]));
  let merged = 0;
  for (const fileName of fs.readdirSync(articlesDir)) {
    if (!fileName.endsWith(".json")) continue;
    const filePath = path.join(articlesDir, fileName);
    let article;
    try {
      article = JSON.parse(fs.readFileSync(filePath, "utf8"));
    } catch {
      continue;
    }
    const key = `${article.user_id}_${article.status_id}`;
    if (byKey.has(key)) continue;
    if (!options.users.includes(String(article.user_id))) continue;
    if (!article.quality?.readable) continue;
    if (options.sinceMs && articleCreatedAtMs(article) && articleCreatedAtMs(article) < options.sinceMs) continue;
    if (!options.noKeywordFilter && !matchedKeywords(`${article.title || ""}\n${article.text || ""}`, options.keywords).length) continue;
    byKey.set(key, article);
    articles.push(article);
    merged += 1;
  }
  return merged;
}

function timestampForFile(date = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
    "_",
    pad(date.getHours()),
    pad(date.getMinutes()),
    pad(date.getSeconds()),
  ].join("");
}

async function main() {
  const options = parseArgs(process.argv);
  if (options.help) {
    console.log(usage());
    return;
  }

  const articlesDir = path.join(options.outDir, "articles");
  const outputsDir = path.join(ROOT_DIR, "outputs");
  ensureDir(articlesDir);
  ensureDir(outputsDir);

  const browser = await chromium.launch({
    executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    headless: !options.headful,
    args: ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
  });

  const authorReports = [];
  const articles = [];
  const startedAt = new Date();

  try {
    const context = await browser.newContext({
      userAgent: USER_AGENT,
      locale: "zh-CN",
      viewport: { width: 1365, height: 900 },
    });
    const page = await context.newPage();

    for (const userId of options.users) {
      const home = await readHome(page, userId);
      await wait(options.delayMs);
      const timeline = await collectCandidates(page, userId, options.keywords, options);
      const authorName = home.profile_name || home.title.replace(/\s+-\s+雪球$/, "");

      const detailReports = [];
      let blockedByVerification = false;
      for (const candidate of timeline.candidates) {
        const cached = options.refresh ? null : readExistingArticle(options.outDir, userId, candidate.status_id);
        const shouldRefreshCached = cached && options.refreshUnreadable && (!cached.quality?.readable || isVerificationBlockedArticle(cached));
        let article;
        if (cached && !shouldRefreshCached) {
          article = cached;
          detailReports.push({
            status_id: candidate.status_id,
            title: cached.title,
            url: cached.url,
            cached: true,
            readable: cached.quality?.readable,
            blocked_by_verification: cached.quality?.blocked_by_verification,
            text_length: cached.text_length,
          });
        } else {
          article = await readDetail(page, candidate, authorName, options.keywords);
          const filePath = writeArticle(options.outDir, article);
          detailReports.push({
            status_id: article.status_id,
            title: article.title,
            url: article.url,
            file: path.relative(ROOT_DIR, filePath),
            cached: false,
            readable: article.quality.readable,
            blocked_by_verification: article.quality.blocked_by_verification,
            text_length: article.text_length,
          });
          await wait(options.delayMs);
        }
        articles.push(article);
        if (isVerificationBlockedArticle(article)) {
          blockedByVerification = true;
          break;
        }
      }

      authorReports.push({
        user_id: userId,
        author_name: authorName,
        home,
        timeline_pages: timeline.pages,
        candidate_count: timeline.candidates.length,
        collected_count: detailReports.length,
        blocked_by_verification: blockedByVerification,
        details: detailReports,
      });
    }
  } finally {
    await browser.close();
  }

  const mergedReadableCacheCount = mergeReadableCachedArticles(options.outDir, articles, options);

  articles.sort((a, b) => {
    const userCmp = String(a.user_id).localeCompare(String(b.user_id));
    if (userCmp) return userCmp;
    return String(b.created_at_ms || "").localeCompare(String(a.created_at_ms || ""));
  });

  const stats = {
    author_count: authorReports.length,
    article_count: articles.length,
    readable_count: articles.filter((a) => a.quality?.readable).length,
    blocked_by_verification_count: articles.filter((a) => isVerificationBlockedArticle(a)).length,
    with_issue_price_count: articles.filter((a) => a.quality?.has_issue_price).length,
    with_price_range_count: articles.filter((a) => a.extracted?.price_range).length,
    with_target_pe_count: articles.filter((a) => a.extracted?.target_pe != null).length,
    by_type: articles.reduce((acc, article) => {
      acc[article.article_type] = (acc[article.article_type] || 0) + 1;
      return acc;
    }, {}),
    by_author: articles.reduce((acc, article) => {
      acc[article.author_name || article.user_id] = (acc[article.author_name || article.user_id] || 0) + 1;
      return acc;
    }, {}),
    merged_readable_cache_count: mergedReadableCacheCount,
  };

  const index = {
    generated_at: new Date().toISOString(),
    started_at: startedAt.toISOString(),
    options: {
      users: options.users,
      keywords: options.keywords,
      since_date: options.sinceDate,
      no_keyword_filter: options.noKeywordFilter,
      max_pages: options.maxPages,
      max_articles_per_author: options.maxArticlesPerAuthor,
      stop_empty_pages: options.stopEmptyPages,
      delay_ms: options.delayMs,
      refresh: options.refresh,
      refresh_unreadable: options.refreshUnreadable,
    },
    stats,
    authors: authorReports.map((report) => ({
      user_id: report.user_id,
      author_name: report.author_name,
      candidate_count: report.candidate_count,
      collected_count: report.collected_count,
      readable_count: report.details.filter((detail) => detail.readable).length,
      blocked_by_verification: report.blocked_by_verification,
      pages: report.timeline_pages,
    })),
    articles: articles.map((article) => ({
      user_id: article.user_id,
      author_name: article.author_name,
      status_id: article.status_id,
      title: article.title,
      url: article.url,
      created_at_text: article.created_at_text,
      article_type: article.article_type,
      matched_keywords: article.matched_keywords,
      text_length: article.text_length,
      readable: article.quality?.readable,
      file: path.relative(ROOT_DIR, existingArticlePath(options.outDir, article.user_id, article.status_id)),
    })),
  };

  const aggregatePaths = writeAggregate(options.outDir, articles, index);
  const reportPath = path.join(outputsDir, `xueqiu_corpus_collect_${timestampForFile()}.json`);
  fs.writeFileSync(reportPath, `${JSON.stringify({ ...index, author_reports: authorReports }, null, 2)}\n`, "utf8");

  console.log(
    JSON.stringify(
      {
        out_dir: options.outDir,
        articles_jsonl: aggregatePaths.jsonlPath,
        index_json: aggregatePaths.indexPath,
        report_json: reportPath,
        stats,
      },
      null,
      2
    )
  );
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exitCode = 1;
});
