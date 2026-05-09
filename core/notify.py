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

def _banner(today: date, *, new_count: int, total_seen: int, buckets: dict, image_count: int,
            with_bars: bool = True) -> str:
    """顶部封面卡。with_bars 控制是否渲染板块分布柱状图（关掉省 ~700 字）。"""
    cov_pct = int(image_count * 100 / max(new_count, 1)) if new_count else 0
    bars_html = ""
    if with_bars:
        counts = [(SECTION_TITLES.get(k, k), len(buckets.get(k, [])), _THEME[k]["main"]) for k in (PRODUCTS, NEWS, MODELS, GITHUB)]
        max_n = max((c for _, c, _ in counts), default=1) or 1
        rows = ""
        for label, n, color in counts:
            pct = max(6, int(n / max_n * 100)) if n else 4
            rows += (
                f'<tr><td style="font-size:11px;color:rgba(255,255,255,.8);padding:3px 0;">{_esc(label)}</td>'
                f'<td style="padding:3px 0;"><div style="height:5px;background:rgba(255,255,255,.15);border-radius:3px;">'
                f'<div style="width:{pct}%;height:5px;background:{color};border-radius:3px;"></div></div></td>'
                f'<td style="font-size:11px;color:#fff;font-weight:700;text-align:right;padding:3px 0 3px 6px;">{n}</td></tr>'
            )
        bars_html = (
            f'<div style="margin-top:12px;padding-top:10px;border-top:1px solid rgba(255,255,255,.12);">'
            f'<div style="font-size:11px;opacity:.7;font-weight:600;margin-bottom:4px;">板块分布</div>'
            f'<table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">{rows}</table></div>'
        )
    return (
        f'<div style="background:linear-gradient(135deg,#0f172a,#1e293b 60%,#3730a3);color:#fff;padding:22px;border-radius:14px;margin-bottom:14px;">'
        f'<div style="display:inline-block;background:rgba(255,255,255,.14);font-size:11px;padding:3px 9px;border-radius:4px;margin-bottom:10px;font-weight:600;">DAILY · 北京 10:00</div>'
        f'<div style="font-size:24px;font-weight:800;line-height:1.25;">🛰️ AI News Radar</div>'
        f'<div style="font-size:12px;margin-top:5px;opacity:.82;">{today.isoformat()} · RSS / Hacker News / Reddit / GitHub</div>'
        f'<table cellpadding="0" cellspacing="0" border="0" style="width:100%;margin-top:12px;border-collapse:separate;border-spacing:6px 0;"><tr>'
        f'<td style="background:rgba(255,255,255,.1);padding:8px 10px;border-radius:6px;width:33%;">'
        f'<div style="font-size:11px;opacity:.7;">今日新增</div>'
        f'<div style="font-size:20px;font-weight:800;">{new_count}<span style="font-size:11px;font-weight:400;opacity:.7;"> 条</span></div></td>'
        f'<td style="background:rgba(255,255,255,.1);padding:8px 10px;border-radius:6px;width:33%;">'
        f'<div style="font-size:11px;opacity:.7;">累计追踪</div>'
        f'<div style="font-size:20px;font-weight:800;">{total_seen}<span style="font-size:11px;font-weight:400;opacity:.7;"> 条</span></div></td>'
        f'<td style="background:rgba(255,255,255,.1);padding:8px 10px;border-radius:6px;width:34%;">'
        f'<div style="font-size:11px;opacity:.7;">图片覆盖</div>'
        f'<div style="font-size:20px;font-weight:800;">{cov_pct}<span style="font-size:11px;font-weight:400;opacity:.7;">%</span></div></td>'
        f'</tr></table>{bars_html}</div>'
    )


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

def _hero_top(it: Item, *, summary_max: int = 120) -> str:
    """第 1 条：超大封面 + 标题压底 + 摘要一两句。"""
    image = (it.extra or {}).get("image", "")
    title_main, title_sub = _display_title(it)
    summary = it.summary if _has_real_summary(it.summary) else ""
    cap = min(summary_max, 140)
    if summary and len(summary) > cap:
        summary = summary[:cap].rstrip() + "…"

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


def _hero_pair(it_a: Item, it_b: Item, *, summary_max: int = 80) -> str:
    """第 2、3 条：并排 2 列中卡（用 table 兼容老微信）。"""
    cap = min(summary_max, 90)

    def col(it: Item) -> str:
        title_main, _ = _display_title(it)
        summary = it.summary if _has_real_summary(it.summary) else ""
        if summary and len(summary) > cap:
            summary = summary[:cap].rstrip() + "…"
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


