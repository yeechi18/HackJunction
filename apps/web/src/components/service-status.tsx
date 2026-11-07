"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";

type Check = { ok: boolean; message: string };
type HealthState = { live: Check | null; ready: Check | null; checkedAt: string | null; checking: boolean };

async function checkHealth(path: string, signal: AbortSignal): Promise<Check> {
  try {
    const payload = await apiFetch<{ status?: unknown }>(path, { signal });
    const ok = typeof payload.status === "string" && ["ok", "ready", "alive", "healthy"].includes(payload.status);
    return { ok, message: ok ? "连接正常" : "服务尚未就绪" };
  } catch (error) {
    return { ok: false, message: error instanceof ApiError ? error.message : "暂时无法检查服务" };
  }
}

export function ServiceStatus({ detailed = false }: { detailed?: boolean }) {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<HealthState>({ live: null, ready: null, checkedAt: null, checking: true });

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    async function check() {
      const [live, ready] = await Promise.all([
        checkHealth("/health/live", controller.signal),
        checkHealth("/health/ready", controller.signal),
      ]);
      if (active) setState({ live, ready, checkedAt: new Date().toLocaleTimeString("zh-CN", { hour12: false }), checking: false });
    }
    void check();
    const timer = setInterval(() => void check(), 15000);
    return () => { active = false; controller.abort(); clearInterval(timer); };
  }, [revision]);

  const mode = state.checking ? "checking" : !state.live?.ok ? "offline" : state.ready?.ok ? "ready" : "limited";
  const label = { checking: "正在检查服务", offline: "基础服务暂未连接", ready: "基础服务已连接", limited: "基础服务准备中" }[mode];

  if (!detailed) {
    return <Link href="/system" className={`service-status status-${mode}`} aria-label={`${label}，查看服务状态`}><span className="status-dot" aria-hidden="true" />{label}<span aria-hidden="true">↗</span></Link>;
  }

  return <div className="health-panel">
    <div className="health-panel-heading"><div className={`service-status status-${mode}`} role="status"><span className="status-dot" aria-hidden="true" />{label}</div><button className="button button-small button-outline" disabled={state.checking} onClick={() => { setState(current => ({ ...current, checking: true })); setRevision(current => current + 1); }}>{state.checking ? "检查中…" : "重新检查"}</button></div>
    <div className="health-row"><div><strong>API 进程</strong><p>检查应用服务是否可访问</p></div><span className={state.live?.ok ? "check-ok" : "check-pending"}>{state.checking ? "检查中" : state.live?.ok ? "正常" : "未连接"}</span></div>
    <div className="health-row"><div><strong>数据库与初始化</strong><p>检查后端是否具备处理请求的基础条件</p></div><span className={state.ready?.ok ? "check-ok" : "check-pending"}>{state.checking ? "检查中" : state.ready?.ok ? "就绪" : "未就绪"}</span></div>
    {!state.checking && (!state.live?.ok || !state.ready?.ok) && <p className="health-notice" role="status">{(!state.live?.ok ? state.live?.message : state.ready?.message)?.replace(/。$/, "")}。请确认本地后端已启动、数据库连接与初始化已完成。</p>}
    <p className="health-footnote">{state.checkedAt ? `最近检查 ${state.checkedAt} · 每 15 秒自动更新` : "正在读取实际服务状态"}</p>
  </div>;
}
