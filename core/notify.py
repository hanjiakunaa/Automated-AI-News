"""把每日报告渲染成杂志级 HTML 推到微信（PushPlus）。

启用方式：在环境变量里设置 PUSHPLUS_TOKEN 即可。
缺 token、网络错误、PushPlus 报错——一律静默警告，不让主流程失败。

PushPlus 文档：https://www.pushplus.plus/doc/
- 接口：POST http://www.pushplus.plus/send
- body：{ token, title, content, template: "html" }
- 单条限制 64KB

视觉准则（WeChat WebView 兼容性）：
- 只用 inline 样式（<style> 标签会被剥）
- table 优先于 flex（老版微信兼容性更好）
- 不用 JS / animation / transition
- 图片走原图 URL，不内联 base64

设计目标（v0.3）：
- 顶部封面图墙（mosaic）+ 杂志级 hero 卡 + 4 个板块各异风格
- 每条卡片信息密度高（150-220 字摘要 + key_points tag）
- 无图条目用首字母 logo 兜底，不再是干巴巴的渐变色块
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
_C = {
    "bg_page": "#f5f7fa",
    "bg_card": "#ffffff",
    "bg_dim":  "#f8fafc",
    "border":  "#e5e9ef",
    "text":    "#0f172a",
    "text_2":  "#334155",
    "text_3":  "#64748b",
    "muted":   "#94a3b8",
    "accent":  "#2563eb",
}

# 板块视觉配置（每个板块完整的色板 + 副标题）
_THEME = {
    PRODUCTS: {
        "main":     "#ff6b35",
        "soft":     "#fff5ef",
        "gradient": "linear-gradient(135deg,#ff6b35 0%,#f7931e 100%)",
        "subtitle": "今日值得关注的 AI 新品",
        "icon":     "🔥",
    },
    NEWS: {
        "main":     "#3b82f6",
        "soft":     "#eff6ff",
        "gradient": "linear-gradient(135deg,#3b82f6 0%,#1e40af 100%)",
        "subtitle": "AI 圈最受讨论的新闻和事件",
        "icon":     "📰",
    },
    MODELS: {
        "main":     "#8b5cf6",
        "soft":     "#f5f3ff",
        "gradient": "linear-gradient(135deg,#8b5cf6 0%,#6d28d9 100%)",
        "subtitle": "前沿大模型的发布、研究与社区讨论",
        "icon":     "🚀",
    },
    GITHUB: {
        "main":     "#22c55e",
        "soft":     "#f0fdf4",
        "gradient": "linear-gradient(135deg,#22c55e 0%,#15803d 100%)",
        "subtitle": "今日开发者最关注的开源项目",
        "icon":     "💻",
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
    "linear-gradient(135deg,#fbbf24 0%,#f59e0b 100%)",
    "linear-gradient(135deg,#60a5fa 0%,#2563eb 100%)",
    "linear-gradient(135deg,#a78bfa 0%,#7c3aed 100%)",
    "linear-gradient(135deg,#34d399 0%,#059669 100%)",
    "linear-gradient(135deg,#fb7185 0%,#e11d48 100%)",
    "linear-gradient(135deg,#f472b6 0%,#db2777 100%)",
    "linear-gradient(135deg,#22d3ee 0%,#0891b2 100%)",
    "linear-gradient(135deg,#fb923c 0%,#ea580c 100%)",
]

_STATS_RE = re.compile(r"^(⬆|💬|⭐)\s*\d+(\s*[·•]\s*(⬆|💬|⭐)\s*\d+)*$")
_GH_LANG_COLOR = {
    "Python": "#3572A5", "TypeScript": "#3178c6", "JavaScript": "#f1e05a",
    "Go": "#00ADD8", "Rust": "#dea584", "C++": "#f34b7d", "C": "#555555",
    "Java": "#b07219", "Ruby": "#701516", "Swift": "#F05138",
    "Shell": "#89e051", "Jupyter Notebook": "#DA5B0B",
}


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


def _fmt_time(iso) -> str:
    if not iso:
        return ""
    if isinstance(iso, datetime):
        return iso.strftime("%m-%d %H:%M")
    if isinstance(iso, str):
        try:
            return datetime.fromisoformat(iso).strftime("%m-%d %H:%M")
        except Exception:
            return ""
    return ""


def _palette_for(seed: str) -> str:
    h = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16)
    return _SOURCE_PALETTE[h % len(_SOURCE_PALETTE)]


def _section_of(it: Item) -> str:
    """反推条目所属板块（不调用 classify 避免循环依赖）。基于 source_kind 粗判。"""
    if it.source_kind == "github":
        return GITHUB
    if it.source == "Product Hunt":
        return PRODUCTS
    return NEWS


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
        f'padding:2px 8px;border-radius:10px;margin-right:6px;">{text}</span>'
    )


def _source_badge(it: Item) -> str:
    sk = it.source_kind
    bg = {"rss": "#f1f5f9", "hackernews": "#fef3c7", "reddit": "#fee2e2", "github": "#dcfce7"}.get(sk, "#f1f5f9")
    fg = {"rss": "#475569", "hackernews": "#92400e", "reddit": "#b91c1c", "github": "#15803d"}.get(sk, "#475569")
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
    title_zh = (it.extra or {}).get("title_zh", "").strip() if it.extra else ""
    if title_zh and title_zh != it.title:
        return title_zh, it.title
    return it.title, ""


def _key_points_tags(it: Item, color: str) -> str:
    """渲染 AI 抽出的 2-3 个 key_points 短词标签。"""
    pts = (it.extra or {}).get("key_points") or []
    if not isinstance(pts, list) or not pts:
        return ""
    chips = []
    for p in pts[:3]:
        s = str(p).strip()
        if not s:
            continue
        chips.append(
            f'<span style="display:inline-block;background:{color}1a;color:{color};font-size:11px;'
            f'font-weight:700;padding:3px 9px;border-radius:10px;margin:0 6px 4px 0;letter-spacing:0.3px;">'
            f'#{_esc(s)}</span>'
        )
    if not chips:
        return ""
    return f'<div style="margin-top:8px;line-height:1.9;">{"".join(chips)}</div>'


def _has_image(it: Item) -> bool:
    return bool((it.extra or {}).get("image"))


def _initial_block(it: Item, *, w: int = 96, h: int = 96, fontsize: int = 28) -> str:
    """无图条目兜底：用来源首字母 + 渐变色块组成"伪 logo"。"""
    src = (it.source or "?").strip()
    # 取来源前 1-2 个有意义字符（中文一字、英文取大写首字）
    if any('一' <= c <= '鿿' for c in src):
        ch = src[0]
    else:
        ch = "".join(c for c in src if c.isalpha())[:1].upper() or src[:1].upper()
    grad = _palette_for(it.source)
    return (
        f'<div style="width:{w}px;height:{h}px;border-radius:10px;background:{grad};'
        f'display:flex;align-items:center;justify-content:center;color:#fff;'
        f'font-size:{fontsize}px;font-weight:800;font-family:-apple-system,sans-serif;'
        f'letter-spacing:1px;text-shadow:0 2px 6px rgba(0,0,0,0.18);box-sizing:border-box;">'
        f'{_esc(ch)}</div>'
    )


def _img_or_initial(it: Item, *, w: int, h: int, radius: int = 10, fontsize: int = 28) -> str:
    """有图就渲染图，没图渲染首字母块。统一尺寸。"""
    image = (it.extra or {}).get("image") or ""
    if image:
        return (
            f'<img src="{_esc(image)}" alt="" style="width:{w}px;height:{h}px;object-fit:cover;'
            f'border-radius:{radius}px;display:block;" />'
        )
    src = (it.source or "?").strip()
    if any('一' <= c <= '鿿' for c in src):
        ch = src[0]
    else:
        ch = "".join(c for c in src if c.isalpha())[:1].upper() or src[:1].upper()
    grad = _palette_for(it.source)
    return (
        f'<div style="width:{w}px;height:{h}px;border-radius:{radius}px;background:{grad};'
        f'display:flex;align-items:center;justify-content:center;color:#fff;'
        f'font-size:{fontsize}px;font-weight:800;letter-spacing:1px;'
        f'text-shadow:0 2px 6px rgba(0,0,0,0.18);box-sizing:border-box;">'
        f'{_esc(ch)}</div>'
    )


# ============ 顶部三件套 ============

def _banner(today: date, *, new_count: int, total_seen: int, buckets: dict, image_count: int) -> str:
    # 4 板块横向条形图（按各板块条数比例）
    counts = [(SECTION_TITLES.get(k, k), len(buckets.get(k, [])), _THEME[k]["main"]) for k in (PRODUCTS, NEWS, MODELS, GITHUB)]
    max_n = max((c for _, c, _ in counts), default=1) or 1
    bars_html = ""
    for label, n, color in counts:
        pct = max(6, int(n / max_n * 100)) if n else 4
        bars_html += (
            f'<div style="margin-top:8px;">'
            f'  <div style="display:flex;justify-content:space-between;font-size:11px;color:rgba(255,255,255,0.85);margin-bottom:3px;">'
            f'    <span>{_esc(label)}</span><span style="font-weight:700;">{n}</span>'
            f'  </div>'
            f'  <div style="height:6px;background:rgba(255,255,255,0.15);border-radius:3px;overflow:hidden;">'
            f'    <div style="width:{pct}%;height:6px;background:{color};border-radius:3px;"></div>'
            f'  </div>'
            f'</div>'
        )
    cov_pct = int(image_count * 100 / max(new_count, 1)) if new_count else 0
    return f"""
