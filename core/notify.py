"""把每日报告渲染成卡片式 HTML 推到微信（PushPlus）。

启用方式：在环境变量里设置 PUSHPLUS_TOKEN 即可。
缺 token、网络错误、PushPlus 报错——一律静默警告，不让主流程失败。

PushPlus 文档：https://www.pushplus.plus/doc/
- 接口：POST http://www.pushplus.plus/send
- body：{ token, title, content, template: "html" }
- 单条限制 64KB，免费版每天 200 条
"""
import os
import re
from datetime import date, datetime

import requests

from config import (
    HTTP_HEADERS,
    HTTP_TIMEOUT,
    PUSH_HIGHLIGHT_COUNT,
    PUSH_TOP_PER_SECTION,
    WEB_URL,
)
from core.classify import GITHUB, MODELS, NEWS, PRODUCTS, SECTION_TITLES
from sources.base import Item

_API = "http://www.pushplus.plus/send"

# —— 视觉常量 ——
_BANNER_GRADIENT = "linear-gradient(135deg, #ff6600 0%, #ff8a00 100%)"
_CARD_BG = "#ffffff"
_PAGE_BG = "#f6f6ef"
_BORDER = "#eaeaea"
_TEXT = "#1a1a1a"
_MUTED = "#828282"
_LINK = "#2563eb"

# 不同板块的强调色（左侧色条）
_SECTION_ACCENT = {
    PRODUCTS: "#ff6600",
    NEWS: "#2563eb",
    MODELS: "#7c3aed",
    GITHUB: "#16a34a",
}

_STATS_RE = re.compile(r"^(⬆|💬|⭐)\s*\d+(\s*[·•]\s*(⬆|💬|⭐)\s*\d+)*$")


def _has_real_summary(s: str) -> bool:
    """判断 summary 是否是有意义的文本（不是 '⬆ 940 · 💬 178' 之类的统计字串）。"""
    if not s:
        return False
    s = s.strip()
    if len(s) < 6:
        return False
    return not _STATS_RE.match(s)


