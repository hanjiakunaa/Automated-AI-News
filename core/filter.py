"""AI 关键词过滤 + 时间窗口过滤 + 每源限量。"""
from datetime import datetime, timedelta

from config import AI_KEYWORDS, MAX_PER_SOURCE, RECENT_DAYS
from sources.base import Item

_KEYWORDS_LOWER = [k.lower() for k in AI_KEYWORDS]

# 这些源全是 AI 内容，跳过关键词过滤
_AI_NATIVE_SOURCES = {
    "量子位",
    "OpenAI Blog", "Google AI", "DeepMind", "Hugging Face",
    "r/LocalLLaMA", "r/MachineLearning", "r/OpenAI",
}


def is_ai_related(item: Item) -> bool:
    if item.source in _AI_NATIVE_SOURCES:
        return True
    haystack = f"{item.title} {item.summary}".lower()
    return any(kw in haystack for kw in _KEYWORDS_LOWER)


def is_recent(item: Item, cutoff: datetime) -> bool:
    """无发布时间的视为「近期」（比如 GitHub Trending、HN 大多数情况）。"""
    if item.published_at is None:
        return True
    return item.published_at >= cutoff


def filter_items(items: list[Item]) -> list[Item]:
    cutoff = datetime.now() - timedelta(days=RECENT_DAYS)
    # 1. AI 相关 + 近期
    keep = [it for it in items if is_ai_related(it) and is_recent(it, cutoff)]
    # 2. 每源最多 MAX_PER_SOURCE 条（按 published_at 倒序，无时间排最后）
    by_source: dict[str, list[Item]] = {}
    for it in keep:
        by_source.setdefault(it.source, []).append(it)
    capped: list[Item] = []
    for src, group in by_source.items():
        group.sort(
            key=lambda x: x.published_at or datetime.min,
            reverse=True,
        )
        capped.extend(group[:MAX_PER_SOURCE])
    return capped
