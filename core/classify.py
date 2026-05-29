"""把条目归到板块。v0.4 起从 4 个扩展到 7 个，覆盖 AI 生态更完整。"""
from sources.base import Item

# 板块 key
PRODUCTS = "products"
NEWS = "news"
MODELS = "models"
GITHUB = "github"
SKILLS_MCP = "skills_mcp"   # v0.4: MCP / Claude Skills / Agent SDK 生态
TOOLS = "tools"             # v0.4: AI 工具 & Coding Agent
PAPERS = "papers"           # v0.4: 论文与研究

SECTION_TITLES = {
    SKILLS_MCP: "🧩 MCP & Skills 生态",
    MODELS:     "🚀 大模型动态",
    NEWS:       "📰 AI 重要新闻",
    PRODUCTS:   "🔥 爆款 AI 产品",
    TOOLS:      "🛠️ AI 工具 & Coding Agent",
    PAPERS:     "📜 论文与研究",
    GITHUB:     "💻 GitHub AI 热门项目",
}

# 报告里板块的展示顺序：把「重要 / 时效性强」的放前面
SECTION_ORDER = [SKILLS_MCP, MODELS, NEWS, PRODUCTS, TOOLS, PAPERS, GITHUB]


# 严格按来源归类（比关键词判断更准）
_BY_SOURCE = {
    # 产品发现
    "Product Hunt":            PRODUCTS,

    # 模型厂官方 → 模型板块
    "OpenAI Blog":             MODELS,
    "Anthropic News":          MODELS,
    "Google AI":               MODELS,
    "DeepMind":                MODELS,
    "Hugging Face":            MODELS,
    "Meta AI":                 MODELS,
    "NVIDIA AI":               MODELS,
    "Mistral News":            MODELS,
    "xAI News":                MODELS,
    "Microsoft Research":      MODELS,

    # AI 媒体（中文+英文）→ 新闻板块
    "量子位":                   NEWS,
    "36氪":                     NEWS,
    "AI科技评论":                NEWS,
    "APPSO":                    NEWS,
    "TechCrunch AI":            NEWS,
    "The Decoder":              NEWS,
    "VentureBeat AI":           NEWS,
    "The Verge AI":             NEWS,
    "Ars Technica AI":          NEWS,
    "Hacker News":              NEWS,
    "r/singularity":            NEWS,

    # 模型/技术深度内容
    "r/LocalLLaMA":             MODELS,
    "r/MachineLearning":        MODELS,
    "r/OpenAI":                 MODELS,
    "Latent Space":             MODELS,
    "Import AI":                MODELS,
    "Interconnects":            MODELS,

    # MCP & Skills & Agent 生态
    "r/ClaudeAI":               SKILLS_MCP,
    "r/AI_Agents":              SKILLS_MCP,
    "r/mcp":                    SKILLS_MCP,

    # AI 工具 / Coding Agent
    "r/cursor":                 TOOLS,
    "Cursor Changelog":         TOOLS,
    "GitHub Blog":              TOOLS,
    "Vercel AI":                TOOLS,

    # 论文 / 研究
    "arXiv cs.AI":              PAPERS,
    "arXiv cs.CL":              PAPERS,
    "arXiv cs.LG":              PAPERS,
    "HF Papers":                PAPERS,
}

# 标题中出现这些词，从 NEWS 升格到 MODELS 板块
_MODEL_HINTS = (
    "发布", "推出", "release", "launch", "unveil", "announce", "preview",
    "model", "模型", "gpt-", "claude ", "gemini ", "llama", "qwen",
    "sonnet", "haiku", "opus", "deepseek",
)

# 标题中出现这些词，进入 SKILLS_MCP 板块（跨源识别）
_SKILLS_MCP_HINTS = (
    "mcp", "model context protocol",
    "claude skills", " skills ", "skill ",  # 带空格避免误伤 "skillset"
    "agent sdk", "agentic ", "ai agent",
    "computer use", "tool use",
)

# 标题中出现这些词，进入 TOOLS 板块
_TOOLS_HINTS = (
    "cursor", "windsurf", "cline", "devin",
    "claude code", "github copilot", "codex",
    "v0.dev", "bolt.new", "lovable", "replit",
    "aider", "continue.dev",
)


def classify(item: Item) -> str:
    if item.source_kind == "github":
        return GITHUB
    if item.source_kind == "hfpapers":
        return PAPERS

    # 先按标题升格（跨源识别）
    t = item.title.lower()
    if any(h in t for h in _SKILLS_MCP_HINTS):
        return SKILLS_MCP
    if any(h in t for h in _TOOLS_HINTS):
        return TOOLS

    # 按来源归类
    if item.source in _BY_SOURCE:
        bucket = _BY_SOURCE[item.source]
        # 综合媒体里如果是发布/模型，升格到模型板块
        if bucket == NEWS and any(h in t for h in _MODEL_HINTS):
            return MODELS
        return bucket

    return NEWS


def bucketize(items: list[Item]) -> dict[str, list[Item]]:
    buckets: dict[str, list[Item]] = {k: [] for k in SECTION_ORDER}
    for it in items:
        buckets[classify(it)].append(it)
    # 每个 bucket 内按 score 倒序，再按时间倒序
    for k in buckets:
        buckets[k].sort(
            key=lambda x: (x.score, x.published_at.timestamp() if x.published_at else 0),
            reverse=True,
        )
    return buckets
