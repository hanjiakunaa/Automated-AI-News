"""GitHub Trending：解析 trending 页面 HTML（GitHub 没有官方 API）。"""
import requests
from bs4 import BeautifulSoup

from config import HTTP_HEADERS, HTTP_TIMEOUT
from sources.base import Item


def fetch(name: str, url: str) -> list[Item]:
    resp = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    items: list[Item] = []
    for article in soup.select("article.Box-row"):
        h2 = article.select_one("h2 a")
        if not h2:
            continue
        repo_path = h2.get("href", "").strip("/")
        if not repo_path:
            continue
        repo_url = f"https://github.com/{repo_path}"
        # 描述
        desc_tag = article.select_one("p")
        desc = desc_tag.get_text(strip=True) if desc_tag else ""
        # 今日新增 star（"123 stars today"）
        stars_today_tag = article.select_one("span.d-inline-block.float-sm-right")
        stars_today = 0
        if stars_today_tag:
            txt = stars_today_tag.get_text(strip=True).split()[0].replace(",", "")
            try:
                stars_today = int(txt)
            except ValueError:
                stars_today = 0
        # 语言
        lang_tag = article.select_one('span[itemprop="programmingLanguage"]')
        lang = lang_tag.get_text(strip=True) if lang_tag else ""
        meta = f"⭐ +{stars_today} 今日"
        if lang:
            meta = f"{lang} · {meta}"
        items.append(
            Item(
                title=repo_path,
                url=repo_url,
                source=f"GitHub Trending ({name})",
                source_kind="github",
                score=stars_today,
                summary=(desc + (" · " if desc else "") + meta).strip(" ·"),
            )
        )
    return items