<div style="background:linear-gradient(135deg,#0f172a 0%,#1e293b 60%,#3730a3 100%);color:#fff;padding:26px 22px 22px;border-radius:16px;margin-bottom:18px;box-shadow:0 12px 28px rgba(15,23,42,0.22);">
  <div style="display:inline-block;background:rgba(255,255,255,0.14);font-size:11px;letter-spacing:2.5px;padding:4px 10px;border-radius:4px;margin-bottom:12px;font-weight:600;">DAILY · 北京 10:00</div>
  <div style="font-size:26px;font-weight:800;letter-spacing:0.5px;line-height:1.25;">🛰️ AI News Radar</div>
  <div style="font-size:13px;margin-top:6px;opacity:0.82;">{today.isoformat()} · 自动汇编自 RSS / Hacker News / Reddit / GitHub Trending</div>
  <table cellpadding="0" cellspacing="0" border="0" style="width:100%;margin-top:14px;border-collapse:separate;border-spacing:8px 0;">
    <tr>
      <td style="background:rgba(255,255,255,0.1);padding:10px 12px;border-radius:8px;width:33%;">
        <div style="font-size:11px;opacity:0.7;">今日新增</div>
        <div style="font-size:22px;font-weight:800;line-height:1.1;margin-top:2px;">{new_count}<span style="font-size:11px;font-weight:400;opacity:0.7;"> 条</span></div>
      </td>
      <td style="background:rgba(255,255,255,0.1);padding:10px 12px;border-radius:8px;width:33%;">
        <div style="font-size:11px;opacity:0.7;">累计追踪</div>
        <div style="font-size:22px;font-weight:800;line-height:1.1;margin-top:2px;">{total_seen}<span style="font-size:11px;font-weight:400;opacity:0.7;"> 条</span></div>
      </td>
      <td style="background:rgba(255,255,255,0.1);padding:10px 12px;border-radius:8px;width:34%;">
        <div style="font-size:11px;opacity:0.7;">图片覆盖</div>
        <div style="font-size:22px;font-weight:800;line-height:1.1;margin-top:2px;">{cov_pct}<span style="font-size:11px;font-weight:400;opacity:0.7;">%</span></div>
      </td>
    </tr>
  </table>
  <div style="margin-top:14px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.1);">
    <div style="font-size:11px;opacity:0.7;letter-spacing:1px;font-weight:600;">板块分布</div>
    {bars_html}
  </div>
