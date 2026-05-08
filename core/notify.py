"""把每日报告渲染成卡片式 HTML 推到微信（PushPlus）。

启用方式：在环境变量里设置 PUSHPLUS_TOKEN 即可。
缺 token、网络错误、PushPlus 报错——一律静默警告，不让主流程失败。

PushPlus 文档：https://www.pushplus.plus/doc/
- 接口：POST http://www.pushplus.plus/send
- body：{ token, title, content, template: "html" }
- 单条限制 64KB

视觉准则（WeChat WebView 兼容性）：
- 只用 inline 样式（<style> 标签会被剥）
- flex 用了，新版微信都支持
- 不用 JS / animation / transition
"""
import hashlib
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
_PAGE_BG = "#f7f8fa"
_CARD_BG = "#ffffff"
_BORDER = "#eef0f3"
_TEXT = "#1a202c"
_TEXT_2 = "#4a5568"
_MUTED = "#94a3b8"
_LINK = "#2563eb"

# 板块视觉配置：颜色 + emoji + 一句副标题（让公众号读者扫一眼就知道板块意图）
_SECTION_THEME = {
    PRODUCTS: {
        "gradient": "linear-gradient(135deg,#ff6b35,#f7931e)",
        "color": "#ff6b35",
        "subtitle": "今日值得关注的 AI 新品发布",
    },
    NEWS: {
        "gradient": "linear-gradient(135deg,#3b82f6,#1e40af)",
        "color": "#3b82f6",
        "subtitle": "AI 圈最受讨论的新闻和事件",
    },
    MODELS: {
        "gradient": "linear-gradient(135deg,#8b5cf6,#6d28d9)",
        "color": "#8b5cf6",
        "subtitle": "前沿大模型的发布、研究与社区讨论",
    },
    GITHUB: {
        "gradient": "linear-gradient(135deg,#22c55e,#15803d)",
        "color": "#22c55e",
        "subtitle": "今日开发者最关注的开源项目",
    },
}

# 高分阈值（单位见各源的 score 含义）
_HOT_THRESHOLD = {
    "hackernews": 200,
    "reddit": 300,
    "github": 200,
}

# 用于占位渐变的源色（没有图时按来源生成不同色块）
_SOURCE_PALETTE = [
    "linear-gradient(135deg,#fde68a,#f59e0b)",
    "linear-gradient(135deg,#bfdbfe,#3b82f6)",
    "linear-gradient(135deg,#ddd6fe,#8b5cf6)",
    "linear-gradient(135deg,#bbf7d0,#22c55e)",
    "linear-gradient(135deg,#fecaca,#ef4444)",
    "linear-gradient(135deg,#fbcfe8,#ec4899)",
    "linear-gradient(135deg,#a5f3fc,#06b6d4)",
]

_STATS_RE = re.compile(r"^(⬆|💬|⭐)\s*\d+(\s*[·•]\s*(⬆|💬|⭐)\s*\d+)*$")


def _has_real_summary(s: str) -> bool:
    if not s:
        return False
    s = s.strip()
    if len(s) < 8:
        return False
    return not _STATS_RE.match(s)


