import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI News Radar",
  description: "每天 5 分钟，掌握全球 AI 圈最热的产品、新闻、模型动向。",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body className="font-sans">
        <div className="max-w-3xl mx-auto px-3 sm:px-4 py-3">
          <header className="bg-accent text-white px-3 py-2 rounded-sm flex items-center justify-between text-sm">
            <a href="/" className="font-bold no-underline hover:no-underline flex items-center gap-2">
              <span>🛰️</span>
              <span>AI News Radar</span>
            </a>
            <span className="text-xs opacity-90 hidden sm:inline">
              每天 5 分钟掌握 AI 圈
            </span>
          </header>
          <main className="mt-3">{children}</main>
          <footer className="text-muted text-xs mt-8 mb-4 text-center">
            自动抓取 RSS / Hacker News / Reddit / GitHub Trending · 每日 09:00 北京时间更新
          </footer>
        </div>
      </body>
    </html>
  );
}
