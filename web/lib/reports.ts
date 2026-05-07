import fs from "node:fs/promises";
import path from "node:path";

export type Item = {
  title: string;
  url: string;
  source: string;
  source_kind: string;
  published_at: string | null;
  score: number;
  summary: string;
  extra: Record<string, unknown>;
};

export type Report = {
  date: string;
  generated_at: string;
  stats: { new_count: number; total_seen: number };
  section_titles: Record<string, string>;
  sections: Record<string, Item[]>;
};

const DATA_DIR = path.join(process.cwd(), "data");

export async function listDates(): Promise<string[]> {
  try {
    const entries = await fs.readdir(DATA_DIR);
    return entries
      .filter((f) => /^\d{4}-\d{2}-\d{2}\.json$/.test(f))
      .map((f) => f.replace(/\.json$/, ""))
      .sort()
      .reverse();
  } catch {
    return [];
  }
}

export async function loadReport(date: string): Promise<Report | null> {
  try {
    const file = path.join(DATA_DIR, `${date}.json`);
    const raw = await fs.readFile(file, "utf-8");
    return JSON.parse(raw) as Report;
  } catch {
    return null;
  }
}

export async function latestDate(): Promise<string | null> {
  const dates = await listDates();
  return dates[0] ?? null;
}

/** 上一天 / 下一天的导航辅助：传入当前日期，返回有效的相邻日期或 null。 */
export async function neighbors(date: string): Promise<{ prev: string | null; next: string | null }> {
  const dates = await listDates(); // 倒序
  const idx = dates.indexOf(date);
  if (idx === -1) return { prev: null, next: null };
  return {
    prev: dates[idx + 1] ?? null, // 更早一天
    next: dates[idx - 1] ?? null, // 更晚一天
  };
}
