"use client";

import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import { ApiError, apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import type { OperationsPanelProps } from "./support-panel";
import styles from "./operations-panel.module.css";

type Metrics = {
  is_demo: boolean;
  publish_mode: string;
  recorded_model_calls: number;
  calls_by_role: Record<string, number>;
  model_mode?: "none" | "live" | "offline" | "mixed" | "unknown";
  calls_by_mode?: Record<string, number>;
  tokens_by_mode?: Record<string, { input_tokens: number | null; output_tokens: number | null; total_tokens: number | null }>;
  tokens: { input_tokens: number | null; output_tokens: number | null; total_tokens: number | null };
  known_cost_usd: string | number | null;
  total_cost_usd: string | number | null;
  versions: number;
  revision_candidates: number;
  failed_reviews: number;
  feedback_requests: number;
  owner_active_seconds: number;
};
type Command = { id: string; kind: string; status: string; expected_revision: number; created_at: string };
type OrderState = { revision: number; phase: string; pending_command_id: string | null; commands: Command[] };
type ActiveTime = { owner_active_seconds: number };

const MAX_TIME_SECONDS = 86400;
const roleLabels: Record<string, string> = { acquisition: "获客整理", acquisition_feedback: "反馈整理", research: "商品调研", generation: "文案生成", review: "图文审校", publishing: "发布准备" };
const commandLabels: Record<string, string> = { START: "首次资料审查", MESSAGE: "资料重审", SUPPLEMENT: "补充资料审查", ACCEPT: "接单与生产", PRODUCE: "商品生产", IMAGES: "图片制作与审校", FEEDBACK: "反馈修改", VETO: "否决后返修", RELEASE: "成品交付", CONFIRM_PUBLISH: "确认后发布" };

function duration(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function amount(value: string | number | null): string {
  return value !== null && Number.isFinite(Number(value)) ? `US$ ${Number(value).toFixed(4)}` : "未知";
}

function useActiveTimer() {
  const milliseconds = useRef(0);
  const startedAt = useRef<number | null>(null);
  const [running, setRunning] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [autoPaused, setAutoPaused] = useState(false);
  const pause = useCallback(() => {
    if (startedAt.current === null) return;
    milliseconds.current = Math.min(MAX_TIME_SECONDS * 1000, milliseconds.current + Math.max(0, performance.now() - startedAt.current));
    startedAt.current = null;
    setRunning(false);
    setSeconds(Math.floor(milliseconds.current / 1000));
  }, []);

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      if (startedAt.current === null) return;
      const elapsed = milliseconds.current + Math.max(0, performance.now() - startedAt.current);
      setSeconds(Math.min(MAX_TIME_SECONDS, Math.floor(elapsed / 1000)));
      if (elapsed >= MAX_TIME_SECONDS * 1000) pause();
    }, 500);
    const leave = () => { pause(); setAutoPaused(true); };
    const visibility = () => { if (document.hidden) leave(); };
    window.addEventListener("blur", leave);
    document.addEventListener("visibilitychange", visibility);
    return () => { clearInterval(timer); window.removeEventListener("blur", leave); document.removeEventListener("visibilitychange", visibility); };
  }, [running, pause]);

  function start() {
    if (running || document.hidden || seconds >= MAX_TIME_SECONDS) return;
    startedAt.current = performance.now(); setRunning(true); setAutoPaused(false);
  }
  function clear() {
    milliseconds.current = 0; startedAt.current = null;
    setRunning(false); setSeconds(0); setAutoPaused(false);
  }
  return { running, seconds, autoPaused, pause, start, clear };
}

