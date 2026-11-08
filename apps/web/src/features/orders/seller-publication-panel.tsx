"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { ApiError, apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import type { components } from "@/lib/generated/api";
import { editableFacts, FactEditor, normalizedFacts } from "./fact-editor";
import type { Order } from "./types";
import styles from "./seller-publication-panel.module.css";

type Delivery = components["schemas"]["DeliveryView"];
type Publication = components["schemas"]["PublicationView"];
type Queued = components["schemas"]["QueuedCommand"];
type Props = { order: Order; csrfToken: string; refresh?: () => void; viewedVersionId?: string | null };
type ActionProps = Props & { delivery: Delivery; disabled: boolean; onQueued: (result: Queued) => void; onBusy: (value: boolean) => void };

const statusLabels: Record<string, string> = { NOT_CONFIRMED: "待卖家确认", INVALIDATED: "原确认已失效", CONFIRMED_QUEUED: "已确认，等待处理", READY: "正在准备发布", PREPARED: "发布计划已核对", SUBMITTING: "正在提交模拟记录", UNKNOWN: "发布结果待核对", FAILED: "本次发布处理失败", BLOCKED: "发布条件尚未满足", SIMULATED: "模拟上架已完成" };

function conflictMessage(caught: unknown) {
  return caught instanceof ApiError && caught.status === 409 ? "资料、成品版本或处理状态已变化，请刷新后查看当前可用操作。" : errorMessage(caught);
}

function FeedbackForm({ order, csrfToken, delivery, disabled, onQueued, onBusy, refresh }: ActionProps) {
  const id = useId();
  const [content, setContent] = useState("");
  const [updateFacts, setUpdateFacts] = useState(false);
  const [facts, setFacts] = useState(() => editableFacts(order.facts));
  const [baseRevision, setBaseRevision] = useState(order.revision);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submission = useRef<{ body: string; key: string } | null>(null);
  const stale = baseRevision !== order.revision;
  const allowed = order.allowed_actions.includes("feedback") && !disabled;
  const selectedFacts = normalizedFacts(facts);
  const complete = selectedFacts.length > 0 && selectedFacts.every(fact => fact.confirmed);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || disabled || stale || !allowed || !csrfToken || !content.trim()) return;
    if (updateFacts && !complete) { setError("请核实你本次明确修改的属性；无需补齐固定参数。"); return; }
    const body = JSON.stringify({ expected_revision: baseRevision, version_id: delivery.version_id, content: content.trim(), fact_updates: updateFacts ? selectedFacts : null });
    if (submission.current?.body !== body) submission.current = { body, key: crypto.randomUUID() };
    setBusy(true); onBusy(true); setError("");
    try {
      const result = await apiFetch<Queued>(`/orders/${encodeURIComponent(order.id)}/feedback`, { method: "POST", headers: csrfHeaders(csrfToken), body, idempotencyKey: submission.current.key });
      setContent(""); submission.current = null; onQueued(result); refresh?.();
    } catch (caught) { setError(conflictMessage(caught)); if (caught instanceof ApiError && caught.status === 409) refresh?.(); }
    finally { setBusy(false); onBusy(false); }
  }

  return <form className={styles.form} onSubmit={submit}>
    <h3>提出修改意见</h3>
    <p className={styles.hint}>反馈针对第 {delivery.version_number} 版成品。文字意见不会自动改写商品参数；涉及参数时请保留全部适用事实并逐项核实。</p>
    {stale && <div className={styles.warning}>商品资料已有更新，未发送的意见仍保留。<button className="text-button" type="button" onClick={() => { setFacts(editableFacts(order.facts)); setBaseRevision(order.revision); setError(""); }}>载入最新商品事实 ↻</button></div>}
    <label htmlFor={`${id}-content`}>反馈内容</label>
    <textarea id={`${id}-content`} rows={3} required maxLength={10000} value={content} disabled={busy || disabled} readOnly={!allowed || stale} onChange={event => setContent(event.target.value)} placeholder="说明需要调整的措辞、图片或已确认的参数…" />
    <label className={styles.checkbox}><input type="checkbox" checked={updateFacts} disabled={busy || !allowed || stale} onChange={event => setUpdateFacts(event.target.checked)} /><span>同时更新商品事实</span></label>
    {updateFacts && <div className={styles.facts}><p className={styles.hint}>这里提交本次全部商品事实。改动后的项目需要重新勾选核实；不可把竞品信息或图片观察直接当作商品参数。</p><FactEditor facts={facts} onChange={setFacts} disabled={busy || !allowed || stale} /></div>}
    {error && <p className={styles.error} role="alert">{error}</p>}
    <button className="button button-outline button-small" type="submit" disabled={busy || !allowed || stale || !csrfToken || !content.trim()}>{busy ? "正在排队…" : "提交修改反馈"}</button>
  </form>;
}

