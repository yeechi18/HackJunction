"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useSyncExternalStore, type ChangeEvent, type ClipboardEvent, type FormEvent } from "react";
import { ErrorNotice } from "@/components/async-feedback";
import { ApiError, apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import { formatBytes, uploadErrorMessage, validateFile } from "./reference-image";
import type { CreatedOrder, SellerSession } from "./types";
import imageStyles from "./reference-assets-panel.module.css";
import styles from "./new-order.module.css";
import type { components } from "@/lib/generated/api";
import { useSellerAccount } from "@/features/account/session";

const subscribe = () => () => {};
const clientReady = () => true;
const serverReady = () => false;
type IntakeFields = components["schemas"]["IntakeFields"];
type ChatResult = components["schemas"]["IntakeDraft"];
type ChatTurn = { user: string; reply: string };
const greeting = "你好，我是你的上架顾问。可以直接描述商品，或上传 CSV、XLSX 商品数据包，我会把已知资料整理到右侧。商品名与明细填写后即可提交；参考链接和图片可选，建单后会结合这些资料进行初审。";
const maxExportSize = 2 * 1024 * 1024;
type SavedOrder = { id: string; url: string; path: string; productName: string };

export function NewOrderForm({ overseas = false }: { overseas?: boolean }) {
  const account = useSellerAccount();
  const [fields, setFields] = useState({ product_name: "", product_details: "", domestic_product_url: "", notes: "" });
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [chatError, setChatError] = useState("");
  const [attachment, setAttachment] = useState<File | null>(null);
  const [sentAttachment, setSentAttachment] = useState<File | null>(null);
  const [updatedKeys, setUpdatedKeys] = useState<string[]>([]);
  const transcript = useRef<HTMLDivElement>(null);
  const exportInput = useRef<HTMLInputElement>(null);
  const hasNewAttachment = attachment !== null && attachment !== sentAttachment;
  const outgoing = [hasNewAttachment ? `附件：${attachment.name}` : "", message.trim()].filter(Boolean).join("\n");
  const completed = Number(Boolean(fields.product_name.trim())) + Number(Boolean(fields.product_details.trim()));
  const hydrated = useSyncExternalStore(subscribe, clientReady, serverReady);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<SavedOrder | null>(null);
  const [copyState, setCopyState] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [uploaded, setUploaded] = useState(false);
  const linkInput = useRef<HTMLInputElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const notice = useRef<HTMLDivElement>(null);
  const success = useRef<HTMLElement>(null);
  const submission = useRef<{ body: string; key: string } | null>(null);

  useEffect(() => { if (error) notice.current?.focus(); }, [error]);
  useEffect(() => { if (created) success.current?.focus(); }, [created]);

  useEffect(() => {
    const log = transcript.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [turns, sending]);

  function updateField(key: keyof IntakeFields, value: string) {
    setFields(current => ({ ...current, [key]: value }));
    setUpdatedKeys(current => current.filter(item => item !== key));
  }

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!outgoing || sending || busy || !hydrated) return;
    if (hasNewAttachment && (!/\.(csv|xlsx)$/i.test(attachment.name) || attachment.size === 0 || attachment.size > maxExportSize)) {
      setChatError("请选择不超过 2 MiB 的 CSV 或 XLSX 商品数据包。");
      return;
    }
    setSending(true);
    setChatError("");
    try {
      const form = new FormData();
      form.set("payload", JSON.stringify({ message: message.trim(), last_reply: turns.at(-1)?.reply ?? "", fields }));
      if (hasNewAttachment) form.set("file", attachment);
      const result = await apiFetch<ChatResult>("/orders/intake-chat", {
        method: "POST", timeoutMs: 45000, body: form, headers: csrfHeaders(account.csrf_token),
      });
      const updates = Object.fromEntries(Object.entries(result.updates).filter(([, value]) => typeof value === "string" && value.trim()));
      setUpdatedKeys(Object.keys(updates));
      setFields(current => ({ ...current, ...updates }));
      if (hasNewAttachment) setSentAttachment(attachment);
      setTurns(current => [...current, { user: outgoing, reply: result.reply }]);
      setMessage("");
    } catch (caught) {
      setChatError(caught instanceof ApiError && [413, 422].includes(caught.status ?? 0)
        ? "无法读取这份资料，请检查文件格式与内容后重试。也可以直接填写资料卡。"
        : `${errorMessage(caught)} 也可以直接填写资料卡。`);
    } finally { setSending(false); }
  }

  function selectFile(selected: File | null) {
    setFile(selected);
    setFileError(selected ? validateFile(selected) ?? "" : "");
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    selectFile(event.target.files?.[0] ?? null);
  }

  function pasteImage(event: ClipboardEvent<HTMLFormElement>) {
    const image = Array.from(event.clipboardData.files).find(item => item.type.startsWith("image/"));
    if (!image || busy || sending) return;
    event.preventDefault();
    if (fileInput.current) fileInput.current.value = "";
    selectFile(image);
  }

  async function uploadReference(order: SavedOrder, selected: File) {
    setUploadError("");
    try {
      const token = new URLSearchParams(new URL(order.url).hash.slice(1)).get("token");
      const session = await apiFetch<SellerSession>("/order-access/exchange", {
        method: "POST", body: JSON.stringify({ order_id: order.id, token }),
      });
      const body = new FormData();
      body.append("file", selected);
      await apiFetch(`/orders/${encodeURIComponent(order.id)}/assets`, {
        method: "POST", body, headers: csrfHeaders(session.csrf_token), timeoutMs: 30000,
      });
      setUploaded(true);
    } catch (caught) { setUploadError(uploadErrorMessage(caught)); }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || sending || !hydrated) return;
    const invalid = file ? validateFile(file) : null;
    if (invalid) { setError(invalid); return; }
    const fields = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const productName = String(fields.get("product_name") ?? "").trim();
      const productDetails = String(fields.get("product_details") ?? "").trim();
      if (!productName || !productDetails) { setError("请填写商品名字和已知的商品明细，不能只填写空格。"); return; }
      const body = JSON.stringify({
        product_name: productName,
        product_details: productDetails,
        domestic_product_url: String(fields.get("domestic_product_url") ?? "").trim() || null,
        notes: String(fields.get("notes") ?? "").trim() || null,
      });
      if (submission.current?.body !== body) submission.current = { body, key: crypto.randomUUID() };
      const result = await apiFetch<CreatedOrder>("/orders", { method: "POST", body, headers: csrfHeaders(account.csrf_token), idempotencyKey: submission.current.key });
      const privateUrl = new URL(result.private_url, window.location.origin);
      if (privateUrl.pathname !== `/o/${result.id}` || !new URLSearchParams(privateUrl.hash.slice(1)).get("token")) {
        throw new Error("私有链接格式异常");
      }
      const order = { id: result.id, url: new URL(privateUrl.pathname + privateUrl.hash, window.location.origin).href, path: privateUrl.pathname + privateUrl.hash, productName };
      setCreated(order);
      if (file) await uploadReference(order, file);
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }

  async function retryUpload() {
    if (busy || !created || !file) return;
    setBusy(true);
    try { await uploadReference(created, file); }
    finally { setBusy(false); }
  }

  async function copyLink() {
    if (!created) return;
    try { await navigator.clipboard.writeText(created.url); setCopyState("链接已复制，请妥善保存。"); }
    catch { linkInput.current?.select(); setCopyState("请选中链接后手动复制。"); }
  }

  if (created) return <section ref={success} tabIndex={-1} className={`creation-success ${styles.success}`} aria-busy={busy}>
    <div className="success-symbol" aria-hidden="true">✓</div><p className="eyebrow">YOUR ORDER, YOUR SPACE.</p><h1>资料已保存<span>.</span></h1>
    <p className="portal-intro">「{created.productName}」已保存到你的账号。以后登录“我的订单”即可找回，无需另外保存链接。</p>
    {file && <div aria-live="polite">
      {busy && <p role="status">订单已创建，正在上传商品参考图…</p>}
      {uploaded && <p className="form-notice notice-success" role="status">商品参考图已上传，可在订单中查看。</p>}
      {uploadError && <><ErrorNotice>订单已创建，但参考图未上传成功：{uploadError} 请重试上传，或进入订单后补充。</ErrorNotice><button type="button" className="button button-outline" disabled={busy} onClick={retryUpload}>重试上传参考图</button></>}
    </div>}
    <div className="private-link-box"><label htmlFor="private-order-link">私有订单链接</label><div><input ref={linkInput} id="private-order-link" readOnly value={created.url} onFocus={event => event.currentTarget.select()} /><button type="button" className="button button-outline" onClick={copyLink}>复制链接</button></div><p>访问此订单需要登录你的卖家账号，其他卖家不能通过链接查看。</p>{copyState && <p role="status">{copyState}</p>}</div>
    {!busy && <Link href={created.path} prefetch={false} className="button button-dark">查看订单进度 <span aria-hidden="true">↗</span></Link>}
    <p className="subtle-note"><Link href="/seller/orders">返回我的订单 ↗</Link></p>
    <p className="subtle-note">接下来由获客 Agent 审查资料，接单由负责人决定。发布还需你确认，当前仅为模拟模式。</p>
  </section>;

  return <>
    <div className={styles.overview}>
      <section className={styles.intro} aria-labelledby="intake-title">
        <span className={styles.eyebrow}>01 / LISTING STUDIO</span>
        <h1 id="intake-title">和上架顾问聊聊<span>.</span></h1>
        <p>边聊边整理商品资料，商家信息从登录账号自动关联。</p>
        <span className={styles.spark} aria-hidden="true">✳</span>
      </section>
      <section className={styles.status} aria-labelledby="intake-progress">
        <span className={styles.eyebrow}>AMAZON US · {overseas ? "出海进阶包" : "一站式上架包"}</span>
        <h2 id="intake-progress">资料收集进度</h2>
        <div className={styles.progressLabel}><span>{completed === 2 ? "资料已整理 · 可提交" : "填写商品名与已有明细即可"}</span><strong>{completed} / 2</strong></div>
        <progress aria-labelledby="intake-progress" max={2} value={completed} />
      </section>
    </div>
    <div className={styles.grid}>
      <section className={styles.panel} aria-labelledby="intake-chat-heading">
        <header className={styles.heading}>
          <div className={styles.headingTitle}><span className={styles.icon} aria-hidden="true">✳</span><div><h2 id="intake-chat-heading">上架顾问</h2><small>描述商品或上传商品数据包</small></div></div>
          <span className={styles.badge}><i aria-hidden="true" />AI 接待</span>
        </header>
        <div className={styles.log} role="log" ref={transcript} aria-label="上架对话">
          <div className={styles.agent}><small>上架顾问 · AI</small><p>{greeting}</p></div>
          {turns.map((turn, index) => <div className={styles.turn} key={index}><div className={styles.user}><small>你</small><p>{turn.user}</p></div><div className={styles.agent}><small>上架顾问 · AI</small><p>{turn.reply}</p></div></div>)}
          {sending && <div className={styles.turn}><div className={styles.agent} role="status"><small>上架顾问 · AI</small><p>正在整理商品资料<span className={styles.dots} aria-hidden="true"><i /><i /><i /></span></p></div></div>}
        </div>
        <form className={styles.composer} method="post" onSubmit={send} aria-busy={sending}>
          <fieldset disabled={!hydrated || busy || sending} className="intake-fields">
            <label className="sr-only" htmlFor="intake-message">描述你的商品</label>
            <textarea id="intake-message" value={message} onChange={event => setMessage(event.target.value)} placeholder="例如：黑色硅胶理线夹，6个装，用于整理桌面线缆。" rows={3} maxLength={5000} />
            <div className={styles.attachment}>
              <label htmlFor="product-export">商品数据包（选填）</label>
              <input ref={exportInput} id="product-export" type="file" accept=".csv,.xlsx" onChange={event => { setAttachment(event.target.files?.[0] ?? null); setChatError(""); }} />
            </div>
            {attachment && <div className={styles.attachment}><span>{attachment.name} · {hasNewAttachment ? "待发送" : "已发送"}</span><button type="button" className="text-button" onClick={() => { setAttachment(null); setSentAttachment(null); if (exportInput.current) exportInput.current.value = ""; }}>移除数据包</button></div>}
            <div className={styles.actions}><small>CSV / XLSX · 不超过 2 MiB</small><button className={styles.button} type="submit" disabled={!outgoing || !hydrated || busy || sending}>{sending ? "整理中…" : "发送"}</button></div>
          </fieldset>
          {chatError && <ErrorNotice>{chatError}</ErrorNotice>}
        </form>
      </section>
      <section className={styles.panel} aria-labelledby="intake-card-heading">
        <header className={styles.heading}><div className={styles.headingTitle}><span className={styles.icon} aria-hidden="true">↗</span><div><h2 id="intake-card-heading">商品资料卡</h2><small>可直接填写，也可由顾问整理</small></div></div><span className={styles.badge}>当前草稿</span></header>
        <form className={`${styles.card} intake-form`} method="post" onSubmit={submit} onPaste={pasteImage} aria-busy={busy}>
          {!hydrated && <p className="form-notice notice-warning" role="status">正在加载表单交互。若长时间无法操作，请刷新页面，并使用 Safari 16.4 或更新版本、Chrome 或 Edge。</p>}
          <noscript><p className="form-notice notice-error">请开启浏览器 JavaScript 后重新打开此页面。</p></noscript>
          {error && <div ref={notice} tabIndex={-1}><ErrorNotice>{error}</ErrorNotice></div>}
          <fieldset className="intake-fields" disabled={!hydrated || busy || sending}>
            <div className="form-section">
              <label className={styles.row} data-filled={Boolean(fields.product_name.trim())} data-updated={updatedKeys.includes("product_name")}><span>商品名字 <em>必填</em></span><input name="product_name" required maxLength={300} placeholder="例如：桌面理线夹" value={fields.product_name} onChange={event => updateField("product_name", event.target.value)} /></label>
              <label className={`${styles.row} ${styles.longRow}`} data-updated={updatedKeys.includes("product_details")}><span>商品明细信息 <em>必填</em></span><textarea name="product_details" rows={5} required maxLength={10000} placeholder="把已知材质、规格和使用信息写在一起即可，无需补齐固定参数。" value={fields.product_details} onChange={event => updateField("product_details", event.target.value)} /></label>
              <label className={`${styles.row} ${styles.longRow}`} data-updated={updatedKeys.includes("domestic_product_url")}><span>国内平台商品链接（选填）</span><input name="domestic_product_url" type="url" maxLength={2000} placeholder="https://…（如淘宝、京东、1688商品页）" value={fields.domestic_product_url} onChange={event => updateField("domestic_product_url", event.target.value)} /></label>
              <p className={styles.notice}>建单后顾问会结合描述、参考图与公开链接进行初审；无法读取链接时，使用已填写的资料。</p>
            </div>
            <div className="form-section">
              <div className={`${imageStyles.uploadForm} intake-reference`}>
                <label className={imageStyles.fileLabel} htmlFor="intake-reference">商品参考图 <span>选填</span></label>
                <input ref={fileInput} id="intake-reference" className={imageStyles.fileInput} type="file" accept="image/png,image/jpeg,image/webp" onChange={chooseFile} aria-describedby="intake-reference-help" />
                <p className={imageStyles.limits} id="intake-reference-help">支持选择或复制后粘贴 PNG、JPEG、WebP 图片，单张不超过 10 MiB。保存资料时上传，建单后也可继续补充。</p>
                {file && <div className={imageStyles.selectedFile}><strong>{file.name}</strong><span>{formatBytes(file.size)}</span><button className="text-button" type="button" onClick={() => { setFile(null); setFileError(""); if (fileInput.current) fileInput.current.value = ""; }}>清除选择</button></div>}
                {fileError && <p className={imageStyles.error} role="alert">{fileError}</p>}
              </div>
              <label className={`${styles.row} ${styles.longRow}`} data-updated={updatedKeys.includes("notes")}><span>补充商品说明与期望卖点（选填）</span><textarea name="notes" rows={3} maxLength={10000} placeholder="已有文案、期望卖点，或需要特别注意的地方。" value={fields.notes} onChange={event => updateField("notes", event.target.value)} /></label>
            </div>
            <div className={styles.review}><p className={styles.notice}>请核对已有资料。订单将保存到当前账号，可从“我的订单”找回。</p><button className={styles.button} disabled={!hydrated || busy || sending} type="submit">{busy ? "正在保存资料…" : "保存资料，创建订单"}</button></div>
          </fieldset>
        </form>
      </section>
    </div>
  </>;
}
