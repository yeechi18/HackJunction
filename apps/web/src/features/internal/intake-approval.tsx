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

function DecisionPanel({ order, csrfToken, refresh }: { order: InternalOrder; csrfToken: string; refresh: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [submittedId, setSubmittedId] = useState("");
  const submission = useRef<{ body: string; key: string } | null>(null);
  const canAccept = order.allowed_actions.includes("accept");
  const canReject = order.allowed_actions.includes("reject");
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

  return <section className="surface workspace-panel decision-panel"><div className="panel-heading"><h2>负责人决定</h2><span>初审后由负责人决定接单</span></div>
    {submittedMessage && <p className="decision-command-status" role="status">{submittedMessage}</p>}
    {!pendingSubmission && (canAccept || canReject) && <>
      <p className="field-help">初审已整理，可结合资料、图片和文案方向决定是否接单。</p>
      <div className="decision-actions">
        {canAccept && <button className="button button-dark" type="button" disabled={busy} onClick={() => decide("accept")}>提交接单批准</button>}
        {canReject && <button className="button button-outline danger-text" type="button" disabled={busy} onClick={() => decide("reject")}>提交拒绝接单</button>}
      </div>
    </>}
    {busy && <p role="status">正在提交决定…</p>}
    {!submittedMessage && !canAccept && !canReject && <p className="field-help">{intakeApprovalStatus(order)}</p>}
    {order.ai_reply_paused && isIntakeOrder(order) &&
      <Link className="text-link" href={`/internal/orders/${order.id}#support`}>另有人工客服工单，前往处理 ↗</Link>}
    {error && <ErrorNotice>{error}</ErrorNotice>}
  </section>;
}
