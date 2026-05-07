"""使用 Claude API 为每条资讯生成一句中文摘要。

启用方式：在环境变量里设置 ANTHROPIC_API_KEY 即可。
默认用 claude-opus-4-7；想省钱可设置 AI_NEWS_MODEL=claude-haiku-4-5。
未设置 key、未装 anthropic SDK、调用失败 都会静默跳过，
不影响主流程的报告生成。
"""
import json
import os

from sources.base import Item

DEFAULT_MODEL = "claude-opus-4-7"

_SYSTEM_PROMPT = """你是一名简洁、专业的中文 AI 资讯编辑。

任务：给定一组 AI 圈资讯（含标题和原文摘要），为每一条写一句中文摘要。

要求：
- 严格控制在 60 个汉字以内
- 抓住「这条资讯到底说了什么 / 解决了什么 / 影响是什么」的核心
- 涉及具体的产品名、公司名、模型名要保留原文（GPT-5.5、Claude 4.7、DeepSeek V4 等）
- 语气客观、信息密度高，不要"震惊体"或营销腔
- 不要套话开头（"近日"、"据悉"、"据报道"）
- 标题已经很自解释的（比如 GitHub 仓库名 + 一行说明），直接重述其作用即可

输出严格按照给定的 JSON schema：每条 {index, summary}，index 与输入对应。"""

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "summary": {"type": "string"},
                },
                "required": ["index", "summary"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["summaries"],
    "additionalProperties": False,
}


def _build_user_message(items: list[Item]) -> str:
    parts = [f"以下是 {len(items)} 条 AI 资讯，请为每一条写一句中文摘要：\n"]
    for i, it in enumerate(items):
        parts.append(f"[{i}] 标题：{it.title}")
        if it.summary and len(it.summary) > 5 and it.summary != it.title:
            parts.append(f"     原文：{it.summary[:300]}")
        parts.append(f"     来源：{it.source}")
    return "\n".join(parts)


def summarize(items: list[Item]) -> int:
    """给 items 中每条添加 AI 摘要（覆盖原 summary 字段）。
    返回成功生成的条数；任何失败情况返回 0 并保留原摘要。"""
    if not items:
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return 0

    try:
        import anthropic
    except ImportError:
        print("⚠️  未安装 anthropic SDK，跳过 AI 摘要。安装：pip3 install anthropic")
        return 0

    model = os.environ.get("AI_NEWS_MODEL") or DEFAULT_MODEL
    client = anthropic.Anthropic()

    print(f"🤖 调用 Claude ({model}) 生成 {len(items)} 条 AI 中文摘要……")
    try:
        with client.messages.stream(
            model=model,
            max_tokens=16000,
            system=[{
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": _build_user_message(items)}],
            output_config={"format": {"type": "json_schema", "schema": _OUTPUT_SCHEMA}},
        ) as stream:
            response = stream.get_final_message()
    except Exception as e:  # noqa: BLE001 — 任何失败都降级为原摘要
        print(f"⚠️  Claude 摘要调用失败：{e}（保留原始摘要继续）")
        return 0

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"⚠️  Claude 返回内容无法解析为 JSON：{e}（保留原始摘要）")
        return 0

    n = 0
    for entry in data.get("summaries", []):
        idx = entry.get("index")
        summary = (entry.get("summary") or "").strip()
        if isinstance(idx, int) and 0 <= idx < len(items) and summary:
            items[idx].summary = summary
            n += 1

    u = response.usage
    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
    print(
        f"✅ 已生成 {n}/{len(items)} 条 · "
        f"用量 in={u.input_tokens} cache_read={cache_read} out={u.output_tokens}"
    )
    return n
