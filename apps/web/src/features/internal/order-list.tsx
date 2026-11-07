"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AccessError, ErrorNotice, LoadingPanel } from "@/components/async-feedback";
import { ApiError, apiFetch, errorMessage } from "@/lib/api";
import { formatTime, type Order, type OwnerSession } from "@/features/orders/types";
import { LogoutButton } from "./auth";
import { orderStatusLabel, pollIntervalFor } from "@/features/orders/workflow-progress";

export function InternalOrderList() {
  const [session, setSession] = useState<OwnerSession | null>(null);
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [error, setError] = useState("");
  const [denied, setDenied] = useState(false);
  const [revision, setRevision] = useState(0);
  const [search, setSearch] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let nextDelay = 5000;
    let blocked = false;
    async function load() {

      try {
        const owner = await apiFetch<OwnerSession>("/internal/auth/session", { signal: controller.signal });
        const response = await apiFetch<{ items: Order[] }>("/internal/orders", { signal: controller.signal });
        nextDelay = response.items.some(order => pollIntervalFor(order) === 2000) ? 2000 : 5000;
        if (active) { setSession(owner); setOrders(response.items); setError(""); setDenied(false); }
      } catch (caught) {
        if (active) { setError(errorMessage(caught)); const noAccess = caught instanceof ApiError && [401, 403].includes(caught.status ?? 0); setDenied(noAccess); blocked = noAccess; if (noAccess) { setOrders(null); setSession(null); } }
      } finally { if (active && !blocked) timer = setTimeout(() => void load(), nextDelay); }
    }
    void load();
    return () => { active = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [revision]);
  if (denied) return <AccessError internal />;
  if (!orders || !session) return error ? <div><ErrorNotice>{error}</ErrorNotice><button className="button button-outline" onClick={() => setRevision(current => current + 1)}>重新连接</button></div> : <LoadingPanel message="正在读取工作台…" />;
  const filtered = orders.filter(order => `${order.product_name} ${order.seller_name} ${order.id}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));

  return <><div className="internal-heading"><div className="portal-page-heading"><p className="eyebrow">THE WORKSHOP DESK.</p><h1>订单工作台<span>.</span></h1><p>查看资料与沟通记录，让每一次接单都有依据。</p></div><LogoutButton csrfToken={session.csrf_token} /></div><div className="order-list-tools"><div className="field"><label htmlFor="order-search" className="sr-only">搜索订单</label><input id="order-search" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索商品、卖家或订单编号" type="search" /></div><button type="button" className="button button-outline" onClick={() => setRevision(current => current + 1)}>刷新 ↻</button></div>{error && <ErrorNotice>{error} 当前显示最近一次成功读取的资料。</ErrorNotice>}<section className="surface order-list"><div className="order-list-caption"><strong>{filtered.length} 份订单</strong><span>处理时每 2 秒更新，等待时每 5 秒更新</span></div><div className="order-list-labels" aria-hidden="true"><span>商品与卖家</span><span>订单状态</span><span>最近更新</span><span /></div>{filtered.length ? filtered.map(order => <Link href={`/internal/orders/${order.id}`} className="order-list-row" key={order.id}><div className="order-list-product"><div><strong>{order.product_name}</strong></div><p>{order.seller_name} · {order.id.slice(0, 8)}</p></div><span className="list-phase">{orderStatusLabel(order)}{order.pending_command_id && <small>命令等待处理</small>}</span><time dateTime={order.updated_at}>{formatTime(order.updated_at)}</time><span className="row-arrow" aria-hidden="true">↗</span></Link>) : <div className="panel-empty">{orders.length ? "没有符合筛选条件的订单。" : "还没有订单。卖家提交的资料将出现在这里。"}</div>}</section><p className="subtle-note">订单状态来自持久工作流。获客审查、卖家补充与接单决定均保留记录；研究阶段接单后等待开展。</p></>;
}