def _hero_compact(it: Item, n: int, *, summary_max: int = 90) -> str:
    """第 4-6 条：紧凑横长卡（80×80 图 + 紧凑文字）。"""
    title_main, _ = _display_title(it)
    summary = it.summary if _has_real_summary(it.summary) else ""
    cap = min(summary_max, 100)
    if summary and len(summary) > cap:
        summary = summary[:cap].rstrip() + "…"
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


def _render_hero(highlights: list[Item], *, summary_max: int = 120, with_pair: bool = True) -> str:
    if not highlights:
        return ""
    parts = [
        f'<div style="background:linear-gradient(135deg,#fbbf24 0%,#f59e0b 100%);color:#fff;padding:14px 18px;border-radius:12px;margin:18px 0 12px;box-shadow:0 6px 16px rgba(245,158,11,0.25);">'
        f'  <div style="font-size:18px;font-weight:800;letter-spacing:0.5px;">⭐ 今日必看</div>'
        f'  <div style="font-size:12px;opacity:0.92;margin-top:3px;">编辑精选最值得花 30 秒读的几条</div>'
        f'</div>'
    ]
    parts.append(_hero_top(highlights[0], summary_max=summary_max))
    if with_pair and len(highlights) >= 3:
        parts.append(_hero_pair(highlights[1], highlights[2], summary_max=summary_max))
        rest = highlights[3:]
        start_idx = 4
    elif with_pair and len(highlights) == 2:
        parts.append(_hero_top(highlights[1], summary_max=summary_max))
        return "\n".join(parts)
    else:
        rest = highlights[1:]
        start_idx = 2
    for i, it in enumerate(rest, start=start_idx):
        parts.append(_hero_compact(it, i, summary_max=summary_max))
    return "\n".join(parts)


# ============ 4 个板块各异风格 ============

def _section_header(key: str, count: int, *, with_subtitle: bool = True) -> str:
    theme = _THEME[key]
    title = SECTION_TITLES.get(key, key)
    sub = f'<div style="font-size:11px;opacity:.85;margin-top:1px;">{_esc(theme["subtitle"])}</div>' if with_subtitle else ""
    badge = f'<span style="float:right;background:rgba(255,255,255,.22);font-size:11px;font-weight:700;padding:1px 8px;border-radius:8px;margin-top:3px;">{count}</span>'
    return (
        f'<div style="background:{theme["gradient"]};color:#fff;padding:10px 14px;border-radius:9px 9px 0 0;margin:14px 0 0;">'
        f'{badge}<div style="font-size:16px;font-weight:800;">{_esc(title)}</div>{sub}'
        f'</div>'
    )


def _section_body_open() -> str:
    return f'<div style="background:#fff;border:1px solid {_C["border"]};border-top:none;border-radius:0 0 9px 9px;padding:4px;margin-bottom:4px;">'


def _section_body_close() -> str:
    return '</div>'


# ----- 紧凑卡（低档位用，~350 字/张，去除大量装饰元素） -----

def _card_slim(it: Item, idx: int, *, color: str, summary_max: int = 120, with_source: bool = True) -> str:
    """所有板块通用的紧凑列表卡，单张约 270-330 字。视觉简但信息齐：外框彩色编号 + 标题 + (可选)来源 + 摘要。"""
    title_main, _ = _display_title(it)
    summary = it.summary if _has_real_summary(it.summary) else ""
    if summary and len(summary) > summary_max:
        summary = summary[:summary_max].rstrip() + "…"
    sm_html = f'<div style="color:#475569;font-size:12px;line-height:1.55;margin-top:2px;">{_esc(summary)}</div>' if summary else ""
    src_html = f'<span style="color:#94a3b8;font-size:11px;"> · {_esc(it.source)}</span>' if with_source else ""
    # 外框彩色数字徽章：1px 边框 + 透明背景 + 同色数字
    badge = (
        f'<span style="display:inline-block;border:1px solid {color};color:{color};'
        f'font-size:11px;font-weight:800;padding:0 6px;border-radius:4px;margin-right:6px;'
        f'min-width:14px;text-align:center;line-height:18px;">{idx}</span>'
    )
    return (
        f'<a href="{_esc(it.url)}" style="display:block;padding:8px 11px;border-bottom:1px solid #eef2f7;color:inherit;text-decoration:none;">'
        f'<div style="font-size:14px;font-weight:700;line-height:1.5;color:#0f172a;">'
        f'{badge}{_esc(title_main)}{src_html}</div>{sm_html}</a>'
    )


