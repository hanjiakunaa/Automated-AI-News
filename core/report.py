"""把分好类的条目渲染成 Markdown 报告 + JSON（供前端读取）。"""
import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import REPORT_DIR, ROOT, SECTION_LIMIT, TIMEZONE, WEB_DATA_DIR
from core.classify import SECTION_TITLES
from sources.base import Item


_env = Environment(
    loader=FileSystemLoader(str(ROOT / "templates")),
    autoescape=select_autoescape(disabled_extensions=("md", "j2")),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.strftime("%m-%d %H:%M")


_env.filters["fmt_dt"] = _fmt_dt


def _today_local() -> date:
    """按业务时区取「今天」，避免 GitHub Actions 的 UTC 把 09:00 北京时间渲染成前一天。"""
    return datetime.now(ZoneInfo(TIMEZONE)).date()


def _item_to_dict(it: Item) -> dict:
    """把 Item 序列化为 JSON-friendly dict。datetime 转 ISO 字符串。"""
    d = asdict(it)
    if it.published_at:
        d["published_at"] = it.published_at.isoformat()
    else:
        d["published_at"] = None
    return d


def _write_json(buckets: dict, today: date, *, new_count: int, total_seen: int) -> Path:
    """写 web/data/YYYY-MM-DD.json，并刷新 web/data/index.json（日期列表）。"""
    WEB_DATA_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "date": today.isoformat(),
        "generated_at": datetime.now(ZoneInfo(TIMEZONE)).isoformat(timespec="seconds"),
        "stats": {"new_count": new_count, "total_seen": total_seen},
        "section_titles": SECTION_TITLES,
        "sections": {
            key: [_item_to_dict(it) for it in items[:SECTION_LIMIT]]
            for key, items in buckets.items()
        },
    }
    out = WEB_DATA_DIR / f"{today.isoformat()}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 刷新 index.json：所有 YYYY-MM-DD.json 文件名按降序排列
    dates = sorted(
        (p.stem for p in WEB_DATA_DIR.glob("[0-9][0-9][0-9][0-9]-*.json")),
        reverse=True,
    )
    index_path = WEB_DATA_DIR / "index.json"
    index_path.write_text(
        json.dumps({"dates": dates, "latest": dates[0] if dates else None}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def render(buckets: dict, *, total_seen: int, new_count: int) -> Path:
    template = _env.get_template("daily.md.j2")
    today = _today_local()
    # 截断每个板块到 SECTION_LIMIT
    trimmed = {k: v[:SECTION_LIMIT] for k, v in buckets.items()}
    md = template.render(
        date=today.isoformat(),
        sections=trimmed,
        section_titles=SECTION_TITLES,
        new_count=new_count,
        total_seen=total_seen,
        generated_at=datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"{today.isoformat()}.md"
    out.write_text(md, encoding="utf-8")

    # 同时写一份 JSON 给前端
    _write_json(buckets, today, new_count=new_count, total_seen=total_seen)
    return out