</div>
""".strip()


def _cover_wall(buckets: dict) -> str:
    """从各板块挑前 6 张图，用 2×3 表格做封面图墙。无图时整段不渲染。"""
    images: list[tuple[Item, str]] = []
    seen_urls = set()
    # 优先级：必看候选（高分有图） > 各板块第一名
    for key in (NEWS, MODELS, PRODUCTS, GITHUB):
        for it in buckets.get(key, []):
            if not _has_image(it) or it.url in seen_urls:
                continue
            images.append((it, _THEME[key]["main"]))
            seen_urls.add(it.url)
            if len(images) >= 6:
                break
        if len(images) >= 6:
            break
    if len(images) < 3:
        return ""

    # 补满 6 张（不足时不补，按实际数量布局）
    cells = ""
    rows = [images[:3], images[3:6]] if len(images) >= 6 else [images[:3], images[3:]]
    for row in rows:
        if not row:
            continue
        tds = ""
        for it, color in row:
            img = (it.extra or {}).get("image", "")
            title_main, _ = _display_title(it)
            tds += (
                f'<td style="width:33.33%;padding:3px;vertical-align:top;">'
                f'  <a href="{_esc(it.url)}" style="text-decoration:none;display:block;">'
                f'    <div style="position:relative;border-radius:8px;overflow:hidden;background:#000;">'
                f'      <img src="{_esc(img)}" alt="" style="width:100%;height:88px;object-fit:cover;display:block;opacity:0.95;" />'
                f'      <div style="position:absolute;inset:0;background:linear-gradient(180deg,transparent 30%,rgba(0,0,0,0.7) 100%);"></div>'
                f'      <div style="position:absolute;top:6px;left:6px;width:8px;height:8px;border-radius:50%;background:{color};box-shadow:0 0 0 2px rgba(255,255,255,0.9);"></div>'
                f'      <div style="position:absolute;bottom:5px;left:7px;right:7px;color:#fff;font-size:11px;font-weight:600;line-height:1.3;text-shadow:0 1px 2px rgba(0,0,0,0.6);'
                f'overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">'
                f'{_esc(title_main[:40])}</div>'
                f'    </div>'
                f'  </a>'
                f'</td>'
            )
        cells += f'<tr>{tds}</tr>'

    return f"""
<div style="background:{_C['bg_card']};border-radius:12px;padding:14px;margin-bottom:18px;border:1px solid {_C['border']};">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">
    <div style="font-size:14px;font-weight:800;color:{_C['text']};letter-spacing:0.5px;">📸 今日封面图墙</div>
    <div style="font-size:11px;color:{_C['muted']};">点击直达原文</div>
  </div>
  <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:separate;border-spacing:0;">
    {cells}
  </table>
