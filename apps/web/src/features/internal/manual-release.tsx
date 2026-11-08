"use client";

import { ReleaseImages } from "./release-images";
import { useRef, useState } from "react";
import { apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import type { InternalOrder } from "@/features/orders/types";

type Confirmation = {
  token: string; version_id: string; revision: number;
  issues: { field: string; message: string; suggestion?: string }[];
  blockers: string[];
};

export function ManualRelease({ order, versionId, versionNumber, csrfToken, refresh }: {
  order: InternalOrder; versionId: string; versionNumber: number; csrfToken: string; refresh: () => void;
}) {
  const [review, setReview] = useState<Confirmation | null>(null);
  const [reason, setReason] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const submission = useRef<{ body: string; key: string } | null>(null);

  async function inspect() {
    setBusy(true); setError(""); setAcknowledged(false); setReview(null);
    try {
      setReview(await apiFetch<Confirmation>(`/internal/orders/${order.id}/release-review?version_id=${versionId}`));
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }
  async function confirm() {
    if (!review || !acknowledged || !reason.trim() || review.blockers.length || busy || submitted) return;
    setBusy(true); setError("");
    try {
      const body = JSON.stringify({ action: "release", version_id: versionId, expected_revision: review.revision,
        override_review: true, review_token: review.token, reason: reason.trim() });
      if (submission.current?.body !== body) submission.current = { body, key: crypto.randomUUID() };
      await apiFetch(`/internal/orders/${order.id}/decisions`, {
        method: "POST", headers: csrfHeaders(csrfToken), idempotencyKey: submission.current.key,
        body,
      });
      setSubmitted(true); refresh();
    } catch (caught) { setError(errorMessage(caught)); setAcknowledged(false); refresh(); }
    finally { setBusy(false); }
  }
  return <div className="manual-release">
    {!review && !submitted && <button className="button button-dark button-small" type="button" disabled={busy || !!order.pending_command_id} onClick={inspect}>同意放行并交付</button>}
    {review && !submitted && <section aria-label="人工放行确认" className="form-notice notice-warning">
      <h4>v{versionNumber} 当前存在以下问题，确定仍要放行吗？</h4>
      <p>以下问题来自本版当前审校记录。人工放行会交付这一版文案与图片，保留原有 AI 审校结论，不会改成“审校通过”。</p>
      <ul>{review.issues.length ? review.issues.map((issue, index) => <li key={index}><strong>{issue.message}</strong>{issue.suggestion && <p>建议：{issue.suggestion}</p>}</li>) : <li>未记录具体审校问题，请自行核对本版成品。</li>}</ul>
      {review.blockers.length > 0 ? <><p><strong>还不能形成交付包：</strong></p><ul>{review.blockers.map((message, index) => <li key={index}>{message}</li>)}</ul><p>AI 审校未通过可以由你决定放行；请先在下方将图片加入交付候选。</p>{review.blockers.some(message => message.includes("图片")) && <ReleaseImages order={order} csrfToken={csrfToken} refresh={refresh} />}</> : <>
        <label>人工放行理由<textarea rows={3} maxLength={2000} value={reason} disabled={busy} onChange={event => setReason(event.target.value)} placeholder="例如：已人工核对商品与图片，接受列出的文案风险。" /></label>
        <label><input type="checkbox" checked={acknowledged} disabled={busy} onChange={event => setAcknowledged(event.target.checked)} /> 我已查看本版问题，确认由我决定放行。</label>
        <button type="button" className="button button-dark button-small" disabled={busy || !acknowledged || !reason.trim() || !!order.pending_command_id} onClick={confirm}>确认仍然放行 v{versionNumber}</button>
      </>}
      <button type="button" className="text-button" disabled={busy} onClick={inspect}>重新核对最新问题</button>
      <button type="button" className="text-button" disabled={busy} onClick={() => { setReview(null); setAcknowledged(false); }}>取消</button>
    </section>}
    {submitted && <p role="status">人工放行决定已提交，正在生成交付包；完成后更新交付状态。</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
