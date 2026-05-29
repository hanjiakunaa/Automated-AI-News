"""HTML 页面爬虫：用于无 RSS 的关键官方页面。

每个站点结构不同，所以是「按站点写解析函数」的模式，而不是统一的 selector。
集中在这里是为了在一个文件里管理所有「需要 HTML 爬」的源，方便维护。

已覆盖：
- Anthropic News（最关键：Claude Skills / 模型发布 / 公司公告）
- Cursor Changelog（最关键的 Coding Agent 工具更新）
- Mistral AI News
- xAI News

未来可加：
- 机器之心、新智元（公众号镜像）
- DeepSeek / Kimi / 智谱 / 阶跃 / MiniMax 官方公告页
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import requests
from bs4 import BeautifulSoup

from config import HTTP_HEADERS, HTTP_TIMEOUT
from sources.base import Item


@dataclass
class Site:
    name: str
    url: str
    parser: Callable[[BeautifulSoup, "Site"], list[Item]]


_WS = re.compile(r"\s+")


def _clean(t: str) -> str:
    return _WS.sub(" ", (t or "").strip())


def _absolute(base: str, href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        # 从 url 取 scheme://host
        m = re.match(r"^(https?://[^/]+)", base)
        if m:
            return m.group(1) + href
    return base.rstrip("/") + "/" + href.lstrip("/")


def _parse_date_loose(text: str) -> datetime | None:
    """从一段文本里粗暴提取日期。
    支持：May 28, 2026 / 2026-05-28 / 05-28-26 / 2026/05/28。
    """
    if not text:
        return None
    # ISO 2026-05-28
    m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    # MM-DD-YY (like Cursor 05-28-26)
    m = re.search(r"\b(\d{1,2})-(\d{1,2})-(\d{2})\b", text)
    if m:
        try:
            y = 2000 + int(m.group(3))
            return datetime(y, int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    # English month
    months = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
              "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
    m = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(20\d{2})", text)
    if m:
        try:
            return datetime(int(m.group(3)), months[m.group(1)], int(m.group(2)))
        except (ValueError, KeyError):
            pass
    return None


# ────────────────────── Anthropic News ──────────────────────
def _parse_anthropic(soup: BeautifulSoup, site: Site) -> list[Item]:
    """Anthropic /news 页面：每个新闻是一个 a[href^="/news/"]。
    链接文本里混着 category + date + title，需要拆。
    """
    items: list[Item] = []
    seen: set[str] = set()
    for a in soup.select('a[href^="/news/"]'):
        href = a.get("href", "")
        if not href or href.rstrip("/") in ("/news",):
            continue
        if href in seen:
            continue
        seen.add(href)

        url = _absolute(site.url, href)
        raw = _clean(a.get_text(" ", strip=True))
        if len(raw) < 10:
            continue

        # 形如："Introducing Claude Opus 4.8 Product May 28, 2026 An upgrade to our Opus class..."
        published = _parse_date_loose(raw)
        # 用 category + 日期作为分隔点把"标题"和"正文"切开
        # 先剥掉 category（位置不固定）
        cleaned = raw
        for cat in ("Announcements", "Societal Impacts", "Product", "Policy", "Research"):
            cleaned = cleaned.replace(cat, " ")
        # 用日期当锚点把字符串切两段。Anthropic 有两种排版：
        #   排版 A：「标题 + 日期 + 正文」 → parts[0]=标题, parts[1]=正文
        #   排版 B：「日期 + 标题」（短公告） → parts[0]="", parts[1]=标题
        date_pattern = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+20\d{2}"
        parts = [_clean(p) for p in re.split(date_pattern, cleaned, maxsplit=1)]
        if len(parts) == 2 and parts[0]:
            title = parts[0]
            summary = parts[1][:240]
        elif len(parts) == 2 and parts[1]:
            title = parts[1]
            summary = ""
        else:
            title = _clean(cleaned)
            summary = ""
        # 标题过长则截断到 130 字（保留剩余进摘要）
        if len(title) > 130:
            tail = title[130:]
            summary = (tail + " " + summary)[:240].strip()
            title = title[:130].rstrip(",. ；;") + "…"
        if not title:
            continue

        items.append(
            Item(
                title=title,
                url=url,
                source="Anthropic News",
                source_kind="html",
                published_at=published,
                summary=summary,
            )
        )
    return items


# ────────────────────── Cursor Changelog ──────────────────────
def _parse_cursor(soup: BeautifulSoup, site: Site) -> list[Item]:
    """Cursor /changelog：每条 changelog 是一个 article，标题在 h2，链接在第一个 a[href^="/changelog/"]。
    h2 文本以 # 开头（markdown 锚点风格），需要剥掉。按 url 去重。
    """
    items: list[Item] = []
    seen: set[str] = set()
    for art in soup.select("article"):
        h2 = art.select_one("h2")
        if not h2:
            continue
        title = _clean(h2.get_text()).lstrip("#").strip()
        if not title:
            continue
        link = art.select_one('a[href^="/changelog/"]')
        href = link.get("href") if link else ""
        url = _absolute(site.url, href) if href else site.url
        if url in seen:
            continue
        seen.add(url)
        date_text = ""
        for tag in art.select("time, span, div"):
            t = tag.get_text(" ", strip=True)
            if re.search(r"20\d{2}", t) and len(t) < 30:
                date_text = t
                break
        published = _parse_date_loose(date_text) if date_text else None
        p = art.find("p")
        summary = _clean(p.get_text(" ", strip=True))[:200] if p else ""
        items.append(
            Item(
                title=title,
                url=url,
                source="Cursor Changelog",
                source_kind="html",
                published_at=published,
                summary=summary,
            )
        )
    return items


# ────────────────────── xAI News（regex 兜底） ──────────────────────
def _parse_xai(soup: BeautifulSoup, site: Site) -> list[Item]:
    """xAI 用 React Server Component，BS4 解析出来 a 标签很少。
    用 raw HTML + 正则提取 /news/<slug> 链接，逐个补全标题。
    """
    raw = str(soup)
    hrefs: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r'href=["\'](/news/[^"\'#?]+)["\']', raw):
        h = m.group(1)
        if h in seen or h == "/news":
            continue
        seen.add(h)
        hrefs.append(h)

    items: list[Item] = []
    for href in hrefs[:30]:
        slug = href.rsplit("/", 1)[-1]
        # 标题用 slug 反推（短横线转空格、首字母大写）
        title = slug.replace("-", " ").title()
        items.append(
            Item(
                title=title,
                url=_absolute(site.url, href),
                source="xAI News",
                source_kind="html",
                summary="",
            )
        )
    return items


# ────────────────────── Mistral ──────────────────────
def _parse_mistral(soup: BeautifulSoup, site: Site) -> list[Item]:
    """Mistral 用 Astro，每个 article 带 data-title / data-categories / a[href^=/news/]。"""
    items: list[Item] = []
    seen: set[str] = set()
    for art in soup.select("article"):
        a = art.select_one('a[href^="/news/"]')
        if not a:
            continue
        href = a.get("href", "")
        if href in seen:
            continue
        seen.add(href)

        # 优先用页面里的 h2/h3 显示文本（大小写正确），data-title 兜底
        h = art.find(["h2", "h3"])
        title = _clean(h.get_text()) if h else ""
        if not title:
            raw_title = art.get("data-title") or ""
            title = raw_title.strip().title() if raw_title else ""
        if not title or len(title) < 4:
            continue
        category = art.get("data-categories", "")
        summary = f"[{category}]" if category else ""
        items.append(
            Item(
                title=title,
                url=_absolute(site.url, href),
                source="Mistral News",
                source_kind="html",
                summary=summary,
            )
        )
    return items


# ────────────────────── 通用兜底 ──────────────────────
def _parse_generic_blog(soup: BeautifulSoup, site: Site) -> list[Item]:
    """对 Mistral / xAI 这种「博客卡片」型页面用相对宽松的策略：
    任何 article 内的 h1/h2/h3 标题 + 第一个 a[href] 链接。
    """
    items: list[Item] = []
    seen: set[str] = set()
    candidates = soup.select("article, li.post, div.post")
    if not candidates:
        # 退化：找所有 h2/h3 下的 a
        candidates = []
        for h in soup.select("h2 a, h3 a"):
            candidates.append(h.parent.parent)
    for art in candidates:
        h = art.find(["h1", "h2", "h3"])
        if not h:
            continue
        title = _clean(h.get_text())
        if not title or len(title) < 4:
            continue
        a = art.find("a", href=True)
        href = a.get("href") if a else ""
        if not href:
            continue
        url = _absolute(site.url, href)
        if url in seen:
            continue
        seen.add(url)
        p = art.find("p")
        summary = _clean(p.get_text(" ", strip=True))[:200] if p else ""
        # 找日期
        published = None
        date_tag = art.find("time")
        if date_tag:
            dt_attr = date_tag.get("datetime") or date_tag.get_text(" ", strip=True)
            published = _parse_date_loose(dt_attr)
        items.append(
            Item(
                title=title,
                url=url,
                source=site.name,
                source_kind="html",
                published_at=published,
                summary=summary,
            )
        )
    return items


# ────────────────────── 站点注册表 ──────────────────────
SITES: list[Site] = [
    Site("Anthropic News", "https://www.anthropic.com/news", _parse_anthropic),
    Site("Cursor Changelog", "https://cursor.com/changelog", _parse_cursor),
    Site("Mistral News", "https://mistral.ai/news", _parse_mistral),
    Site("xAI News", "https://x.ai/news", _parse_xai),
]


def fetch_one(site: Site) -> list[Item]:
    resp = requests.get(site.url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    return site.parser(soup, site)
