"""AI News Radar — 一键运行入口。

抓取 → 关键词过滤 → 去重 → 分类 → 生成 Markdown 报告。
任何单个源失败只警告，不影响其它源。
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from datetime import datetime
from zoneinfo import ZoneInfo

from config import GH_TRENDING_URLS, REDDIT_SUBS, RSS_FEEDS, TIMEZONE
from core import notify
from core.classify import bucketize
from core.dedupe import Dedupe
from core.filter import filter_items
from core.report import render
from core.summarize import summarize
from sources import github_trending, hackernews, reddit, rss
from sources.base import Item


def _safe(label: str, fn, *args, **kwargs) -> list[Item]:
    """运行抓取函数，失败打印警告并返回空列表。"""
    try:
        items = fn(*args, **kwargs)
        print(f"  [OK]   {label:30s} {len(items):3d} 条")
        return items
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] {label:30s} {e}", file=sys.stderr)
        return []


def _fetch_reddit_serial() -> list[Item]:
    """Reddit 必须串行：并发请求会触发 429。每次间隔 1s。"""
    out: list[Item] = []
    for name, url in REDDIT_SUBS:
        out.extend(_safe(f"Reddit · {name}", reddit.fetch, name, url))
        time.sleep(1.2)
    return out


def fetch_all() -> list[Item]:
    """RSS / HN / GitHub 并发，Reddit 串行。"""
    all_items: list[Item] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = []
        for name, url in RSS_FEEDS.items():
            jobs.append(pool.submit(_safe, f"RSS · {name}", rss.fetch, name, url))
        jobs.append(pool.submit(_safe, "Hacker News", hackernews.fetch))
        for name, url in GH_TRENDING_URLS:
            jobs.append(pool.submit(_safe, f"GitHub · {name}", github_trending.fetch, name, url))
        # Reddit 串行单独跑
        jobs.append(pool.submit(_fetch_reddit_serial))

        for fut in as_completed(jobs):
            all_items.extend(fut.result())
    return all_items


def _dedup_within_run(items: list[Item]) -> list[Item]:
    """同一 url 只保留分数最高的一份（GitHub Python 榜和全语言榜可能重复）。"""
    best: dict[str, Item] = {}
    for it in items:
        k = it.key()
        if k not in best or it.score > best[k].score:
            best[k] = it
    return list(best.values())


def main() -> int:
    print("🛰️  AI News Radar — 开始抓取……\n")
    raw = fetch_all()
    print(f"\n抓取总计 {len(raw)} 条")

    raw = _dedup_within_run(raw)
    print(f"运行内去重后 {len(raw)} 条")

    ai_items = filter_items(raw)
    print(f"AI 关键词过滤后 {len(ai_items)} 条")

    dedupe = Dedupe()
    fresh = dedupe.filter_new(ai_items)
    dedupe.mark_seen(fresh)
    print(f"今日条目 {len(fresh)} 条（含同日重复运行已见过的）")

    # v0.2: 如果设置了 ANTHROPIC_API_KEY，用 Claude 生成中文一句话摘要
    summarize(fresh)

    buckets = bucketize(fresh)
    out = render(
        buckets,
        total_seen=dedupe.total_count(),
        new_count=len(fresh),
    )
    dedupe.close()

    print(f"\n✅ 报告已生成：{out}")

    # 推送到微信（设置了 PUSHPLUS_TOKEN 才会真的发）
    today = datetime.now(ZoneInfo(TIMEZONE)).date()
    notify.push(buckets, today, new_count=len(fresh))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
