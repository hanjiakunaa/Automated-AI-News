"""Hacker News：用官方 Firebase API。"""
from datetime import datetime

import requests

from config import HN_FETCH_LIMIT, HN_ITEM_URL, HN_TOP_URL, HTTP_HEADERS, HTTP_TIMEOUT
from sources.base import Item


def _get_json(url: str):
    resp = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def fetch() -> list[Item]:
    ids = _get_json(HN_TOP_URL)[:HN_FETCH_LIMIT]
    items: list[Item] = []
    for hid in ids:
        try:
            data = _get_json(HN_ITEM_URL.format(id=hid))
        except Exception:
            continue
        if not data or data.get("type") != "story":
            continue
        title = data.get("title") or ""
        url = data.get("url") or f"https://news.ycombinator.com/item?id={hid}"
        if not title:
            continue
        published = (
            datetime.fromtimestamp(data["time"]) if data.get("time") else None
        )
        items.append(
            Item(
                title=title,
                url=url,
                source="Hacker News",
                source_kind="hackernews",
                published_at=published,
                score=int(data.get("score") or 0),
                summary=f"💬 {data.get('descendants', 0)} 评论 · ⭐ {data.get('score', 0)} 分",
            )
        )
    return items