def _render_section_slim(items: list[Item], section_key: str, *,
                         summary_max: int, with_subtitle: bool, with_source: bool = True) -> str:
    """紧凑模式下的板块渲染：和 rich 模式共用 header，但卡片用 _card_slim。"""
    if not items:
        return ""
    color = _THEME[section_key]["main"]
    parts = [_section_header(section_key, len(items), with_subtitle=with_subtitle)]
    parts.append(f'<div style="background:#fff;border:1px solid {_C["border"]};border-top:none;border-radius:0 0 10px 10px;margin-bottom:6px;">')
    for i, it in enumerate(items, start=1):
        parts.append(_card_slim(it, i, color=color, summary_max=summary_max, with_source=with_source))
    parts.append('</div>')
    return "".join(parts)


# ----- 产品（双列网格） -----

def _card_product(it: Item, *, summary_max: int = 90) -> str:
    title_main, _ = _display_title(it)
    summary = it.summary if _has_real_summary(it.summary) else ""
    cap = min(summary_max, 110)
    if summary and len(summary) > cap:
        summary = summary[:cap].rstrip() + "…"
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


def _render_products(items: list[Item], *, summary_max: int = 90, with_subtitle: bool = True) -> str:
    if not items:
        return ""
    parts = [_section_header(PRODUCTS, len(items), with_subtitle=with_subtitle), _section_body_open()]
    # 两列一行
    rows = [items[i:i+2] for i in range(0, len(items), 2)]
    for row in rows:
        cells = ""
        for it in row:
            cells += f'<td style="width:50%;vertical-align:top;padding:6px;">{_card_product(it, summary_max=summary_max)}</td>'
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

def _card_news(it: Item, idx: int, *, summary_max: int = 320) -> str:
    """News 轻量 rich 卡：彩色左边条 + 顶部图（如有）+ 紧凑 meta + 标题 + 摘要。约 700-800 字/张。"""
    color = _THEME[NEWS]["main"]
    title_main, title_sub = _display_title(it)
    summary = it.summary if _has_real_summary(it.summary) else ""
    if summary and len(summary) > summary_max:
        summary = summary[:summary_max].rstrip() + "…"
    has_img = _has_image(it)
    image = (it.extra or {}).get("image", "") if has_img else ""

    top_img = f'<img src="{_esc(image)}" alt="" style="width:100%;height:120px;object-fit:cover;display:block;" />' if has_img else ""
    score = _score_text(it)
    t = _fmt_time(it.published_at)
    meta_bits = [f'<b style="color:{color};">#{idx}</b>', _esc(it.source)]
    if score: meta_bits.append(_esc(score))
    if t:     meta_bits.append(f'⏱ {_esc(t)}')
    sub = f'<div style="color:{_C["text_3"]};font-size:11px;font-style:italic;margin-top:3px;">{_esc(title_sub[:80])}</div>' if title_sub else ""
    summary_html = f'<div style="color:{_C["text_2"]};font-size:13px;line-height:1.65;margin-top:6px;">{_esc(summary)}</div>' if summary else ""
    tags = _key_points_tags(it, color)
    return (
        f'<a href="{_esc(it.url)}" style="text-decoration:none;color:inherit;display:block;">'
        f'<div style="background:#fff;border:1px solid {_C["border"]};border-left:3px solid {color};border-radius:7px;margin:7px 6px;overflow:hidden;">'
        f'{top_img}<div style="padding:10px 13px;">'
        f'<div style="color:#94a3b8;font-size:11px;margin-bottom:3px;">{" · ".join(meta_bits)}</div>'
        f'<div style="font-size:15px;font-weight:700;color:{_C["text"]};line-height:1.5;">{_esc(title_main)}</div>'
        f'{sub}{summary_html}{tags}</div></div></a>'
    )


def _render_news(items: list[Item], *, summary_max: int = 320, with_subtitle: bool = True) -> str:
    if not items:
        return ""
    parts = [_section_header(NEWS, len(items), with_subtitle=with_subtitle), _section_body_open()]
    for i, it in enumerate(items, start=1):
        parts.append(_card_news(it, i, summary_max=summary_max))
    parts.append(_section_body_close())
    return "\n".join(parts)


# ----- 模型（重点卡） -----

