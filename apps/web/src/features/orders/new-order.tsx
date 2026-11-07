"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ErrorNotice } from "@/components/async-feedback";
import { apiFetch, errorMessage } from "@/lib/api";
import type { components } from "@/lib/generated/api";
import { factLabels, type CreatedOrder } from "./types";
import styles from "./new-order.module.css";

const labels = { seller_name: "你的称呼", product_name: "中文商品名", ...factLabels };
const fieldsToShow = ["product_name", "material", "dimensions", "pack_quantity", "use_cases", "seller_name"];
const optionalFields = ["dimension_scope", "cable_diameter", "color", "contact", "notes"];
const fieldLabels: Record<string, string> = { ...labels, contact: "联系方式", notes: "商品说明与期望卖点" };
const limits: Record<string, number> = { seller_name: 200, product_name: 300, contact: 500, notes: 5000 };
type ChatTurn = { user: string; reply: string; notice?: string | null };
type ChatResult = components["schemas"]["IntakeChatResult"];

const greeting = "你好，我是你的上架顾问。发我淘宝、天猫、京东、1688 或拼多多的店铺或商品链接，也可以直接说说你想上架哪件商品。我会把能整理出的资料放到右边，再和你补齐缺项。";

export function NewOrderForm() {
  const [fields, setFields] = useState<Record<string, string>>({});
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [chatError, setChatError] = useState("");
  const [activeSource, setActiveSource] = useState("");
  const [sources, setSources] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [updatedKeys, setUpdatedKeys] = useState<string[]>([]);
  const composer = useRef<HTMLTextAreaElement>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const missing = fieldsToShow.filter(key => !fields[key]?.trim());
  const canSubmit = Boolean(fields.seller_name?.trim() && fields.product_name?.trim() && confirmed);

  useEffect(() => {
    const log = transcript.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [turns, sending]);

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const user = message.trim();
    if (!user || sending || busy) return;
    setSending(true);
    setChatError("");
    try {
      const result = await apiFetch<ChatResult>("/orders/intake-chat", {
        method: "POST",
        timeoutMs: 45000,
        body: JSON.stringify({
          message: user, last_reply: turns.at(-1)?.reply ?? "", previous_source_url: activeSource, fields,
        }),
      });
      const updates = Object.fromEntries(Object.entries(result.updates).filter(([, value]) => value));
      setUpdatedKeys(Object.keys(updates));
      if (Object.keys(updates).length) {
        setFields(current => ({ ...current, ...updates }));
        setConfirmed(false);
      }
      const source = result.source_url;
      if (source || result.import_notice) setActiveSource(source ?? "");
      if (source && Object.keys(updates).some(key => key === "product_name" || key in factLabels)) {
        setSources(current => [...new Set([...current, source])].slice(-3));
      }
      setTurns(current => [...current, { user, reply: result.reply, notice: result.import_notice }]);
      setMessage("");
    } catch (caught) {
      setChatError(`${errorMessage(caught)} 也可以直接在资料卡补充。`);
    } finally {
      setSending(false);
    }
  }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<{ id: string; url: string; path: string; productName: string } | null>(null);
  const [copyState, setCopyState] = useState("");
  const linkInput = useRef<HTMLInputElement>(null);
  const submission = useRef<{ body: string; key: string } | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || sending || !canSubmit) return;
    const form = new FormData(event.currentTarget);
    const notes = [fields.notes?.trim(), ...sources.map(url => `参考商品链接：${url}`)].filter(Boolean);
    const productName = fields.product_name.trim();
    const body = JSON.stringify({
      seller_name: fields.seller_name.trim(),
      contact: fields.contact?.trim() || null,
      product_name: productName,
      facts: Object.keys(factLabels).flatMap(key => {
        const value = fields[key]?.trim();
        return value ? [{ key, value, confirmed: true }] : [];
      }),
      notes: notes.join("\n") || null,
      is_demo: form.get("is_demo") === "on",
    });
    if (submission.current?.body !== body) submission.current = { body, key: crypto.randomUUID() };
    setBusy(true);
    setError("");
    try {
      const result = await apiFetch<CreatedOrder>("/orders", { method: "POST", body, idempotencyKey: submission.current.key });
      const privateUrl = new URL(result.private_url, window.location.origin);
      if (privateUrl.pathname !== `/o/${result.id}` || !new URLSearchParams(privateUrl.hash.slice(1)).get("token")) {
        throw new Error("私有链接格式异常");
      }
      setCreated({ id: result.id, url: new URL(privateUrl.pathname + privateUrl.hash, window.location.origin).href, path: privateUrl.pathname + privateUrl.hash, productName });
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }

  async function copyLink() {
    if (!created) return;
    try { await navigator.clipboard.writeText(created.url); setCopyState("链接已复制，请妥善保存。"); }
    catch { linkInput.current?.select(); setCopyState("请选中链接后手动复制。"); }
  }

  if (created) return <section className={`creation-success ${styles.success}`}><div className="success-symbol" aria-hidden="true">✓</div><p className="eyebrow">YOUR ORDER, YOUR SPACE.</p><h1>资料已保存<span>.</span></h1><p className="portal-intro">「{created.productName}」已有专属订单入口。你可以继续补充资料，并在同一个入口查看进度。</p><div className="private-link-box"><label htmlFor="private-order-link">私有订单链接</label><div><input ref={linkInput} id="private-order-link" readOnly value={created.url} onFocus={event => event.currentTarget.select()} /><button type="button" className="button button-outline" onClick={copyLink}>复制链接</button></div><p>持有完整链接的人可访问此订单，请保存并仅与信任的人分享。</p>{copyState && <p role="status">{copyState}</p>}</div><Link href={created.path} prefetch={false} className="button button-dark">查看订单进度 <span aria-hidden="true">↗</span></Link><p className="subtle-note">接下来由上架顾问 审查资料，接单由负责人决定。发布还需你确认，当前仅为模拟模式。</p></section>;

  function field(key: string) {
    const value = fields[key] ?? "";
    const update = (value: string) => {
      setFields(current => ({ ...current, [key]: value }));
      setUpdatedKeys(current => current.filter(updated => updated !== key));
      setConfirmed(false);
    };
    return (
      <label className={styles.row} key={key} data-filled={Boolean(value.trim())} data-updated={updatedKeys.includes(key)}>
        <span>{fieldLabels[key]}</span>
        {key === "notes" ? (
          <textarea name={key} rows={2} value={value} maxLength={limits.notes}
            placeholder="待补充" onChange={event => update(event.target.value)} />
        ) : (
          <input name={key} value={value} maxLength={limits[key] ?? 2000}
            placeholder={key === "seller_name" || key === "product_name" ? "待补充 · 必填" : "待补充"}
            onChange={event => update(event.target.value)} />
        )}
      </label>
    );
  }

  return (
    <>
      <div className={styles.overview}>
        <section className={styles.intro} aria-labelledby="intake-title">
          <span className={styles.eyebrow}>01 / LISTING STUDIO</span>
          <h1 id="intake-title">和上架顾问聊聊<span>.</span></h1>
          <p>分享店铺或商品链接，边聊边整理上架资料。</p>
          <span className={styles.spark} aria-hidden="true">✳</span>
        </section>
        <section className={styles.status} aria-labelledby="intake-progress">
          <span className={styles.eyebrow}>AMAZON US · 一站式上架包</span>
          <h2 id="intake-progress">资料收集进度</h2>
          <div className={styles.progressLabel}>
            <span>{missing.length ? `${missing.length} 项待补充` : "资料已整理 · 请核对"}</span>
            <strong>{fieldsToShow.length - missing.length} / {fieldsToShow.length}</strong>
          </div>
          <progress aria-labelledby="intake-progress" max={fieldsToShow.length} value={fieldsToShow.length - missing.length} />
        </section>
      </div>
      <div className={styles.grid}>
        <section className={styles.panel} aria-labelledby="intake-chat-heading">
          <header className={styles.heading}>
            <div className={styles.headingTitle}>
              <span className={styles.icon} aria-hidden="true">✳</span>
              <div><h2 id="intake-chat-heading">上架顾问</h2><small>分享链接或描述商品</small></div>
            </div>
            <span className={styles.badge}><i aria-hidden="true" />AI 接待</span>
          </header>
          <div className={styles.log} role="log" ref={transcript} aria-label="上架对话">
            <div className={styles.agent}><small>上架顾问 · AI</small><p>{greeting}</p></div>
            {turns.map(({ user, reply, notice }, index) => (
              <div className={styles.turn} key={index}>
                <div className={styles.user}><small>你</small><p>{user}</p></div>
                <div className={styles.agent}><small>上架顾问 · AI</small><p>{reply}</p>{notice && <p className={styles.notice}>{notice}</p>}</div>
              </div>
            ))}
            {sending && <div className={styles.turn}>
              <div className={styles.user}><small>你</small><p>{message.trim()}</p></div>
              <div className={styles.agent}>
                正在整理资料 <span className={styles.dots} aria-hidden="true"><i /><i /><i /></span>
              </div>
            </div>}
          </div>
          <form className={styles.composer} method="post" onSubmit={send}>
            {!turns.length && !sending && (
              <div className={styles.prompts}>
                <button type="button" disabled={busy} onClick={() => composer.current?.focus()}>粘贴店铺链接 ↗</button>
                <button type="button" disabled={busy || Boolean(message)} onClick={() => {
                  setMessage("我想上架的商品是");
                  composer.current?.focus();
                }}>描述我的商品 ↗</button>
              </div>
            )}
            <textarea ref={composer} aria-label="发给上架顾问" placeholder="粘贴店铺或商品链接，或补充商品信息…" rows={3}
              value={message} onChange={event => setMessage(event.target.value)} disabled={sending || busy} maxLength={5000} />
            {chatError && <ErrorNotice>{chatError}</ErrorNotice>}
            <div className={styles.actions}>
              <small>链接需要公开访问。不确定的规格可以稍后补充。</small>
              <button className={styles.button} type="submit" disabled={sending || busy || !message.trim()}>发送 <span aria-hidden="true">↗</span></button>
            </div>
          </form>
        </section>
        <section className={styles.panel} aria-labelledby="intake-card-heading">
          <header className={styles.heading}>
            <div className={styles.headingTitle}>
              <span className={styles.icon} aria-hidden="true">≡</span>
              <div><h2 id="intake-card-heading">商品资料卡</h2><small>对话中收集，可直接编辑</small></div>
            </div>
            <span className={styles.badge}>由你确认</span>
          </header>
          <form className={styles.card} method="post" onSubmit={submit}>
            <fieldset disabled={busy || sending}>
              {fieldsToShow.map(field)}
              <details>
                <summary>其他资料（选填）</summary>
                {optionalFields.map(field)}
                <label className={styles.check}><input type="checkbox" name="is_demo" />这是一份虚构演示订单</label>
              </details>
              <div className={styles.review}>
              <p className={styles.notice}>请核对已收集的资料。称呼和商品名为必填，其余规格可在订单入口继续补充。</p>
              <label className={styles.check}>
                <input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />
                我已核对已填写的商品资料
              </label>
              {error && <ErrorNotice>{error}</ErrorNotice>}
              <button className={styles.button} type="submit" disabled={!canSubmit || busy || sending}>
                {busy ? "正在提交…" : "确认资料，提交接单申请"}
              </button>
              <p className={styles.notice}>提交后获得私有订单链接，由负责人确认是否接单。</p>
              </div>
            </fieldset>
          </form>
        </section>
      </div>
    </>
  );
}
