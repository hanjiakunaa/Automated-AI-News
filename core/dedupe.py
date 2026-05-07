"""SQLite 去重：见过的 url 不再进入今日报告。"""
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from config import DATA_DIR, DB_PATH, TIMEZONE
from sources.base import Item


def _today() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).date().isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    url           TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    source        TEXT NOT NULL,
    first_seen    TEXT NOT NULL
);
"""


class Dedupe:
    def __init__(self, db_path=DB_PATH):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    def filter_new(self, items: list[Item]) -> list[Item]:
        """返回今天还有效的条目：从未见过的，或第一次见到就是今天的。
        这样同一天重复运行不会丢内容，跨天才会真正过滤掉昨天的。"""
        today = _today()
        cur = self.conn.cursor()
        fresh: list[Item] = []
        for it in items:
            cur.execute("SELECT first_seen FROM seen WHERE url = ?", (it.key(),))
            row = cur.fetchone()
            if row is None or row[0] == today:
                fresh.append(it)
        return fresh

    def mark_seen(self, items: list[Item]) -> None:
        today = _today()
        rows = [(it.key(), it.title, it.source, today) for it in items]
        # IGNORE：已经在库里的不会更新 first_seen，保持原本日期
        self.conn.executemany(
            "INSERT OR IGNORE INTO seen(url, title, source, first_seen) VALUES (?,?,?,?)",
            rows,
        )
        self.conn.commit()

    def total_count(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) FROM seen")
        return cur.fetchone()[0]

    def close(self) -> None:
        self.conn.close()
