"""Hugging Face Papers 每日精选论文。

HF Papers 是 AK（@_akhaliq）人工挑的、每日热度最高的 AI 论文集合，
是学术圈最权威的「今日值得看的论文」入口。无公开 RSS，走 HTML 爬虫。
"""
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from config import HTTP_HEADERS, HTTP_TIMEOUT
from sources.base import Item


_BASE = "https://huggingface.co"
_LIST_URL = f"{_BASE}/papers"


def _extract_upvotes(article) -> int:
    """upvote 数字在 article 内某个 `div.leading-none` 里。"""
    for div in article.select("div.leading-none"):
        t = div.get_text(strip=True)
        if t.isdigit():
            return int(t)
    return 0


def fetch() -> list[Item]:
    resp = requests.get(_LIST_URL, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    items: list[Item] = []
    now = datetime.now()
    for art in soup.select("article"):
        link_tag = art.select_one("h3 a[href^='/papers/']")
        if not link_tag:
            continue
        title = link_tag.get_text(strip=True)
        href = link_tag.get("href", "")
        if not title or not href:
            continue
        url = f"{_BASE}{href}"
        paper_id = href.rsplit("/", 1)[-1]

        upvotes = _extract_upvotes(art)
        img_tag = art.select_one("img[src*='cdn-thumbnails']")
        image = img_tag.get("src", "") if img_tag else ""

        items.append(
            Item(
                title=title,
                url=url,
                source="HF Papers",
                source_kind="hfpapers",
                published_at=now,  # HF 不暴露准确时间，按抓取时刻视为「今天」
                score=upvotes,
                summary=f"⬆ {upvotes} · arXiv {paper_id}",
                extra={"image": image, "arxiv_id": paper_id} if image else {"arxiv_id": paper_id},
            )
        )
    return items