function canonicalPrice(value: string): string | null {
  const input = value.trim();
  if (!/^(?:0|[1-9]\d{0,5})(?:\.\d{1,2})?$/.test(input)) return null;
  const [whole, decimals = ""] = input.split(".");
  const normalized = `${whole}.${decimals.padEnd(2, "0")}`;
  return normalized === "0.00" ? null : normalized;
}

function ConfirmationForm({ order, csrfToken, delivery, disabled, onQueued, onBusy, refresh }: ActionProps) {
  const id = useId();
  const [price, setPrice] = useState("");
  const [sku, setSku] = useState("");
  const [consent, setConsent] = useState(false);
  const [baseRevision] = useState(order.revision);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submission = useRef<{ body: string; key: string } | null>(null);
  const normalizedPrice = canonicalPrice(price);
  const stale = baseRevision !== order.revision;
  const allowed = order.allowed_actions.includes("confirm_publish") && !disabled && !stale;

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !allowed || !consent || !csrfToken || !sku.trim()) return;
    if (!normalizedPrice) { setError("请填写大于 0 的商品售价，最多两位小数。"); return; }
    const body = JSON.stringify({ expected_revision: baseRevision, version_id: delivery.version_id, listing_price: normalizedPrice, currency: "USD", seller_sku: sku.trim() });
    if (submission.current?.body !== body) submission.current = { body, key: crypto.randomUUID() };
    setBusy(true); onBusy(true); setError("");
    try {
      const result = await apiFetch<Queued>(`/orders/${encodeURIComponent(order.id)}/confirmations`, { method: "POST", body, headers: csrfHeaders(csrfToken), idempotencyKey: submission.current.key });
      setConsent(false); submission.current = null; onQueued(result); refresh?.();
    } catch (caught) { setError(conflictMessage(caught)); if (caught instanceof ApiError && caught.status === 409) { setConsent(false); refresh?.(); } }
    finally { setBusy(false); onBusy(false); }
  }

  return <form className={styles.form} onSubmit={confirm}>
    <h3>确认成品与模拟售价</h3>
    <p className={styles.hint}>请先检查第 {delivery.version_number} 版文案和全部图片。商品售价由你填写，竞品价格区间仅供参考。</p>
    {stale && <p className={styles.warning}>当前商品事实已变化，旧版确认不可使用。请等待新版本交付后重新确认。</p>}
    <div className={styles.priceFields}><div><label htmlFor={`${id}-price`}>售价（USD）</label><input id={`${id}-price`} inputMode="decimal" required maxLength={9} value={price} disabled={busy || !allowed} onChange={event => { setPrice(event.target.value); setConsent(false); }} placeholder="例如 12.99" /></div><div><label htmlFor={`${id}-currency`}>币种</label><input id={`${id}-currency`} value="USD" readOnly tabIndex={-1} /></div></div>
    <label htmlFor={`${id}-sku`}>卖家 SKU</label><input id={`${id}-sku`} required maxLength={100} value={sku} disabled={busy || !allowed} onChange={event => { setSku(event.target.value); setConsent(false); }} placeholder="填写这件商品的唯一 SKU" />
    <label className={styles.checkbox}><input type="checkbox" required checked={consent} disabled={busy || !allowed || !normalizedPrice || !sku.trim()} onChange={event => setConsent(event.target.checked)} /><span>我已检查第 {delivery.version_number} 版文案和图片，确认售价 <strong>USD {normalizedPrice ?? "待填写"}</strong>、SKU <strong>{sku.trim() || "待填写"}</strong>，并同意为本单商品生成模拟刊登记录。不会向 Amazon 提交。</span></label>
    {error && <p className={styles.error} role="alert">{error}</p>}
    <button className="button button-dark button-small" type="submit" disabled={busy || !allowed || !csrfToken || !consent || !normalizedPrice || !sku.trim()}>{busy ? "正在排队…" : "确认本版并模拟上架"}</button>
  </form>;
}