def _fmt_time(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return ""


def _stats_line(it: Item) -> str:
    parts: list[str] = []
    sk = it.source_kind
    if sk == "hackernews" and it.score:
        parts.append(f"⭐ {it.score}")
    elif sk == "reddit" and it.score:
        parts.append(f"⬆ {it.score}")
    elif sk == "github" and it.score:
        parts.append(f"⭐ +{it.score} 今日")
    return " · ".join(parts)


def _esc(s: str) -> str:
    """转义防止 title/source 里有 <、>、& 把 HTML 弄炸。"""
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ============ HTML 片段渲染 ============

def _banner(today: date, *, new_count: int, total_seen: int) -> str:
    return f"""
<div style="background:{_BANNER_GRADIENT};color:#fff;padding:20px 18px;border-radius:10px;margin-bottom:18px;">
  <div style="font-size:20px;font-weight:700;letter-spacing:0.5px;">🛰️ AI News Radar</div>
  <div style="font-size:14px;margin-top:4px;opacity:0.95;">{today.isoformat()} · 今日新增 <b>{new_count}</b> 条 · 累计追踪 {total_seen} 条</div>
</div>
""".strip()


def _highlight_card(it: Item, n: int) -> str:
    """今日必看 TOP 的大卡片：大图 + 大标题 + 摘要 + 来源行。"""
    image = (it.extra or {}).get("image") or ""
    img_html = ""
    if image:
        img_html = (
            f'<img src="{_esc(image)}" alt="" '
            f'style="width:100%;max-height:220px;object-fit:cover;border-radius:6px;margin-bottom:10px;display:block;" />'
        )
    summary = it.summary if _has_real_summary(it.summary) else ""
    summary_html = (
        f'<div style="color:#333;font-size:14px;line-height:1.65;margin-top:6px;">{_esc(summary)}</div>'
        if summary else ""
    )
    stats = _stats_line(it)
    meta_bits = [_esc(it.source)]
    if stats:
        meta_bits.append(_esc(stats))
    t = _fmt_time(it.published_at if isinstance(it.published_at, str) else None)
    if t:
        meta_bits.append(_esc(t))
    meta_line = " · ".join(meta_bits)

    return f"""
<div style="background:{_CARD_BG};border:1px solid {_BORDER};border-radius:10px;padding:14px;margin-bottom:14px;">
  <div style="display:inline-block;background:#fff7ed;color:#c2410c;font-size:12px;font-weight:700;padding:2px 8px;border-radius:10px;margin-bottom:8px;">⭐ TOP {n}</div>
  {img_html}
  <a href="{_esc(it.url)}" style="color:{_TEXT};text-decoration:none;">
    <div style="font-size:17px;font-weight:700;line-height:1.4;">{_esc(it.title)}</div>
  </a>
  <div style="color:{_MUTED};font-size:12px;margin-top:6px;">{meta_line}</div>
  {summary_html}
  <div style="margin-top:10px;"><a href="{_esc(it.url)}" style="color:{_LINK};font-size:13px;text-decoration:none;">阅读原文 →</a></div>
</div>
""".strip()


def _section_header(title: str, count: int, accent: str) -> str:
    return f"""
<div style="border-left:4px solid {accent};padding:6px 0 6px 12px;margin:22px 0 12px;">
  <div style="font-size:17px;font-weight:700;color:{_TEXT};">{_esc(title)}</div>
  <div style="font-size:12px;color:{_MUTED};margin-top:2px;">{count} 条</div>
</div>
""".strip()


def _item_card(it: Item, accent: str) -> str:
    image = (it.extra or {}).get("image") or ""
    summary = it.summary if _has_real_summary(it.summary) else ""

    # 缩略图：固定 80px 方块，左侧
    thumb_html = ""
    if image:
        thumb_html = (
            f'<td style="width:80px;vertical-align:top;padding-right:12px;">'
            f'<img src="{_esc(image)}" alt="" '
            f'style="width:80px;height:80px;object-fit:cover;border-radius:6px;display:block;" />'
            f'</td>'
        )

    stats = _stats_line(it)
    meta_bits = [_esc(it.source)]
    if stats:
        meta_bits.append(_esc(stats))
    t = _fmt_time(it.published_at if isinstance(it.published_at, str) else None)
    if t:
        meta_bits.append(_esc(t))
    meta_line = " · ".join(meta_bits)

    summary_html = (
        f'<div style="color:#444;font-size:13px;line-height:1.6;margin-top:6px;">{_esc(summary)}</div>'
        if summary else ""
    )

    return f"""
<div style="background:{_CARD_BG};border:1px solid {_BORDER};border-radius:8px;padding:12px;margin-bottom:10px;">
  <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
    <tr>
      {thumb_html}
      <td style="vertical-align:top;">
        <a href="{_esc(it.url)}" style="color:{_TEXT};text-decoration:none;">
          <div style="font-size:15px;font-weight:600;line-height:1.45;">{_esc(it.title)}</div>
        </a>
        <div style="color:{_MUTED};font-size:12px;margin-top:4px;">{meta_line}</div>
        {summary_html}
      </td>
    </tr>
  </table>
</div>
""".strip()


def _footer() -> str:
    web_link = ""
    if WEB_URL:
        web_link = (
            f'<div style="text-align:center;margin:24px 0 12px;">'
            f'<a href="{_esc(WEB_URL)}" style="display:inline-block;background:#1a1a1a;color:#fff;padding:10px 22px;border-radius:6px;font-size:14px;text-decoration:none;">查看完整列表（网页版）</a>'
            f'</div>'
        )
    return f"""
{web_link}
<div style="color:{_MUTED};font-size:12px;text-align:center;margin-top:14px;line-height:1.6;">
  自动抓取 RSS / Hacker News / Reddit / GitHub Trending<br/>
  来源：AI News Radar · 每日 10:00 北京时间更新
</div>
""".strip()


# ============ 主入口 ============

def _flatten_for_highlights(buckets: dict) -> list[Item]:
    """跨板块按 score 取 TOP，但要优先含图片+真摘要的，让 TOP 看着丰满。"""
    flat: list[Item] = []
    for items in buckets.values():
        flat.extend(items)
    # 评分：score 加成 + 有图 + 有真摘要
    def rank(it: Item) -> tuple:
        has_image = 1 if (it.extra or {}).get("image") else 0
        has_summary = 1 if _has_real_summary(it.summary) else 0
        return (has_image + has_summary, it.score or 0)
    flat.sort(key=rank, reverse=True)
    return flat


def render_html(buckets: dict, today: date, *, new_count: int, total_seen: int) -> str:
    parts: list[str] = []
    parts.append(f'<div style="background:{_PAGE_BG};padding:14px;font-family:-apple-system,BlinkMacSystemFont,\'PingFang SC\',\'Hiragino Sans GB\',\'Microsoft YaHei\',sans-serif;color:{_TEXT};">')
    parts.append(_banner(today, new_count=new_count, total_seen=total_seen))

    # 今日必看
    highlights = _flatten_for_highlights(buckets)[:PUSH_HIGHLIGHT_COUNT]
    if highlights:
        parts.append(_section_header("⭐ 今日必看", len(highlights), "#ff6600"))
        for i, it in enumerate(highlights, start=1):
            parts.append(_highlight_card(it, i))

    # 4 个板块（每板块 PUSH_TOP_PER_SECTION 条）
    highlight_keys = {it.url for it in highlights}
    for key in (PRODUCTS, NEWS, MODELS, GITHUB):
        items = buckets.get(key, [])
        # 不在 TOP 里的剩余条目
        remaining = [it for it in items if it.url not in highlight_keys][:PUSH_TOP_PER_SECTION]
        if not remaining:
            continue
        accent = _SECTION_ACCENT.get(key, "#1a1a1a")
        parts.append(_section_header(SECTION_TITLES.get(key, key), len(remaining), accent))
        for it in remaining:
            parts.append(_item_card(it, accent))

    parts.append(_footer())
    parts.append("</div>")
    return "\n".join(parts)


def push(buckets: dict, today: date, *, new_count: int, total_seen: int = 0) -> bool:
    """成功返回 True；任何异常静默打印警告并返回 False。"""
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not token:
        return False

    if new_count == 0:
        print("📭 今日新增为 0，跳过微信推送。")
        return False

    title = f"🛰️ AI News · {today.isoformat()} · {new_count} 条"
    content = render_html(buckets, today, new_count=new_count, total_seen=total_seen)

    # 64KB 上限，先粗略检查
    body_size = len(content.encode("utf-8"))
    if body_size > 60_000:
        print(f"⚠️  推送内容过大 ({body_size} 字节)，截断到前 60KB")
        content = content.encode("utf-8")[:60_000].decode("utf-8", errors="ignore") + "</div>"

    try:
        resp = requests.post(
            _API,
            json={"token": token, "title": title, "content": content, "template": "html"},
            headers=HTTP_HEADERS,
            timeout=HTTP_TIMEOUT,
        )
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.ok and data.get("code") == 200:
            print(f"📲 已推送到微信（PushPlus）: {title} · {body_size} 字节")
            return True
        print(f"⚠️  PushPlus 返回非 200: status={resp.status_code} body={data or resp.text[:200]}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  PushPlus 调用失败：{e}")
        return False
