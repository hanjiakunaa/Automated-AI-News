# 🛰️ AI News Radar

每天 5 分钟，掌握全球 AI 圈最热的产品、新闻、模型动向。

它会自动抓取一堆 AI 媒体、官方博客、Reddit、Hacker News、GitHub Trending、Product Hunt，过滤出当天值得关注的内容，整理成一份本地 Markdown 简报。

## 它能给你看到什么

- 🔥 **爆款 AI 产品** — Product Hunt 当日新品
- 📰 **AI 重要新闻** — 36 氪、量子位、Hacker News 上当天讨论最热的 AI 话题
- 🚀 **大模型动态** — OpenAI / Google / DeepMind / Hugging Face 官方博客 + r/LocalLLaMA、r/OpenAI 高赞帖
- 💻 **GitHub AI 热门项目** — 当日 trending（全语言 + Python）

每条带原文链接、来源、热度、一句话描述。

---

## 零基础使用步骤

### 1. 装依赖（只需做一次）

打开「终端」（Terminal.app），把下面这行粘贴进去回车：

```bash
cd ~/Desktop/AI-News && pip3 install -r requirements.txt
```

### 2. 跑一次

```bash
python3 ~/Desktop/AI-News/main.py
```

大约 30 秒后，终端会告诉你报告生成在哪里，比如：

```
✅ 报告已生成：/Users/你/Desktop/AI-News/reports/2026-05-06.md
```

### 3. 打开报告

直接双击 `reports/` 目录下的当日 `.md` 文件。
推荐用 [Typora](https://typora.io/) 或 VS Code 打开，链接可以直接点。

---

## 让它每天自动跑（可选）

macOS 用 `launchd` 配置每天早上 9 点自动跑：

```bash
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/com.ainews.radar.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ainews.radar</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/python3</string>
    <string>/Users/你的用户名/Desktop/AI-News/main.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/tmp/ai-news.log</string>
  <key>StandardErrorPath</key><string>/tmp/ai-news.err</string>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.ainews.radar.plist
```

把 `/Users/你的用户名/` 改成你自己路径（终端跑 `whoami` 看用户名）。

---

## 想增删数据源？

只改一个文件：[`config.py`](./config.py)。

- **加 RSS**：往 `RSS_FEEDS` 字典里加一行 `"名字": "URL"`。
- **加 Reddit 子版块**：往 `REDDIT_SUBS` 加 `("r/xxx", "https://www.reddit.com/r/xxx/hot.json?limit=20")`。
- **改关键词**：编辑 `AI_KEYWORDS` 列表。
- **改每个板块最多显示几条**：改 `SECTION_LIMIT`。

---

## 目录结构

```
AI-News/
├── main.py              # 一键入口
├── config.py            # 数据源 / 关键词 / 阈值，唯一需要改的配置
├── requirements.txt
├── sources/             # 各数据源抓取器（RSS、HN、Reddit、GitHub）
├── core/                # 过滤 / 去重 / 分类 / 渲染
├── templates/daily.md.j2
├── data/seen.db         # 自动生成，记录见过的 URL 用于跨天去重
└── reports/YYYY-MM-DD.md
```

---

## 部署到云端：网页 + 微信推送（v0.3）

不想每天手动跑？把项目推到 GitHub 后可以做到：
- ✅ **每天 09:00 北京时间自动抓取**（GitHub Actions cron）
- ✅ **抓完推送到微信**（PushPlus 公众号）
- ✅ **手机网页随时翻历史**（Vercel 自动部署 `web/`）

### 一次性配置（10 分钟）

1. **把项目推到 GitHub**（必须是独立仓库）：
   ```bash
   cd ~/Desktop/AI-News
   git init && git add . && git commit -m "init"
   gh repo create ai-news --private --source=. --push
   ```

2. **配置 PushPlus 微信推送**：
   - 打开 https://www.pushplus.plus/ → 微信扫码登录
   - 复制页面上的 `token`
   - GitHub 仓库 → Settings → Secrets and variables → Actions → New secret
     - Name: `PUSHPLUS_TOKEN`，Value: 你的 token

3. **（可选）配置 Claude 摘要**：
   - 添加 secret：`ANTHROPIC_API_KEY` = 你的 Anthropic API key

4. **部署网页到 Vercel**：
   - 打开 https://vercel.com/ → Import Git Repository → 选择你刚推的仓库
   - **Root Directory** 设置为 `web/`（重要！否则会构建失败）
   - Framework 自动识别 Next.js，点 Deploy
   - 几十秒后拿到 `xxx.vercel.app` 域名

5. **手动触发一次验证**：
   - GitHub 仓库 → Actions → Daily AI News → Run workflow
   - 跑完会看到一条 commit `data: 2026-XX-XX`，Vercel 自动重新部署
   - 手机微信收到一条 PushPlus 推送

之后每天 09:00（北京时间）自动跑一次，无需任何操作。

### 改推送时间？

编辑 `.github/workflows/daily.yml`，改 cron 表达式（用 UTC 时间）：
```yaml
schedule:
  - cron: "0 1 * * *"  # 北京 09:00 = UTC 01:00
```

### 网页本地预览

```bash
cd web && npm install && npm run dev
# 浏览器打开 http://localhost:3000
```

---

## 路线图

- ~~**v0.2** Claude API 中文摘要~~ ✅
- ~~**v0.3** 微信推送 + Web 仪表盘~~ ✅
- **v0.4** 趋势分析：同一主题（比如「Claude 4.7」）的连续多天热度曲线
- **v0.5** RSS 订阅（让别人也能在自己的 RSS 阅读器里看）

---

## 出问题了？

- **某个源 `[FAIL] xxx`**：一般是网站临时抽风或地区不可达。其它源照常工作，不影响整体。
- **报告全空**：先 `ping google.com` 看网络。如果 RSS 全 0，多半是 Python 没装根证书——本工具已经规避了 macOS 系统 Python 的 SSL 问题，应不再出现。
- **想完全重置**：删掉 `data/seen.db` 即可，下次跑会重新抓。