</div>
""".strip()


# ============ 杂志级 Hero ============

def _hero_top(it: Item) -> str:
    """第 1 条：超大封面 + 标题压底 + 摘要一两句。"""
    image = (it.extra or {}).get("image", "")
    title_main, title_sub = _display_title(it)
    summary = it.summary if _has_real_summary(it.summary) else ""
    if summary and len(summary) > 120:
        summary = summary[:120].rstrip() + "…"

    if image:
        cover = (
            f'<div style="position:relative;">'
            f'  <img src="{_esc(image)}" alt="" style="width:100%;height:280px;object-fit:cover;display:block;" />'
            f'  <div style="position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,0.0) 35%,rgba(0,0,0,0.85) 100%);"></div>'
            f'  <div style="position:absolute;top:14px;left:14px;background:#fbbf24;color:#1f2937;font-size:11px;font-weight:800;padding:4px 12px;border-radius:14px;letter-spacing:1px;">⭐ TOP 1 头条</div>'
            f'  <div style="position:absolute;bottom:14px;left:16px;right:16px;color:#fff;">'
            f'    <div style="font-size:20px;font-weight:800;line-height:1.4;text-shadow:0 2px 8px rgba(0,0,0,0.45);">{_esc(title_main)}</div>'
            f'  </div>'
            f'</div>'
        )
    else:
        cover = (
            f'<div style="background:{_palette_for(it.source)};padding:36px 18px;color:#fff;">'
            f'  <div style="display:inline-block;background:#fbbf24;color:#1f2937;font-size:11px;font-weight:800;padding:4px 12px;border-radius:14px;letter-spacing:1px;margin-bottom:10px;">⭐ TOP 1 头条</div>'
            f'  <div style="font-size:22px;font-weight:800;line-height:1.4;text-shadow:0 2px 8px rgba(0,0,0,0.3);">{_esc(title_main)}</div>'
            f'</div>'
        )

    sub = f'<div style="color:{_C["text_3"]};font-size:11px;font-style:italic;margin-top:6px;">{_esc(title_sub[:90])}</div>' if title_sub else ""
    summary_html = f'<div style="color:{_C["text_2"]};font-size:14px;line-height:1.8;margin-top:10px;">{_esc(summary)}</div>' if summary else ""
    tags = _key_points_tags(it, _C["accent"])

    return f"""
<div style="background:{_C['bg_card']};border-radius:14px;overflow:hidden;margin-bottom:14px;box-shadow:0 6px 18px rgba(15,23,42,0.10);">
  {cover}
  <div style="padding:14px 16px 18px;">
    <div style="margin-bottom:6px;">{_hot_badge(it)}{_source_badge(it)}<span style="color:{_C['muted']};font-size:11px;margin-left:6px;">{_esc(_score_text(it))}</span></div>
    {sub}
    {summary_html}
    {tags}
    <div style="margin-top:14px;"><a href="{_esc(it.url)}" style="display:inline-block;background:{_C['text']};color:#fff;font-size:13px;font-weight:600;padding:8px 18px;border-radius:6px;text-decoration:none;">阅读原文 →</a></div>
  </div>
</div>
""".strip()


def _hero_pair(it_a: Item, it_b: Item) -> str:
    """第 2、3 条：并排 2 列中卡（用 table 兼容老微信）。"""
    def col(it: Item) -> str:
        title_main, _ = _display_title(it)
        summary = it.summary if _has_real_summary(it.summary) else ""
        if summary and len(summary) > 80:
            summary = summary[:80].rstrip() + "…"
        cover = _img_or_initial(it, w=300, h=128, radius=8, fontsize=42)
        # 上面那个 _img_or_initial 返回固定宽，但表格 td 会自动伸缩——img 用 max-width 兜底
        cover = cover.replace(f'width:300px', 'width:100%').replace(f'height:128px', 'height:128px')
        sm = f'<div style="color:{_C["text_2"]};font-size:12px;line-height:1.6;margin-top:6px;">{_esc(summary)}</div>' if summary else ""
        return (
            f'<a href="{_esc(it.url)}" style="text-decoration:none;color:inherit;display:block;">'
            f'  <div style="background:{_C["bg_card"]};border:1px solid {_C["border"]};border-radius:10px;overflow:hidden;">'
            f'    <div style="position:relative;">'
            f'      {cover}'
            f'      <div style="position:absolute;top:8px;left:8px;background:rgba(0,0,0,0.7);color:#fff;font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px;">必看</div>'
            f'    </div>'
            f'    <div style="padding:10px 12px 12px;">'
            f'      <div style="font-size:14px;font-weight:700;color:{_C["text"]};line-height:1.45;'
            f'overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">'
            f'{_esc(title_main)}</div>'
            f'      {sm}'
            f'    </div>'
            f'  </div>'
            f'</a>'
        )

    return f"""
<table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:separate;border-spacing:8px 0;margin-bottom:10px;">
  <tr>
    <td style="width:50%;vertical-align:top;">{col(it_a)}</td>
    <td style="width:50%;vertical-align:top;">{col(it_b)}</td>
  </tr>
