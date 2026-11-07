"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import { formatTime, phaseLabel } from "@/features/orders/types";
import type { components } from "@/lib/generated/api";
import { SignInPrompt, useAccountSession } from "./session";

type Orders = components["schemas"]["AccountOrderList"];

export function MyOrders() {
  const router = useRouter();
  const { session, loading, error: sessionError, reload } = useAccountSession();
  const [orders, setOrders] = useState<Orders | null>(null);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!session) return;
    let active = true;
    const controller = new AbortController();
    void apiFetch<Orders>(`/seller/orders?offset=${offset}&limit=20`, { signal: controller.signal })
      .then(result => { if (active) { setOrders(result); setError(""); } })
      .catch(caught => { if (active) { setOrders(null); setError(errorMessage(caught)); if (caught instanceof ApiError && caught.status === 401) reload(); } });
    return () => { active = false; controller.abort(); };
  }, [session, offset, revision, reload]);
  async function logout() {
    if (!session || busy) return;
    setBusy(true); setError("");
    try {
      await apiFetch("/seller/auth/logout", { method: "POST", headers: csrfHeaders(session.csrf_token) });
      setOrders(null); router.replace("/seller/login");
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }
  if (loading) return <p role="status">正在确认登录状态…</p>;
  if (sessionError) return <section className="surface empty-panel"><p role="alert">{sessionError}</p><button className="button button-outline" onClick={reload}>重新连接</button></section>;
  if (!session) return <SignInPrompt />;
  return <>
    <div className="order-heading"><div><p className="eyebrow">YOUR ORDERS</p><h1>我的订单<span>.</span></h1><p>{session.display_name} · {session.email}</p></div><button className="text-button" disabled={busy} onClick={logout}>退出卖家账号 ↗</button></div>
    <div className="account-actions"><Link className="button button-dark" href="/orders/new">提交新的商品资料 ↗</Link><button className="button button-outline" onClick={() => setRevision(value => value + 1)}>刷新订单</button></div>
    {error && <p className="form-notice notice-error" role="alert">{error}</p>}
    {!orders && !error && <p role="status">正在读取你的订单…</p>}
    {orders && <section className="surface account-order-list"><p>{orders.total} 份订单</p>
      {orders.items.length ? orders.items.map(order => <Link className="account-order" href={`/o/${order.id}`} prefetch={false} key={order.id}><div><strong>{order.product_name}</strong><p>创建于 {formatTime(order.created_at)} · 订单 {order.id.slice(0, 8)}{order.is_demo ? " · 虚构演示" : ""}</p></div><span>{phaseLabel(order.phase)} ↗</span></Link>) : <div className="panel-empty">你还没有提交订单，先提交一件商品资料吧。</div>}
      {orders.total > 20 && <div className="account-actions"><button className="button button-outline" disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - 20))}>上一页</button><span>第 {Math.floor(offset / 20) + 1} 页</span><button className="button button-outline" disabled={offset + 20 >= orders.total} onClick={() => setOffset(value => value + 20)}>下一页</button></div>}
    </section>}
  </>;
}
