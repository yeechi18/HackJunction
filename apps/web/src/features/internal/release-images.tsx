"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import { apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import type { InternalOrder } from "@/features/orders/types";

type Task = { id: string; kind: string; title: string; status: string; asset_url?: string; stale: boolean; concept_only: boolean };

export function ReleaseImages({ order, csrfToken, refresh }: { order: InternalOrder; csrfToken: string; refresh: () => void }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [checked, setChecked] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [queued, setQueued] = useState(false);
  const [commandId, setCommandId] = useState("");
  const command = order.commands.find(item => item.id === commandId);
  const failed = !!command && ["failed", "stale", "cancelled"].includes(command.status);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const root = `/internal/orders/${order.id}/image-tasks`;
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    apiFetch<{ items: Task[] }>(root, { signal: controller.signal }).then(result => {
      if (!active) return;
      const latest = new Map<string, Task>();
      for (const task of result.items) {
        if (!task.stale && task.asset_url && ["returned", "reviewed"].includes(task.status) && !latest.has(task.kind)) latest.set(task.kind, task);
      }
      setTasks([...latest.values()]); setError("");
    }).catch(caught => { if (active) setError(errorMessage(caught)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [root, reload]);

  async function adopt() {
    if (busy || queued || order.pending_command_id || !tasks.length || tasks.some(t => t.concept_only || !checked.includes(t.id))) return;
    const main = tasks.find(t => t.kind === "main");
    if (!main) return;
    setBusy(true); setError("");
    try {
      // Re-read state so retrying a partial save never approves the same task twice.
      const latest = await apiFetch<{ items: Task[] }>(root);
      for (const task of tasks) {
        const current = latest.items.find(item => item.id === task.id);
        if (!current || current.stale || current.concept_only || current.asset_url !== task.asset_url || !["returned", "reviewed"].includes(current.status)) throw new Error("图片状态已变化，请重新读取并核对图片。");
        if (current.status === "returned") await apiFetch(`${root}/${task.id}/status`, {
          method: "POST", headers: csrfHeaders(csrfToken),
          body: JSON.stringify({ action: "approve", note: "负责人在交付审批区查看图片并勾选确认商品一致性，采用为本次候选图片。" }),
        });
      }
      const result = await apiFetch<{ command_id: string }>(`${root}/${main.id}/submit`, { method: "POST", headers: csrfHeaders(csrfToken) });
      setCommandId(result.command_id); setQueued(true); refresh();
    } catch (caught) { setError(errorMessage(caught)); refresh(); }
    finally { setBusy(false); }
  }
  return <section className="release-images" aria-label="补齐本版交付图片">
    <h4>先确认采用已生成的图片</h4>
    <p>图片已回传，但还没加入交付版本。在这里逐张核对后确认采用，系统会建立包含图片的新候选；准备完成后，再确认新版本的问题并放行。</p>
    {loading && <p role="status">正在读取本订单图片…</p>}
    <div className="release-image-grid">{tasks.map(task => <div key={task.id}>
      <a href={task.asset_url} target="_blank" rel="noreferrer"><Image src={task.asset_url!} alt={task.title} width={260} height={260} unoptimized /></a>
      <label><input type="checkbox" checked={checked.includes(task.id)} disabled={busy || queued || task.concept_only || !!order.pending_command_id} onChange={event => setChecked(items => event.target.checked ? [...items, task.id] : items.filter(id => id !== task.id))} />{task.title}：已核对商品一致性，确认采用</label>
      {task.concept_only && <p>概念预览不能作为实物商品图交付，请补充实物资料。</p>}
    </div>)}</div>
    {!loading && !tasks.length && <p>当前没有可采用的回传图片，请先完成图片制作。</p>}
    {!loading && tasks.length > 0 && !tasks.some(t => t.kind === "main") && <p>还缺少主图，请先完成主图制作。</p>}
    {!queued && tasks.length > 0 && <button className="button button-dark button-small" type="button" disabled={loading || busy || !!order.pending_command_id || !tasks.some(t => t.kind === "main") || tasks.some(t => t.concept_only || !checked.includes(t.id))} onClick={adopt}>{busy ? "正在加入交付候选…" : "确认采用图片，准备交付版本"}</button>}
    {queued && !failed && <p role="status">图片已提交，正在准备新候选并记录图文审校结果。页面会自动更新；新候选出现后请再次点击“同意放行并交付”。即使 AI 审校未通过，仍可确认问题后人工放行。</p>}
    {failed && <p role="alert">新候选准备未完成：{order.workflow_error || "任务已失败或依据已变化，请核对最新资料后重试。"}<button type="button" className="text-button" onClick={() => { setQueued(false); setChecked([]); setReload(value => value + 1); }}>重新核对并重试</button></p>}
    {error && <p role="alert">{error}</p>}
    {!queued && <button type="button" className="text-button" disabled={busy} onClick={() => { setChecked([]); setLoading(true); setReload(value => value + 1); }}>重新读取图片</button>}
  </section>;
}
