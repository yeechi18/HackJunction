"use client";

import Link from "next/link";
import { overseasOrderPath } from "@/lib/service-packages";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { ApiError, apiFetch, errorMessage } from "@/lib/api";
import type { components } from "@/lib/generated/api";

export type AccountSession = components["schemas"]["AccountSessionView"];
const AccountContext = createContext<AccountSession | null>(null);

export function safeReturnTo(value: string | null | undefined): string {
  return value && (value === "/orders/new" || value === overseasOrderPath || value === "/seller/orders" || /^\/o\/[a-f0-9-]{36}$/i.test(value)) ? value : "/seller/orders";
}

export function useAccountSession() {
  const [session, setSession] = useState<AccountSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    void apiFetch<AccountSession>("/seller/auth/session", { signal: controller.signal })
      .then(result => { if (active) { setSession(result); setError(""); } })
      .catch(caught => { if (active) { setSession(null); setError(caught instanceof ApiError && caught.status === 401 ? "" : errorMessage(caught)); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [revision]);
  const reload = useCallback(() => { setLoading(true); setRevision(value => value + 1); }, []);
  return { session, loading, error, reload };
}

export function SignInPrompt({ next = "/seller/orders" }: { next?: string }) {
  const query = `?next=${encodeURIComponent(safeReturnTo(next))}`;
  return <section className="surface empty-panel"><p className="eyebrow">YOUR SELLER ACCOUNT</p><h1>请先登录卖家账号</h1><p>登录后提交商品，所有订单都会保存在“我的订单”中，换浏览器或重启电脑后也能找回。</p><div className="account-actions"><Link className="button button-dark" href={`/seller/login${query}`}>卖家登录 ↗</Link><Link className="button button-outline" href={`/seller/register${query}`}>注册卖家账号</Link></div></section>;
}

export function SellerGate({ children, next = "/orders/new" }: { children: React.ReactNode; next?: string }) {
  const { session, loading, error, reload } = useAccountSession();
  if (loading) return <div className="form-notice notice-warning" role="status"><p>正在确认卖家登录状态…请确保已启用 JavaScript，再稍候。</p></div>;
  if (error) return <section className="surface empty-panel"><p role="alert">{error}</p><button className="button button-outline" onClick={reload}>重新连接</button></section>;
  if (!session) return <SignInPrompt next={next} />;
  return <AccountContext.Provider value={session}>{children}</AccountContext.Provider>;
}

export function useSellerAccount(): AccountSession {
  const session = useContext(AccountContext);
  if (!session) throw new Error("A seller account gate is required.");
  return session;
}