function PublicationWorkspace({ order, csrfToken, refresh, viewedVersionId = null }: Props) {
  const [deliveries, setDeliveries] = useState<Delivery[] | null>(null);
  const [publication, setPublication] = useState<Publication | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [queuedId, setQueuedId] = useState<string | null>(null);
  const [serverPendingId, setServerPendingId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [revision, setRevision] = useState(0);
  const base = `/orders/${encodeURIComponent(order.id)}`;

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    async function load() {
      let denied = false;
      try {
        const [saved, state, currentOrder] = await Promise.all([
          apiFetch<{ items: Delivery[] }>(`${base}/deliverables`, { signal: controller.signal }),
          apiFetch<Publication>(`${base}/publication`, { signal: controller.signal }),
          apiFetch<Order>(base, { signal: controller.signal }),
        ]);
        if (active) {
          setDeliveries(saved.items); setPublication(state); setError("");
          setServerPendingId(currentOrder.pending_command_id);
          if (queuedId && currentOrder.pending_command_id !== queuedId) setQueuedId(null);
        }
      } catch (caught) {
        if (active) {
          denied = caught instanceof ApiError && [401, 403, 404].includes(caught.status ?? 0);
          setError(errorMessage(caught)); if (denied) { setDeliveries(null); setPublication(null); }
        }
      } finally { if (active && !denied) timer = setTimeout(() => void load(), 5000); }
    }
    void load();
    return () => { active = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [base, order.revision, revision, queuedId]);

  const latest = deliveries?.reduce<Delivery | undefined>((current, item) => !current || item.version_number > current.version_number ? item : current, undefined);
  const viewingLatest = Boolean(latest && viewedVersionId === latest.version_id);
  const requestPending = Boolean(queuedId || order.pending_command_id || serverPendingId || submitting);
  const disabled = requestPending || !viewingLatest;
  const simulated = publication?.mode === "simulated";
  function queued(result: Queued) { setQueuedId(result.command_id); setNotice("请求已入队，处理进度与实际结果会在订单中更新。"); setRevision(current => current + 1); }

  return <section className={`surface workspace-panel ${styles.panel}`} id="seller-confirmation" style={{ scrollMarginTop: 24 }} aria-label="卖家反馈与发布确认">
    <div className="panel-heading"><h2>反馈与发布确认</h2><button className="text-button" type="button" onClick={() => { setRevision(current => current + 1); refresh?.(); }}>刷新 ↻</button></div>
    <p className={styles.mode}>{publication && !simulated ? "当前请求的发布模式尚未接通，请联系负责人核实。" : "当前发布为模拟模式，不代表 Amazon 真实上架、付款或成交。"}</p>
    {error && <p className={styles.error} role="alert">{error}</p>}
    {!deliveries && !error && <p className={styles.hint} role="status">正在读取已交付版本与发布状态…</p>}
    {publication && <div className={styles.status}><span>实际发布状态</span><strong>{statusLabels[publication.status] ?? "等待状态更新"}</strong></div>}
    {publication?.status === "UNKNOWN" && <p className={styles.warning}>结果仍待核对，请联系负责人检查记录，暂不要重复确认发布。</p>}
    {publication?.confirmation && <p className={styles.hint}>{publication.confirmation.valid ? "已保存的确认" : "已失效的历史确认"}：USD {publication.confirmation.listing_price} · SKU {publication.confirmation.seller_sku}</p>}
    {notice && <p className={styles.notice} role="status">{notice}</p>}
    {latest ? <><div className={styles.version}><strong>当前已交付：第 {latest.version_number} 版</strong><span>商品资料 · {latest.images.length} 张图片</span><p lang="en-US">{latest.listing.title}</p></div>
      {!viewingLatest && <p className={styles.warning} role="status">正在查看历史交付版本，或最新版本尚未加载。请到上方“已放行交付包”切换并查看最新第 {latest.version_number} 版，再提交反馈或确认。</p>}
      <FeedbackForm key={`feedback-${latest.version_id}`} order={order} csrfToken={csrfToken} delivery={latest} disabled={disabled} onQueued={queued} onBusy={setSubmitting} refresh={refresh} />
      {simulated ? <ConfirmationForm key={`confirmation-${latest.version_id}-${order.revision}-${viewedVersionId ?? "unviewed"}`} order={order} csrfToken={csrfToken} delivery={latest} disabled={disabled} onQueued={queued} onBusy={setSubmitting} refresh={refresh} /> : <p className={styles.warning}>当前发布模式暂不可用，请联系负责人核实发布配置。</p>}
      {requestPending && <p className={styles.hint}>已有请求等待处理，当前无法追加反馈或发布确认。</p>}
    </> : deliveries && <p className={styles.hint}>完整图文通过审核并由负责人放行后，才会出现可反馈和确认的交付版本。</p>}
  </section>;
}

export function SellerPublicationPanel(props: Props) {
  return <PublicationWorkspace key={props.order.id} {...props} />;
}
