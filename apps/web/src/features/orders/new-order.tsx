"use client";

import Link from "next/link";
import { useRef, useState, type FormEvent } from "react";
import { ErrorNotice } from "@/components/async-feedback";
import { apiFetch, errorMessage } from "@/lib/api";
import { emptyFacts, FactEditor, normalizedFacts } from "./fact-editor";
import type { CreatedOrder } from "./types";

export function NewOrderForm() {
  const [facts, setFacts] = useState(emptyFacts);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<{ id: string; url: string; path: string; productName: string } | null>(null);
  const [copyState, setCopyState] = useState("");
  const linkInput = useRef<HTMLInputElement>(null);
  const submission = useRef<{ body: string; key: string } | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const fields = new FormData(event.currentTarget);
    const body = JSON.stringify({
      seller_name: String(fields.get("seller_name") ?? "").trim(),
      contact: String(fields.get("contact") ?? "").trim() || null,
      product_name: String(fields.get("product_name") ?? "").trim(),
      facts: normalizedFacts(facts),
      notes: String(fields.get("notes") ?? "").trim() || null,
      is_demo: fields.get("is_demo") === "on",
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
      setCreated({ id: result.id, url: new URL(privateUrl.pathname + privateUrl.hash, window.location.origin).href, path: privateUrl.pathname + privateUrl.hash, productName: String(fields.get("product_name")) });
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }

  async function copyLink() {
    if (!created) return;
    try { await navigator.clipboard.writeText(created.url); setCopyState("链接已复制，请妥善保存。"); }
    catch { linkInput.current?.select(); setCopyState("请选中链接后手动复制。"); }
  }

  if (created) return <section className="creation-success"><div className="success-symbol" aria-hidden="true">✓</div><p className="eyebrow">YOUR ORDER, YOUR SPACE.</p><h1>资料已保存<span>.</span></h1><p className="portal-intro">「{created.productName}」已有专属订单入口。你可以继续补充资料，并在同一个入口查看进度。</p><div className="private-link-box"><label htmlFor="private-order-link">私有订单链接</label><div><input ref={linkInput} id="private-order-link" readOnly value={created.url} onFocus={event => event.currentTarget.select()} /><button type="button" className="button button-outline" onClick={copyLink}>复制链接</button></div><p>持有完整链接的人可访问此订单，请保存并仅与信任的人分享。</p>{copyState && <p role="status">{copyState}</p>}</div><Link href={created.path} prefetch={false} className="button button-dark">查看订单进度 <span aria-hidden="true">↗</span></Link><p className="subtle-note">接下来由获客 Agent 审查资料，接单由负责人决定。发布还需你确认，当前仅为模拟模式。</p></section>;

  return <><div className="portal-page-heading"><p className="eyebrow">LET’S MEET YOUR PRODUCT.</p><h1>从一件好商品开始<span>.</span></h1><p>告诉我们你正在做的产品，先保存资料，再一起把细节补齐。</p></div><div className="intake-grid"><form className="surface intake-form" onSubmit={submit}><div className="form-section"><div className="form-section-title"><span>01</span><h2>先认识你和你的商品</h2></div><div className="form-two-columns"><div className="field"><label htmlFor="seller_name">你的称呼 <em>必填</em></label><input id="seller_name" name="seller_name" required maxLength={200} autoComplete="name" placeholder="我们应该怎么称呼你" disabled={busy} /></div><div className="field"><label htmlFor="contact">联系方式 <span>选填</span></label><input id="contact" name="contact" maxLength={500} autoComplete="email" placeholder="邮箱或其他联系方式" disabled={busy} /></div></div><div className="field"><label htmlFor="product_name">中文商品名 <em>必填</em></label><input id="product_name" name="product_name" required maxLength={300} placeholder="例如：桌面理线夹" disabled={busy} /></div></div><div className="form-section"><div className="form-section-title"><span>02</span><h2>把你确定的事实告诉我们</h2></div><p className="field-help">资料不全也可以先提交。填写后请逐项勾选「我已核实」；未核实的信息会保留为待确认项。</p><FactEditor facts={facts} onChange={setFacts} disabled={busy} /></div><div className="form-section"><div className="form-section-title"><span>03</span><h2>还有什么想补充的</h2></div><div className="field"><label htmlFor="notes">商品说明与期望卖点 <span>选填</span></label><textarea id="notes" name="notes" rows={4} maxLength={10000} placeholder="已有文案、希望突出的特点，或需要我们特别注意的地方。" disabled={busy} /></div><label className="checkbox-field demo-checkbox"><input type="checkbox" name="is_demo" disabled={busy} /><span>这是一份虚构演示订单</span></label><p className="field-help">真实商品资料与演示数据分别记录。</p></div>{error && <ErrorNotice>{error}</ErrorNotice>}<div className="form-submit-row"><p>提交后获得私有链接，无需注册。</p><button className="button button-dark" disabled={busy} type="submit">{busy ? "正在保存资料…" : "保存资料，创建订单"}<span aria-hidden="true">↗</span></button></div></form><aside className="intake-aside"><p className="eyebrow">START WITH ONE GOOD PRODUCT.</p><h2>一件商品，<br />一个清楚的开始。</h2><p>首版专注非电子类桌面收纳用品，为 Amazon 美国站准备美式英语内容。</p><ol><li><span>01</span><div><strong>先保存，再补充</strong><p>还没准备好的资料，可以回到订单入口继续补全。</p></div></li><li><span>02</span><div><strong>事实与想法分开</strong><p>已核实的商品属性是文案依据，期望卖点也需要事实支持。</p></div></li><li><span>03</span><div><strong>进度保留在同一处</strong><p>订单入口承载资料与沟通记录，刷新后也能继续查看。</p></div></li></ol><div className="aside-note">资料保存后，获客 Agent 会审查并请求补充；经接单、研究、生成和审校后交付。当前发布仅为模拟模式。</div></aside></div></>;
}
