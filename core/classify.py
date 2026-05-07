"""把条目归到 4 个板块：产品 / 新闻 / 模型 / GitHub。"""
from sources.base import Item

# 4 个板块的 key
PRODUCTS = "products"
NEWS = "news"
MODELS = "models"
GITHUB = "github"

SECTION_TITLES = {
    PRODUCTS: "🔥 爆款 AI 产品",
    NEWS:     "📰 AI 重要新闻",
    MODELS:   "🚀 大模型动态",
    GITHUB:   "💻 GitHub AI 热门项目",
}

# 严格按来源归类，比关键词判断更准
_BY_SOURCE = {
    "Product Hunt":   PRODUCTS,
    "OpenAI Blog":    MODELS,
    "Anthropic News": MODELS,
    "Google AI":      MODELS,
    "DeepMind":       MODELS,
    "Hugging Face":   MODELS,
    "r/LocalLLaMA":   MODELS,
    "r/OpenAI":       MODELS,
    "机器之心":        NEWS,
    "量子位":          NEWS,
    "36氪AI":          NEWS,
    "Hacker News":    NEWS,
    "r/singularity":     NEWS,
    "r/MachineLearning": MODELS,
}

_MODEL_HINTS = ("发布", "推出", "release", "launch", "unveil", "announce", "preview", "model", "模型", "gpt-", "claude ", "gemini ", "llama")


def classify(item: Item) -> str:
    if item.source_kind == "github":
        return GITHUB
    if item.source in _BY_SOURCE:
        bucket = _BY_SOURCE[item.source]
        # HN/r/singularity 里如果明显是发布/模型，归到「模型」
        if bucket == NEWS:
            t = item.title.lower()
            if any(h in t for h in _MODEL_HINTS):
                return MODELS
        return bucket
    return NEWS


def bucketize(items: list[Item]) -> dict[str, list[Item]]:
    buckets: dict[str, list[Item]] = {PRODUCTS: [], NEWS: [], MODELS: [], GITHUB: []}
    for it in items:
        buckets[classify(it)].append(it)
    # 每个 bucket 内按 score 倒序，再按时间倒序
    for k in buckets:
        buckets[k].sort(
            key=lambda x: (x.score, x.published_at.timestamp() if x.published_at else 0),
            reverse=True,
        )
    return buckets