</table>
""".strip()


def _hero_compact(it: Item, n: int) -> str:
    """第 4-6 条：紧凑横长卡（80×80 图 + 紧凑文字）。"""
    title_main, _ = _display_title(it)
    summary = it.summary if _has_real_summary(it.summary) else ""
    if summary and len(summary) > 90:
        summary = summary[:90].rstrip() + "…"
    cover = _img_or_initial(it, w=80, h=80, radius=8, fontsize=24)
    sm = f'<div style="color:{_C["text_3"]};font-size:12px;line-height:1.55;margin-top:4px;'\
         f'overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">'\
         f'{_esc(summary)}</div>' if summary else ""
    return f"""
<a href="{_esc(it.url)}" style="text-decoration:none;color:inherit;display:block;">
  <div style="background:{_C['bg_card']};border:1px solid {_C['border']};border-radius:10px;padding:10px;margin-bottom:8px;">
    <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
      <tr>
        <td style="width:80px;vertical-align:top;padding-right:12px;">{cover}</td>
        <td style="vertical-align:top;">
          <div style="margin-bottom:4px;">
            <span style="display:inline-block;background:#fef3c7;color:#92400e;font-size:10px;font-weight:800;padding:2px 6px;border-radius:8px;margin-right:6px;">TOP {n}</span>
            {_source_badge(it)}
          </div>
          <div style="font-size:14px;font-weight:700;color:{_C['text']};line-height:1.45;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">{_esc(title_main)}</div>
          {sm}
        </td>
      </tr>
    </table>
  </div>
</a>
""".strip()


def _render_hero(highlights: list[Item]) -> str:
    if not highlights:
        return ""
    parts = [
        f'<div style="background:linear-gradient(135deg,#fbbf24 0%,#f59e0b 100%);color:#fff;padding:14px 18px;border-radius:12px;margin:18px 0 12px;box-shadow:0 6px 16px rgba(245,158,11,0.25);">'
        f'  <div style="font-size:18px;font-weight:800;letter-spacing:0.5px;">⭐ 今日必看</div>'
        f'  <div style="font-size:12px;opacity:0.92;margin-top:3px;">编辑精选最值得花 30 秒读的几条</div>'
        f'</div>'
    ]
    parts.append(_hero_top(highlights[0]))
    if len(highlights) >= 3:
        parts.append(_hero_pair(highlights[1], highlights[2]))
    elif len(highlights) == 2:
        parts.append(_hero_top(highlights[1]))
    rest = highlights[3:]
    for i, it in enumerate(rest, start=4):
        parts.append(_hero_compact(it, i))
    return "\n".join(parts)


# ============ 4 个板块各异风格 ============

def _section_header(key: str, count: int) -> str:
    theme = _THEME[key]
    title = SECTION_TITLES.get(key, key)
    return f"""
<div style="background:{theme['gradient']};color:#fff;padding:14px 18px;border-radius:12px 12px 0 0;margin:24px 0 0;box-shadow:0 -2px 12px rgba(0,0,0,0.04);">
  <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
    <tr>
      <td style="vertical-align:middle;">
        <div style="font-size:18px;font-weight:800;letter-spacing:0.5px;">{_esc(title)}</div>
        <div style="font-size:12px;opacity:0.88;margin-top:3px;">{_esc(theme['subtitle'])}</div>
      </td>
      <td style="vertical-align:middle;text-align:right;">
        <span style="background:rgba(255,255,255,0.22);font-size:12px;font-weight:700;padding:3px 12px;border-radius:12px;letter-spacing:0.5px;">{count} 条</span>
      </td>
    </tr>
  </table>
