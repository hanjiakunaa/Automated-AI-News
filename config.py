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
        "Chrome/124.0 Safari/537.36 AI-News-Radar/0.1"
    )
}

# 单个源最多保留多少条（防止 OpenAI Blog 这类全量 RSS 一次返回上千条历史文章）
MAX_PER_SOURCE = 30
# 只保留 N 天内的内容；带时间戳的条目超过此时长则丢弃
RECENT_DAYS = 5

# AI 关键词（命中任一则保留），中英文混合，全部用小写匹配
AI_KEYWORDS = [
    "ai", "a.i.", "artificial intelligence",
    "gpt", "chatgpt", "llm", "llms", "large language model",
    "claude", "anthropic", "gemini", "openai", "deepmind",
    "mistral", "llama", "qwen", "deepseek", "kimi", "doubao",
    "agent", "agentic", "智能体", "copilot",
    "diffusion", "sora", "midjourney", "stable diffusion", "veo", "runway",
    "rag", "fine-tun", "embedding", "transformer", "neural",
    "prompt", "多模态", "multimodal",
    "机器学习", "深度学习", "大模型", "模型", "生成式", "推理模型",
]

# RSS 源：name -> url。失败的会被跳过，不会让程序挂掉。
# 已移除：机器之心（已停用 RSS），Anthropic News（无公开 RSS）。
RSS_FEEDS = {
    # 中文 AI 媒体
    "量子位":        "https://www.qbitai.com/feed",
    "36氪":          "https://36kr.com/feed",
    # 官方博客
    "OpenAI Blog":   "https://openai.com/news/rss.xml",
    "Google AI":     "https://blog.google/technology/ai/rss/",
    "DeepMind":      "https://deepmind.google/blog/rss.xml",
    "Hugging Face":  "https://huggingface.co/blog/feed.xml",
    # Product Hunt 当日
    "Product Hunt":  "https://www.producthunt.com/feed",
}

# Reddit 子版块
REDDIT_SUBS = [
    ("r/LocalLLaMA",      "https://www.reddit.com/r/LocalLLaMA/hot.json?limit=25"),
    ("r/singularity",     "https://www.reddit.com/r/singularity/hot.json?limit=20"),
    ("r/MachineLearning", "https://www.reddit.com/r/MachineLearning/hot.json?limit=15"),
    ("r/OpenAI",          "https://www.reddit.com/r/OpenAI/hot.json?limit=15"),
]

# Hacker News
HN_TOP_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
HN_FETCH_LIMIT = 60   # 拉前 60 条，关键词过滤后通常剩 5~15 条

# GitHub Trending
GH_TRENDING_URLS = [
    ("daily / 全语言", "https://github.com/trending?since=daily"),
    ("daily / Python", "https://github.com/trending/python?since=daily"),
]

# 每个板块在报告里展示的最大条数
SECTION_LIMIT = 12

# 微信推送时每个板块取多少条（推送内容要短，否则 PushPlus 会截断）
PUSH_TOP_PER_SECTION = 3