def _esc(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt_time(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return ""


def _palette_for(source: str) -> str:
    h = int(hashlib.md5(source.encode("utf-8")).hexdigest(), 16)
    return _SOURCE_PALETTE[h % len(_SOURCE_PALETTE)]


def _hot_badge(it: Item) -> str:
    sk = it.source_kind
    threshold = _HOT_THRESHOLD.get(sk, 0)
    if not threshold or not it.score:
        return ""
    if it.score >= threshold * 3:
        text, bg, fg = "🔥 爆款", "#fee2e2", "#dc2626"
    elif it.score >= threshold * 1.5:
        text, bg, fg = "🔥 热门", "#ffedd5", "#ea580c"
    elif it.score >= threshold:
        text, bg, fg = "⬆ 上升", "#dbeafe", "#2563eb"
    else:
        return ""
    return (
        f'<span style="display:inline-block;background:{bg};color:{fg};font-size:11px;font-weight:700;'
        f'padding:2px 8px;border-radius:10px;margin-right:6px;vertical-align:middle;">{text}</span>'
    )


def _source_badge(it: Item) -> str:
    sk = it.source_kind
    bg = {
        "rss": "#f1f5f9",
        "hackernews": "#fef3c7",
        "reddit": "#fee2e2",
        "github": "#dcfce7",
    }.get(sk, "#f1f5f9")
    fg = {
        "rss": "#475569",
        "hackernews": "#92400e",
        "reddit": "#b91c1c",
        "github": "#15803d",
    }.get(sk, "#475569")
    return (
        f'<span style="display:inline-block;background:{bg};color:{fg};font-size:11px;font-weight:600;'
        f'padding:2px 8px;border-radius:4px;">{_esc(it.source)}</span>'
    )


def _score_text(it: Item) -> str:
    sk = it.source_kind
    if sk == "hackernews" and it.score:
        return f"⭐ {it.score} 分"
    if sk == "reddit" and it.score:
        return f"⬆ {it.score}"
    if sk == "github" and it.score:
        return f"⭐ +{it.score}"
    return ""


def _display_title(it: Item) -> tuple[str, str]:
    """返回 (主标题, 次标题)。优先中文翻译标题，原标题作小字辅助。"""
    title_zh = (it.extra or {}).get("title_zh", "").strip()
    if title_zh and title_zh != it.title:
        return title_zh, it.title
    return it.title, ""


# ============ HTML 片段 ============

def _banner(today: date, *, new_count: int, total_seen: int) -> str:
    return f"""
<div style="background:linear-gradient(135deg,#0f172a 0%,#1e293b 50%,#334155 100%);color:#fff;padding:28px 22px;border-radius:14px;margin-bottom:18px;box-shadow:0 8px 24px rgba(15,23,42,0.18);">
  <div style="display:inline-block;background:rgba(255,255,255,0.12);font-size:11px;letter-spacing:2px;padding:4px 10px;border-radius:4px;margin-bottom:10px;">DAILY · 北京 10:00</div>
  <div style="font-size:24px;font-weight:800;letter-spacing:0.5px;line-height:1.3;">🛰️ AI News Radar</div>
  <div style="font-size:14px;margin-top:6px;opacity:0.85;">{today.isoformat()} · 自动汇编自 RSS / Hacker News / Reddit / GitHub Trending</div>
  <div style="margin-top:14px;display:flex;gap:10px;flex-wrap:wrap;">
    <div style="background:rgba(255,255,255,0.1);padding:8px 14px;border-radius:8px;">
      <div style="font-size:11px;opacity:0.7;">今日新增</div>
      <div style="font-size:20px;font-weight:800;">{new_count}<span style="font-size:12px;font-weight:400;opacity:0.7;"> 条</span></div>
    </div>
    <div style="background:rgba(255,255,255,0.1);padding:8px 14px;border-radius:8px;">
      <div style="font-size:11px;opacity:0.7;">累计追踪</div>
      <div style="font-size:20px;font-weight:800;">{total_seen}<span style="font-size:12px;font-weight:400;opacity:0.7;"> 条</span></div>
    </div>
  </div>
</div>
""".strip()


def _highlight_card(it: Item, n: int) -> str:
    image = (it.extra or {}).get("image") or ""
    title_main, title_sub = _display_title(it)
    summary = it.summary if _has_real_summary(it.summary) else ""

    if image:
        cover = (
            f'<div style="position:relative;">'
            f'<img src="{_esc(image)}" alt="" style="width:100%;max-height:240px;object-fit:cover;border-radius:10px 10px 0 0;display:block;" />'
            f'<div style="position:absolute;top:12px;left:12px;background:rgba(0,0,0,0.65);color:#fff;font-size:12px;font-weight:700;padding:4px 10px;border-radius:14px;">⭐ TOP {n}</div>'
            f'</div>'
        )
    else:
        cover = (
            f'<div style="background:{_palette_for(it.source)};height:120px;border-radius:10px 10px 0 0;display:flex;align-items:center;justify-content:center;">'
            f'<div style="background:rgba(0,0,0,0.4);color:#fff;font-size:13px;font-weight:700;padding:6px 14px;border-radius:14px;">⭐ TOP {n}</div>'
            f'</div>'
        )

    sub_html = (
        f'<div style="color:{_MUTED};font-size:12px;margin-top:4px;font-style:italic;">{_esc(title_sub)}</div>'
        if title_sub else ""
    )
    summary_html = (
        f'<div style="color:{_TEXT_2};font-size:14px;line-height:1.75;margin-top:10px;">{_esc(summary)}</div>'
        if summary else ""
    )
    score = _score_text(it)
    score_html = f'<span style="color:{_MUTED};font-size:12px;margin-left:8px;">· {_esc(score)}</span>' if score else ""

    return f"""
<div style="background:{_CARD_BG};border-radius:10px;overflow:hidden;margin-bottom:16px;box-shadow:0 4px 12px rgba(0,0,0,0.06);border:1px solid {_BORDER};">
  {cover}
  <div style="padding:16px 16px 18px;">
    <div style="margin-bottom:8px;">{_hot_badge(it)}{_source_badge(it)}{score_html}</div>
    <a href="{_esc(it.url)}" style="color:{_TEXT};text-decoration:none;">
      <div style="font-size:18px;font-weight:700;line-height:1.45;">{_esc(title_main)}</div>
    </a>
    {sub_html}
    {summary_html}
    <div style="margin-top:12px;"><a href="{_esc(it.url)}" style="display:inline-block;color:{_LINK};font-size:13px;font-weight:600;text-decoration:none;">阅读原文 →</a></div>
  </div>
</div>
""".strip()


def _section_header(key: str, count: int) -> str:
    theme = _SECTION_THEME.get(key, {"gradient": "#1a202c", "color": "#1a202c", "subtitle": ""})
    title = SECTION_TITLES.get(key, key)
    return f"""
<div style="background:{theme['gradient']};color:#fff;padding:14px 16px;border-radius:10px 10px 0 0;margin:24px 0 0;">
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px;">
    <div style="font-size:17px;font-weight:800;">{_esc(title)}</div>
    <div style="background:rgba(255,255,255,0.22);font-size:12px;font-weight:600;padding:2px 10px;border-radius:10px;">{count} 条</div>
  </div>
  <div style="font-size:12px;opacity:0.85;margin-top:4px;">{_esc(theme['subtitle'])}</div>
</div>
""".strip()


def _item_card(it: Item, idx: int) -> str:
    image = (it.extra or {}).get("image") or ""
    title_main, title_sub = _display_title(it)
    summary = it.summary if _has_real_summary(it.summary) else ""

    # 序号徽章
    num_badge = (
        f'<div style="display:inline-block;width:22px;height:22px;line-height:22px;text-align:center;'
        f'background:#0f172a;color:#fff;border-radius:50%;font-size:11px;font-weight:700;margin-right:8px;">{idx}</div>'
    )

    # 缩略图：固定 96x96，没图就用渐变占位
    if image:
        thumb = (
            f'<td style="width:96px;vertical-align:top;padding-right:14px;">'
            f'<img src="{_esc(image)}" alt="" style="width:96px;height:96px;object-fit:cover;border-radius:8px;display:block;" />'
            f'</td>'
        )
    else:
        thumb = (
            f'<td style="width:96px;vertical-align:top;padding-right:14px;">'
            f'<div style="width:96px;height:96px;border-radius:8px;background:{_palette_for(it.source)};display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:700;text-align:center;line-height:1.3;padding:6px;box-sizing:border-box;">{_esc(it.source[:12])}</div>'
            f'</td>'
        )

    sub_html = (
        f'<div style="color:{_MUTED};font-size:11px;margin-top:3px;font-style:italic;">{_esc(title_sub[:80])}</div>'
        if title_sub else ""
    )
    # 防御性截断（DeepSeek 偶尔会返回 200+ 字）
    if summary and len(summary) > 240:
        summary = summary[:240].rstrip() + "…"
    summary_html = (
        f'<div style="color:{_TEXT_2};font-size:13px;line-height:1.7;margin-top:8px;">{_esc(summary)}</div>'
        if summary else ""
    )
    score = _score_text(it)
    score_html = f'<span style="color:{_MUTED};font-size:11px;margin-left:6px;">· {_esc(score)}</span>' if score else ""
    t = _fmt_time(it.published_at if isinstance(it.published_at, str) else None)
    time_html = f'<span style="color:{_MUTED};font-size:11px;margin-left:6px;">· {_esc(t)}</span>' if t else ""

    return f"""
<div style="background:{_CARD_BG};border:1px solid {_BORDER};border-top:none;padding:14px 16px;">
  <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
    <tr>
      {thumb}
      <td style="vertical-align:top;">
        <div style="margin-bottom:6px;">{_hot_badge(it)}{_source_badge(it)}{score_html}{time_html}</div>
        <a href="{_esc(it.url)}" style="color:{_TEXT};text-decoration:none;">
          <div style="font-size:15px;font-weight:700;line-height:1.5;">{num_badge}{_esc(title_main)}</div>
        </a>
        {sub_html}
        {summary_html}
      </td>
    </tr>
  </table>
</div>
""".strip()


def _section_footer() -> str:
    return f'<div style="height:1px;background:{_BORDER};border-radius:0 0 10px 10px;margin-bottom:6px;"></div>'


def _footer() -> str:
    web_link = ""
    if WEB_URL:
        web_link = (
            f'<div style="text-align:center;margin:28px 0 16px;">'
            f'<a href="{_esc(WEB_URL)}" style="display:inline-block;background:linear-gradient(135deg,#0f172a,#334155);color:#fff;padding:13px 28px;border-radius:8px;font-size:14px;font-weight:600;text-decoration:none;box-shadow:0 4px 12px rgba(15,23,42,0.2);">📖 查看完整列表（网页版）</a>'
            f'</div>'
        )
    return f"""
{web_link}
<div style="color:{_MUTED};font-size:11px;text-align:center;margin-top:16px;line-height:1.7;">
  自动抓取 · RSS / Hacker News / Reddit / GitHub Trending<br/>
  内容由 AI 自动整理翻译 · 仅供参考<br/>
  <span style="opacity:0.7;">每日 10:00 北京时间更新</span>
</div>
""".strip()


# ============ 必看挑选 ============

def _pick_highlights(buckets: dict) -> list[Item]:
    """跨板块挑 N 条「今日必看」：
    - 优先级：news ≈ models > products > github
    - 每个板块至少 1 条（如该板块有内容），避免被某一类垄断
    - GitHub 最多 1 条（开源项目不算「头条新闻」）
    - 同板块内按 score 排序，但有图+有真摘要的轻微加权
    """
    def candidate_score(it: Item) -> tuple:
        has_image = 1 if (it.extra or {}).get("image") else 0
        has_summary = 1 if _has_real_summary(it.summary) else 0
        return ((it.score or 0), has_image + has_summary)

    quotas = {NEWS: max(2, PUSH_HIGHLIGHT_COUNT // 3),
              MODELS: max(2, PUSH_HIGHLIGHT_COUNT // 3),
              PRODUCTS: max(1, PUSH_HIGHLIGHT_COUNT // 4),
              GITHUB: 1}

    picked: list[Item] = []
    for key in (NEWS, MODELS, PRODUCTS, GITHUB):
        items = sorted(buckets.get(key, []), key=candidate_score, reverse=True)
        picked.extend(items[: quotas[key]])
    # 用 url 去重
    seen = set()
    uniq = []
    for it in picked:
        if it.url not in seen:
            seen.add(it.url)
            uniq.append(it)
    # 整体再按 score 倒序，并截断
    uniq.sort(key=lambda x: x.score or 0, reverse=True)
    return uniq[:PUSH_HIGHLIGHT_COUNT]


# ============ 主入口 ============

def _render_with_caps(buckets: dict, today: date, *, new_count: int, total_seen: int,
                      per_section: int, highlight_count: int) -> str:
    parts: list[str] = []
    parts.append(
        f'<div style="background:{_PAGE_BG};padding:14px;font-family:-apple-system,BlinkMacSystemFont,'
        f'\'PingFang SC\',\'Hiragino Sans GB\',\'Microsoft YaHei\',sans-serif;color:{_TEXT};">'
    )
    parts.append(_banner(today, new_count=new_count, total_seen=total_seen))

    highlights = _pick_highlights(buckets)[:highlight_count]
    if highlights:
        parts.append(
            f'<div style="background:linear-gradient(135deg,#ff6b35,#f7931e);color:#fff;padding:14px 16px;border-radius:10px;margin:18px 0 12px;">'
            f'<div style="font-size:17px;font-weight:800;">⭐ 今日必看</div>'
            f'<div style="font-size:12px;opacity:0.9;margin-top:3px;">编辑精选最值得花 30 秒读的几条</div>'
            f'</div>'
        )
        for i, it in enumerate(highlights, start=1):
            parts.append(_highlight_card(it, i))

    highlight_urls = {it.url for it in highlights}
    for key in (PRODUCTS, NEWS, MODELS, GITHUB):
        items = buckets.get(key, [])
        remaining = [it for it in items if it.url not in highlight_urls][:per_section]
        if not remaining:
            continue
        parts.append(_section_header(key, len(remaining)))
        for i, it in enumerate(remaining, start=1):
            parts.append(_item_card(it, i))
        parts.append(_section_footer())

    parts.append(_footer())
    parts.append("</div>")
    return "\n".join(parts)


def render_html(buckets: dict, today: date, *, new_count: int, total_seen: int) -> str:
    """自适应裁剪：从配置上限开始，逐级缩减直到内容 ≤ 60KB（PushPlus 64KB 上限留 4KB 余量）。"""
    SOFT_LIMIT = 60_000
    # 候选阶梯：(per_section, highlight_count)
    ladders = [
        (PUSH_TOP_PER_SECTION, PUSH_HIGHLIGHT_COUNT),
        (8, 6),
        (7, 5),
        (6, 5),
        (5, 4),
        (4, 3),
    ]
    last = ""
    for per_section, hi in ladders:
        last = _render_with_caps(
            buckets, today, new_count=new_count, total_seen=total_seen,
            per_section=per_section, highlight_count=hi,
        )
        if len(last.encode("utf-8")) <= SOFT_LIMIT:
            return last
    return last


def push(buckets: dict, today: date, *, new_count: int, total_seen: int = 0) -> bool:
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not token:
        return False
    if new_count == 0:
        print("📭 今日新增为 0，跳过微信推送。")
        return False

    title = f"🛰️ AI News · {today.isoformat()} · {new_count} 条精选"
    content = render_html(buckets, today, new_count=new_count, total_seen=total_seen)

    body_size = len(content.encode("utf-8"))
    if body_size > 60_000:
        print(f"⚠️  推送内容过大 ({body_size} 字节)，截断到 60KB")
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
