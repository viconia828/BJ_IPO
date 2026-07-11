const fs = require("fs");
const path = require("path");
const { chromium } = require(
  "C:\\Users\\Ai\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\node\\node_modules\\.pnpm\\playwright-core@1.61.1\\node_modules\\playwright-core"
);

const USERS = [
  "8889879564",
  "8692639756",
  "9833039947",
  "8851207271",
];

const KEYWORDS = ["上市估值", "上市前瞻", "首日价格分析", "首日股价"];

function stripHtml(value) {
  return String(value || "")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/\s+/g, " ")
    .trim();
}

function hitKeywords(text) {
  return KEYWORDS.filter((kw) => text.includes(kw));
}

async function readHome(page, userId) {
  const url = `https://xueqiu.com/u/${userId}`;
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 25000 }).catch(() => {});
  await page.waitForTimeout(7000);
  return page.evaluate((keywords) => {
    const text = (document.body?.innerText || document.body?.textContent || "").slice(0, 5000);
    const title = document.title;
    const profileName = (text.match(/\n([^\n]+)\n\d+ 关注/) || [])[1] || "";
    const links = Array.from(document.querySelectorAll("a[href]"))
      .map((a) => {
        const nearby = (a.closest("[class]")?.innerText || a.parentElement?.innerText || "").slice(0, 400);
        return {
          text: (a.innerText || a.textContent || "").trim().slice(0, 120),
          href: a.href,
          nearby,
        };
      })
      .filter((item) => keywords.some((kw) => item.text.includes(kw) || item.nearby.includes(kw)))
      .slice(0, 30);
    return {
      url: location.href,
      title,
      profileName,
      loginVisible: text.includes("登录"),
      textHitKeywords: keywords.filter((kw) => text.includes(kw)),
      snippet: text.slice(0, 1200),
      keywordLinks: links,
    };
  }, KEYWORDS);
}

async function readTimelineApi(page, userId, maxPages = 5) {
  const pages = [];
  const candidates = new Map();
  for (let pageNo = 1; pageNo <= maxPages; pageNo += 1) {
    const api = `https://xueqiu.com/statuses/original/timeline.json?user_id=${userId}&page=${pageNo}`;
    const payload = await page.evaluate(async (apiUrl) => {
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
    }, api).catch((error) => ({ ok: false, error: String(error) }));

    const list = Array.isArray(payload.body?.list)
      ? payload.body.list
      : Array.isArray(payload.body?.statuses)
        ? payload.body.statuses
        : Array.isArray(payload.body)
          ? payload.body
          : [];

    pages.push({
      page: pageNo,
      ok: payload.ok,
      status: payload.status,
      contentType: payload.contentType,
      count: list.length,
      error: payload.error,
      bodyPreview: typeof payload.body === "string" ? payload.body.slice(0, 200) : undefined,
    });

    for (const item of list) {
      const text = stripHtml(`${item.title || ""} ${item.description || ""} ${item.text || ""}`);
      const hits = hitKeywords(text);
      if (!hits.length) continue;
      const id = item.id || item.status_id;
      if (!id) continue;
      candidates.set(String(id), {
        id: String(id),
        url: `https://xueqiu.com/${userId}/${id}`,
        createdAt: item.created_at || item.timeBefore || item.createdAt || "",
        title: stripHtml(item.title || "").slice(0, 120),
        text: text.slice(0, 600),
        hits,
      });
    }

    if (!list.length) break;
  }
  return { pages, candidates: Array.from(candidates.values()) };
}

async function readDetail(page, candidate) {
  await page.goto(candidate.url, { waitUntil: "domcontentloaded", timeout: 25000 }).catch(() => {});
  await page.waitForTimeout(6000);
  const detail = await page.evaluate((keywords) => {
    const text = (document.body?.innerText || document.body?.textContent || "").slice(0, 7000);
    return {
      url: location.href,
      title: document.title,
      loginVisible: text.includes("登录"),
      textHitKeywords: keywords.filter((kw) => text.includes(kw)),
      readableSignals: {
        hasCompanySection: text.includes("公司概述") || text.includes("公司概况"),
        hasValuation: text.includes("估值"),
        hasFirstDay: text.includes("首日"),
        hasIssuePrice: text.includes("发行价格") || text.includes("发行价"),
      },
      snippet: text.slice(0, 1600),
    };
  }, KEYWORDS);
  return { ...candidate, detail };
}

async function main() {
  const browser = await chromium.launch({
    executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    headless: true,
    args: ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
  });
  try {
    const context = await browser.newContext({
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
      locale: "zh-CN",
      viewport: { width: 1365, height: 900 },
    });
    const page = await context.newPage();
    const results = [];
    for (const userId of USERS) {
      const home = await readHome(page, userId);
      const timeline = await readTimelineApi(page, userId);
      const details = [];
      for (const candidate of timeline.candidates.slice(0, 3)) {
        details.push(await readDetail(page, candidate));
      }
      results.push({ userId, home, timeline, details });
    }

    const outDir = path.join(process.cwd(), "outputs");
    fs.mkdirSync(outDir, { recursive: true });
    const outPath = path.join(outDir, "xueqiu_access_probe.json");
    fs.writeFileSync(outPath, JSON.stringify({ generatedAt: new Date().toISOString(), keywords: KEYWORDS, results }, null, 2));
    console.log(JSON.stringify({ outPath, keywords: KEYWORDS, results }, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exitCode = 1;
});
