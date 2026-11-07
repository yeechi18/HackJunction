"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, useSyncExternalStore, type FormEvent } from "react";
import { ErrorNotice } from "@/components/async-feedback";
import { ApiError, apiFetch, errorMessage } from "@/lib/api";
import { safeReturnTo, type AccountSession } from "./session";

const subscribe = () => () => {};
const clientReady = () => true;
const serverReady = () => false;

function validPassword(value: string) {
  return /^[\x21-\x7e]{8,12}$/.test(value)
    && [/[A-Za-z]/, /[0-9]/, /[^A-Za-z0-9]/].filter(group => group.test(value)).length >= 2;
}

export function AccountForm({ register = false, next }: { register?: boolean; next?: string }) {
  const router = useRouter();
  const hydrated = useSyncExternalStore(subscribe, clientReady, serverReady);
  const destination = safeReturnTo(next);
  const emailInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [code, setCode] = useState("");
  useEffect(() => {
    let active = true;
    void apiFetch<AccountSession>("/seller/auth/session").then(() => { if (active) router.replace(destination); }).catch(() => {});
    return () => { active = false; };
  }, [destination, router]);
  useEffect(() => {
    if (!cooldown) return;
    const timer = setTimeout(() => setCooldown(value => Math.max(0, value - 1)), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  async function sendCode() {
    if (sending || busy || cooldown || !emailInput.current?.reportValidity()) return;
    setSending(true); setError(""); setNotice("");
    try {
      const delivery = await apiFetch<{ retry_after_seconds: number }>("/seller/auth/verification-code", {
        method: "POST", timeoutMs: 30000,
        body: JSON.stringify({ email: emailInput.current.value.trim() }),
      });
      setCooldown(delivery.retry_after_seconds);
      setNotice("验证码已发送，10分钟内有效，请检查收件箱和垃圾邮件。");
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status === 409 ? "这个邮箱已注册，请前往登录。"
        : caught instanceof ApiError && caught.status === 503 ? "验证码暂时无法发送，请稍后重试或联系负责人。" : errorMessage(caught));
    } finally { setSending(false); }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || sending || !hydrated) return;
    const fields = new FormData(event.currentTarget);
    const password = String(fields.get("password") ?? "");
    if (register && !validPassword(password)) { setError("密码需要8–12位，字母、数字、特殊符号至少包含两类，不含空格。"); return; }
    if (register && password !== String(fields.get("password_confirm") ?? "")) { setError("两次输入的密码不一致。"); return; }
    setBusy(true); setError("");
    try {
      await apiFetch<AccountSession>(register ? "/seller/auth/register" : "/seller/auth/login", {
        method: "POST", timeoutMs: 15000,
        body: JSON.stringify({ email: String(fields.get("email") ?? "").trim(), password,
          ...(register ? { display_name: String(fields.get("display_name") ?? "").trim(), verification_code: code } : {}) }),
      });
      router.replace(destination);
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status === 400 ? "验证码不正确或已过期，请检查邮箱或重新获取。"
        : caught instanceof ApiError && caught.status === 401 ? "邮箱或密码不正确。"
        : caught instanceof ApiError && caught.status === 409 ? "这个邮箱已注册，请登录或使用其他邮箱。"
        : caught instanceof ApiError && caught.status === 429 && register ? "尝试次数过多，请稍后重新获取验证码。" : errorMessage(caught));
    } finally { setBusy(false); }
  }
  return <form className="surface login-form" method="post" onSubmit={submit}>
    <p className="eyebrow">YOUR SELLER ACCOUNT</p><h1>{register ? "创建卖家账号" : "欢迎回来"}<span>.</span></h1>
    <p>用同一个账号查看商品资料、订单进度和已交付内容。</p>
    {!hydrated && <p className="form-notice notice-warning" role="status">正在加载登录表单交互，请启用 JavaScript 后稍候。</p>}
    <fieldset className="intake-fields" disabled={!hydrated || busy}>
      {register && <div className="field"><label htmlFor="account-name">你的称呼</label><input id="account-name" name="display_name" required maxLength={200} autoComplete="name" /></div>}
      <div className="field"><label htmlFor="account-email">邮箱</label><input ref={emailInput} id="account-email" type="email" name="email" required maxLength={254} autoComplete="username" disabled={sending} onChange={() => { setCode(""); setNotice(""); }} />{register && <p className="field-help">使用你能收信的邮箱，注册时需要验证。</p>}</div>
      {register && <div className="field"><label htmlFor="account-code">邮箱验证码</label><div className="verification-row"><input id="account-code" name="verification_code" required inputMode="numeric" pattern="[0-9]{6}" minLength={6} maxLength={6} autoComplete="one-time-code" value={code} onChange={event => setCode(event.target.value.replace(/[^0-9]/g, "").slice(0, 6))} /><button className="button button-outline" type="button" disabled={!hydrated || sending || cooldown > 0} onClick={sendCode}>{sending ? "正在发送…" : cooldown ? `${cooldown}秒后重发` : "获取验证码"}</button></div><p className="field-help">输入邮箱收到的6位数字。</p></div>}
      <div className="field"><label htmlFor="account-password">密码</label><input id="account-password" type="password" name="password" required minLength={register ? 8 : 1} maxLength={register ? 12 : 128} autoComplete={register ? "new-password" : "current-password"} />{register && <p className="field-help">8–12位，字母、数字、特殊符号（如 !@#$%）至少包含两类，不含空格。</p>}</div>
      {register && <div className="field"><label htmlFor="account-confirm">确认密码</label><input id="account-confirm" type="password" name="password_confirm" required minLength={8} maxLength={12} autoComplete="new-password" /></div>}
      {notice && <p className="form-notice notice-success" role="status">{notice}</p>}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <button className="button button-dark" type="submit" disabled={!hydrated || busy || sending}>{busy ? "正在处理…" : register ? "验证邮箱并注册" : "登录卖家账号"}<span aria-hidden="true">↗</span></button>
    </fieldset>
    <p className="subtle-note">{register ? "已有账号？" : "还没有账号？"}<Link href={`${register ? "/seller/login" : "/seller/register"}?next=${encodeURIComponent(destination)}`}>{register ? "前往登录" : "注册卖家账号"} ↗</Link></p>
    <p className="field-help">负责人请使用<Link href="/internal/login">内部登录入口</Link>。</p>
  </form>;
}
