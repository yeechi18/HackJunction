"use client";

import Link from "next/link";
import { CopyPreviewPanel } from "@/features/orders/copy-preview-panel";
import { IntegrationsPanel } from "./integrations-panel";
import { OwnerActions } from "./owner-actions";
import { ModelBudgetPanel } from "./model-budget-panel";
import { AccessError, ErrorNotice, LoadingPanel } from "@/components/async-feedback";
import { EventsPanel, FactsPanel, MessagesPanel, OrderHeading, WorkflowNotice } from "@/features/orders/order-panels";
import { factLabels, formatTime, type InternalOrder } from "@/features/orders/types";
import { useWorkspace } from "@/features/orders/use-workspace";
import { isIntakeOrder } from "./intake-approval";
import { operationsHref } from "./operations-views";
import { LogoutButton } from "./auth";
import { AgentRunsPanel } from "./agent-runs-panel";
import { ProductionPanel } from "./production-panel";
import { ReferenceAssetsPanel } from "@/features/orders/reference-assets-panel";
import { OwnerSupportPanel } from "@/features/operations/support-panel";
import { OwnerUsagePanel } from "@/features/operations/usage-panel";

function HistoryPanel({ order }: { order: InternalOrder }) {
  return <section className="surface workspace-panel"><div className="panel-heading"><h2>资料版本</h2><span>{order.input_snapshots.length} 个保存快照</span></div><div className="snapshot-list">{order.input_snapshots.map(snapshot => <details key={snapshot.id}><summary><strong>资料版本 {snapshot.revision}</strong><span>{formatTime(snapshot.created_at)} <b aria-hidden="true">＋</b></span></summary><div className="snapshot-content"><p className="snapshot-source">来源：{({ seller_form: "卖家提交", seller_submission: "卖家提交", seller_supplement: "卖家补充", initial: "首次提交", supplement: "补充资料" } as Record<string, string>)[snapshot.source] ?? snapshot.source}</p>{snapshot.facts.length ? <dl>{snapshot.facts.map((fact, index) => <div key={`${fact.key}-${index}`}><dt>{factLabels[fact.key] ?? fact.key}</dt><dd>{fact.value} {fact.unit ?? ""}<small>{fact.confirmed ? "已核实" : "待确认"}</small></dd></div>)}</dl> : <p className="field-help">本版尚未填写商品事实。</p>}{snapshot.notes && <p className="snapshot-notes">{snapshot.notes}</p>}</div></details>)}</div></section>;
}

function CommandsPanel({ order }: { order: InternalOrder }) {
  return <section className="surface workspace-panel"><div className="panel-heading"><h2>操作命令</h2><span>{order.commands.length} 条</span></div>{order.commands.length ? <ol className="command-list">{order.commands.map(command => <li key={command.id}><div><strong>{({ COPY_PREVIEW: "英文初稿", START: "首次资料审查", MESSAGE: "消息后重审", SUPPLEMENT: "补充后重审", ACCEPT: "接单批准", REJECT: "拒绝接单", accept: "接单批准", reject: "拒绝接单", intake: "资料审查", supplement: "补充后重审" } as Record<string, string>)[command.kind] ?? command.kind}</strong><span>{({ queued: "等待处理", pending: "等待处理", running: "处理中", completed: "已处理", applied: "已生效", stale: "已过期", cancelled: "已取消", failed: "处理失败" } as Record<string, string>)[command.status] ?? command.status}</span></div><p>基于版本 {command.expected_revision} · {formatTime(command.created_at)}</p></li>)}</ol> : <p className="panel-empty">暂无操作命令。</p>}</section>;
}

export function InternalOrderWorkspace({ orderId }: { orderId: string }) {
  const { data, error, accessDenied, pollIntervalMs, refresh } = useWorkspace<InternalOrder>(orderId, true);
  if (accessDenied) return <AccessError internal message="请确认已登录负责人账号，且订单仍然存在。" />;
  if (!data) return error ? <div><ErrorNotice>{error}</ErrorNotice><button className="button button-outline" type="button" onClick={refresh}>重新连接</button></div> : <LoadingPanel message="正在读取订单与内部记录…" />;
  const { order, messages, events, csrfToken, checkedAt } = data;

  return <><div className="workspace-toplinks"><Link href="/internal/orders" className="text-link">← 返回订单列表</Link><LogoutButton csrfToken={csrfToken} /></div><OrderHeading order={order} internal /><div className="workspace-toolbar"><p>最近更新 {checkedAt} · 每 {pollIntervalMs / 1000} 秒刷新</p><button className="text-button" type="button" onClick={refresh}>刷新 ↻</button></div>{error && <ErrorNotice>{error} 当前仍显示最近一次成功读取的资料。</ErrorNotice>}<WorkflowNotice order={order} /><OwnerActions order={order} /><div className="workspace-grid"><div className="workspace-primary"><section className="surface workspace-panel"><div className="panel-heading"><h2>卖家信息</h2><span>仅内部可见</span></div><dl className="seller-details"><div><dt>称呼</dt><dd>{order.seller_name}</dd></div><div><dt>联系方式</dt><dd>{order.contact || "未填写"}</dd></div><div><dt>提交时间</dt><dd>{formatTime(order.created_at)}</dd></div></dl></section><CopyPreviewPanel order={order} csrfToken={csrfToken} refresh={refresh} /><FactsPanel order={order} /><IntegrationsPanel order={order} csrfToken={csrfToken} refresh={refresh} /><ReferenceAssetsPanel orderId={order.id} csrfToken={csrfToken} internal /><div id="production"><ProductionPanel order={order} csrfToken={csrfToken} refresh={refresh} /></div><MessagesPanel messages={messages} /><div id="support"><OwnerSupportPanel orderId={order.id} csrfToken={csrfToken} refresh={refresh} /></div><AgentRunsPanel runs={order.agent_runs ?? []} /><HistoryPanel order={order} /></div><aside className="workspace-secondary">{isIntakeOrder(order) && <Link href={operationsHref("approvals")} className="text-link">前往审批中心处理接单 ↗</Link>}<EventsPanel events={events} /><CommandsPanel order={order} /><ModelBudgetPanel order={order} csrfToken={csrfToken} refresh={refresh} /><OwnerUsagePanel orderId={order.id} csrfToken={csrfToken} refresh={refresh} /><div className="workspace-side-note"><strong>每次决定都有记录</strong><p>决定仅对当前资料版本生效。命令入队后，订单会等待真实工作流处理。</p></div></aside></div></>;
}
