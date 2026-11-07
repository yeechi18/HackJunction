"use client";

import { useCallback, useState, type FormEvent } from "react";
import { AccessError, ErrorNotice, LoadingPanel } from "@/components/async-feedback";
import { ApiError, apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import { editableFacts, FactEditor, normalizedFacts } from "./fact-editor";
import { EventsPanel, FactsPanel, MessagesPanel, OrderHeading, WorkflowNotice } from "./order-panels";
import type { Message, Order } from "./types";
import { useWorkspace } from "./use-workspace";
import { ReferenceAssetsPanel } from "./reference-assets-panel";
import { DeliverablesPanel } from "./deliverables-panel";
import { SellerSupportPanel } from "@/features/operations/support-panel";
import { SellerPublicationPanel } from "./seller-publication-panel";

function SupplementForm({ order, csrfToken, onSaved, onCancel, refresh }: { order: Order; csrfToken: string; onSaved: () => void; onCancel: () => void; refresh: () => void }) {
  const [facts, setFacts] = useState(() => editableFacts(order.facts));
  const [notes, setNotes] = useState(order.notes ?? "");
  const [baseRevision, setBaseRevision] = useState(order.revision);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const stale = baseRevision !== order.revision;
  const allowed = order.allowed_actions.includes("supplement");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || stale || !allowed) return;
    setBusy(true); setError("");
    try {
      await apiFetch<Order>(`/orders/${order.id}/supplements`, { method: "POST", headers: csrfHeaders(csrfToken), body: JSON.stringify({ expected_revision: baseRevision, facts: normalizedFacts(facts), notes: notes.trim() || null }) });
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
      if (caught instanceof ApiError && caught.status === 409) refresh();
    } finally { setBusy(false); }
  }

  return <form className="surface workspace-panel supplement-form" onSubmit={submit}><div className="panel-heading"><h2>补充商品资料</h2><button className="text-button" type="button" onClick={onCancel} disabled={busy}>收起 ↑</button></div><p className="field-help">以下为当前全部事实。请修改或移除不适用的候选，并分别核实保留项；保存后形成完整新版本。</p>{stale && <div className="form-notice notice-warning">订单已有更新。请载入最新资料，再继续编辑；这将替换当前未保存内容。<button className="text-button" type="button" onClick={() => { setFacts(editableFacts(order.facts)); setNotes(order.notes ?? ""); setBaseRevision(order.revision); setError(""); }}>载入最新资料 ↻</button></div>}{!allowed && <div className="form-notice notice-warning">订单阶段已更新，当前暂不接受资料修改。你的未保存草稿仍保留在表单中。</div>}<FactEditor facts={facts} onChange={setFacts} disabled={busy || stale || !allowed} /><div className="field"><label htmlFor="supplement-notes">补充说明</label><textarea id="supplement-notes" value={notes} onChange={event => setNotes(event.target.value)} rows={4} maxLength={10000} disabled={busy || stale || !allowed} /></div>{error && <ErrorNotice>{error}</ErrorNotice>}<div className="form-submit-row"><p>基于资料版本 {baseRevision}</p><button className="button button-dark" type="submit" disabled={busy || stale || !allowed}>{busy ? "正在保存…" : "保存补充资料"}<span aria-hidden="true">↗</span></button></div></form>;
}

