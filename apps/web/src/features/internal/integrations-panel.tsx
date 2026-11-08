"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ImageTasksPanel } from "./image-tasks-panel";
import { apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import type { InternalOrder } from "@/features/orders/types";

type Status = { competitor_search: boolean; search_provider: "mimo" | "brave" | "ark"; research_provider: "mimo" | "ark"; research_model?: string };

export function IntegrationsPanel({ order, csrfToken, refresh }: { order: InternalOrder; csrfToken: string; refresh: () => void }) {
  const root = `/internal/orders/${order.id}`;
  const [status, setStatus] = useState<Status | null>(null);
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("");
  const [url, setUrl] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const processing = Boolean(order.pending_command_id);
  const canResearch = ["INTAKE", "NEEDS_INFO", "AWAITING_ACCEPTANCE"].includes(order.phase) && !processing;
  const researchReason = processing ? "订单正在处理，请等待本轮任务结束。" : !canResearch ? "本轮研究与制作已开始或完成，不能直接向已审校版本追加资料。请卖家先更新商品资料，形成新版本并重新接单后，再补充研究来源。" : !source.trim() || !content.trim() ? "请填写来源名称和原始资料；来源链接可以不填。" : "来源名称和原文已填写，可以保存。";

  useEffect(() => {
    let cancelled = false;
    apiFetch<Status>(`${root}/integration-status`)
      .then(s => { if (!cancelled) setStatus(s); })
      .catch(e => { if (!cancelled) setError(errorMessage(e)); });
    return () => { cancelled = true; };
  }, [root, order.revision]);

  async function perform(action: () => Promise<string>) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try { setNotice(await action()); refresh(); } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  return <section id="research-integrations" style={{ scrollMarginTop: 24 }} className="surface workspace-panel" aria-label="图片制作与研究资料">
    <div className="panel-heading"><h2>图片制作与研究资料</h2><span>资料与制作任务</span></div>
    <details><summary>补充研究资料</summary>
      <p className="field-help">结合已确认商品资料，整理买家可能关心的问题、信息缺口与图文改进建议。接单前可补充竞品、客服记录等有来源的材料。</p>
      {!status?.competitor_search && <p className="field-help">当前支持分析已提供的资料，自动联网搜索尚未启用。这个表单用于导入来源原文。</p>}
      <div className="form-notice" role="status"><strong>{canResearch ? "保存条件" : "当前不能追加本轮研究资料"}</strong><p id="research-import-reason">{researchReason}</p>{!canResearch && order.phase === "AWAITING_RELEASE" && <Link className="text-link" href={`/o/${order.id}#delivery-facts`}>查看卖家资料与补充入口 ↗</Link>}</div>
      
      {status?.competitor_search && <>
      <form onSubmit={e => { e.preventDefault(); void perform(async () => {
        const result = await apiFetch<{ message: string; items_added: number }>(`${root}/competitor-search`, { method: "POST", headers: csrfHeaders(csrfToken), timeoutMs: 60000, body: JSON.stringify({ expected_revision: order.revision, query }) });
        return `${result.message} 新增 ${result.items_added} 条。`;
      }); }}>
        <div className="field"><label htmlFor="competitor-query">竞品英文搜索词</label><input id="competitor-query" value={query} maxLength={300} onChange={e => setQuery(e.target.value)} placeholder="silicone desk cable clips" required /></div>

        <button className="button button-outline button-small" disabled={busy || !canResearch || !query.trim() || !status?.competitor_search}>搜索并保存竞品线索</button>
      </form>
      </>}
      <form onSubmit={e => { e.preventDefault(); void perform(async () => {
        await apiFetch(`${root}/research-sources`, { method: "POST", headers: csrfHeaders(csrfToken), body: JSON.stringify({ expected_revision: order.revision, source, source_url: url || null, content }) });
        setContent(""); return "来源资料已保存；不会改写已经冻结的研究或商品事实。";
      }); }}>
        <div className="field"><label htmlFor="research-source">来源名称</label><input id="research-source" disabled={!canResearch || busy} value={source} onChange={e => setSource(e.target.value)} maxLength={500} required /></div>
        <div className="field"><label htmlFor="research-url">来源链接</label><input id="research-url" disabled={!canResearch || busy} type="url" value={url} onChange={e => setUrl(e.target.value)} maxLength={2000} placeholder="https://…" /></div>
        <div className="field"><label htmlFor="research-content">竞品、评论或客服原始资料</label><textarea id="research-content" disabled={!canResearch || busy} value={content} onChange={e => setContent(e.target.value)} maxLength={20000} rows={4} required placeholder="粘贴有权使用的原文，保留日期、商品和样本范围；不要用模型编造评论。" /></div>
        <button className="button button-outline button-small" aria-describedby="research-import-reason" disabled={busy || !canResearch || !source.trim() || !content.trim()}>保存来源资料</button>
      </form>
      {!canResearch && <p className="field-help">本轮生产已开始或订单正在处理；新增研究资料请在下一次接单前准备。</p>}
    </details>
    <ImageTasksPanel order={order} csrfToken={csrfToken} refresh={refresh} />
    {busy && <p role="status">正在处理…</p>}{notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
  </section>;
}
