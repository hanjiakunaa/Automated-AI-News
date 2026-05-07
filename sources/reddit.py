"""Reddit 子版块：URL 加 .json 即得公开数据，无需登录。"""
from datetime import datetime

import requests

from config import HTTP_HEADERS, HTTP_TIMEOUT
from sources.base import Item


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
        # 优先用外部链接（如果是文章/产品），否则用 reddit 帖子页
        link = external if external and "reddit.com" not in external else f"https://www.reddit.com{permalink}"
        published = datetime.fromtimestamp(d["created_utc"]) if d.get("created_utc") else None
        ups = int(d.get("ups") or 0)
        comments = int(d.get("num_comments") or 0)
        items.append(
            Item(
                title=title,
                url=link,
                source=name,
                source_kind="reddit",
                published_at=published,
                score=ups,
                summary=f"⬆ {ups} · 💬 {comments}",
            )
        )
    return items