function UsageWorkspace({ orderId, csrfToken, refresh }: OperationsPanelProps) {
  const id = useId();
  const base = `/internal/orders/${encodeURIComponent(orderId)}`;
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [order, setOrder] = useState<OrderState | null>(null);
  const [loadError, setLoadError] = useState("");
  const [revision, setRevision] = useState(0);
  const reload = () => setRevision(current => current + 1);
  const timer = useActiveTimer();
  const [timeReason, setTimeReason] = useState("审阅商品资料与图文成品");
  const [savingTime, setSavingTime] = useState(false);
  const [uncertainTime, setUncertainTime] = useState(false);
  const [timeError, setTimeError] = useState("");
  const [timeNotice, setTimeNotice] = useState("");
  const timeSubmission = useRef<{ body: string; key: string } | null>(null);
  const [selectedCommand, setSelectedCommand] = useState("");
  const [retryReason, setRetryReason] = useState("已检查技术问题，授权恢复本次失败任务。");
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState("");
  const [retryNotice, setRetryNotice] = useState("");
  const retrySubmission = useRef<{ body: string; key: string } | null>(null);

  useEffect(() => {
    let active = true;
    let poll: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    async function load() {
      let denied = false;
      try {
        const [nextMetrics, nextOrder] = await Promise.all([
          apiFetch<Metrics>(`${base}/metrics`, { signal: controller.signal }),
          apiFetch<OrderState>(base, { signal: controller.signal }),
        ]);
        if (active) { setMetrics(nextMetrics); setOrder(nextOrder); setLoadError(""); }
      } catch (caught) {
        if (active) {
          denied = caught instanceof ApiError && [401, 403, 404].includes(caught.status ?? 0);
          setLoadError(errorMessage(caught));
          if (denied) { setMetrics(null); setOrder(null); }
        }
      } finally { if (active && !denied) poll = setTimeout(() => void load(), 10000); }
    }
    void load();
    return () => { active = false; controller.abort(); if (poll) clearTimeout(poll); };
  }, [base, revision]);

  const failed = order && !order.pending_command_id && !["COMPLETED", "REJECTED"].includes(order.phase)
    ? order.commands.filter(command => command.status === "failed" && command.expected_revision === order.revision) : [];
  const currentCommand = failed.find(command => command.id === selectedCommand) ?? failed[failed.length - 1];

  async function saveTime(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (savingTime || timer.running || !csrfToken || timer.seconds < 1 || !timeReason.trim()) return;
    const body = JSON.stringify({ seconds: timer.seconds, reason: timeReason.trim() });
    if (!uncertainTime && timeSubmission.current?.body !== body) timeSubmission.current = { body, key: crypto.randomUUID() };
    const pending = timeSubmission.current;
    if (!pending) return;
    setSavingTime(true); setTimeError(""); setTimeNotice("");
    try {
      const result = await apiFetch<ActiveTime>(`${base}/time-entries`, { method: "POST", body: pending.body, headers: csrfHeaders(csrfToken), idempotencyKey: pending.key });
      setMetrics(current => current ? { ...current, owner_active_seconds: result.owner_active_seconds } : current);
      timer.clear(); setUncertainTime(false); timeSubmission.current = null;
      setTimeNotice("本段实际处理工时已保存。"); reload(); refresh?.();
    } catch (caught) {
      const uncertain = caught instanceof ApiError && (caught.status === null || caught.kind === "invalid_response");
      setUncertainTime(uncertain);
      if (!uncertain) timeSubmission.current = null;
      setTimeError(uncertain ? "保存结果暂时无法确认，请重试保存同一段工时；重复点击不会重复记账。" : errorMessage(caught));
    } finally { setSavingTime(false); }
  }

  async function retry(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (retrying || !csrfToken || !order || !currentCommand || !retryReason.trim()) return;
    const body = JSON.stringify({ command_id: currentCommand.id, expected_revision: order.revision, reason: retryReason.trim() });
    if (retrySubmission.current?.body !== body) retrySubmission.current = { body, key: crypto.randomUUID() };
    setRetrying(true); setRetryError(""); setRetryNotice("");
    try {
      await apiFetch(`${base}/retries`, { method: "POST", body, headers: csrfHeaders(csrfToken), idempotencyKey: retrySubmission.current.key });
      setOrder(current => current ? { ...current, pending_command_id: currentCommand.id, commands: current.commands.map(command => command.id === currentCommand.id ? { ...command, status: "queued" } : command) } : current);
      retrySubmission.current = null;
      setRetryNotice("已授权恢复并排队，执行结果稍后更新。"); reload(); refresh?.();
    } catch (caught) {
      setRetryError(caught instanceof ApiError && caught.status === 409 ? "任务状态或返修预算不允许重试，请刷新后检查最新记录。" : errorMessage(caught));
      if (caught instanceof ApiError && caught.status === 409) { reload(); refresh?.(); }
    } finally { setRetrying(false); }
  }

  return <section className={`surface workspace-panel ${styles.panel}`} aria-labelledby={`${id}-heading`}>
    <div className="panel-heading"><h2 id={`${id}-heading`}>用量与实际工时</h2><button className="text-button" type="button" onClick={reload}>刷新 ↻</button></div>
    {loadError && <p className={styles.error} role="alert">{loadError}{metrics ? " 当前显示最近一次成功读取的数据。" : ""}</p>}
    {!metrics && !loadError && <p className={styles.hint} role="status">正在读取本单用量…</p>}
    {metrics && <>
      <dl className={styles.stats}>
        <div className={styles.stat}><dt>已记录 token</dt><dd>{metrics.tokens.total_tokens?.toLocaleString("zh-CN") ?? "未知"}</dd></div>
        <div className={styles.stat}><dt>模型调用记录</dt><dd>{metrics.recorded_model_calls}<small>次</small></dd></div>
        <div className={styles.stat}><dt>成品候选版本</dt><dd>{metrics.versions}<small>版</small></dd></div>
        <div className={styles.stat}><dt>已保存活跃工时</dt><dd>{duration(metrics.owner_active_seconds)}</dd></div>
      </dl>
      <dl className={styles.details}>
        {metrics.calls_by_mode && <>
          <div><dt>真实模型请求</dt><dd>{metrics.calls_by_mode.live ?? 0} 次 · {metrics.tokens_by_mode?.live?.total_tokens?.toLocaleString("zh-CN") ?? ((metrics.calls_by_mode.live ?? 0) === 0 ? "0" : "未知")} token</dd></div>
          <div><dt>离线演示调用</dt><dd>{metrics.calls_by_mode["offline-fixture"] ?? 0} 次 · {metrics.tokens_by_mode?.["offline-fixture"]?.total_tokens?.toLocaleString("zh-CN") ?? ((metrics.calls_by_mode["offline-fixture"] ?? 0) === 0 ? "0" : "未知")} token</dd></div>
          {(metrics.calls_by_mode.unknown ?? 0) > 0 && <div><dt>模式未识别的记录</dt><dd>{metrics.calls_by_mode.unknown} 次</dd></div>}
        </>}
        <div><dt>已知费用</dt><dd>{amount(metrics.known_cost_usd)}</dd></div>
        <div><dt>完整费用</dt><dd>{amount(metrics.total_cost_usd)}</dd></div>
        <div><dt>输入 / 输出 token</dt><dd>{metrics.tokens.input_tokens?.toLocaleString("zh-CN") ?? "未知"} / {metrics.tokens.output_tokens?.toLocaleString("zh-CN") ?? "未知"}</dd></div>
        {Object.entries(metrics.calls_by_role).map(([role, count]) => <div key={role}><dt>{roleLabels[role] ?? "其他模型处理"}</dt><dd>{count} 次记录</dd></div>)}
        <div><dt>卖家反馈 / 未过审记录</dt><dd>{metrics.feedback_requests} / {metrics.failed_reviews}</dd></div>
      </dl>
      <p className={styles.hint}>仅统计本单已保存用量；缺失费用保留为未知。{metrics.is_demo ? " 当前为虚构样例订单。" : ""}{metrics.publish_mode === "simulated" ? " 发布使用模拟模式。" : ""}</p>
      {(metrics.model_mode === "offline" || metrics.model_mode === "mixed") && <p className={styles.hint}>离线演示调用不向模型服务发出真实请求，已与真实模型用量分列。</p>}
    </>}
    <div className={styles.timer}>
      <h3 className={styles.subheading}>记录本段人工处理时间</h3>
      <p className={styles.hint}>实际审阅或操作时开始，等待模型时暂停。离开窗口会自动暂停。</p>
      <output className={styles.clock} aria-label="本段实际处理时长">{duration(timer.seconds)}</output>
      <p className={styles.timerState} role="status">{timer.running ? "正在记录实际处理时间" : timer.autoPaused ? "已在离开窗口时自动暂停" : timer.seconds ? "已暂停，保存后计入本单工时" : "尚未开始计时"}</p>
      <div className={styles.actions}>
        <button type="button" className="button button-outline button-small" disabled={savingTime || uncertainTime || !csrfToken || (!timer.running && timer.seconds >= MAX_TIME_SECONDS)} onClick={timer.running ? timer.pause : timer.start}>{timer.running ? "暂停计时" : timer.seconds ? "继续计时" : "开始计时"}</button>
        <button type="button" className="text-button" disabled={savingTime || uncertainTime || timer.running || timer.seconds === 0} onClick={() => { timer.clear(); setTimeError(""); setTimeNotice(""); }}>清除未保存计时</button>
      </div>
      <form className={styles.form} onSubmit={saveTime}>
        <label htmlFor={`${id}-time-reason`}>处理内容</label>
        <input id={`${id}-time-reason`} required maxLength={2000} value={timeReason} disabled={savingTime || uncertainTime} onChange={event => setTimeReason(event.target.value)} />
        <button className="button button-dark button-small" type="submit" disabled={savingTime || timer.running || !csrfToken || timer.seconds < 1 || !timeReason.trim()}>{savingTime ? "正在保存…" : uncertainTime ? "重试保存同一段工时" : "保存本段实际工时"}</button>
      </form>
      {timeError && <p className={styles.error} role="alert">{timeError}</p>}{timeNotice && <p className={styles.notice} role="status">{timeNotice}</p>}
    </div>
    <div className={styles.retry}>
      <h3 className={styles.subheading}>恢复技术失败任务</h3>
      <p className={styles.hint}>核实技术问题后授权恢复一次，成功步骤和返修次数上限仍会保留。</p>
      {currentCommand ? <form className={styles.form} onSubmit={retry}>
        <label htmlFor={`${id}-command`}>当前可申请恢复的失败任务</label>
        <select id={`${id}-command`} value={currentCommand.id} disabled={retrying} onChange={event => setSelectedCommand(event.target.value)}>{failed.map(command => <option key={command.id} value={command.id}>{commandLabels[command.kind] ?? "订单处理"} · 资料版本 {command.expected_revision} · {new Date(command.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</option>)}</select>
        <label htmlFor={`${id}-retry-reason`}>授权原因</label>
        <textarea id={`${id}-retry-reason`} required rows={2} maxLength={2000} value={retryReason} disabled={retrying} onChange={event => setRetryReason(event.target.value)} />
        <button className="button button-outline button-small" type="submit" disabled={retrying || !csrfToken || !retryReason.trim()}>{retrying ? "正在排队…" : "授权恢复一次"}</button>
      </form> : <p className={styles.empty}>{order?.pending_command_id ? "已有任务待处理，暂不可追加恢复请求。" : "当前没有同一资料版本下可恢复的失败任务。"}</p>}
      {retryError && <p className={styles.error} role="alert">{retryError}</p>}{retryNotice && <p className={styles.notice} role="status">{retryNotice}</p>}
    </div>
  </section>;
}

export function OwnerUsagePanel(props: OperationsPanelProps) {
  return <UsageWorkspace key={props.orderId} {...props} />;
}

export const UsagePanel = OwnerUsagePanel;