def _card_models(it: Item, idx: int, *, summary_max: int = 320) -> str:
    """Models 轻量 rich 卡：紫色左边条 + 缩略图（如有）+ 紧凑 meta + 标题 + 摘要。约 700-800 字/张。"""
    color = _THEME[MODELS]["main"]
    title_main, title_sub = _display_title(it)
    summary = it.summary if _has_real_summary(it.summary) else ""
    if summary and len(summary) > summary_max:
        summary = summary[:summary_max].rstrip() + "…"
    has_img = _has_image(it)
    image = (it.extra or {}).get("image", "") if has_img else ""

    thumb = ""
    if has_img:
        thumb = (
            f'<td style="width:80px;vertical-align:top;padding-right:10px;">'
            f'<img src="{_esc(image)}" alt="" style="width:80px;height:80px;object-fit:cover;border-radius:6px;display:block;" /></td>'
        )
    score = _score_text(it)
    meta_bits = [f'<b style="color:{color};">#{idx}</b>', _esc(it.source)]
    if score: meta_bits.append(_esc(score))
    sub = f'<div style="color:{_C["text_3"]};font-size:11px;font-style:italic;margin-top:3px;">{_esc(title_sub[:80])}</div>' if title_sub else ""
    summary_html = f'<div style="color:{_C["text_2"]};font-size:13px;line-height:1.65;margin-top:6px;">{_esc(summary)}</div>' if summary else ""
    tags = _key_points_tags(it, color)

    return (
        f'<a href="{_esc(it.url)}" style="text-decoration:none;color:inherit;display:block;">'
        f'<div style="background:#fff;border:1px solid {_C["border"]};border-left:3px solid {color};border-radius:7px;padding:10px 13px;margin:7px 6px;">'
        f'<table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;"><tr>{thumb}'
        f'<td style="vertical-align:top;">'
        f'<div style="color:#94a3b8;font-size:11px;margin-bottom:3px;">{" · ".join(meta_bits)}</div>'
        f'<div style="font-size:15px;font-weight:700;color:{_C["text"]};line-height:1.5;">{_esc(title_main)}</div>'
        f'{sub}{summary_html}{tags}</td></tr></table></div></a>'
    )


def _render_models(items: list[Item], *, summary_max: int = 320, with_subtitle: bool = True) -> str:
    if not items:
        return ""
    parts = [_section_header(MODELS, len(items), with_subtitle=with_subtitle), _section_body_open()]
    for i, it in enumerate(items, start=1):
        parts.append(_card_models(it, i, summary_max=summary_max))
    parts.append(_section_body_close())
    return "\n".join(parts)


# ----- GitHub（代码卡风格 - 深色） -----

def _card_github(it: Item, idx: int, *, summary_max: int = 220) -> str:
    """GitHub 轻量深色卡：保留终端绿色风格 + 头像 + stars。约 800-900 字/张。"""
    title_main, _ = _display_title(it)
    summary = it.summary if _has_real_summary(it.summary) else ""
    if summary and len(summary) > summary_max:
        summary = summary[:summary_max].rstrip() + "…"
    lang = (it.extra or {}).get("language") or ""
    lang_color = _GH_LANG_COLOR.get(lang, "#22c55e")
    image = (it.extra or {}).get("image", "")
    avatar = (
        f'<img src="{_esc(image)}" alt="" style="width:28px;height:28px;border-radius:5px;background:#fff;display:block;" />'
        if image else
        f'<div style="width:28px;height:28px;border-radius:5px;background:#1f2937;color:#94a3b8;font-size:13px;font-weight:800;text-align:center;line-height:28px;">⌘</div>'
    )
    stars = f'<span style="color:#facc15;font-size:13px;font-weight:800;">⭐ +{it.score}</span>' if it.score else ""
    lang_html = f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{lang_color};margin-right:4px;vertical-align:middle;"></span>{_esc(lang)} · ' if lang else ""
    summary_html = f'<div style="color:#cbd5e1;font-size:13px;line-height:1.6;margin-top:6px;">{_esc(summary)}</div>' if summary else ""
    tags = _key_points_tags(it, "#22c55e")

    return (
        f'<a href="{_esc(it.url)}" style="text-decoration:none;color:inherit;display:block;">'
        f'<div style="background:#0f172a;border-left:3px solid #22c55e;border-radius:7px;padding:10px 13px;margin:7px 6px;">'
        f'<table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;"><tr>'
        f'<td style="width:28px;vertical-align:top;padding-right:9px;">{avatar}</td>'
        f'<td style="vertical-align:top;">'
        f'<div style="color:#22c55e;font-size:11px;font-weight:700;">$ #{idx} · <span style="color:#94a3b8;font-weight:400;">{lang_html}github.com</span></div>'
        f'<div style="font-size:14px;color:#f1f5f9;font-weight:700;line-height:1.45;margin-top:2px;">{_esc(title_main)}</div>'
        f'</td><td style="vertical-align:top;text-align:right;padding-left:6px;">{stars}</td>'
        f'</tr></table>{summary_html}{tags}</div></a>'
    )


