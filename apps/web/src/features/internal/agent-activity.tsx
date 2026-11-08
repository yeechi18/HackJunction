"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { EventsPanel } from "@/features/orders/order-panels";
import { type InternalOrder, type OrderEvent } from "@/features/orders/types";
import { workflowProgress } from "@/features/orders/workflow-progress";
import { AgentRunsPanel } from "./agent-runs-panel";
import styles from "./agent-activity.module.css";

const tasks: Record<string, string> = {
  INTAKE: "接待 Agent · 正在整理商品资料",
  NEEDS_INFO: "接待 Agent · 正在核对补充资料",
  RESEARCH: "研究 Agent · 正在分析商品与定价依据",
  GENERATION: "生成 Agent · 正在制作英文文案",
  REVIEW: "审校 Agent · 正在核对事实与成品",
  NEEDS_IMAGE: "生成 Agent · 正在处理商品图片任务",
  PUBLISHING: "正在保存成品与售价确认",
};
const runTasks: Record<string, string> = {
  acquisition: "接待 Agent · 正在审查商品资料",
  copy_preview: "生成 Agent · 正在撰写英文初稿",
  image_prompt: "生成 Agent · 正在编写商品图片提示词",
  research: tasks.RESEARCH, generation: tasks.GENERATION, review: tasks.REVIEW,
};

type ImageTask = { id: string; title: string; status: string; stale: boolean; asset_url?: string; note?: string };

export function AgentActivity({ order, events }: { order: InternalOrder; events: OrderEvent[] }) {
  const [images, setImages] = useState<ImageTask[]>([]);
  const [imageError, setImageError] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function load() {
      try {
        const result = await apiFetch<{ items: ImageTask[] }>(`/internal/orders/${order.id}/image-tasks`, { signal: controller.signal });
        if (!cancelled) { setImages(result.items); setImageError(false); }
      } catch { if (!cancelled) setImageError(true); }
      finally { if (!cancelled) timer = setTimeout(load, 3000); }
    }
    void load();
    return () => { cancelled = true; controller.abort(); clearTimeout(timer); };
  }, [order.id]);
  const currentImages = images.filter(item => !item.stale);
  const drawing = currentImages.find(item => ["planning", "generating"].includes(item.status));
  const returned = currentImages.filter(item => item.asset_url && ["returned", "reviewed"].includes(item.status));
  const imageQueued = currentImages.some(item => item.status === "queued");
  const progress = workflowProgress(order);
  const active = ["running", "processing"].includes(order.workflow_status) || Boolean(drawing && !imageError);
  const currentRun = [...(order.agent_runs ?? [])].reverse().find(run => run.input_revision === order.revision && run.status === "running");
  const title = drawing && !imageError ? drawing.status === "planning" ? "生成 Agent · 正在编写图片提示词" : "生成 Agent · 正在绘图"
    : !["running", "processing"].includes(order.workflow_status) && order.phase === "NEEDS_IMAGE"
      ? imageError ? "Agent 动态 · 图片状态暂时无法读取" : returned.length ? `图片已生成 ${returned.length} 张 · 待核对与提交审校` : imageQueued ? "图片制作已排队 · 等待开始" : "Agent 已暂停 · 等待图片资料或制作结果"
    : order.phase === "AWAITING_SELLER"
    ? "已放行交付 · 等待卖家确认"
    : active ? (currentRun && runTasks[currentRun.role]) || tasks[order.phase] || progress.title : progress.title;
  return <details className={styles.activity} data-active={active}>
    <summary>
      <span className={styles.dot} data-failed={order.workflow_status === "failed"} aria-hidden="true" />
      <span className={styles.current} role="status" aria-live="polite">{title}</span>
      <span className={styles.hint}>查看执行过程</span><span className={styles.chevron} aria-hidden="true">⌄</span>
    </summary>
    <div className={styles.body}>
      {returned.length > 0 && <><div className={styles.images}>{returned.map(item => <a key={item.id} href={item.asset_url} target="_blank" rel="noreferrer"><Image src={item.asset_url!} width={240} height={240} unoptimized alt={item.title} /><span>{item.title} · {item.status === "reviewed" ? "已核对，尚未交付" : "待核对"}</span></a>)}</div><a className="text-link" href="#research-integrations" onClick={() => {
        let target: HTMLElement | null = document.getElementById("research-integrations");
        while (target) { if (target instanceof HTMLDetailsElement) target.open = true; target = target.parentElement; }
      }}>打开图片核对与提交审校 →</a></>}
      {imageError && <p role="status">图片状态读取失败，正在自动重试；已显示图片为上次读取结果。</p>}
      <p className="field-help">{returned.length && order.phase === "NEEDS_IMAGE" ? "图片已回传，尚未完成核对、完整图文审校和负责人放行。" : progress.description} 状态随页面自动刷新，以下为实际执行记录。</p>
      <EventsPanel events={events} />
      <AgentRunsPanel runs={order.agent_runs ?? []} />
    </div>
  </details>;
}
