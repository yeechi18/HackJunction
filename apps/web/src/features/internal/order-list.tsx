"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AccessError, ErrorNotice, LoadingPanel } from "@/components/async-feedback";
import { ApiError, apiFetch, errorMessage } from "@/lib/api";
import { formatTime, phaseLabel, type Order, type OwnerSession } from "@/features/orders/types";
import { LogoutButton } from "./auth";
import { orderStatusLabel, pollIntervalFor } from "@/features/orders/workflow-progress";

import { OperationsDashboard, type OperationsRun, type SupportTicket } from "./operations-dashboard";
import { operationsViews, type OperationsView } from "./operations-views";

export function InternalOrderList({ view = "orders" }: { view?: OperationsView }) {
  const [runs, setRuns] = useState<OperationsRun[]>([]);
  const [tickets, setTickets] = useState<SupportTicket[]>([]);
  const [checkedAt, setCheckedAt] = useState(0);
  const [session, setSession] = useState<OwnerSession | null>(null);
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [error, setError] = useState("");
  const [denied, setDenied] = useState(false);
  const [revision, setRevision] = useState(0);
  const [search, setSearch] = useState("");
  const [phase, setPhase] = useState("");
  const [page, setPage] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let nextDelay = 5000;
    let blocked = false;
    async function load() {

      try {
        const owner = await apiFetch<OwnerSession>("/internal/auth/session", { signal: controller.signal });
        const options = { signal: controller.signal };
        const [response, runItems, support] = await Promise.all([
          ["orders", "overview", "approvals"].includes(view)
            ? apiFetch<{ items: Order[] }>("/internal/orders?limit=100", options) : { items: [] },
          ["overview", "agents", "runs"].includes(view)
            ? apiFetch<OperationsRun[]>(view === "agents" ? "/internal/orders/agent-runs?include_trace=true" : "/internal/orders/agent-runs", options) : [],
          ["overview", "support"].includes(view)
            ? apiFetch<{ items: SupportTicket[] }>("/internal/support-tickets?status=all", options) : { items: [] },
        ]);
        nextDelay = response.items.some(order => pollIntervalFor(order) === 2000) ? 2000 : 5000;
        if (active) {
          setSession(owner); setOrders(response.items); setRuns(runItems); setTickets(support.items);
          setCheckedAt(Date.now()); setError(""); setDenied(false);
        }
      } catch (caught) {
        if (active) { setError(errorMessage(caught)); const noAccess = caught instanceof ApiError && [401, 403].includes(caught.status ?? 0); setDenied(noAccess); blocked = noAccess; if (noAccess) { setOrders(null); setSession(null); } }
      } finally { if (active && !blocked) timer = setTimeout(() => void load(), nextDelay); }
    }
    void load();
    return () => { active = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [revision, view]);
  if (denied) return <AccessError internal />;
  if (!orders || !session) return error ? <div><ErrorNotice>{error}</ErrorNotice><button className="button button-outline" onClick={() => setRevision(current => current + 1)}>重新连接</button></div> : <LoadingPanel message="正在读取工作台…" />;
  const phases = [...new Set(orders.map(order => order.phase))];
  const query = search.trim().toLocaleLowerCase();
  const filtered = orders.filter(order =>
    `${order.product_name} ${order.seller_name} ${order.id}`.toLocaleLowerCase().includes(query)
    && (!phase || order.phase === phase)
  ).sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const pageCount = Math.max(1, Math.ceil(filtered.length / 10));
  const currentPage = Math.min(page, pageCount - 1);
  const visibleOrders = filtered.slice(currentPage * 10, currentPage * 10 + 10);

  const heading = <><div className="internal-heading"><div className="portal-page-heading">
    <p className="eyebrow">JUNCTION / OPERATIONS</p><h1>{operationsViews[view].title}</h1><p>{operationsViews[view].description}</p>
  </div><LogoutButton csrfToken={session.csrf_token} /></div>
    {error && <ErrorNotice>{error} 当前显示最近一次成功读取的资料。</ErrorNotice>}
  </>;
  if (view !== "orders") return <>{heading}<OperationsDashboard view={view} orders={orders} runs={runs} tickets={tickets} checkedAt={checkedAt} /></>;
  return <>{heading}<div className="order-list-tools"><div className="field"><label htmlFor="order-search" className="sr-only">搜索订单</label><input id="order-search" value={search} onChange={event => { setSearch(event.target.value); setPage(0); }} placeholder="搜索商品、卖家或订单编号" type="search" /></div><div className="field">
    <select aria-label="订单阶段" value={phase} onChange={event => { setPhase(event.target.value); setPage(0); }}>
      <option value="">全部阶段</option>
      {Array.from(new Set(phase ? [phase, ...phases] : phases)).map(item => <option key={item} value={item}>{phaseLabel(item)}</option>)}
    </select>
  </div><button type="button" className="button button-outline" onClick={() => setRevision(current => current + 1)}>刷新 ↻</button></div><section className="surface order-list"><div className="order-list-caption"><strong>{filtered.length} 份订单</strong><span>处理时每 2 秒更新，等待时每 5 秒更新</span></div><div className="order-list-labels" aria-hidden="true"><span>商品与卖家</span><span>订单状态</span><span>最近更新</span><span /></div>{visibleOrders.length ? visibleOrders.map(order => <Link href={`/internal/orders/${order.id}`} className="order-list-row" key={order.id}><div className="order-list-product"><div><strong>{order.product_name}</strong></div><p>{order.seller_name} · {order.id.slice(0, 8)}</p></div><span className="list-phase">{orderStatusLabel(order)}{order.pending_command_id && <small>命令等待处理</small>}</span><time dateTime={order.updated_at}>{formatTime(order.updated_at)}</time><span className="row-arrow" aria-hidden="true">↗</span></Link>) : <div className="panel-empty">{orders.length ? "没有符合筛选条件的订单。" : "还没有订单。卖家提交的资料将出现在这里。"}</div>}</section>{pageCount > 1 && <nav className="ops-pagination" aria-label="订单分页">
    <span>第 {currentPage + 1} / {pageCount} 页</span>
    <button type="button" className="button button-outline button-small" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button>
    <button type="button" className="button button-outline button-small" disabled={currentPage + 1 === pageCount} onClick={() => setPage(currentPage + 1)}>下一页</button>
  </nav>}<p className="subtle-note">订单状态来自持久工作流。获客审查、卖家补充与接单决定均保留记录；研究阶段接单后等待开展。</p></>;
}
