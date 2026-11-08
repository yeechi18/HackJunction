"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import type { InternalOrder } from "@/features/orders/types";

type Brief = { revision: number; input_digest: string; prompt: string; has_reference: boolean; references: { id: string; url: string }[] };
type Status = { competitor_search: boolean; search_provider: "mimo" | "brave" | "ark"; research_provider: "mimo" | "ark"; research_model?: string };

export function IntegrationsPanel({ order, csrfToken, refresh }: { order: InternalOrder; csrfToken: string; refresh: () => void }) {
  const root = `/internal/orders/${order.id}`;
  const [brief, setBrief] = useState<Brief | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("");
  const [url, setUrl] = useState("");
  const [content, setContent] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [purpose, setPurpose] = useState("concept");
  const [approved, setApproved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const processing = Boolean(order.pending_command_id);
  const canResearch = ["INTAKE", "NEEDS_INFO", "AWAITING_ACCEPTANCE"].includes(order.phase) && !processing;
  const canReturnImage = ["INTAKE", "NEEDS_INFO", "AWAITING_ACCEPTANCE", "NEEDS_IMAGE"].includes(order.phase) && !processing;
  const researchReason = processing ? "订单正在处理，请等待本轮任务结束。" : !canResearch ? "本轮研究与制作已开始或完成，不能直接向已审校版本追加资料。请卖家先更新商品资料，形成新版本并重新接单后，再补充研究来源。" : !source.trim() || !content.trim() ? "请填写来源名称和原始资料；来源链接可以不填。" : "来源名称和原文已填写，可以保存。";
  const stale = brief?.revision !== order.revision;

  useEffect(() => {
    let cancelled = false;
    Promise.all([apiFetch<Brief>(`${root}/image-brief`), apiFetch<Status>(`${root}/integration-status`)])
      .then(([b, s]) => { if (!cancelled) { setBrief(b); setStatus(s); setApproved(false); } })
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
    <details open><summary>补充研究资料</summary>
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
    <details><summary>商品图片制作任务与回传</summary>
      <p className="field-help">复制本版制作要求，完成图片制作后上传核对。任务绑定当前商品资料；只有文字时制作概念图，有实物参考图时优先保留真实商品外观。</p>
      {brief && <>
        <div className="field"><label htmlFor="image-brief">本版图片制作任务</label><textarea id="image-brief" value={brief.prompt} rows={7} readOnly /></div>
        <button type="button" className="button button-outline button-small" disabled={busy || stale} onClick={() => void perform(async () => { await navigator.clipboard.writeText(brief.prompt); return "图片制作任务已复制。"; })}>复制图片制作任务</button>
        <ul>{brief.references.map((r, i) => <li key={r.id}><a href={r.url} target="_blank" rel="noreferrer">查看参考图 {i + 1}</a></li>)}</ul>
        <form onSubmit={e => { e.preventDefault(); void perform(async () => {
          if (!file) return "请选择图片。";
          const body = new FormData(); body.append("file", file);
          const args = new URLSearchParams({ expected_revision: String(brief.revision), input_digest: brief.input_digest, purpose, approved: String(approved) });
          await apiFetch(`${root}/generated-images?${args}`, { method: "POST", headers: csrfHeaders(csrfToken), body, timeoutMs: 30000 });
          setFile(null); setApproved(false);
          setBrief(await apiFetch<Brief>(`${root}/image-brief`));
          return purpose === "concept" ? "概念图已保存，仅内部查看，不进入真实刊登交付。" : "参考编辑图已回传；等待图片任务会继续制作和审校，不沿用旧批准。";
        }); }}>
          <div className="field"><label htmlFor="generated-purpose">回传用途</label><select id="generated-purpose" value={purpose} onChange={e => { setPurpose(e.target.value); setApproved(false); }}><option value="concept">概念或未核实生成图（内部预览）</option><option value="reference_edit" disabled={!brief.has_reference}>基于参考图的编辑图（需核实）</option></select></div>
          <div className="field"><label htmlFor="generated-file">回传生成图片</label><input id="generated-file" type="file" accept="image/png,image/jpeg,image/webp" onChange={e => { setFile(e.target.files?.[0] ?? null); setApproved(false); }} /></div>
          {purpose === "reference_edit" && <label className="checkbox-field"><input type="checkbox" checked={approved} onChange={e => setApproved(e.target.checked)} />我已对照实物与原图核实主体、颜色、数量和配件；生成图不得作为新增性能依据</label>}
          <button className="button button-dark button-small" disabled={busy || !canReturnImage || stale || !file || (purpose === "reference_edit" && !approved)}>保存生成图并进入后续核验</button>
          {!canReturnImage && <p className="field-help">当前版本已进入生产或交付审核，不能直接替换其图片。需先更新资料形成新版本，再回传图片、重新审校。</p>}
        </form>
      </>}
    </details>
    {busy && <p role="status">正在处理…</p>}{notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
  </section>;
}
