"""所有数据源的共用数据结构。"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Item:
    title: str
    url: str
    source: str                          # 来源名（机器之心 / Hacker News / r/LocalLLaMA ...）
    source_kind: str                     # rss / hackernews / reddit / github / producthunt
    published_at: Optional[datetime] = None
    score: int = 0                       # HN 分数 / Reddit upvotes / GitHub star 增量。没有就 0
    summary: str = ""                    # 一句话摘要（来自原文 description）
    extra: dict = field(default_factory=dict)

    def key(self) -> str:
        """去重用。同一 url 视为同一条。"""
        return self.url.strip().rstrip("/")
