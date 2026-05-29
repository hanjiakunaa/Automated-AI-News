"""所有可调参数集中在这里。改源 / 改关键词只动这一个文件。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
WEB_DATA_DIR = ROOT / "web" / "data"   # Next.js 前端读取的 JSON 落点
DB_PATH = DATA_DIR / "seen.db"

# 时区：CI 跑在 UTC，但我们要按北京时间分日
TIMEZONE = "Asia/Shanghai"

HTTP_TIMEOUT = 15
# 用主流浏览器 UA，否则 量子位 会 403、Reddit 会更容易 429
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36 AI-News-Radar/0.4"
    )
}

# 单个源最多保留多少条（防止 OpenAI Blog 这类全量 RSS 一次返回上千条历史文章）
MAX_PER_SOURCE = 30
# 只保留 N 天内的内容；带时间戳的条目超过此时长则丢弃
RECENT_DAYS = 5

# AI 关键词（命中任一则保留），中英文混合，全部用小写匹配
AI_KEYWORDS = [
    # 通用
    "ai", "a.i.", "artificial intelligence",
    "gpt", "chatgpt", "llm", "llms", "large language model",
    "claude", "anthropic", "gemini", "openai", "deepmind",
    "mistral", "llama", "qwen", "deepseek", "kimi", "doubao",
    "perplexity", "grok", "xai", "cohere", "stability",
    "agent", "agentic", "智能体", "copilot",
    "diffusion", "sora", "midjourney", "stable diffusion", "veo", "runway",
    "rag", "fine-tun", "embedding", "transformer", "neural",
    "prompt", "多模态", "multimodal",
    "机器学习", "深度学习", "大模型", "模型", "生成式", "推理模型",

    # MCP / Skills / Agent 生态（v0.4 新增）
    "mcp", "model context protocol",
    "skills", "claude skills", "claude code",
    "agent sdk", "ai agent", "ai agents",
    "computer use", "tool use", "tool-use", "function calling",
    "browser use", "operator",

    # AI Coding 工具（v0.4 新增）
    "cursor", "windsurf", "cline", "devin", "v0.dev", "lovable",
    "bolt.new", "replit agent", "codex", "github copilot",
    "continue.dev", "aider",

    # 模型技术（v0.4 新增）
    "vlm", "vision language model", "moe", "mixture of experts",
    "rlhf", "dpo", "sft", "reasoning model", "chain of thought", "cot",
    "long context", "context window", "kv cache", "world model",

    # 厂商/产品名（v0.4 新增）
    "sonnet", "haiku", "opus", "o3", "o4", "gpt-5", "gpt-5.5", "gpt5",
    "qwen3", "qwen-3", "glm-4", "glm-5", "deepseek-r1", "deepseek-r2",
    "deepseek-v3", "llama 4", "gemini 2", "gemini 3",

    # 国内中文产品/术语（v0.4 新增）
    "豆包", "星火", "文心", "混元", "通义", "千问", "智谱",
    "阶跃", "minimax", "百川", "零一万物", "moonshot",
    "工具调用", "智能体框架", "多模态大模型",
    "具身智能", "世界模型", "扩散模型", "强化学习",
    "对齐", "微调", "蒸馏", "算力", "推理加速",
]

# 负向词：命中即丢弃（即使前面 AI 关键词也命中）。
# 用来过滤 36氪、APPSO 这类综合媒体的广告/课程/营销内容。
EXCLUDE_KEYWORDS = [
    "招聘", "课程", "培训", "广告", "招商", "团购", "促销",
    "优惠", "折扣", "限时", "下单", "购买", "立减",
    "送你", "免费领", "扫码", "公众号",
]

# RSS 源：name -> url。失败的会被跳过，不会让程序挂掉。
# 已移除：机器之心（已停用 RSS，改走 HTML 爬虫），Anthropic News（无公开 RSS，改走 HTML 爬虫）。
RSS_FEEDS = {
    # ────── 中文 AI 媒体 ──────
    "量子位":        "https://www.qbitai.com/feed",
    "36氪":          "https://36kr.com/feed",
    "AI科技评论":     "https://www.leiphone.com/category/ai/feed",
    "APPSO":         "https://www.appso.cn/feed",

    # ────── 国外 AI 媒体 ──────
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Decoder":   "https://the-decoder.com/feed/",
    "VentureBeat AI": "https://venturebeat.com/category/ai/feed/",
    "The Verge AI":  "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "Ars Technica AI": "https://arstechnica.com/ai/feed/",

    # ────── 模型厂官方博客 ──────
    # 备注：Meta AI、Perplexity、Cohere、Replit、LangChain 当前均无可用 RSS
    # （CDN 403 或返回 HTML），未来可通过 html_pages 模块加 HTML 爬虫接入
    "OpenAI Blog":   "https://openai.com/news/rss.xml",
    "Google AI":     "https://blog.google/technology/ai/rss/",
    "DeepMind":      "https://deepmind.google/blog/rss.xml",
    "Hugging Face":  "https://huggingface.co/blog/feed.xml",
    "NVIDIA AI":     "https://blogs.nvidia.com/blog/category/deep-learning/feed/",
    "Microsoft Research": "https://www.microsoft.com/en-us/research/blog/feed/",

    # ────── Coding Agent / AI 工具 ──────
    "GitHub Blog":   "https://github.blog/feed/",   # 全量，靠 AI 关键词过滤出 AI/ML 内容
    "Vercel AI":     "https://vercel.com/atom",

    # ────── 行业 Newsletter ──────
    "Latent Space":  "https://www.latent.space/feed",
    "Import AI":     "https://jack-clark.net/feed/",
    "Interconnects": "https://www.interconnects.ai/feed",

    # ────── 学术论文（arXiv） ──────
    "arXiv cs.AI":   "http://export.arxiv.org/rss/cs.AI",
    "arXiv cs.CL":   "http://export.arxiv.org/rss/cs.CL",
    "arXiv cs.LG":   "http://export.arxiv.org/rss/cs.LG",

    # ────── 社区/产品发现 ──────
    "Product Hunt":  "https://www.producthunt.com/feed",
}

# Reddit 子版块
REDDIT_SUBS = [
    # 原有
    ("r/LocalLLaMA",      "https://www.reddit.com/r/LocalLLaMA/hot.json?limit=25"),
    ("r/singularity",     "https://www.reddit.com/r/singularity/hot.json?limit=20"),
    ("r/MachineLearning", "https://www.reddit.com/r/MachineLearning/hot.json?limit=15"),
    ("r/OpenAI",          "https://www.reddit.com/r/OpenAI/hot.json?limit=15"),
    # v0.4 新增：MCP / Claude / Agent / Cursor 实战社区
    ("r/ClaudeAI",        "https://www.reddit.com/r/ClaudeAI/hot.json?limit=20"),
    ("r/AI_Agents",       "https://www.reddit.com/r/AI_Agents/hot.json?limit=20"),
    ("r/mcp",             "https://www.reddit.com/r/mcp/hot.json?limit=15"),
    ("r/cursor",          "https://www.reddit.com/r/cursor/hot.json?limit=15"),
]

# Hacker News
HN_TOP_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
HN_FETCH_LIMIT = 60   # 拉前 60 条，关键词过滤后通常剩 5~15 条

# GitHub Trending
# 按 topic 抓覆盖 Agent / MCP / LLM 生态，不只是大杂烩
GH_TRENDING_URLS = [
    ("daily / 全语言",      "https://github.com/trending?since=daily"),
    ("daily / Python",     "https://github.com/trending/python?since=daily"),
    ("daily / TypeScript", "https://github.com/trending/typescript?since=daily"),
    # v0.4 新增：按 topic 抓最贴近 AI 生态
    ("topic / ai-agents",  "https://github.com/trending?since=daily&topic=ai-agents"),
    ("topic / mcp",        "https://github.com/trending?since=daily&topic=mcp"),
    ("topic / llm",        "https://github.com/trending?since=daily&topic=llm"),
]

# 每个板块在报告里展示的最大条数
SECTION_LIMIT = 12

# 微信推送时每个板块取多少条
PUSH_TOP_PER_SECTION = 10
# 推送顶部「今日必看」抽几条（跨板块挑，新闻/模型/产品 优先，GitHub 最多 1 条）
PUSH_HIGHLIGHT_COUNT = 6
# Vercel 部署的网页地址，用于推送底部「查看完整列表」按钮（可选）
import os as _os
WEB_URL = _os.environ.get("WEB_URL", "").strip()
