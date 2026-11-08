"use client";

import { useEffect, useState } from "react";
import { apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import type { InternalOrder } from "@/features/orders/types";

type Proposal = { kind: string; title: string; prompt: string };
type Brief = { revision: number; input_digest: string; concept_only: boolean; proposals: Proposal[]; references: { id: string; url: string }[] };
type TaskList = { items: Task[]; automatic_generation: boolean; auto_on_upload: boolean; image_model: string; prompt_model: string };
type Task = Proposal & { id: string; status: string; concept_only: boolean; stale: boolean; asset_url?: string; note?: string; tool?: string; prompt_model?: string; image_model?: string; history: { status: string; at: string; note?: string }[] };
const labels: Record<string, string> = { queued: "已排队 · 等待自动制作", planning: "MiMo 正在编写商品专属提示词", generating: "Seedream 正在绘图", unknown: "调用结果不确定 · 核查后可重试", prepared: "任务已生成 · 尚未调用工具", started: "已记录外部工具调用 · 等待回传", failed: "制作失败 · 可重试", returned: "结果已回传 · 待核对", reviewed: "图片核对通过 · 尚非交付批准", rejected: "核对未通过 · 请新建任务" };

export function ImageTasksPanel({ order, csrfToken, refresh }: { order: InternalOrder; csrfToken: string; refresh: () => void }) {
  const root = `/internal/orders/${order.id}`;
  const [brief, setBrief] = useState<Brief | null>(null);
  const [capability, setCapability] = useState<TaskList | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [kind, setKind] = useState("main");
  const [adjustment, setAdjustment] = useState("");
  const [tool, setTool] = useState("Codex 内置图片工具");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const blocked = busy || Boolean(order.pending_command_id);
  const proposal = brief?.proposals.find(p => p.kind === kind) ?? brief?.proposals[0];

  useEffect(() => {
    let cancelled = false;
    const load = () => Promise.all([apiFetch<Brief>(`${root}/image-brief`), apiFetch<TaskList>(`${root}/image-tasks`)])
      .then(([b, t]) => { if (!cancelled) { setBrief(b); setTasks(t.items); setCapability(t); } })
      .catch(e => { if (!cancelled) setError(errorMessage(e)); });
    void load();
    const timer = window.setInterval(load, 5000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [root, order.revision, order.pending_command_id]);

  async function perform(action: () => Promise<void>, success: string) {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await action();
      const [b, t] = await Promise.all([apiFetch<Brief>(`${root}/image-brief`), apiFetch<TaskList>(`${root}/image-tasks`)]);
      setBrief(b); setTasks(t.items); setCapability(t); setMessage(success); refresh();
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  function update(task: Task, action: string) {
    void perform(async () => {
      await apiFetch(`${root}/image-tasks/${task.id}/status`, { method: "POST", headers: csrfHeaders(csrfToken), body: JSON.stringify({ action, tool, note }) });
    }, action === "approve" ? "已记录图片核对；概念图仅内部预览，实物图仍需本版图文审校与负责人放行。" : "制作记录已更新。");
  }

  return <details><summary>商品图片制作任务与回传 · {tasks.length} 个任务</summary>
    <p className="field-help">{capability?.automatic_generation ? `MiMo 自动编写商品专属提示词 → Seedream 实际绘图并保存 → 图片核对 → 完整图文审校 → 负责人放行。${capability.auto_on_upload ? "卖家上传参考图后会自动排队，无需回到 Codex。" : "点击自动制作即可在后台完成。"}` : "保存或复制任务不会调用绘图工具；可在外部制作后回传核对。"}</p>
    <button className="button button-outline button-small" disabled={busy} onClick={() => void perform(async () => {}, "已重新加载商品资料、参考图与制作记录。")}>刷新任务依据与状态</button>
    {brief?.concept_only && <p className="form-notice">当前缺少实物依据或资料标注为虚拟演示，只能制作带水印的概念预览，不进入正式交付。</p>}
    {brief && proposal && <>
      <div className="field"><label htmlFor="image-task-kind">按资料选择图片类型</label><select id="image-task-kind" value={proposal.kind} onChange={e => setKind(e.target.value)}>{brief.proposals.map(p => <option key={p.kind} value={p.kind}>{p.title}</option>)}</select></div>
      <p className="field-help">只建议资料支持的类型；未确认尺寸不制作尺寸文字，未明确用途不强行制作场景图。</p>
      <details><summary>查看本图提示词与参考图</summary><textarea aria-label="自动图片提示词" rows={12} value={proposal.prompt} readOnly />
        <ul>{brief.references.map((r, i) => <li key={r.id}><a href={r.url} target="_blank" rel="noreferrer">参考图 {i + 1}</a></li>)}</ul>
      </details>
      <div className="field"><label htmlFor="image-adjustment">负责人调整构图或风格</label><textarea id="image-adjustment" value={adjustment} onChange={e => setAdjustment(e.target.value)} maxLength={4000} rows={3} placeholder="例如：保持原角度，背景改为浅木桌面；不能改动商品事实。" /></div>
      <button className="button button-outline button-small" disabled={blocked} onClick={() => void perform(async () => {
        await apiFetch(`${root}/image-tasks`, { method: "POST", headers: csrfHeaders(csrfToken), body: JSON.stringify({ expected_revision: brief.revision, input_digest: brief.input_digest, kind: proposal.kind, adjustment }) });
      }, "任务已保存，尚未调用绘图工具。")}>保存新制作任务</button>
      {capability?.automatic_generation && <button className="button button-dark button-small" disabled={blocked} onClick={() => void perform(async () => {
        const task = await apiFetch<Task>(`${root}/image-tasks`, { method: "POST", headers: csrfHeaders(csrfToken), body: JSON.stringify({ expected_revision: brief.revision, input_digest: brief.input_digest, kind: proposal.kind, adjustment }) });
        await apiFetch(`${root}/image-tasks/${task.id}/generate`, { method: "POST", headers: csrfHeaders(csrfToken) });
      }, "已排队；后台自动编写提示词、调用 Seedream 并保存候选图，完成后仍需审核。")}>自动编写提示词并生成本图</button>}
    </>}
    <details><summary>手动工具回传设置</summary><div className="field"><label htmlFor="image-tool">实际使用的工具（手动记录）</label><input id="image-tool" value={tool} onChange={e => setTool(e.target.value)} maxLength={120} /></div></details>
    <div className="field"><label htmlFor="image-review-note">调用凭据、真实失败原因或核对意见（至少 8 字）</label><textarea id="image-review-note" value={note} onChange={e => setNote(e.target.value)} rows={2} maxLength={2000} placeholder="核对形状、颜色、结构、数量、标识、规格及生成缺陷；不要记录密钥。" /></div>
    {tasks.map(task => <details key={task.id}>
      <summary>{task.title} · {task.stale ? "依据已过期，请新建任务" : labels[task.status]}{task.concept_only ? " · 概念预览" : ""}</summary>
      <p className="field-help">任务 {task.id} · {task.tool ?? "未调用"}</p>
      <textarea aria-label="已保存的制作提示词" rows={8} value={task.prompt} readOnly />
      <button className="button button-outline button-small" disabled={busy} onClick={() => void perform(() => navigator.clipboard.writeText(task.prompt), "提示词已复制；这不会发起绘图。")}>复制完整提示词</button>
      {task.asset_url && <p><a href={task.asset_url} target="_blank" rel="noreferrer">查看回传图片 ↗</a></p>}
      {task.note && <p>{task.note}</p>}
      {task.prompt_model && <p className="field-help">提示词：{task.prompt_model} · 绘图：{task.image_model}</p>}
      {!task.stale && <>
        {capability?.automatic_generation && ["prepared", "failed", "unknown"].includes(task.status) && <>
          {task.status === "unknown" && <p className="field-help">远端可能已完成或计费，请核查方舟调用记录；再次点击会发起新调用。</p>}
          <button className="button button-dark button-small" disabled={blocked} onClick={() => void perform(async () => {
            await apiFetch(`${root}/image-tasks/${task.id}/generate`, { method: "POST", headers: csrfHeaders(csrfToken) });
          }, "已排队自动制作；页面将更新进度。")}>{task.status === "prepared" ? "自动制作" : "重新调用 Seedream"}</button>
        </>}
        {["prepared", "failed"].includes(task.status) && <button className="button button-outline button-small" disabled={blocked || note.trim().length < 8 || !tool.trim()} onClick={() => update(task, "started")}>记录已在外部调用工具{task.status === "failed" ? "（重试）" : ""}</button>}
        {task.status === "started" && <>
          <label className="field">上传本任务结果（至少 1024×1024 正方形）<input type="file" accept="image/png,image/jpeg,image/webp" disabled={blocked} onChange={e => {
            const file = e.target.files?.[0]; if (!file) return;
            void perform(async () => { const body = new FormData(); body.append("file", file); await apiFetch(`${root}/image-tasks/${task.id}/result`, { method: "POST", headers: csrfHeaders(csrfToken), body, timeoutMs: 30000 }); }, "结果已回传，等待核对；没有自动批准或替换旧图片。");
            e.target.value = "";
          }} /></label>
          <button className="button button-outline button-small" disabled={blocked || note.trim().length < 8} onClick={() => update(task, "failed")}>记录制作失败</button>
        </>}
        {task.status === "returned" && <>
          <button className="button button-dark button-small" disabled={blocked || note.trim().length < 8} onClick={() => update(task, "approve")}>{task.concept_only ? "概念预览核对通过（不能交付）" : "已对照实物核对"}</button>
          <button className="button button-outline button-small" disabled={blocked || note.trim().length < 8} onClick={() => update(task, "reject")}>驳回，保留记录</button>
        </>}
        {task.status === "reviewed" && !task.concept_only && <>
          <p className="field-help">先核对本组所有图片，再提交。每种类型取当前资料下最近一次核对通过的结果，必须包含主图。生成新候选，不覆盖已批准图片。</p>
          <button className="button button-dark button-small" disabled={blocked} onClick={() => void perform(async () => {
            await apiFetch(`${root}/image-tasks/${task.id}/submit`, { method: "POST", headers: csrfHeaders(csrfToken) });
          }, "新候选已排队，等待完整图文审校；完成后在主要成品审批区重新放行。")}>将本组实物图片提交为新候选</button>
        </>}
      </>}
      <ol>{task.history.map((h, i) => <li key={i}>{labels[h.status]} · {new Date(h.at).toLocaleString()}{h.note ? ` · ${h.note}` : ""}</li>)}</ol>
    </details>)}
    {busy && <p role="status">正在保存…</p>}{message && <p role="status">{message}</p>}{error && <p role="alert">{error} 可修正后重试；已保存的任务和旧交付保持保留。</p>}
  </details>;
}
