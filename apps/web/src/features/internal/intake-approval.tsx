"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { AccessError, ErrorNotice, LoadingPanel } from "@/components/async-feedback";
import { ApiError, apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import type { DecisionCommand, InternalOrder, Order } from "@/features/orders/types";
import { useWorkspace } from "@/features/orders/use-workspace";
import { orderStatusLabel } from "@/features/orders/workflow-progress";

export function intakeApprovalStatus(order: Order) {
  if (order.allowed_actions.includes("accept") || order.allowed_actions.includes("reject")) return "可审批";
  if (order.phase === "AWAITING_ACCEPTANCE" && order.pending_command_id) return "决定等待工作流处理";
  return orderStatusLabel(order);
}

export function isIntakeOrder(order: Order) {
  return ["INTAKE", "NEEDS_INFO", "AWAITING_ACCEPTANCE"].includes(order.phase);
}

export function IntakeApproval({ orderId }: { orderId: string }) {
  const { data, error, accessDenied, refresh } = useWorkspace<InternalOrder>(orderId, true);
  if (accessDenied) return <AccessError internal />;
  return <>
    {error && <><ErrorNotice>{error}</ErrorNotice><button type="button" className="text-button" onClick={refresh}>重新读取</button></>}
    {data ? <DecisionPanel order={data.order} csrfToken={data.csrfToken} refresh={refresh} />
      : !error && <LoadingPanel message="正在读取接单审批…" />}
  </>;
}

export function DecisionPanel({ order, csrfToken, refresh }: { order: InternalOrder; csrfToken: string; refresh: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [submittedId, setSubmittedId] = useState("");
  const submission = useRef<{ body: string; key: string } | null>(null);
  const canAccept = order.allowed_actions.includes("accept");
  const canReject = order.allowed_actions.includes("reject");
  const unavailableReason = order.pending_command_id
    ? "系统正在处理已提交的任务，完成后会自动更新接单操作。若长时间停留，请查看执行记录或用量与恢复。"
    : order.workflow_status === "failed"
      ? "资料初审未完成：任务已暂停，请先查看用量与恢复，处理失败原因后重试。"
      : order.phase === "NEEDS_INFO"
        ? "资料初审要求卖家补充信息；补充并完成重审后才能决定接单。"
        : "资料初审尚未完成，暂不能同意或拒绝接单。初审完成后按钮会自动启用；若长时间排队，请检查后台工作进程。";
  const submittedCommand = order.commands.find(command => command.id === submittedId);
  const pendingSubmission = Boolean(submittedId && (!submittedCommand || ["queued", "running"].includes(submittedCommand.status)));
  const decisionStatus: Record<string, string> = {
    queued: "决定已入队，等待工作流处理。",
    running: "工作流正在处理本次决定。",
    applied: "本次决定已由工作流应用，订单进度已更新。",
    stale: "本次决定对应的资料已变化，未应用于当前订单。请核对最新资料。",
    failed: "本次决定处理失败，请查看运行记录。",
    cancelled: "本次决定已取消。",
  };
  const submittedMessage = submittedId ? submittedCommand
    ? decisionStatus[submittedCommand.status] ?? "决定已记录，请查看订单动态。"
    : "决定已提交，正在读取处理状态。"
    : null;

  async function decide(action: "accept" | "reject") {
    if (busy || pendingSubmission || !order.allowed_actions.includes(action)) return;
    const body = JSON.stringify({ action, expected_revision: order.revision });
    if (submission.current?.body !== body) submission.current = { body, key: crypto.randomUUID() };
    setBusy(true); setError("");
    try {
      const result = await apiFetch<DecisionCommand>(`/internal/orders/${order.id}/decisions`, { method: "POST", headers: csrfHeaders(csrfToken), body, idempotencyKey: submission.current.key });
      setSubmittedId(result.command_id);
      refresh();
    } catch (caught) { setError(errorMessage(caught)); if (caught instanceof ApiError && caught.status === 409) refresh(); }
    finally { setBusy(false); }
  }

  return <section className="surface workspace-panel decision-panel"><div className="panel-heading"><h2>接单决定</h2><span>{canAccept || canReject ? "待负责人决定" : "暂不可审批"}</span></div>
    <p className="field-help">同意后开始正式研究与制作；本次接单不代表批准最终交付。</p>
    {submittedMessage && <p className="decision-command-status" role="status">{submittedMessage}</p>}
    {!submittedMessage && !canAccept && !canReject && <p className="decision-command-status" role="status">{unavailableReason}</p>}
    <div className="decision-actions">
      <button className="button button-dark" type="button" disabled={busy || pendingSubmission || !canAccept} onClick={() => decide("accept")}>同意接单</button>
      <button className="button button-outline danger-text" type="button" disabled={busy || pendingSubmission || !canReject} onClick={() => decide("reject")}>拒绝接单</button>
      {!canAccept && !canReject && <button className="text-button" type="button" onClick={refresh}>刷新审批状态 ↻</button>}
    </div>
    {busy && <p role="status">正在提交决定…</p>}
    {order.ai_reply_paused && isIntakeOrder(order) &&
      <Link className="text-link" href={`/internal/orders/${order.id}#support`}>另有人工客服工单，前往处理 ↗</Link>}
    {error && <ErrorNotice>{error}</ErrorNotice>}
  </section>;
}
