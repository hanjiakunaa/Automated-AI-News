"""通用 RSS 抓取器：机器之心、量子位、官方博客、Product Hunt 都走这里。

注意：macOS 系统 Python 自带的 SSL 不带根证书，feedparser 内部用 urllib 抓
HTTPS 会全部失败。所以这里改用 requests（自带 certifi）先抓 bytes，再交给
feedparser.parse() 解析，绕过这个坑。
"""
import re
from datetime import datetime
from time import mktime

import feedparser
import requests

from config import HTTP_HEADERS, HTTP_TIMEOUT
from sources.base import Item


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Product Hunt RSS 尾巴：「 Discussion | Link」之类
_PH_TAIL_RE = re.compile(r"\s*(Discussion\s*\|\s*Link)\s*$", re.IGNORECASE)
_IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


def _strip_html(text: str) -> str:
    if not text:
        return ""
    s = _TAG_RE.sub(" ", text)
    s = _WS_RE.sub(" ", s).strip()
    s = _PH_TAIL_RE.sub("", s).strip()
    return s


def _to_datetime(struct_time) -> datetime | None:
    if not struct_time:
        return None
    try:
        return datetime.fromtimestamp(mktime(struct_time))
    except Exception:
        return None


def _pick_image(entry) -> str:
    """按优先级提取一张代表图：media:thumbnail > media:content > enclosure > description 里的 <img>。"""
    # 1. media:thumbnail
    thumbs = entry.get("media_thumbnail") or []
    if thumbs and isinstance(thumbs, list):
        u = thumbs[0].get("url")
        if u:
            return u
    # 2. media:content（image 类型）
    contents = entry.get("media_content") or []
    if contents and isinstance(contents, list):
        for c in contents:
            u = c.get("url")
            t = (c.get("type") or "").lower()
            if u and (t.startswith("image/") or not t):
                return u
    # 3. enclosure
    encs = entry.get("enclosures") or []
    for e in encs:
        u = e.get("href") or e.get("url")
        t = (e.get("type") or "").lower()
        if u and t.startswith("image/"):
            return u
    # 4. content/summary 里的第一张 <img>
    blob = ""
    if entry.get("content"):
        blob = entry["content"][0].get("value", "") if isinstance(entry["content"], list) else ""
    blob = blob or entry.get("summary") or entry.get("description") or ""
    m = _IMG_SRC_RE.search(blob)
    if m:
        return m.group(1)
    return ""


def fetch(name: str, url: str, kind: str = "rss") -> list[Item]:
    """抓单个 RSS。失败抛异常，由上层捕获。"""
    resp = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    items: list[Item] = []
    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        summary = _strip_html(entry.get("summary") or entry.get("description") or "")
        if len(summary) > 200:
            summary = summary[:200].rstrip() + "…"
        published = _to_datetime(entry.get("published_parsed") or entry.get("updated_parsed"))
        image = _pick_image(entry)
        items.append(
            Item(
                title=title,
                url=link,
                source=name,
                source_kind=kind,
                published_at=published,
                summary=summary,
                extra={"image": image} if image else {},
            )
        )
    return items
