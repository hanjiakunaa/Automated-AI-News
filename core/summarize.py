"""为每条资讯生成一句中文摘要。

支持两个后端，按 env 变量自动选择：
- ANTHROPIC_API_KEY → 走 Claude（首选，质量最佳）
- DEEPSEEK_API_KEY  → 走 DeepSeek（OpenAI 兼容接口，便宜，约 ¥0.01/天）
- 两个都没设       → 静默跳过，保留原始摘要

模型可通过 AI_NEWS_MODEL 覆盖：
- Claude  默认 claude-opus-4-7（省钱可设 claude-haiku-4-5）
- DeepSeek 默认 deepseek-chat（推理增强可设 deepseek-reasoner）
"""
import json
import os

import requests

from sources.base import Item

CLAUDE_DEFAULT_MODEL = "claude-opus-4-7"
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"
DEEPSEEK_API = "https://api.deepseek.com/chat/completions"

_SYSTEM_PROMPT = """你是一名为微信公众号撰稿的中文 AI 资讯编辑。受众是国内开发者 / 产品经理，他们看不到外网，需要从你的摘要里就拿到关键信息。

任务：给定一组 AI 圈资讯（含标题和原文摘要），为每一条写：
1. **title_zh（中文标题）** —— 10-22 字，动词驱动、有信息量、保留具体公司/模型名
2. **summary（中文摘要）** —— 150-220 字，结构化两段：
   - 第 1 句：「发生了什么」—— 一句话事实陈述
   - 第 2-3 句：「为什么重要 / 关键数据 / 影响」—— 必须带具体数字、公司名、对比基准、应用场景中的至少 1-2 个
3. **key_points（关键标签）** —— 2-3 个 3-6 字的短词标签，用于在卡片上做 tag 展示（例：["开源", "Apache 2.0", "推理加速"] 或 ["多模态", "文生视频", "Sora 2 对标"]）

写作要求：
- 标题示例：「Anthropic 与 SpaceX 合作扩容 Claude 速率限制」「Qwen 3.6 27B 推理提速 2.5 倍」「字节豆包发布 80B MoE 视觉模型」
- 摘要要带具体数字（参数量、价格、性能、增长率、融资金额、用户数等任何能找到的）
- 涉及产品名、公司名、模型名、版本号必须保留原文（GPT-5.5、Claude 4.7、DeepSeek V4、Sora 2 等）
- GitHub 项目：「它做什么 + 解决什么 + 技术亮点 + star 增长 + 协议」
- Reddit/HN 讨论帖：「讨论的核心观点 + 主要分歧 + 谁在说」
- 语气客观、信息密度高，不要"震惊体"、不要营销腔、不要"近日/据悉/据报道"开头
- 中文输入的标题（量子位、36氪等），title_zh 直接照抄原标题
- key_points 是名词或短语，不要句子；尽量提炼最具记忆点的关键词

输出严格按 JSON schema：每条 {index, title_zh, summary, key_points}，index 与输入对应。"""

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "title_zh": {"type": "string"},
                    "summary": {"type": "string"},
                    "key_points": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 3,
                    },
                },
                "required": ["index", "title_zh", "summary"],
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


def _apply_summaries(items: list[Item], data: dict) -> int:
    """把 LLM 返回的 summaries / title_zh / key_points 写回 items。返回成功条数。"""
    n = 0
    for entry in data.get("summaries", []):
        idx = entry.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(items)):
            continue
        summary = (entry.get("summary") or "").strip()
        title_zh = (entry.get("title_zh") or "").strip()
        key_points = entry.get("key_points") or []
        if summary:
            items[idx].summary = summary
            n += 1
        if title_zh:
            # 翻译过的中文标题存到 extra，notify.py 优先用这个
            items[idx].extra["title_zh"] = title_zh
        if isinstance(key_points, list) and key_points:
            # 过滤空字符串并截断长标签，最多 3 个
            cleaned = [str(p).strip()[:12] for p in key_points if p and str(p).strip()]
            if cleaned:
                items[idx].extra["key_points"] = cleaned[:3]
    return n


def _summarize_with_claude(items: list[Item]) -> int:
    try:
        import anthropic
    except ImportError:
        print("⚠️  未安装 anthropic SDK，跳过 Claude 摘要。安装：pip3 install anthropic")
        return 0

    model = os.environ.get("AI_NEWS_MODEL") or CLAUDE_DEFAULT_MODEL
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
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  Claude 摘要调用失败：{e}（保留原始摘要继续）")
        return 0

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"⚠️  Claude 返回内容无法解析为 JSON：{e}（保留原始摘要）")
        return 0

    n = _apply_summaries(items, data)
    u = response.usage
    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
    print(
        f"✅ Claude 已生成 {n}/{len(items)} 条 · "
        f"用量 in={u.input_tokens} cache_read={cache_read} out={u.output_tokens}"
    )
    return n


def _summarize_with_deepseek(items: list[Item], api_key: str) -> int:
    model = os.environ.get("AI_NEWS_MODEL") or DEEPSEEK_DEFAULT_MODEL
    print(f"🤖 调用 DeepSeek ({model}) 生成 {len(items)} 条 AI 中文摘要……")

    # DeepSeek 用 OpenAI 兼容接口，response_format=json_object 保证返回合法 JSON
    user_msg = (
        _build_user_message(items)
        + '\n\n请输出 JSON，结构为 {"summaries": [{"index": 0, "summary": "…"}, ...]}。'
    )

    try:
        resp = requests.post(
            DEEPSEEK_API,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.3,
                "max_tokens": 16000,
            },
            timeout=120,
        )
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  DeepSeek 网络错误：{e}（保留原始摘要）")
        return 0

    if not resp.ok:
        print(f"⚠️  DeepSeek 返回 {resp.status_code}: {resp.text[:200]}（保留原始摘要）")
        return 0

    try:
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
        data = json.loads(content)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        print(f"⚠️  DeepSeek 返回结构异常：{e}（保留原始摘要）")
        return 0

    n = _apply_summaries(items, data)
    usage = body.get("usage") or {}
    print(
        f"✅ DeepSeek 已生成 {n}/{len(items)} 条 · "
        f"用量 in={usage.get('prompt_tokens', '?')} out={usage.get('completion_tokens', '?')}"
    )
    return n


def summarize(items: list[Item]) -> int:
    """给 items 中每条添加 AI 摘要（覆盖原 summary 字段）。
    返回成功生成的条数；任何失败情况返回 0 并保留原摘要。"""
    if not items:
        return 0

    # 优先 Claude，回退 DeepSeek
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _summarize_with_claude(items)

    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if deepseek_key:
        return _summarize_with_deepseek(items, deepseek_key)

    return 0
