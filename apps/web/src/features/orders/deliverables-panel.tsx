"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import { ApiError, apiFetch, errorMessage } from "@/lib/api";
import type { components } from "@/lib/generated/api";
import { formatTime } from "./types";
import styles from "./deliverables-panel.module.css";

type Delivery = components["schemas"]["DeliveryView"];
type Props = { orderId: string; onViewedVersionChange?: (versionId: string | null) => void };

function CopyButton({ value, label }: { value: string; label: string }) {
  const [notice, setNotice] = useState("");
  async function copy() {
    try { await navigator.clipboard.writeText(value); setNotice("已复制"); }
    catch { setNotice("请选择下方文案手动复制"); }
  }
  return <span className={styles.copyControl}><button type="button" className="text-button" onClick={copy}>{label}</button>{notice && <small role="status">{notice}</small>}</span>;
}

function DeliveryContent({ delivery }: { delivery: Delivery }) {
  const { listing } = delivery;
  const rangeValue = delivery.pricing.suggested_listing_price_range;
  const range = rangeValue && typeof rangeValue === "object" ? rangeValue as Record<string, unknown> : null;
  const pricingNotes = typeof delivery.pricing.notes === "string" ? delivery.pricing.notes : "";
  const allCopy = `${listing.title}\n\n${listing.bullets.map((value, index) => `${index + 1}. ${value}`).join("\n")}\n\n${listing.description}\n\n${delivery.faqs.map(item => `Q: ${item.question}\nA: ${item.answer}`).join("\n\n")}`;
  return <article className={styles.delivery} data-version-id={delivery.version_id}><div className={styles.deliveryHeading}><div><h3>已交付版本 v{delivery.version_number}</h3><p>{formatTime(delivery.delivered_at)}</p></div>{delivery.is_demo && <span className="demo-tag">虚构演示交付</span>}</div><div className={styles.deliveryActions}><a href={delivery.download_url} download className="button button-dark button-small">下载本版 ZIP <span aria-hidden="true">↧</span></a><CopyButton value={allCopy} label="复制本版全部文案" /></div><div className={styles.copyField}><div><span>PRODUCT TITLE</span><CopyButton value={listing.title} label="复制标题" /></div><h4>{listing.title}</h4></div><div className={styles.copyField}><div><span>FIVE BULLET POINTS</span><CopyButton value={listing.bullets.join("\n")} label="复制五点卖点" /></div><ol>{listing.bullets.map((bullet, index) => <li key={index}>{bullet}</li>)}</ol></div><div className={styles.copyField}><div><span>DESCRIPTION</span><CopyButton value={listing.description} label="复制详情" /></div><p>{listing.description}</p></div>{delivery.faqs.length > 0 && <details className={styles.faqs}><summary>买家 FAQ <span>＋</span></summary>{delivery.faqs.map((item, index) => <div key={index}><strong>{item.question}</strong><p>{item.answer}</p></div>)}</details>}<div className={styles.pricing}><span>配套资料 · 商品售价参考</span><strong>{range ? `${String(range.low ?? "—")} – ${String(range.high ?? "—")} ${String(range.currency ?? "")}` : "暂无可靠定价建议"}</strong>{pricingNotes && <p>{pricingNotes}</p>}</div><h4 className={styles.imageTitle}>本版商品图片</h4><div className={styles.images}>{delivery.images.map(asset => <a href={asset.url} target="_blank" rel="noreferrer" className={styles.imageCard} key={asset.id}><div><Image src={asset.url} alt={`已交付商品${asset.kind === "main_candidate" ? "主图候选" : "信息附图"}`} fill unoptimized sizes="(max-width: 600px) 100vw, 50vw" /></div><span>{asset.width} × {asset.height} px · 查看原图 ↗</span></a>)}</div><p className={styles.hash}>本版内容摘要 {delivery.content_hash.slice(0, 16)}</p></article>;
}

function DeliverablesWorkspace({ orderId, onViewedVersionChange }: Props) {
  const [items, setItems] = useState<Delivery[] | null>(null);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    async function load() {
      let denied = false;
      try {
        const response = await apiFetch<{ items: Delivery[] }>(`/orders/${orderId}/deliverables`, { signal: controller.signal });
        if (active) { setItems(response.items); setError(""); }
      } catch (caught) { if (active) { setError(errorMessage(caught)); denied = caught instanceof ApiError && [401, 403].includes(caught.status ?? 0); if (denied) setItems(null); } }
      finally { if (active && !denied) timer = setTimeout(() => void load(), 5000); }
    }
    void load();
    return () => { active = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [orderId, revision]);
  const versions = [...(items ?? [])].sort((left, right) => right.version_number - left.version_number);
  const current = versions.find(item => item.id === selected) ?? versions[0];
  const viewedVersionId = current?.version_id ?? null;
  useEffect(() => { onViewedVersionChange?.(viewedVersionId); }, [viewedVersionId, onViewedVersionChange]);

  function selectDelivery(id: string) {
    const next = versions.find(item => item.id === id);
    // Revoke cross-panel actions in the same event that changes the visible document.
    onViewedVersionChange?.(next?.version_id ?? null);
    setSelected(id);
  }
  return <section className={`surface workspace-panel ${styles.panel}`} aria-labelledby="deliverables-title"><div className="panel-heading"><h2 id="deliverables-title">已放行交付包</h2><button className="text-button" type="button" onClick={() => setRevision(value => value + 1)}>刷新 ↻</button></div>{error && <p className={styles.error} role="alert">{error}</p>}{versions.length > 1 && <label className={styles.versionSelect}>查看交付版本<select value={current.id} onChange={event => selectDelivery(event.target.value)}>{versions.map(item => <option value={item.id} key={item.id}>v{item.version_number} · {formatTime(item.delivered_at)}</option>)}</select></label>}{current ? <DeliveryContent delivery={current} /> : <p className={styles.empty}>{items === null && !error ? "正在读取交付记录…" : "尚无已放行的交付包。完整图文审校通过后，还需要负责人放行。"}</p>}</section>;
}

export function DeliverablesPanel(props: Props) {
  return <DeliverablesWorkspace key={props.orderId} {...props} />;
}