</div>
""".strip()


def _section_body_open() -> str:
    return f'<div style="background:{_C["bg_card"]};border:1px solid {_C["border"]};border-top:none;border-radius:0 0 12px 12px;padding:6px;margin-bottom:6px;">'


def _section_body_close() -> str:
    return '</div>'


# ----- 产品（双列网格） -----

def _card_product(it: Item) -> str:
    title_main, _ = _display_title(it)
    summary = it.summary if _has_real_summary(it.summary) else ""
    if summary and len(summary) > 90:
        summary = summary[:90].rstrip() + "…"
    cover = _img_or_initial(it, w=300, h=120, radius=8, fontsize=42)
    cover = cover.replace('width:300px', 'width:100%')
    tags = _key_points_tags(it, _THEME[PRODUCTS]["main"])
    sm = f'<div style="color:{_C["text_2"]};font-size:12px;line-height:1.6;margin-top:6px;'\
         f'overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">'\
         f'{_esc(summary)}</div>' if summary else ""
    return (
        f'<a href="{_esc(it.url)}" style="text-decoration:none;color:inherit;display:block;height:100%;">'
        f'  <div style="background:{_C["bg_card"]};border:1px solid {_C["border"]};border-radius:10px;overflow:hidden;height:100%;">'
        f'    {cover}'
        f'    <div style="padding:10px 12px 12px;">'
        f'      <div style="font-size:14px;font-weight:700;color:{_C["text"]};line-height:1.45;'
        f'overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">'
        f'{_esc(title_main)}</div>'
        f'      {sm}{tags}'
        f'    </div>'
        f'  </div>'
        f'</a>'
    )


def _render_products(items: list[Item]) -> str:
    if not items:
        return ""
    parts = [_section_header(PRODUCTS, len(items)), _section_body_open()]
    # 两列一行
    rows = [items[i:i+2] for i in range(0, len(items), 2)]
    for row in rows:
        cells = ""
        for it in row:
            cells += f'<td style="width:50%;vertical-align:top;padding:6px;">{_card_product(it)}</td>'
        if len(row) == 1:
            cells += '<td style="width:50%;"></td>'
        parts.append(
            f'<table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:separate;border-spacing:0;">'
            f'  <tr>{cells}</tr>'
            f'</table>'
        )
    parts.append(_section_body_close())
    return "\n".join(parts)


# ----- 新闻（引用式列表） -----

def _card_news(it: Item, idx: int) -> str:
    color = _THEME[NEWS]["main"]
    title_main, title_sub = _display_title(it)
    summary = it.summary if _has_real_summary(it.summary) else ""
    if summary and len(summary) > 320:
        summary = summary[:320].rstrip() + "…"
    has_img = _has_image(it)
    image = (it.extra or {}).get("image", "") if has_img else ""

    # 顶部图片（如有）— 横向 16:5 比例
    top_img = (
        f'<img src="{_esc(image)}" alt="" style="width:100%;height:140px;object-fit:cover;display:block;border-radius:8px 8px 0 0;" />'
        if has_img else ""
    )

    sub = f'<div style="color:{_C["text_3"]};font-size:11px;font-style:italic;margin-top:4px;">{_esc(title_sub[:90])}</div>' if title_sub else ""
    summary_html = (
        f'<div style="background:{_THEME[NEWS]["soft"]};border-left:3px solid {color};color:{_C["text_2"]};'
        f'font-size:13px;line-height:1.78;padding:10px 12px;border-radius:0 6px 6px 0;margin-top:10px;">{_esc(summary)}</div>'
        if summary else ""
    )
    tags = _key_points_tags(it, color)
    score = _score_text(it)
    t = _fmt_time(it.published_at)
    meta_bits = []
    if score:
        meta_bits.append(f'<span style="color:{_C["muted"]};font-size:11px;">{_esc(score)}</span>')
    if t:
        meta_bits.append(f'<span style="color:{_C["muted"]};font-size:11px;">⏱ {_esc(t)}</span>')
    meta = " · ".join(meta_bits)
    meta_html = f'<div style="margin-top:10px;">{meta}</div>' if meta else ""

    return f"""
<a href="{_esc(it.url)}" style="text-decoration:none;color:inherit;display:block;">
  <div style="background:{_C['bg_card']};border:1px solid {_C['border']};border-left:4px solid {color};border-radius:8px;margin:8px 6px;overflow:hidden;">
    {top_img}
    <div style="padding:14px 16px;">
      <div style="margin-bottom:6px;">
        <span style="display:inline-block;width:22px;height:22px;line-height:22px;text-align:center;background:{color};color:#fff;border-radius:6px;font-size:11px;font-weight:800;margin-right:8px;">{idx}</span>
        {_hot_badge(it)}{_source_badge(it)}
      </div>
      <div style="font-size:15px;font-weight:700;color:{_C['text']};line-height:1.55;">{_esc(title_main)}</div>
      {sub}{summary_html}{tags}{meta_html}
    </div>
  </div>
</a>
""".strip()


def _render_news(items: list[Item]) -> str:
    if not items:
        return ""
    parts = [_section_header(NEWS, len(items)), _section_body_open()]
    for i, it in enumerate(items, start=1):
        parts.append(_card_news(it, i))
    parts.append(_section_body_close())
    return "\n".join(parts)


# ----- 模型（重点卡） -----

def _card_models(it: Item, idx: int) -> str:
    color = _THEME[MODELS]["main"]
    title_main, title_sub = _display_title(it)
    summary = it.summary if _has_real_summary(it.summary) else ""
    if summary and len(summary) > 320:
        summary = summary[:320].rstrip() + "…"
    has_img = _has_image(it)
    image = (it.extra or {}).get("image", "") if has_img else ""

    # 顶部色条
    top_bar = f'<div style="height:4px;background:{_THEME[MODELS]["gradient"]};"></div>'

    # 缩略图
    thumb = ""
    if has_img:
        thumb = (
            f'<td style="width:104px;vertical-align:top;padding-right:14px;">'
            f'<img src="{_esc(image)}" alt="" style="width:104px;height:104px;object-fit:cover;border-radius:8px;display:block;" />'
            f'</td>'
        )

    sub = f'<div style="color:{_C["text_3"]};font-size:11px;font-style:italic;margin-top:4px;">{_esc(title_sub[:90])}</div>' if title_sub else ""
    summary_html = (
        f'<div style="color:{_C["text_2"]};font-size:13px;line-height:1.75;margin-top:8px;">{_esc(summary)}</div>'
        if summary else ""
    )
    tags = _key_points_tags(it, color)
    score = _score_text(it)
    score_html = f'<span style="color:{_C["muted"]};font-size:11px;margin-left:6px;">· {_esc(score)}</span>' if score else ""

    return f"""