def _render_github(items: list[Item], *, summary_max: int = 220, with_subtitle: bool = True) -> str:
    if not items:
        return ""
    parts = [_section_header(GITHUB, len(items), with_subtitle=with_subtitle), _section_body_open()]
    for i, it in enumerate(items, start=1):
        parts.append(_card_github(it, i, summary_max=summary_max))
    parts.append(_section_body_close())
    return "\n".join(parts)


def _footer() -> str:
    web_link = ""
    if WEB_URL:
        web_link = (
            f'<div style="text-align:center;margin:18px 0 10px;">'
            f'<a href="{_esc(WEB_URL)}" style="display:inline-block;background:#0f172a;color:#fff;padding:10px 24px;border-radius:6px;font-size:13px;font-weight:600;text-decoration:none;">📖 查看完整列表（网页版）</a>'
            f'</div>'
        )
    return (
        f'{web_link}'
        f'<div style="background:#fff;border:1px solid {_C["border"]};border-radius:8px;padding:12px;margin-top:12px;text-align:center;color:{_C["text_2"]};font-size:12px;line-height:1.7;">'
        f'🛰️ <b>AI News Radar</b> · 自动抓取 + AI 翻译整理<br/>'
        f'<span style="color:{_C["muted"]};font-size:11px;">每日 10:00 北京时间更新 · 仅供参考</span>'
        f'</div>'
    )


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

def _render_with_caps(buckets: dict, today: date, *, new_count: int, total_seen: int, caps: dict) -> str:
    """根据 caps 字典控制密度。caps 字段：
    - per_section: 每板块最多多少条
    - highlight_count: 顶部「今日必看」抽几条（0 = 不显示 hero）
    - summary_max: 卡片摘要最大字数
    - hero_summary_max: hero 区摘要最大字数
    - with_cover_wall: 是否显示封面图墙
    - with_hero_pair: 是否显示 hero 第 2/3 条的并排卡（False 则全用 compact）
    - with_section_summary: 各板块卡片是否带摘要文字（极简档可关）
    """
    image_count = sum(1 for items in buckets.values() for it in items if _has_image(it))

    parts: list[str] = []
    parts.append(
        f'<div style="background:{_C["bg_page"]};padding:14px;font-family:-apple-system,BlinkMacSystemFont,'
        f'\'PingFang SC\',\'Hiragino Sans GB\',\'Microsoft YaHei\',sans-serif;color:{_C["text"]};">'
    )
    parts.append(_banner(today, new_count=new_count, total_seen=total_seen, buckets=buckets,
                         image_count=image_count, with_bars=caps.get("with_bars", True)))

    if caps.get("with_cover_wall"):
        cw = _cover_wall(buckets)
        if cw:
            parts.append(cw)

    highlight_count = caps.get("highlight_count", 0)
    highlights = _pick_highlights(buckets, highlight_count) if highlight_count > 0 else []
    if highlights:
        parts.append(_render_hero(
            highlights,
            summary_max=caps.get("hero_summary_max", 120),
            with_pair=caps.get("with_hero_pair", True),
        ))

    highlight_urls = {it.url for it in highlights}

    sm = caps.get("summary_max", 320)
    sub = caps.get("with_subtitle", True)
    compact = caps.get("compact_cards", False)
    # 各板块按各自风格渲染（rich 模式各异；compact 模式统一走 _card_slim）
    if compact:
        with_source = caps.get("with_source", True)
        renderers = [
            (PRODUCTS, lambda items: _render_section_slim(items, PRODUCTS, summary_max=sm, with_subtitle=sub, with_source=with_source)),
            (NEWS,     lambda items: _render_section_slim(items, NEWS,     summary_max=sm, with_subtitle=sub, with_source=with_source)),
            (MODELS,   lambda items: _render_section_slim(items, MODELS,   summary_max=sm, with_subtitle=sub, with_source=with_source)),
            (GITHUB,   lambda items: _render_section_slim(items, GITHUB,   summary_max=sm, with_subtitle=sub, with_source=with_source)),
        ]
    else:
        renderers = [
            (PRODUCTS, lambda items: _render_products(items, summary_max=min(sm, 110), with_subtitle=sub)),
            (NEWS,     lambda items: _render_news(items, summary_max=sm, with_subtitle=sub)),
            (MODELS,   lambda items: _render_models(items, summary_max=sm, with_subtitle=sub)),
            (GITHUB,   lambda items: _render_github(items, summary_max=min(sm, 240), with_subtitle=sub)),
        ]
    per_section = caps.get("per_section", 10)
    for key, fn in renderers:
        items = buckets.get(key, [])
        remaining = [it for it in items if it.url not in highlight_urls][:per_section]
        if remaining:
            parts.append(fn(remaining))

    parts.append(_footer())
    parts.append("</div>")
    return "\n".join(parts)


