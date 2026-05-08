"""为没有图片的条目，抓原文 og:image 补一张。

策略：
- 只对 it.extra["image"] 为空的条目发请求
- 并发 + 单条短超时（默认 4s × 8 worker），最差总耗时 ~30s
- 缓存到 data/og_cache.json，TTL 7 天，避免重复抓
- 任何失败（网络/解析/HTTP 错误）一律静默跳过，不阻断主流程

解析顺序：
  og:image → twitter:image → link rel="image_src" → 第一张 <img>
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from config import DATA_DIR, HTTP_HEADERS, HTTP_TIMEOUT
from sources.base import Item

_CACHE_PATH = DATA_DIR / "og_cache.json"
_CACHE_TTL = timedelta(days=7)
# 一些图片域名经验性偏小或失效，提前过滤掉
_BAD_IMAGE_HOSTS = ("gravatar.com",)


def _load_cache() -> dict:
    if not _CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _is_fresh(entry: dict) -> bool:
    ts = entry.get("fetched_at")
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt < _CACHE_TTL
    except Exception:
        return False


def _normalize(url: str, base: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        from urllib.parse import urlparse
        u = urlparse(base)
        return f"{u.scheme}://{u.netloc}{url}"
    return url


def _extract_image(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    selectors = [
        ("meta", {"property": "og:image"}, "content"),
        ("meta", {"property": "og:image:secure_url"}, "content"),
        ("meta", {"name": "twitter:image"}, "content"),
        ("meta", {"name": "twitter:image:src"}, "content"),
        ("link", {"rel": "image_src"}, "href"),
    ]
    for tag, attrs, prop in selectors:
        el = soup.find(tag, attrs=attrs)
        if el:
            url = (el.get(prop) or "").strip()
            if url:
                u = _normalize(url, base_url)
                if not any(b in u for b in _BAD_IMAGE_HOSTS):
                    return u
    # 兜底：第一张较大的 <img>
    img = soup.find("img")
    if img:
        src = (img.get("src") or "").strip()
        if src and not any(b in src for b in _BAD_IMAGE_HOSTS):
            return _normalize(src, base_url)
    return ""


def _fetch_one(url: str, timeout: int) -> str:
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=timeout, allow_redirects=True)
        if not resp.ok:
            return ""
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype.lower():
            return ""
        # 只看前 200KB，绝大多数 og:image 在 <head>，能省时间
        html = resp.text[:200_000]
        return _extract_image(html, resp.url)
    except Exception:
        return ""


def fetch_og_images(items: list[Item], *, max_workers: int = 8, timeout: int = 4) -> int:
    """并发为缺图条目抓 og:image，返回新补到的图片数。"""
    cache = _load_cache()
    targets = [it for it in items if not (it.extra or {}).get("image")]
    if not targets:
        return 0

    # 先用缓存命中
    cache_hits = 0
    pending: list[Item] = []
    for it in targets:
        entry = cache.get(it.url)
        if entry and _is_fresh(entry):
            img = entry.get("image", "")
            if img:
                it.extra = {**(it.extra or {}), "image": img}
                cache_hits += 1
            # 即使缓存里 image="" 也跳过，避免反复抓死链
            continue
        pending.append(it)

    if not pending:
        if cache_hits:
            print(f"🖼️  og:image 缓存命中 {cache_hits} 条")
        return cache_hits

    print(f"🖼️  og:image 抓取中：{len(pending)} 条（缓存命中 {cache_hits}）……")
    new_count = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_fetch_one, it.url, timeout): it for it in pending}
        for fut in as_completed(futs):
            it = futs[fut]
            try:
                img = fut.result() or ""
            except Exception:
                img = ""
            cache[it.url] = {
                "image": img,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            if img:
                it.extra = {**(it.extra or {}), "image": img}
                new_count += 1

    # 简易缓存清理：超过 1000 条时丢弃过期项
    if len(cache) > 1000:
        cache = {k: v for k, v in cache.items() if _is_fresh(v)}
    _save_cache(cache)

    total = cache_hits + new_count
    print(f"✅ og:image 已补图 {new_count} 条（含缓存命中 {cache_hits}，总计 +{total}）")
    return total
