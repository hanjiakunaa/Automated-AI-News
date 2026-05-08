"""Reddit 子版块：URL 加 .json 即得公开数据，无需登录。"""
import html
from datetime import datetime

import requests

from config import HTTP_HEADERS, HTTP_TIMEOUT
from sources.base import Item

# 这些是 reddit 用来表示「没有图」/「特殊状态」的占位符，要忽略
_PLACEHOLDER_THUMBS = {"self", "default", "nsfw", "spoiler", "image", ""}


def _pick_image(d: dict) -> str:
    """从 Reddit JSON 数据里提取一张代表图。"""
    # 1. 帖子是直链图片：i.redd.it / i.imgur.com
    url = d.get("url_overridden_by_dest") or d.get("url") or ""
    if url and any(host in url for host in ("i.redd.it", "i.imgur.com", ".jpg", ".jpeg", ".png", ".gif", ".webp")):
        return url
    # 2. preview.images[0].source.url（高清）
    preview = d.get("preview") or {}
    images = preview.get("images") or []
    if images:
        src = (images[0].get("source") or {}).get("url")
        if src:
            return html.unescape(src)
    # 3. thumbnail（小图）
    thumb = d.get("thumbnail") or ""
    if thumb and thumb not in _PLACEHOLDER_THUMBS and thumb.startswith("http"):
        return thumb
    return ""


def _make_summary(d: dict) -> str:
    """优先用 selftext（帖子正文），否则给统计行。"""
    selftext = (d.get("selftext") or "").strip()
    if selftext:
        # 截断，避免长贴占满
        return selftext[:200].replace("\n", " ").strip() + ("…" if len(selftext) > 200 else "")
    ups = int(d.get("ups") or 0)
    comments = int(d.get("num_comments") or 0)
    return f"⬆ {ups} · 💬 {comments}"


def fetch(name: str, url: str) -> list[Item]:
    # Reddit 对默认 UA 限流严格，必须传一个明确的 UA（config 里已设）
    resp = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    items: list[Item] = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        if d.get("stickied") or d.get("pinned"):
            continue
        title = d.get("title") or ""
        permalink = d.get("permalink") or ""
        if not title or not permalink:
            continue
        external = d.get("url_overridden_by_dest") or ""
        link = external if external and "reddit.com" not in external else f"https://www.reddit.com{permalink}"
        published = datetime.fromtimestamp(d["created_utc"]) if d.get("created_utc") else None
        ups = int(d.get("ups") or 0)
        image = _pick_image(d)
        items.append(
            Item(
                title=title,
                url=link,
                source=name,
                source_kind="reddit",
                published_at=published,
                score=ups,
                summary=_make_summary(d),
                extra={"image": image} if image else {},
            )
        )
    return items
