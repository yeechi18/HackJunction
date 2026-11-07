"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { AccessError, ErrorNotice, LoadingPanel } from "@/components/async-feedback";
import { ApiError, apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import { editableFacts, FactEditor, normalizedFacts } from "./fact-editor";
import { FactsPanel, WorkflowNotice } from "./order-panels";
import { formatTime, type Message, type Order, type OrderEvent } from "./types";
import { orderStatusLabel } from "./workflow-progress";
import styles from "./seller-workspace.module.css";
import { useWorkspace } from "./use-workspace";
import { ReferenceAssetsPanel } from "./reference-assets-panel";

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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const allowed = order.allowed_actions.includes("message");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !content.trim() || !allowed) return;
    setBusy(true); setError("");
    try {
      await apiFetch<Message>(`/orders/${order.id}/messages`, { method: "POST", headers: csrfHeaders(csrfToken), body: JSON.stringify({ expected_revision: order.revision, content: content.trim() }) });
      setContent(""); onSent();
    } catch (caught) { setError(errorMessage(caught)); if (caught instanceof ApiError && caught.status === 409) onSent(); }
    finally { setBusy(false); }
  }

  if (!allowed && !content.trim()) return <p className="field-help message-composer-closed">当前订单状态暂不接受新消息。</p>;
  return <form className="message-composer" onSubmit={submit}><label htmlFor="seller-message">给这份订单留个消息</label><textarea id="seller-message" value={content} onChange={event => setContent(event.target.value)} placeholder="补充你的想法或需要说明的事项。" maxLength={10000} rows={3} disabled={busy} readOnly={!allowed} required /><div><p className="field-help">{allowed ? "消息会交给上架顾问，结合本版资料继续审查。" : "当前阶段不能发送，未发送草稿仍保留在此。"}</p><button type="submit" className="button button-dark button-small" disabled={busy || !content.trim() || !allowed}>{busy ? "发送中…" : "发送消息"}<span aria-hidden="true">↗</span></button></div>{error && <ErrorNotice>{error}</ErrorNotice>}</form>;
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
            const author = seller ? "你" : message.role === "owner" ? "负责人" : "上架顾问";
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
            <WorkflowNotice order={order} description={order.phase === "AWAITING_SELLER" && order.workflow_status === "waiting_for_seller" ? "你可以在会话中留言，向上架顾问 询问交付确认的后续安排。" : undefined} />
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

  if (accessDenied) return <AccessError />;
  if (!data) return error ? <div><ErrorNotice>{error}</ErrorNotice><AccessError /><button className="button button-outline" type="button" onClick={refresh}>重新连接</button></div> : <LoadingPanel message="正在验证私有访问并读取订单…" />;
  const { order, messages, events, csrfToken, checkedAt } = data;
  return (
    <>
      <header className={styles.heading}>
        <div>
          <p className="sw-eyebrow">SHELFWISE / ORDER CONVERSATION</p>
          <h1>{order.product_name}</h1>
          <p className={styles.orderMeta}>订单 {order.id.slice(0, 8)} · 资料版本 {order.revision}{order.is_demo && " · 虚构演示"}</p>
        </div>
        <section className={styles.summary} aria-label="订单概览">
          <span className={styles.status}>{orderStatusLabel(order)}</span>
          <p>Amazon US · 美式英语 · 单 SKU</p>
          <p>请妥善保存完整私有链接。</p>
        </section>
      </header>
      <div className={styles.layout}>
        <section className={styles.chat} aria-labelledby="agent-heading">
          <header className={styles.chatHeader}>
            <span className={styles.agentIcon} aria-hidden="true">✦</span>
            <div><h2 id="agent-heading">上架顾问</h2><p>订单进度与沟通记录</p></div>
            <span className={styles.sync} data-live={!error}>{error ? "同步暂时中断" : "自动同步"}</span>
          </header>
          {error && <ErrorNotice>{error} 当前仍显示最近一次成功读取的资料。</ErrorNotice>}
          <Conversation order={order} messages={messages} events={events} />
          <div className={styles.compose}>
            <MessageComposer order={order} csrfToken={csrfToken} onSent={refresh} />
            <div className={styles.syncFooter}>
              <span>最近检查 {checkedAt} · 每 {pollIntervalMs / 1000} 秒同步</span>
              <button className="text-button" type="button" onClick={refresh}>刷新 ↻</button>
            </div>
          </div>
        </section>
        <aside className={styles.sidebar} aria-label="订单资料">
          <FactsPanel order={order} action={!editing && order.allowed_actions.includes("supplement") ? (
            <button type="button" className={styles.editButton} onClick={() => {
              setEditing(true);
              setNotice("");
            }}>补充商品资料 <span aria-hidden="true">↗</span></button>
          ) : undefined} />
          {notice && <div className="form-notice notice-success" role="status">{notice}</div>}
          {editing && (
            <SupplementForm order={order} csrfToken={csrfToken} onSaved={() => {
              setEditing(false);
              setNotice("补充资料已保存，等待重新审查。");
              refresh();
            }} onCancel={() => setEditing(false)} refresh={refresh} />
          )}
          <ReferenceAssetsPanel orderId={order.id} csrfToken={csrfToken} />
        </aside>
      </div>
    </>
  );
}
