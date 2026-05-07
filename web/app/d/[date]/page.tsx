import Link from "next/link";
import { notFound } from "next/navigation";
import { listDates, loadReport, neighbors, type Item } from "@/lib/reports";

export const dynamic = "force-static";

export async function generateStaticParams() {
  const dates = await listDates();
  return dates.map((date) => ({ date }));
}

const SECTION_ORDER = ["products", "news", "models", "github"] as const;

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function relTime(iso: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  const diff = Date.now() - t;
  const m = Math.floor(diff / 60_000);
  if (m < 1) return "刚刚";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  return `${d}d`;
}

function sectionTag(key: string): string {
  return (
    {
      products: "产品",
      news: "新闻",
      models: "模型",
      github: "GitHub",
    } as Record<string, string>
  )[key] ?? "";
}

function ItemRow({ it, n, sectionKey }: { it: Item; n: number; sectionKey: string }) {
  const tag = sectionTag(sectionKey);
  const score = it.score ?? 0;
  return (
    <li className="flex gap-2 py-2 border-b border-gray-200 last:border-0">
      <div className="text-muted text-xs w-7 text-right pt-1 tabular-nums select-none">
        {n}.
      </div>
      <div className="flex-1 min-w-0">
        <div className="leading-snug">
          {score > 0 && (
            <span className="text-muted text-xs mr-1 tabular-nums">▲ {score}</span>
          )}
          <a
            href={it.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[15px] sm:text-base text-black hover:underline break-words"
          >
            {it.title}
          </a>
          {hostOf(it.url) && (
            <span className="text-muted text-xs ml-1.5">({hostOf(it.url)})</span>
          )}
        </div>
        <div className="text-muted text-xs mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5">
          <span>{it.source}</span>
          {it.published_at && <span>· {relTime(it.published_at)}</span>}
          {tag && <span className="bg-gray-200 text-gray-700 px-1 rounded-sm">{tag}</span>}
        </div>
        {it.summary && it.summary !== it.title && (
          <p className="text-gray-700 text-sm mt-1 leading-relaxed">{it.summary}</p>
        )}
      </div>
    </li>
  );
}

export default async function DatePage({ params }: { params: Promise<{ date: string }> }) {
  const { date } = await params;
  const report = await loadReport(date);
  if (!report) notFound();
  const { prev, next } = await neighbors(date);

  let counter = 0;

  return (
    <div className="bg-card">
      <div className="px-3 py-2 border-b border-gray-200 flex items-center justify-between text-sm">
        <div>
          <span className="font-semibold">{report.date}</span>
          <span className="text-muted ml-2">
            新增 {report.stats.new_count} · 累计 {report.stats.total_seen}
          </span>
        </div>
        <div className="flex gap-3 text-link">
          {prev ? (
            <Link href={`/d/${prev}`} prefetch>
              ‹ 前一天
            </Link>
          ) : (
            <span className="text-muted">‹ 前一天</span>
          )}
          {next ? (
            <Link href={`/d/${next}`} prefetch>
              后一天 ›
            </Link>
          ) : (
            <span className="text-muted">后一天 ›</span>
          )}
        </div>
      </div>

      {report.stats.new_count === 0 && (
        <div className="px-4 py-6 text-muted text-sm">今日各源暂无新增内容。</div>
      )}

      {SECTION_ORDER.map((key) => {
        const items = report.sections[key] ?? [];
        if (items.length === 0) return null;
        const title = report.section_titles[key] ?? key;
        return (
          <section key={key}>
            <h2 className="px-3 py-1.5 bg-gray-100 text-sm font-semibold border-y border-gray-200">
              {title} <span className="text-muted font-normal">({items.length})</span>
            </h2>
            <ol className="px-3">
              {items.map((it) => {
                counter += 1;
                return <ItemRow key={`${key}-${it.url}`} it={it} n={counter} sectionKey={key} />;
              })}
            </ol>
          </section>
        );
      })}
    </div>
  );
}
