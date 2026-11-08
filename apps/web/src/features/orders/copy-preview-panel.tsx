"use client";

import { useEffect, useRef, useState } from "react";
import { apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import { factLabels, type Order } from "./types";

type Brand = { brand: string; facts: { key: string; value: string }[]; sources: string[]; variant_options: string[]; scope: string };
type Draft = { title: string; bullets: string[]; description: string; faqs: { question: string; answer: string }[] };
type Preview = { id: string; revision: number; status: string; draft: Draft | null; missing_fields: string[]; message?: string; issues: { reason: string }[] };

export function CopyPreviewPanel({ order, csrfToken, refresh }: { order: Order; csrfToken: string; refresh: () => void }) {
  const [storedPreview, setPreview] = useState<Preview | null>(null);
  const preview = storedPreview?.revision === order.revision ? storedPreview : null;
  const [brand, setBrand] = useState<Brand | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const revision = useRef(order.revision);
  const path = `/orders/${order.id}`;
  useEffect(() => {
    revision.current = order.revision;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const data = await apiFetch<{ preview: Preview | null; brand_profile: Brand | null }>(`${path}/copy-preview`);
        if (!cancelled && !data.preview && csrfToken && ["INTAKE", "NEEDS_INFO", "AWAITING_ACCEPTANCE", "AWAITING_RELEASE"].includes(order.phase)) {
          setPreview({ id: "pending", revision: order.revision, status: "running", draft: null, missing_fields: [], issues: [] });
          data.preview = await apiFetch<Preview>(`${path}/copy-preview`, { method: "POST", headers: csrfHeaders(csrfToken), timeoutMs: 65000, body: JSON.stringify({ expected_revision: order.revision }) });
        }
        if (cancelled) return;
        setPreview(data.preview); setBrand(data.brand_profile); setError(""); setNotice("");
        if (data.preview?.status === "running") timer = setTimeout(load, 2000);
      } catch (e) { if (!cancelled) setError(errorMessage(e)); }
    }
    void load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [path, order.revision, order.phase, csrfToken, preview?.status]);

  async function generate() {
    if (busy) return;
    const base = order.revision;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await apiFetch<Preview>(`${path}/copy-preview`, { method: "POST", headers: csrfHeaders(csrfToken), timeoutMs: 65000, body: JSON.stringify({ expected_revision: base }) });
      if (revision.current !== base) { setError("商品资料已更新，请按最新版重新生成草稿。"); return; }
      setPreview(result); refresh();
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  async function applyBrand() {
    setBusy(true); setError("");
    try {
      await apiFetch(`${path}/brand-basics`, { method: "POST", headers: csrfHeaders(csrfToken), body: JSON.stringify({ expected_revision: order.revision }) });
      setNotice("品牌基础信息已补入，容量等具体规格仍保留待确认。"); refresh();
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  const draft = preview?.draft;
  const confirmedVolume = order.facts.find(f => f.key === "net_volume" && f.confirmed && f.value && f.unit);
  const brandMissing = brand?.facts.some(f => !order.facts.some(item => item.key === f.key));
  return <section className="surface workspace-panel" aria-label="英文文案草稿">
    <div className="panel-heading"><h2>先看看英文草稿</h2><span>未审核 · 非正式交付</span></div>
    <p className="field-help">打开待补资料订单后，自动用已有信息生成本版标题、五点、详情和 FAQ；未知参数留空。补齐必要信息并通过审校、负责人批准后，才会成为正式交付包。</p>
    {brand && <div className="form-notice"><strong>已识别品牌：{brand.brand}</strong><p>可补入：{brand.facts.map(f => `${factLabels[f.key] ?? f.key}：${f.value}`).join("；")}。</p><p>官网有 {brand.variant_options.join("、")} 等规格。{confirmedVolume ? `本单已确认 ${confirmedVolume.value} ${confirmedVolume.unit}，不按品牌规格替换。` : "本单容量待确认，不自动猜选。"}</p><p>{brand.sources.map((url, i) => <a href={url} key={url} target="_blank" rel="noreferrer">官方资料 {i + 1} ↗ </a>)}</p>{brandMissing && <button type="button" className="text-button" disabled={busy || Boolean(order.pending_command_id) || !["INTAKE", "NEEDS_INFO", "AWAITING_ACCEPTANCE", "AWAITING_RELEASE"].includes(order.phase)} onClick={() => void applyBrand()}>补入品牌基础资料</button>}</div>}
    <button type="button" className="button button-dark button-small" disabled={busy || preview?.status === "running" || preview?.status === "succeeded"} onClick={() => void generate()}>{busy || preview?.status === "running" ? "正在生成草稿…" : preview?.status === "succeeded" ? "本版草稿已生成" : "生成英文草稿"}</button>
    {preview?.missing_fields.length ? <p className="field-help">正式交付前待确认：{preview.missing_fields.map(k => factLabels[k] ?? k).join("、")}。这些参数不会自动写入草稿。</p> : null}
    {preview?.status === "failed" && <p role="alert">{preview.message}</p>}
    {preview?.status === "needs_review" && <p role="alert">草稿包含待核对的声明，已拦截展示。请检查商品资料后重新生成。</p>}
    {preview?.status === "stale" && <p role="alert">此草稿对应旧资料版本，不能用于当前商品。</p>}
    {draft && preview?.status !== "stale" && <div>
      <h3>英文标题</h3><p>{draft.title}</p>
      <h3>五点描述</h3><ol>{draft.bullets.map((line, i) => <li key={i}>{line}</li>)}</ol>
      <h3>详情文案</h3><p style={{ whiteSpace: "pre-wrap" }}>{draft.description}</p>
      <h3>配套 FAQ</h3>{draft.faqs.map((f, i) => <div key={i}><strong>{f.question}</strong><p>{f.answer}</p></div>)}
      {preview?.issues.length ? <details><summary>待核对的问题</summary><ul>{preview.issues.map((x, i) => <li key={i}>{x.reason}</li>)}</ul></details> : null}
      <button type="button" className="text-button" onClick={() => { void navigator.clipboard.writeText([draft.title, ...draft.bullets, draft.description, ...draft.faqs.map(f => `${f.question}\n${f.answer}`)].join("\n\n")).then(() => setNotice("英文草稿已复制，仍需核对后才能正式交付。"), () => setError("复制失败，请手动选择文案。")); }}>复制英文草稿</button>
    </div>}
    {notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
  </section>;
}
