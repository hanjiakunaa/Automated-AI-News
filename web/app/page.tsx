import { redirect } from "next/navigation";
import { latestDate } from "@/lib/reports";

export const dynamic = "force-static";

export default async function Home() {
  const latest = await latestDate();
  if (!latest) {
    return (
      <div className="bg-card p-6 text-center text-muted">
        暂无数据。等首次抓取完成后会出现在这里。
      </div>
    );
  }
  redirect(`/d/${latest}`);
}