function MessageComposer({ order, csrfToken, onSent }: { order: Order; csrfToken: string; onSent: () => void }) {
  const [content, setContent] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const allowed = order.allowed_actions.includes("message");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !content.trim() || !allowed || order.ai_reply_paused) return;
    setBusy(true); setError(""); setSent(false);
    try {
      await apiFetch<Message>(`/orders/${order.id}/messages`, { method: "POST", headers: csrfHeaders(csrfToken), body: JSON.stringify({ expected_revision: order.revision, content: content.trim() }) });
      setContent(""); setSent(true); onSent();
    } catch (caught) { setError(errorMessage(caught)); if (caught instanceof ApiError && caught.status === 409) onSent(); }
    finally { setBusy(false); }
  }

  if (order.ai_reply_paused) return <div className="form-notice notice-warning" role="status" aria-label="自动回复状态">本单已转交人工处理，获客 Agent 自动回复已暂停。请在“联系人工协助”中继续沟通；负责人回复并结束人工处理后，自动回复将恢复。{content.trim() && <p>你尚未发送的消息已保留。</p>}</div>;
  if (!allowed && !content.trim()) return <p className="field-help message-composer-closed">当前订单状态暂不接受新消息。</p>;
  return <form className="message-composer" onSubmit={submit}><label htmlFor="seller-message">给这份订单留个消息</label><textarea id="seller-message" value={content} onChange={event => setContent(event.target.value)} placeholder="补充你的想法或需要说明的事项。" maxLength={10000} rows={3} disabled={busy} readOnly={!allowed} required /><div><p className="field-help">{allowed ? "消息会交给获客 Agent，结合本版资料继续审查。" : "当前阶段不能发送，未发送草稿仍保留在此。"}</p><button type="submit" className="button button-dark button-small" disabled={busy || !content.trim() || !allowed}>{busy ? "发送中…" : "发送消息"}<span aria-hidden="true">↗</span></button></div>{sent && <p className="form-notice notice-success" role="status">消息已保存。{["queued", "running", "processing"].includes(order.workflow_status) ? "正在等待获客 Agent 处理，回复会自动出现在沟通记录中。" : "请查看沟通记录中的最新回复。"}若长时间未更新，请查看上方订单进度。</p>}{error && <ErrorNotice>{error}</ErrorNotice>}</form>;
}

export function SellerWorkspace({ orderId }: { orderId: string }) {
  const { data, error, accessDenied, pollIntervalMs, refresh } = useWorkspace(orderId);
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState("");
  const [viewedDelivery, setViewedDelivery] = useState<{ orderId: string; versionId: string | null } | null>(null);
  const onViewedVersionChange = useCallback((versionId: string | null) => {
    setViewedDelivery(current => current?.orderId === orderId && current.versionId === versionId ? current : { orderId, versionId });
  }, [orderId]);

  if (accessDenied) return <AccessError />;
  if (!data) return error ? <div><ErrorNotice>{error}</ErrorNotice><AccessError /><button className="button button-outline" type="button" onClick={refresh}>重新连接</button></div> : <LoadingPanel message="正在验证私有访问并读取订单…" />;
  const { order, messages, events, csrfToken, checkedAt } = data;
  return <><OrderHeading order={order} /><div className="workspace-toolbar"><p>最近更新 {checkedAt} · 进度每 {pollIntervalMs / 1000} 秒刷新</p><button className="text-button" type="button" onClick={refresh}>刷新 ↻</button></div>{error && <ErrorNotice>{error} 当前仍显示最近一次成功读取的资料。</ErrorNotice>}{notice && <div className="form-notice notice-success" role="status">{notice}</div>}<WorkflowNotice order={order} /><div className="workspace-grid"><div className="workspace-primary"><DeliverablesPanel orderId={order.id} onViewedVersionChange={onViewedVersionChange} /><SellerPublicationPanel order={order} csrfToken={csrfToken} refresh={refresh} viewedVersionId={viewedDelivery?.orderId === order.id ? viewedDelivery.versionId : null} /><FactsPanel order={order} /><ReferenceAssetsPanel orderId={order.id} csrfToken={csrfToken} />{editing ? <SupplementForm order={order} csrfToken={csrfToken} onSaved={() => { setEditing(false); setNotice("补充资料已保存，等待重新审查。"); refresh(); }} onCancel={() => setEditing(false)} refresh={refresh} /> : order.allowed_actions.includes("supplement") && <button type="button" className="supplement-trigger" onClick={() => { setEditing(true); setNotice(""); }}><span><strong>还有资料要补充？</strong><small>补齐尺寸、确认事实，或调整商品说明。</small></span><span>补充商品资料 ↗</span></button>}<SellerSupportPanel orderId={order.id} csrfToken={csrfToken} refresh={refresh} /><MessagesPanel messages={messages}><MessageComposer order={order} csrfToken={csrfToken} onSent={refresh} /></MessagesPanel></div><aside className="workspace-secondary"><EventsPanel events={events} /><div className="workspace-side-note"><strong>你的专属订单入口</strong><p>此页面仅展示本单的公开进度与资料。请妥善保存创建时获得的完整私有链接。</p></div></aside></div></>;
}
