"use client";
import { CopyPreviewPanel } from "./copy-preview-panel";
import { DeliveryFactsPanel } from "./delivery-facts-panel";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { AccessError, ErrorNotice, LoadingPanel } from "@/components/async-feedback";
import { ApiError, apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import { FactsPanel, WorkflowNotice } from "./order-panels";
import { formatTime, type Message, type Order, type OrderEvent } from "./types";
import { orderStatusLabel } from "./workflow-progress";
import styles from "./seller-workspace.module.css";
import { useWorkspace } from "./use-workspace";
import { IntakeAnalysisPanel } from "./intake-analysis";
import { ReferenceAssetsPanel } from "./reference-assets-panel";
import { DeliverablesPanel } from "./deliverables-panel";
import { SellerSupportPanel } from "@/features/operations/support-panel";
import { SellerPublicationPanel } from "./seller-publication-panel";

function SupplementForm({ order, csrfToken, onSaved, onCancel, refresh }: { order: Order; csrfToken: string; onSaved: () => void; onCancel: () => void; refresh: () => void }) {
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
      await apiFetch<Order>(`/orders/${order.id}/supplements`, { method: "POST", headers: csrfHeaders(csrfToken), body: JSON.stringify({ expected_revision: baseRevision, notes: notes.trim() || null }) });
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
      if (caught instanceof ApiError && caught.status === 409) refresh();
    } finally { setBusy(false); }
  }

  return <form className="surface workspace-panel supplement-form" onSubmit={submit}><div className="panel-heading"><h2>补充商品资料</h2><button className="text-button" type="button" onClick={onCancel} disabled={busy}>收起 ↑</button></div><p className="field-help">如果需要更正商品描述或表达偏好，直接补充说明即可，无需填写固定参数表。</p>{stale && <div className="form-notice notice-warning">订单已有更新。请载入最新资料，再继续编辑；这将替换当前未保存内容。<button className="text-button" type="button" onClick={() => { setNotes(order.notes ?? ""); setBaseRevision(order.revision); setError(""); }}>载入最新资料 ↻</button></div>}{!allowed && <div className="form-notice notice-warning">订单阶段已更新，当前暂不接受资料修改。你的未保存草稿仍保留在表单中。</div>}<div className="field"><label htmlFor="supplement-notes">补充说明</label><textarea id="supplement-notes" value={notes} onChange={event => setNotes(event.target.value)} rows={4} maxLength={10000} disabled={busy || stale || !allowed} /></div>{error && <ErrorNotice>{error}</ErrorNotice>}<div className="form-submit-row"><p>基于资料版本 {baseRevision}</p><button className="button button-dark" type="submit" disabled={busy || stale || !allowed}>{busy ? "正在保存…" : "保存补充资料"}<span aria-hidden="true">↗</span></button></div></form>;
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

  if (order.ai_reply_paused) return <div className="form-notice notice-warning" role="status" aria-label="自动回复状态">人工客服工单处理中，自动聊天回复已暂停。资料审查和接单审批照常进行。请在“联系人工协助”中继续沟通。{content.trim() && <p>你尚未发送的消息已保留。</p>}</div>;
  if (!allowed && !content.trim()) return <p className="field-help message-composer-closed">当前订单状态暂不接受新消息。</p>;
  return <form className="message-composer" onSubmit={submit}><label htmlFor="seller-message">给这份订单留个消息</label><textarea id="seller-message" value={content} onChange={event => setContent(event.target.value)} placeholder="补充你的想法或需要说明的事项。" maxLength={10000} rows={3} disabled={busy} readOnly={!allowed} required /><div><p className="field-help">{allowed ? "消息会交给获客 Agent，结合本版资料继续审查。" : "当前阶段不能发送，未发送草稿仍保留在此。"}</p><button type="submit" className="button button-dark button-small" disabled={busy || !content.trim() || !allowed}>{busy ? "发送中…" : "发送消息"}<span aria-hidden="true">↗</span></button></div>{sent && <p className="form-notice notice-success" role="status">消息已保存。{["queued", "running", "processing"].includes(order.workflow_status) ? "正在等待获客 Agent 处理，回复会自动出现在沟通记录中。" : "请查看沟通记录中的最新回复。"}若长时间未更新，请查看上方订单进度。</p>}{error && <ErrorNotice>{error}</ErrorNotice>}</form>;
}

