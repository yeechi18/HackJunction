"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { ApiError, apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import styles from "./operations-panel.module.css";

export type OperationsPanelProps = { orderId: string; csrfToken: string; refresh?: () => void };

type SupportMessage = { id: string; role: string; content: string; created_at: string };
type SupportTicket = {
  id: string;
  status: string;
  created_at: string;
  resolved_at: string | null;
  ai_reply_paused: boolean;
  messages: SupportMessage[];
};
type SupportList = { items: SupportTicket[]; ai_reply_paused: boolean | null };

function timestamp(value: string) {
  return new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

function useTickets(path: string) {
  const [tickets, setTickets] = useState<SupportList | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    async function load() {
      let denied = false;
      try {
        const result = await apiFetch<SupportList>(path, { signal: controller.signal });
        if (active) { setTickets(result); setError(""); }
      } catch (caught) {
        if (active) {
          denied = caught instanceof ApiError && [401, 403, 404].includes(caught.status ?? 0);
          setError(errorMessage(caught));
          if (denied) setTickets(null);
        }
      } finally {
        if (active && !denied) timer = setTimeout(() => void load(), 8000);
      }
    }
    void load();
    return () => { active = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [path, revision]);
  return { tickets, error, reload: () => setRevision(current => current + 1) };
}

function TicketHistory({ tickets }: { tickets: SupportTicket[] }) {
  return <div className={styles.ticketList}>{tickets.map(ticket => <article className={styles.ticket} key={ticket.id}>
    <header className={styles.ticketHeading}><strong>{ticket.status === "open" ? "人工处理中" : "人工处理已结束"}</strong><time dateTime={ticket.created_at}>{timestamp(ticket.created_at)}</time></header>
    <ol className={styles.messages}>{ticket.messages.map(message => <li key={message.id} className={message.role === "human" ? styles.humanMessage : styles.sellerMessage}>
      <div className={styles.messageMeta}><span>{message.role === "human" ? "负责人" : "卖家"}</span><time dateTime={message.created_at}>{timestamp(message.created_at)}</time></div>
      <p>{message.content}</p>
    </li>)}</ol>
    {ticket.resolved_at && <p className={styles.hint}>结束于 {timestamp(ticket.resolved_at)}</p>}
  </article>)}</div>;
}

function SupportWorkspace({ orderId, csrfToken, refresh, internal }: OperationsPanelProps & { internal: boolean }) {
  const id = useId();
  const encodedId = encodeURIComponent(orderId);
  const path = internal ? `/internal/support-tickets?order_id=${encodedId}&status=all` : `/orders/${encodedId}/support-requests`;
  const { tickets, error, reload } = useTickets(path);
  const [content, setContent] = useState("");
  const [resolve, setResolve] = useState(false);
  const [busy, setBusy] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [notice, setNotice] = useState("");
  const submission = useRef<{ body: string; key: string; path: string } | null>(null);
  const openTicket = tickets?.items.find(ticket => ticket.status === "open");
  const paused = tickets?.ai_reply_paused ?? Boolean(openTicket);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !csrfToken || !content.trim() || (internal && !openTicket)) return;
    const target = internal ? `/internal/support-tickets/${encodeURIComponent(openTicket!.id)}/messages` : `/orders/${encodedId}/support-requests`;
    const body = JSON.stringify(internal ? { content: content.trim(), resolve } : { content: content.trim() });
    if (submission.current?.body !== body || submission.current.path !== target) submission.current = { body, path: target, key: crypto.randomUUID() };
    setBusy(true); setSubmitError(""); setNotice("");
    try {
      await apiFetch<SupportTicket>(target, { method: "POST", body, headers: csrfHeaders(csrfToken), idempotencyKey: submission.current.key });
      setNotice(internal ? resolve ? "人工回复已发送，本次人工处理已结束。" : "人工回复已发送。" : "已转交负责人，请在下方查看人工处理消息。");
      setContent(""); setResolve(false); submission.current = null;
      reload(); refresh?.();
    } catch (caught) {
      setSubmitError(caught instanceof ApiError && caught.status === 409 ? "人工处理状态已变化，请刷新后确认最新消息。" : errorMessage(caught));
      if (caught instanceof ApiError && caught.status === 409) { reload(); refresh?.(); }
    } finally { setBusy(false); }
  }

  return <section className={`surface workspace-panel ${styles.panel}`} aria-labelledby={`${id}-heading`}>
    <div className="panel-heading"><h2 id={`${id}-heading`}>{internal ? "人工客服" : "联系人工协助"}</h2><button type="button" className="text-button" disabled={busy} onClick={reload}>刷新 ↻</button></div>
    <p className={styles.intro}>{paused ? "负责人正在人工处理，自动资料回复暂时暂停。" : internal ? "查看本单的人工请求，并回复卖家。" : "遇到疑问可以转交负责人，人工回复会保留在本单记录中。"}</p>
    {error && <p className={styles.error} role="alert">{error}{tickets ? " 下方保留最近一次读取的记录。" : ""}</p>}
    {!tickets && !error && <p className={styles.hint} role="status">正在读取人工处理记录…</p>}
    {tickets && <TicketHistory tickets={tickets.items} />}
    {tickets?.items.length === 0 && <p className={styles.empty}>{internal ? "本单暂时没有人工请求。" : "目前没有人工处理记录。"}</p>}
    {(!internal || openTicket) && <form className={styles.form} onSubmit={submit}>
      <label htmlFor={`${id}-content`}>{internal ? "人工回复" : openTicket ? "补充给负责人" : "需要协助的问题"}</label>
      <textarea id={`${id}-content`} rows={3} required maxLength={10000} value={content} disabled={busy || !csrfToken} onChange={event => { setContent(event.target.value); setNotice(""); }} placeholder={internal ? "说明已核实的情况和下一步处理…" : "请描述需要人工核实或协助的事项…"} />
      {internal && <label className={styles.checkLabel}><input type="checkbox" checked={resolve} disabled={busy} onChange={event => setResolve(event.target.checked)} /><span>回复后结束本次人工处理</span></label>}
      <button className="button button-dark button-small" type="submit" disabled={busy || !csrfToken || !content.trim()}>{busy ? "正在发送…" : internal ? resolve ? "回复并结束处理" : "发送人工回复" : openTicket ? "发送补充说明" : "转交人工处理"}</button>
    </form>}
    {submitError && <p className={styles.error} role="alert">{submitError}</p>}
    {notice && <p className={styles.notice} role="status">{notice}</p>}
  </section>;
}

export function SellerSupportPanel(props: OperationsPanelProps) {
  return <SupportWorkspace key={props.orderId} {...props} internal={false} />;
}

export function OwnerSupportPanel(props: OperationsPanelProps) {
  return <SupportWorkspace key={props.orderId} {...props} internal />;
}