<a href="{_esc(it.url)}" style="text-decoration:none;color:inherit;display:block;">
  <div style="background:{_C['bg_card']};border:1px solid {_C['border']};border-radius:10px;overflow:hidden;margin:8px 6px;">
    {top_bar}
    <div style="padding:14px 16px;">
      <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
        <tr>
          {thumb}
          <td style="vertical-align:top;">
            <div style="margin-bottom:6px;">
              <span style="display:inline-block;background:{_THEME[MODELS]['soft']};color:{color};font-size:11px;font-weight:800;padding:3px 10px;border-radius:10px;margin-right:6px;letter-spacing:0.3px;">#{idx} 模型动态</span>
              {_source_badge(it)}{score_html}
            </div>
            <div style="font-size:15px;font-weight:700;color:{_C['text']};line-height:1.5;">{_esc(title_main)}</div>
            {sub}{summary_html}{tags}
          </td>
        </tr>
      </table>
    </div>
  </div>
</a>
""".strip()


def _render_models(items: list[Item]) -> str:
    if not items:
        return ""
    parts = [_section_header(MODELS, len(items)), _section_body_open()]
    for i, it in enumerate(items, start=1):
        parts.append(_card_models(it, i))
    parts.append(_section_body_close())
    return "\n".join(parts)


# ----- GitHub（代码卡风格 - 深色） -----

def _card_github(it: Item, idx: int) -> str:
    title_main, _ = _display_title(it)
    summary = it.summary if _has_real_summary(it.summary) else ""
    if summary and len(summary) > 220:
        summary = summary[:220].rstrip() + "…"
    lang = (it.extra or {}).get("language") or ""
    lang_color = _GH_LANG_COLOR.get(lang, "#22c55e")
    image = (it.extra or {}).get("image", "")
    avatar = ""
    if image:
        avatar = (
            f'<img src="{_esc(image)}" alt="" style="width:36px;height:36px;border-radius:8px;'
            f'background:#fff;display:block;" />'
        )
    else:
        avatar = (
            f'<div style="width:36px;height:36px;border-radius:8px;background:#1f2937;'
            f'display:flex;align-items:center;justify-content:center;color:#94a3b8;font-size:14px;font-weight:800;">⌘</div>'
        )

    star_html = (
        f'<div style="color:#facc15;font-size:18px;font-weight:800;line-height:1;">⭐ +{it.score}</div>'
        f'<div style="color:#94a3b8;font-size:10px;margin-top:2px;letter-spacing:0.5px;">STARS TODAY</div>'
        if it.score else ""
    )
    lang_html = (
        f'<span style="display:inline-block;color:#cbd5e1;font-size:11px;font-weight:600;margin-right:10px;">'
        f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:{lang_color};margin-right:4px;vertical-align:middle;"></span>'
        f'{_esc(lang)}</span>'
        if lang else ""
    )
    tags = _key_points_tags(it, "#22c55e")
    summary_html = (
        f'<div style="color:#cbd5e1;font-size:13px;line-height:1.7;margin-top:8px;font-family:-apple-system,BlinkMacSystemFont,sans-serif;">{_esc(summary)}</div>'
        if summary else ""
    )

    return f"""
<a href="{_esc(it.url)}" style="text-decoration:none;color:inherit;display:block;">
  <div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:14px 16px;margin:8px 6px;font-family:'SF Mono','Menlo','Consolas',monospace;">
    <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
      <tr>
        <td style="width:36px;vertical-align:top;padding-right:12px;">{avatar}</td>
        <td style="vertical-align:top;">
          <div style="font-size:11px;color:#22c55e;font-weight:700;letter-spacing:0.5px;font-family:'SF Mono',monospace;">$ git clone #{idx}</div>
          <div style="font-size:14px;color:#f1f5f9;font-weight:700;line-height:1.45;margin-top:2px;font-family:'SF Mono','Menlo',monospace;">{_esc(title_main)}</div>
        </td>
        <td style="vertical-align:top;text-align:right;padding-left:8px;">{star_html}</td>
      </tr>
    </table>
    <div style="margin-top:10px;padding-top:10px;border-top:1px solid #1e293b;">
      <div>{lang_html}<span style="color:#64748b;font-size:11px;">github.com</span></div>
      {summary_html}{tags}
    </div>
  </div>
