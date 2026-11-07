"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useSyncExternalStore, type ChangeEvent, type ClipboardEvent, type FormEvent } from "react";
import { ErrorNotice } from "@/components/async-feedback";
import { apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import { formatBytes, uploadErrorMessage, validateFile } from "./reference-image";
import type { CreatedOrder, SellerSession } from "./types";
import styles from "./reference-assets-panel.module.css";
import { useSellerAccount } from "@/features/account/session";

const subscribe = () => () => {};
const clientReady = () => true;
const serverReady = () => false;
type SavedOrder = { id: string; url: string; path: string; productName: string };

export function NewOrderForm() {
  const account = useSellerAccount();
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

  function selectFile(selected: File | null) {
    setFile(selected);
    setFileError(selected ? validateFile(selected) ?? "" : "");
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    selectFile(event.target.files?.[0] ?? null);
  }

  function pasteImage(event: ClipboardEvent<HTMLFormElement>) {
    const image = Array.from(event.clipboardData.files).find(item => item.type.startsWith("image/"));
    if (!image || busy) return;
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
    if (busy || !hydrated) return;
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

  if (created) return <section ref={success} tabIndex={-1} className="creation-success" aria-busy={busy}>
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
    <div className="portal-page-heading"><p className="eyebrow">LET’S MEET YOUR PRODUCT.</p><h1>从一件好商品开始<span>.</span></h1><p>只需填写商品资料，商家信息会从当前登录账号自动关联。</p></div>
    <div className="intake-grid"><form className="surface intake-form" method="post" onSubmit={submit} onPaste={pasteImage} aria-busy={busy}>
      {!hydrated && <p className="form-notice notice-warning" role="status">正在加载表单交互。若长时间无法操作，请刷新页面，并使用 Safari 16.4 或更新版本、Chrome 或 Edge。</p>}
      <noscript><p className="form-notice notice-error">请开启浏览器 JavaScript 后重新打开此页面。</p></noscript>
      {error && <div ref={notice} tabIndex={-1}><ErrorNotice>{error}</ErrorNotice></div>}
      <fieldset className="intake-fields" disabled={!hydrated || busy}>
        <div className="form-section"><div className="form-section-title"><span>01</span><h2>商品名字与明细</h2></div>
          <div className="field"><label htmlFor="product_name">商品名字 <em>必填</em></label><input id="product_name" name="product_name" required maxLength={300} placeholder="例如：桌面理线夹" /></div>
          <div className="field"><label htmlFor="product_details">商品明细信息 <em>必填</em></label><textarea id="product_details" name="product_details" rows={5} required maxLength={10000} placeholder="例如：硅胶材质，黑色，6个装，单件4×3×2cm，用于整理桌面线缆。先写已知信息，缺少的内容可之后补充。" /><p className="field-help">把已有参数和使用信息写在一起即可，顾问会结合图片和参考链接分析资料，整理内容方向。</p></div>
          <div className="field"><label htmlFor="domestic_product_url">国内平台商品链接 <span>选填</span></label><input id="domestic_product_url" name="domestic_product_url" type="url" maxLength={2000} placeholder="https://…（如淘宝、京东、1688商品页）" /><p className="field-help">顾问会尝试读取公开页面；需要登录或验证时，将优先使用你填写的资料和图片。</p></div>
        </div>
        <div className="form-section"><div className="form-section-title"><span>02</span><h2>商品图片与补充说明</h2></div>
          <div className={`${styles.uploadForm} intake-reference`}>
            <label className={styles.fileLabel} htmlFor="intake-reference">商品参考图 <span>选填</span></label>
            <input ref={fileInput} id="intake-reference" className={styles.fileInput} type="file" accept="image/png,image/jpeg,image/webp" onChange={chooseFile} aria-describedby="intake-reference-help" />
            <p className={styles.limits} id="intake-reference-help">支持选择或复制后粘贴 PNG、JPEG、WebP 图片，单张不超过 10 MiB。保存资料时上传，建单后也可继续补充。</p>
            {file && <div className={styles.selectedFile}><strong>{file.name}</strong><span>{formatBytes(file.size)}</span><button className="text-button" type="button" onClick={() => { setFile(null); setFileError(""); if (fileInput.current) fileInput.current.value = ""; }}>清除选择</button></div>}
            {fileError && <p className={styles.error} role="alert">{fileError}</p>}
          </div>
          <div className="field intake-reference"><label htmlFor="notes">补充商品说明与期望卖点 <span>选填</span></label><textarea id="notes" name="notes" rows={4} maxLength={10000} placeholder="已有文案、希望突出的特点，或需要我们特别注意的地方。" /></div>
        </div>
        <div className="form-submit-row"><p>订单将保存到你的账号，可从“我的订单”找回。</p><button className="button button-dark" disabled={!hydrated || busy} type="submit">{busy ? "正在保存资料…" : "保存资料，创建订单"}<span aria-hidden="true">↗</span></button></div>
      </fieldset>
    </form>
      <aside className="intake-aside"><p className="eyebrow">START WITH ONE GOOD PRODUCT.</p><h2>一件商品，<br />一个清楚的开始。</h2><p>根据商品本身的特点，为 Amazon 美国站准备美式英语内容。</p><ol><li><span>01</span><div><strong>先保存，再补充</strong><p>还没准备好的资料，可以回到订单入口继续补全。</p></div></li><li><span>02</span><div><strong>事实与想法分开</strong><p>已核实的商品属性是文案依据，期望卖点也需要事实支持。</p></div></li><li><span>03</span><div><strong>进度保留在同一处</strong><p>订单入口承载资料与沟通记录，刷新后也能继续查看。</p></div></li></ol><div className="aside-note">资料保存后，获客顾问会分析资料并提出文案方向；经接单、研究、生成和审校后交付。当前发布仅为模拟模式。</div></aside>
    </div>
  </>;
}