function Conversation({ order, messages, events }: { order: Order; messages: Message[]; events: OrderEvent[] }) {
  const feed = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const [canJump, setCanJump] = useState(false);
  const entries = [
    ...messages.map(message => ({ type: "message" as const, at: message.created_at, message })),
    ...events.map(event => ({ type: "event" as const, at: event.created_at, event })),
  ].sort((left, right) => Date.parse(left.at) - Date.parse(right.at));
  const latest = entries.at(-1);
  const latestEvent = entries.filter(entry => entry.type === "event").at(-1);
  const latestKey = latest?.type === "message" ? `message-${latest.message.id}` : latest?.event.id;

  useEffect(() => {
    if (following.current && feed.current) feed.current.scrollTop = feed.current.scrollHeight;
  }, [latestKey, entries.length, order.phase, order.workflow_status]);

  function trackScroll() {
    const element = feed.current;
    if (!element) return;
    following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 32;
    setCanJump(!following.current);
  }

  return (
    <div className={styles.feedArea}>
      <div ref={feed} className={styles.feed} tabIndex={0} aria-label="会话记录" onScroll={trackScroll}>
        <ol className={styles.transcript} role="log" aria-label="消息和进度更新">
          <li className={styles.message}>
            <span className={styles.avatar} aria-hidden="true">✦</span>
            <div>
              <div className={styles.byline}><strong>上架顾问</strong></div>
              <div className={styles.bubble}>你好！我会在这里同步「{order.product_name}」的处理进度。你可以在这里留言、补充商品资料或上传参考图。</div>
            </div>
          </li>
          {entries.map(entry => {
            if (entry.type === "event") {
              return (
                <li className={styles.activity} key={`event-${entry.event.id}`}>
                  <details open={entry === latestEvent}>
                    <summary><span aria-hidden="true">⌁</span> 订单动态 <time dateTime={entry.at}>{formatTime(entry.at)}</time></summary>
                    <p>{entry.event.message}</p>
                  </details>
                </li>
              );
            }
            const { message } = entry;
            const seller = message.role === "seller";
            const author = seller ? "你" : ["owner", "human"].includes(message.role) ? "负责人" : "上架顾问";
            return (
              <li className={`${styles.message} ${seller ? styles.seller : ""}`} key={`message-${message.id}`}>
                <span className={styles.avatar} aria-hidden="true">{seller ? "你" : author === "负责人" ? "负" : "✦"}</span>
                <div>
                  <div className={styles.byline}><strong>{author}</strong><time dateTime={entry.at}>{formatTime(entry.at)}</time></div>
                  <div className={styles.bubble}>{message.content}</div>
                </div>
              </li>
            );
          })}
        </ol>
        <div className={styles.current}>
          <span className={styles.avatar} aria-hidden="true">✦</span>
          <div>
            <p className={styles.byline}><strong>上架顾问</strong> · 当前进度</p>
            <WorkflowNotice order={order} />
          </div>
        </div>
      </div>
      {canJump && <button className={styles.jump} type="button" onClick={() => {
        const element = feed.current;
        if (!element) return;
        following.current = true;
        element.scrollTo({ top: element.scrollHeight, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      }}>查看最新进度 ↓</button>}
    </div>
  );
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
  if (!data) return error ? <div><ErrorNotice>{error}</ErrorNotice><AccessError /><button className="button button-outline" type="button" onClick={refresh}>重新连接</button></div> : <LoadingPanel message="正在验证卖家账号并读取订单…" />;
  const { order, messages, events, csrfToken, checkedAt } = data;
  return <>
    <header className={styles.heading}>
      <div><p className="sw-eyebrow">SHELFWISE / ORDER CONVERSATION</p><h1>{order.product_name}</h1><p className={`${styles.orderMeta} order-identity`}>订单 {order.id.slice(0, 8)} · 资料版本 {order.revision}</p></div>
      <section className={styles.summary} aria-label="订单概览"><span className={styles.status}>{orderStatusLabel(order)}</span><p>Amazon US · 美式英语 · 单 SKU</p><p>订单已保存在你的卖家账号中，可从“我的订单”找回。</p></section>
    </header>
    {order.phase === "AWAITING_SELLER" && <section className="surface workspace-panel" aria-label="下一步操作">
      <h2>下一步：确认交付成品</h2><p>先查看文案与图片，再填写售价和 SKU 提交成品确认；也可以提出修改。</p>
      <div className="account-actions"><a className="button button-dark" href="#seller-confirmation">去确认成品 / 提出修改 ↓</a><a className="button button-outline" href="#delivered-package">查看交付包 ↓</a></div>
    </section>}
    <div className={styles.layout}>
      <div className={styles.primary}>
        <section className={styles.chat} aria-labelledby="agent-heading">
          <header className={styles.chatHeader}><span className={styles.agentIcon} aria-hidden="true">✦</span><div><h2 id="agent-heading">上架顾问</h2><p>订单进度与沟通记录</p></div><span className={styles.sync} data-live={!error}>{error ? "同步暂时中断" : "自动同步"}</span></header>
          {error && <ErrorNotice>{error} 当前仍显示最近一次成功读取的资料。</ErrorNotice>}
          <Conversation order={order} messages={messages} events={events} />
          <div className={styles.compose}>
            <MessageComposer order={order} csrfToken={csrfToken} onSent={refresh} />
            <div className={`${styles.syncFooter} workspace-toolbar`}><span>最近检查 {checkedAt} · 每 {pollIntervalMs / 1000} 秒同步</span><button className="text-button" type="button" onClick={refresh}>刷新 ↻</button></div>
          </div>
        </section>
        <details className="workspace-disclosure"><summary>查看英文初稿</summary><div className="disclosure-body"><CopyPreviewPanel order={order} csrfToken={csrfToken} refresh={refresh} /></div></details>
        <details className="workspace-disclosure"><summary>核对交付资料</summary><div className="disclosure-body"><DeliveryFactsPanel key={order.revision} order={order} csrfToken={csrfToken} refresh={refresh} /></div></details>
        <div id="delivered-package" style={{ scrollMarginTop: 24 }}><DeliverablesPanel orderId={order.id} onViewedVersionChange={onViewedVersionChange} /></div>
        <SellerPublicationPanel order={order} csrfToken={csrfToken} refresh={refresh} viewedVersionId={viewedDelivery?.orderId === order.id ? viewedDelivery.versionId : null} />
        <details className="workspace-disclosure"><summary>联系负责人 / 查看处理记录</summary><div className="disclosure-body"><SellerSupportPanel orderId={order.id} csrfToken={csrfToken} refresh={refresh} /></div></details>
      </div>
      <aside className={styles.sidebar} aria-label="订单资料">
        <FactsPanel order={order} action={!editing && order.allowed_actions.includes("supplement") ? <button type="button" className={styles.editButton} onClick={() => { setEditing(true); setNotice(""); }}>补充商品资料 <span aria-hidden="true">↗</span></button> : undefined} />
        {notice && <div className="form-notice notice-success" role="status">{notice}</div>}
        {editing && <SupplementForm order={order} csrfToken={csrfToken} onSaved={() => { setEditing(false); setNotice("补充资料已保存，等待重新审查。"); refresh(); }} onCancel={() => setEditing(false)} refresh={refresh} />}
        <details className="workspace-disclosure"><summary>查看资料分析</summary><div className="disclosure-body"><section className="surface workspace-panel"><IntakeAnalysisPanel order={order} /></section></div></details>
        <details className="workspace-disclosure"><summary>补充参考图片</summary><div className="disclosure-body"><ReferenceAssetsPanel orderId={order.id} csrfToken={csrfToken} /></div></details>
      </aside>
    </div>
  </>;
}
