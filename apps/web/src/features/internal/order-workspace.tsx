"use client";

import Link from "next/link";
import { useEffect } from "react";
import { OrderDiagnostics } from "./order-diagnostics";
import { CopyPreviewPanel } from "@/features/orders/copy-preview-panel";
import { IntegrationsPanel } from "./integrations-panel";
import { OwnerActions } from "./owner-actions";
import { ModelBudgetPanel } from "./model-budget-panel";
import { AccessError, ErrorNotice, LoadingPanel } from "@/components/async-feedback";
import { EventsPanel, FactsPanel, MessagesPanel, OrderHeading } from "@/features/orders/order-panels";
import { factLabels, formatTime, type InternalOrder } from "@/features/orders/types";
import { useWorkspace } from "@/features/orders/use-workspace";
import { DecisionPanel, isIntakeOrder } from "./intake-approval";
import { operationsHref } from "./operations-views";
import { LogoutButton } from "./auth";
import { AgentActivity } from "./agent-activity";
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
  useEffect(() => {
    let observer: MutationObserver | undefined;
    function reveal() {
      const target = document.getElementById(window.location.hash.slice(1));
      if (!target) return false;
      observer?.disconnect();
      let parent: HTMLElement | null = target;
      while (parent) { if (parent instanceof HTMLDetailsElement) parent.open = true; parent = parent.parentElement; }
      target.scrollIntoView({ block: "start" });
      return true;
    }
    function followHash() {
      observer?.disconnect();
      if (!window.location.hash || reveal()) return;
      observer = new MutationObserver(() => { reveal(); });
      observer.observe(document.body, { childList: true, subtree: true });
    }
    followHash(); window.addEventListener("hashchange", followHash);
    return () => { observer?.disconnect(); window.removeEventListener("hashchange", followHash); };
  }, [data?.order.id]);
  if (accessDenied) return <AccessError internal message="请确认已登录负责人账号，且订单仍然存在。" />;
  if (!data) return error ? <div><ErrorNotice>{error}</ErrorNotice><button className="button button-outline" type="button" onClick={refresh}>重新连接</button></div> : <LoadingPanel message="正在读取订单与内部记录…" />;
  const { order, messages, events, csrfToken, checkedAt } = data;

  return <>
    <div className="workspace-toplinks"><Link href="/internal/orders" className="text-link">← 返回订单列表</Link><LogoutButton csrfToken={csrfToken} /></div>
    <OrderHeading order={order} internal />
    <div className="workspace-toolbar"><p>最近更新 {checkedAt} · 每 {pollIntervalMs / 1000} 秒刷新</p><button className="text-button" type="button" onClick={refresh}>刷新 ↻</button></div>
    {error && <ErrorNotice>{error} 当前仍显示最近一次成功读取的资料。</ErrorNotice>}
    <OwnerActions order={order} />
    {isIntakeOrder(order) && <div id="acceptance-review"><DecisionPanel order={order} csrfToken={csrfToken} refresh={refresh} /></div>}
    <div className="workspace-grid owner-review-layout">
      <div className="workspace-primary">
        <section className="surface workspace-panel owner-seller-summary">
          <div className="panel-heading"><h2>卖家信息</h2><span>仅内部可见</span></div>
          <dl className="seller-details"><div><dt>称呼</dt><dd>{order.seller_name}</dd></div><div><dt>联系方式</dt><dd>{order.contact || "未填写"}</dd></div><div><dt>提交时间</dt><dd>{formatTime(order.created_at)}</dd></div></dl>
        </section>
        <AgentActivity order={order} events={events} />
        <CopyPreviewPanel order={order} csrfToken={csrfToken} refresh={refresh} />
        <div id="production"><ProductionPanel order={order} csrfToken={csrfToken} refresh={refresh} /></div>
      </div>
      <aside className="workspace-secondary" aria-label="审核依据与辅助工具">
        <FactsPanel order={order} />
        <section className="surface workspace-panel owner-toolbox">
          <div className="panel-heading"><h2>辅助工具</h2><span>按需打开</span></div>
          <div className="owner-tool-buttons">
            <details className="owner-tool"><summary>运行日志与问题</summary><div className="owner-tool-body"><OrderDiagnostics order={order} csrfToken={csrfToken} refresh={refresh} /><Link className="text-link" href={`/internal/operations/runs?order_id=${order.id}`}>查看本单全部运行日志 ↗</Link></div></details>
            <details className="owner-tool"><summary>研究与制图</summary><div className="owner-tool-body"><IntegrationsPanel order={order} csrfToken={csrfToken} refresh={refresh} /></div></details>
            <details className="owner-tool"><summary>参考素材</summary><div className="owner-tool-body"><ReferenceAssetsPanel orderId={order.id} csrfToken={csrfToken} internal /></div></details>
            <details className="owner-tool"><summary>客户反馈</summary><div id="support" className="owner-tool-body"><OwnerSupportPanel orderId={order.id} csrfToken={csrfToken} refresh={refresh} /></div></details>
            <details className="owner-tool"><summary>沟通记录</summary><div className="owner-tool-body"><MessagesPanel messages={messages} /></div></details>
            <details className="owner-tool"><summary>历史版本</summary><div className="owner-tool-body"><HistoryPanel order={order} /></div></details>
            <details className="owner-tool"><summary>订单事件</summary><div className="owner-tool-body"><EventsPanel events={events} /></div></details>
            <details className="owner-tool"><summary>操作记录</summary><div className="owner-tool-body"><CommandsPanel order={order} /></div></details>
            <details className="owner-tool"><summary>用量与恢复</summary><div className="owner-tool-body"><ModelBudgetPanel order={order} csrfToken={csrfToken} refresh={refresh} /><OwnerUsagePanel orderId={order.id} csrfToken={csrfToken} refresh={refresh} /></div></details>
          </div>
        </section>
        {isIntakeOrder(order) && <Link href={operationsHref("approvals")} className="text-link">前往审批中心 ↗</Link>}
      </aside>
    </div>
  </>;
}