def render_html(buckets: dict, today: date, *, new_count: int, total_seen: int) -> str:
    """自适应裁剪：双闸门 ≤ 19000 字符 且 ≤ 60000 字节（PushPlus 双重上限：2 万字 / 64KB）。

    从最丰富开始逐级降密度，第一个同时满足两个上限的档位即返回。
    板块数永远是 4，每板块至少 5 条——保证「全面」基线，被压缩的是摘要字数和装饰元素。
    """
    CHAR_LIMIT = 19_000   # PushPlus 上限 2 万字，留 1000 字余量
    BYTE_LIMIT = 60_000   # PushPlus 上限 64KB，留 4KB 余量

    # 每档：per, hero, summary_max, hero_sm, cover_wall, hero_pair, bars, subtitle, compact_cards, with_source
    ladders = [
        # ——— L0-L4: rich 杂志风 / per=5 主力档 ———
        (5, 4, 200, 100, True,  True,  True,  True,  False, True),   # L0 完整：图墙 + 4 必看 + 5 条/板块
        (5, 3, 200, 90,  False, True,  True,  True,  False, True),   # L1 去图墙
        (5, 2, 180, 80,  False, False, True,  True,  False, True),   # L2 去并排
        (5, 0, 160, 0,   False, False, False, True,  False, True),   # L3 去 hero
        (5, 0, 130, 0,   False, False, False, False, False, True),   # L4 去副标
        # ——— L5-L7: rich 减条数 / per=4-3 ———
        (4, 0, 130, 0,   False, False, False, True,  False, True),   # L5
        (4, 0, 100, 0,   False, False, False, False, False, True),   # L6
        (3, 0, 100, 0,   False, False, False, False, False, True),   # L7
        # ——— L8-L13: compact 列表卡兜底 ———
        (12, 0, 130, 0,  False, False, False, True,  True,  True),   # L8 切到 compact
        (10, 0, 100, 0,  False, False, False, False, True,  True),   # L9
        (10, 0, 80,  0,  False, False, False, False, True,  False),  # L10 去 source
        (8,  0, 60,  0,  False, False, False, False, True,  False),  # L11
        (6,  0, 40,  0,  False, False, False, False, True,  False),  # L12
        (5,  0, 20,  0,  False, False, False, False, True,  False),  # L13 最低兜底
    ]
    last = ""
    last_chars = 0
    last_bytes = 0
    for per_section, hi, sm, hsm, cw, hp, bars, sub, compact, src in ladders:
        last = _render_with_caps(
            buckets, today, new_count=new_count, total_seen=total_seen,
            caps={
                "per_section": per_section,
                "highlight_count": hi,
                "summary_max": sm,
                "hero_summary_max": hsm,
                "with_cover_wall": cw,
                "with_hero_pair": hp,
                "with_bars": bars,
                "with_subtitle": sub,
                "compact_cards": compact,
                "with_source": src,
            },
        )
        last_chars = len(last)
        last_bytes = len(last.encode("utf-8"))
        if last_chars <= CHAR_LIMIT and last_bytes <= BYTE_LIMIT:
            return last
    # 即便跑到最后一档仍超限——返回最小档（push() 会再次校验）
    print(f"⚠️  即使最低档位仍超限：{last_chars} 字符 / {last_bytes} 字节")
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
    char_count = len(content)
    print(f"📝 推送内容：{char_count} 字符 / {body_size} 字节")

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