</a>
""".strip()


def _render_github(items: list[Item]) -> str:
    if not items:
        return ""
    parts = [_section_header(GITHUB, len(items)), _section_body_open()]
    for i, it in enumerate(items, start=1):
        parts.append(_card_github(it, i))
    parts.append(_section_body_close())
    return "\n".join(parts)


def _footer() -> str:
    web_link = ""
    if WEB_URL:
        web_link = (
            f'<div style="text-align:center;margin:28px 0 16px;">'
            f'<a href="{_esc(WEB_URL)}" style="display:inline-block;background:linear-gradient(135deg,#0f172a,#334155);color:#fff;padding:13px 30px;border-radius:8px;font-size:14px;font-weight:600;text-decoration:none;box-shadow:0 4px 12px rgba(15,23,42,0.2);letter-spacing:0.5px;">📖 查看完整列表（网页版）</a>'
            f'</div>'
        )
    return f"""
{web_link}
<div style="background:{_C['bg_card']};border:1px solid {_C['border']};border-radius:10px;padding:16px;margin-top:18px;text-align:center;">
  <div style="color:{_C['text_2']};font-size:12px;line-height:1.8;">
    🛰️ <b>AI News Radar</b> · 自动抓取 + AI 翻译整理<br/>
    <span style="color:{_C['muted']};">RSS · Hacker News · Reddit · GitHub Trending</span>
  </div>
  <div style="margin-top:10px;color:{_C['muted']};font-size:11px;">每日 10:00 北京时间更新 · 仅供参考</div>
</div>
""".strip()


# ============ 必看挑选 ============

def _pick_highlights(buckets: dict, n: int) -> list[Item]:
    """跨板块挑 N 条「今日必看」。"""
    def candidate_score(it: Item) -> tuple:
        has_image = 1 if _has_image(it) else 0
        has_summary = 1 if _has_real_summary(it.summary) else 0
        has_kp = 1 if (it.extra or {}).get("key_points") else 0
        return ((it.score or 0), has_image + has_summary + has_kp)

    quotas = {NEWS: max(2, n // 3), MODELS: max(2, n // 3), PRODUCTS: max(1, n // 4), GITHUB: 1}
    picked: list[Item] = []
    for key in (NEWS, MODELS, PRODUCTS, GITHUB):
        items = sorted(buckets.get(key, []), key=candidate_score, reverse=True)
        picked.extend(items[: quotas[key]])
    seen = set()
    uniq = []
    for it in picked:
        if it.url not in seen:
            seen.add(it.url)
            uniq.append(it)
    uniq.sort(key=lambda x: x.score or 0, reverse=True)
    return uniq[:n]


# ============ 主入口 ============

def _render_with_caps(buckets: dict, today: date, *, new_count: int, total_seen: int,
                      per_section: int, highlight_count: int, with_cover_wall: bool) -> str:
    image_count = sum(1 for items in buckets.values() for it in items if _has_image(it))

    parts: list[str] = []
    parts.append(
        f'<div style="background:{_C["bg_page"]};padding:14px;font-family:-apple-system,BlinkMacSystemFont,'
        f'\'PingFang SC\',\'Hiragino Sans GB\',\'Microsoft YaHei\',sans-serif;color:{_C["text"]};">'
    )
    parts.append(_banner(today, new_count=new_count, total_seen=total_seen, buckets=buckets, image_count=image_count))

    if with_cover_wall:
        cw = _cover_wall(buckets)
        if cw:
            parts.append(cw)

    highlights = _pick_highlights(buckets, highlight_count)
    if highlights:
        parts.append(_render_hero(highlights))

    highlight_urls = {it.url for it in highlights}

    # 各板块按各自风格渲染
    renderers = [
        (PRODUCTS, _render_products),
        (NEWS,     _render_news),
        (MODELS,   _render_models),
        (GITHUB,   _render_github),
    ]
    for key, fn in renderers:
        items = buckets.get(key, [])
        remaining = [it for it in items if it.url not in highlight_urls][:per_section]
        if remaining:
            parts.append(fn(remaining))

    parts.append(_footer())
    parts.append("</div>")
    return "\n".join(parts)


def render_html(buckets: dict, today: date, *, new_count: int, total_seen: int) -> str:
    """自适应裁剪：从最丰富开始，逐级缩减直到 ≤ 60KB（PushPlus 64KB 上限留 4KB 余量）。"""
    SOFT_LIMIT = 60_000
    # (per_section, highlight_count, with_cover_wall)
    ladders = [
        (PUSH_TOP_PER_SECTION, PUSH_HIGHLIGHT_COUNT, True),
        (10, 6, True),
        (8,  5, True),
        (7,  5, False),   # 去掉图片墙
        (6,  4, False),
        (5,  3, False),
    ]
    last = ""
    for per_section, hi, cw in ladders:
        last = _render_with_caps(
            buckets, today, new_count=new_count, total_seen=total_seen,
            per_section=per_section, highlight_count=hi, with_cover_wall=cw,
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
