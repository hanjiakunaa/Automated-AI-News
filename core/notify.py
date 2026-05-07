"""把每日报告精简后推送到微信（PushPlus）。

启用方式：在环境变量里设置 PUSHPLUS_TOKEN 即可。
缺 token、网络错误、PushPlus 报错——一律静默警告，不让主流程失败。

PushPlus 文档：https://www.pushplus.plus/doc/
- 接口：POST http://www.pushplus.plus/send
- body：{ token, title, content, template: "markdown" }
- 单条限制 64KB，免费版每天 200 条
"""
import os
from datetime import date

import requests

from config import HTTP_HEADERS, HTTP_TIMEOUT, PUSH_TOP_PER_SECTION
from core.classify import SECTION_TITLES
from sources.base import Item

_API = "http://www.pushplus.plus/send"


def _render_content(buckets: dict, today: date, *, new_count: int) -> str:
    """渲染推送 markdown：每板块只取 top N，链接保留，summary 一行。"""
    lines = [f"> 今日新增 **{new_count}** 条"]
    for key, title in SECTION_TITLES.items():
        items: list[Item] = buckets.get(key, [])[:PUSH_TOP_PER_SECTION]
        if not items:
            continue
        lines.append(f"\n### {title}")
        for it in items:
            lines.append(f"- [{it.title}]({it.url}) — `{it.source}`")
            if it.summary and it.summary != it.title:
                lines.append(f"  > {it.summary}")
    lines.append("\n---\n点击任意标题在浏览器查看。完整列表见网页。")
    return "\n".join(lines)


def push(buckets: dict, today: date, *, new_count: int) -> bool:
    """成功返回 True；任何异常静默打印警告并返回 False。"""
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not token:
        return False

    if new_count == 0:
        # 没新东西就别打扰
        print("📭 今日新增为 0，跳过微信推送。")
        return False

    title = f"🛰️ AI News · {today.isoformat()} · {new_count} 条"
    content = _render_content(buckets, today, new_count=new_count)

    try:
        resp = requests.post(
            _API,
            json={"token": token, "title": title, "content": content, "template": "markdown"},
            headers=HTTP_HEADERS,
            timeout=HTTP_TIMEOUT,
        )
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.ok and data.get("code") == 200:
            print(f"📲 已推送到微信（PushPlus）: {title}")
            return True
        print(f"⚠️  PushPlus 返回非 200: status={resp.status_code} body={data or resp.text[:200]}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  PushPlus 调用失败：{e}")
        return False
