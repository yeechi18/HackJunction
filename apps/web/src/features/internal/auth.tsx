"use client";

import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore, type FormEvent } from "react";
import { ErrorNotice } from "@/components/async-feedback";
import { ApiError, apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import type { OwnerSession } from "@/features/orders/types";

const subscribeToHydration = () => () => {};
const clientReady = () => true;
const serverReady = () => false;

export function InternalLoginForm() {
  const router = useRouter();
  const hydrated = useSyncExternalStore(subscribeToHydration, clientReady, serverReady);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const fields = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try {
      await apiFetch<OwnerSession>("/internal/auth/login", { method: "POST", body: JSON.stringify({ username: "owner", password: String(fields.get("password") ?? "") }) });
      router.replace("/internal/orders");
    } catch (caught) { setError(caught instanceof ApiError && caught.status === 401 ? "登录未通过，请检查负责人的登录密码。" : errorMessage(caught)); }
    finally { setBusy(false); }
  }

  return <form className="surface login-form" method="post" onSubmit={submit}><div className="login-symbol" aria-hidden="true">↗</div><p className="eyebrow">OWNER WORKSPACE</p><h1>欢迎回到工坊<span>.</span></h1><p>查看订单、核对资料，把重要的决定留在人手中。</p><div className="field"><label htmlFor="owner-username">负责人账号</label><input id="owner-username" name="username" value="owner" autoComplete="username" readOnly /></div><div className="field"><label htmlFor="owner-password">登录密码</label><input id="owner-password" type="password" name="password" required autoComplete="current-password" maxLength={1000} disabled={!hydrated || busy} /></div>{error && <ErrorNotice>{error}</ErrorNotice>}<button className="button button-dark" type="submit" disabled={!hydrated || busy}>{busy ? "正在登录…" : "登录工作台"}<span aria-hidden="true">↗</span></button><p className="subtle-note">内部入口仅供公司负责人使用。</p></form>;
}

export function LogoutButton({ csrfToken }: { csrfToken: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function logout() {
    setBusy(true); setError("");
    try { await apiFetch("/internal/auth/logout", { method: "POST", headers: csrfHeaders(csrfToken) }); router.replace("/internal/login"); }
    catch (caught) { setError(errorMessage(caught)); setBusy(false); }
  }
  return <div className="logout-area"><button type="button" className="text-button" disabled={busy} onClick={logout}>{busy ? "退出中…" : "退出登录 ↗"}</button>{error && <p role="alert">{error}</p>}</div>;
}
