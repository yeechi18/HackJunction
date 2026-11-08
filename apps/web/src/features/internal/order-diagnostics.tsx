"use client";
import Link from "next/link";
import { isIntakeOrder } from "./intake-approval";
import { useRef, useState } from "react";
import { apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import { useWorkspace } from "@/features/orders/use-workspace";
import type { InternalOrder } from "@/features/orders/types";

export function OrderDiagnostics({ order, csrfToken, refresh }: { order: InternalOrder; csrfToken: string; refresh: () => void }) {
  const [action, setAction] = useState<"ignore" | "seller_advice" | null>(null);
  const [commandId, setCommandId] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const request = useRef<{ body: string; id: string } | null>(null);
  const failed = order.commands.filter(item => item.status === "failed" && item.expected_revision === order.revision);
  const selected = failed.find(item => item.id === commandId) ?? failed[failed.length - 1];
  async function submit() {
    if (!action || busy || !content.trim()) return;
    const body = JSON.stringify({ action, expected_revision: order.revision, command_id: action === "ignore" ? selected?.id : null, content: content.trim() });
    if (request.current?.body !== body) request.current = { body, id: crypto.randomUUID() };
    setBusy(true); setNotice("");
    try {
      await apiFetch(`/internal/orders/${order.id}/issue-actions`, { method: "POST", headers: csrfHeaders(csrfToken), body: JSON.stringify({ ...JSON.parse(body), request_id: request.current.id }) });
      setNotice(action === "ignore" ? "已记录忽略提示。失败记录保留，未恢复任务或批准交付。" : "建议已发送到卖家订单沟通记录。");
      setAction(null); setContent(""); request.current = null; refresh();
    } catch (caught) { setNotice(errorMessage(caught)); }
    finally { setBusy(false); }
  }
  return <section className="diagnostic-card" id="run-diagnostics">
    <div className="panel-heading"><h3>问题定位与处理</h3><span>{order.product_name}</span></div>
    <p className="diagnostic-cause">{order.workflow_status === "failed" ? order.workflow_error || "任务已暂停，请结合下方运行记录核对原因。" : "当前没有阻断订单的技术故障；历史失败仍保留在运行记录中。"}</p>
    <div className="diagnostic-actions">
      <Link className="button button-outline button-small" href={`/internal/orders/${order.id}#model-budget`}>授权额度 / 恢复任务</Link>
      <Link className="button button-outline button-small" href={`/internal/orders/${order.id}#${isIntakeOrder(order) ? "acceptance-review" : "production"}`}>{isIntakeOrder(order) ? "查看接单条件与审批" : "核对成品问题并放行"}</Link>
      {failed.length > 0 && <button className="text-button" disabled={busy} onClick={() => { setAction("ignore"); setNotice(""); }}>忽略提示并记录原因</button>}
      <button className="text-button" disabled={busy} onClick={() => { setAction("seller_advice"); setNotice(""); }}>给卖家建议</button>
    </div>
    {action && <div className="diagnostic-editor">
      <h4>{action === "ignore" ? "记录忽略原因" : "编辑给卖家的建议"}</h4>
      {action === "ignore" && <><p>仅记录你已知悉此提示，不会将任务改成成功，也不会自动放行。</p><select aria-label="选择失败任务" value={selected?.id ?? ""} disabled={busy} onChange={event => setCommandId(event.target.value)}>{failed.map(item => <option value={item.id} key={item.id}>{item.kind} · {item.id.slice(0, 8)}</option>)}</select></>}
      <textarea aria-label={action === "ignore" ? "忽略原因" : "给卖家的建议"} rows={3} maxLength={2000} value={content} disabled={busy} onChange={event => setContent(event.target.value)} placeholder={action === "ignore" ? "说明为何无需处理此提示…" : "请说明已核实的情况，以及卖家需要补充或确认的内容…"} />
      <button className="button button-dark button-small" disabled={busy || !content.trim() || (action === "ignore" && !selected)} onClick={submit}>{busy ? "正在保存…" : action === "ignore" ? "确认忽略并留档" : "发送建议给卖家"}</button>
      <button className="text-button" disabled={busy} onClick={() => setAction(null)}>取消</button>
    </div>}
    {notice && <p role="status">{notice}</p>}
  </section>;
}

export function DiagnosticsForOrder({ orderId }: { orderId: string }) {
  const { data, error, refresh } = useWorkspace<InternalOrder>(orderId, true);
  if (!data) return <p role="status">{error || "正在读取本单问题…"}</p>;
  return <OrderDiagnostics order={data.order} csrfToken={data.csrfToken} refresh={refresh} />;
}
