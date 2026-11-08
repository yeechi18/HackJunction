"use client";

import { useState } from "react";
import { apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import { factLabels, type Order } from "./types";

export function DeliveryFactsPanel({ order, csrfToken, refresh }: { order: Order; csrfToken: string; refresh: () => void }) {
  const required = order.required_fact_keys ?? [];
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(required.map(key => [key, order.facts.find(f => f.key === key)?.value ?? ""])));
  const [unit, setUnit] = useState(order.facts.find(f => f.key === "net_volume")?.unit ?? "mL");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!required.length) return null;
  const editable = order.allowed_actions.includes("supplement");
  async function save() {
    setBusy(true); setError("");
    try {
      const facts = required.filter(key => values[key]?.trim()).map(key => ({ key, value: values[key].trim(), unit: key === "net_volume" ? unit : key === "pack_quantity" ? "bottles" : null, confirmed: true }));
      await apiFetch(`/orders/${order.id}/required-facts`, { method: "POST", headers: csrfHeaders(csrfToken), body: JSON.stringify({ expected_revision: order.revision, facts }) });
      refresh();
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return <section id="delivery-facts" style={{ scrollMarginTop: 24 }} className="surface workspace-panel" aria-label="交付必要信息">
    <div className="panel-heading"><h2>正式交付前确认</h2><span>草稿可先查看</span></div>
    <p className="field-help">只确认本次商品的必要规格。未知可留空，已经提供的品牌基础信息不重复询问。保存后形成新资料版本，重新审查并由负责人批准。</p>
    {(order.missing_delivery_fields ?? []).length ? <p className="form-notice">待确认：{(order.missing_delivery_fields ?? []).map(k => factLabels[k] ?? k).join("、")}</p> : <p>本版必要信息已确认。</p>}
    {required.map(key => <div className="field" key={key}><label htmlFor={`delivery-${key}`}>{factLabels[key] ?? key}</label><input id={`delivery-${key}`} disabled={busy || !editable} value={values[key] ?? ""} onChange={e => setValues(v => ({ ...v, [key]: e.target.value }))} placeholder={key === "net_volume" ? "未知先留空" : key === "pack_quantity" ? "例如：1" : "例如：饮用"} />{key === "net_volume" && <select aria-label="净含量单位" value={unit} disabled={busy || !editable} onChange={e => setUnit(e.target.value)}><option value="mL">mL</option><option value="L">L</option></select>}</div>)}
    <button className="button button-outline button-small" type="button" disabled={busy || !editable || !required.some(k => values[k]?.trim())} onClick={() => void save()}>{busy ? "正在保存…" : "确认已填信息并保存"}</button>
    {!editable && <p className="field-help">当前任务处理中或已交付；请等待可修改资料时再确认。</p>}{error && <p role="alert">{error}</p>}
  </section>;
}
