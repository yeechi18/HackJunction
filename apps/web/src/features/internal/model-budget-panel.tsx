"use client";
import { useEffect, useRef, useState } from "react";
import { apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import type { InternalOrder } from "@/features/orders/types";

type Budget = { used_tokens: number; base_limit: number; granted_tokens: number; effective_limit: number; exhausted: boolean; additional_grant_available: number };
export function ModelBudgetPanel({ order, csrfToken, refresh }: { order: InternalOrder; csrfToken: string; refresh: () => void }) {
  const [budget, setBudget] = useState<Budget | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const request = useRef<string | null>(null);
  const path = `/internal/orders/${order.id}/model-budget`;
  useEffect(() => { let active = true; void apiFetch<Budget>(path).then(value => { if (active) setBudget(value); }).catch(e => { if (active) setMessage(errorMessage(e)); }); return () => { active = false; }; }, [path, order.revision, order.workflow_status]);
  async function grant() {
    if (busy) return;
    setBusy(true); setMessage("");
    request.current ??= crypto.randomUUID();
    try {
      setBudget(await apiFetch<Budget>(path, { method: "POST", headers: csrfHeaders(csrfToken), body: JSON.stringify({ expected_revision: order.revision, request_id: request.current, additional_tokens: 20000, reason: "负责人已核对中断原因，授权本单追加 20000 token 处理上限，后续恢复仍需单独决定。" }) }));
      request.current = null;
      setMessage("本单额度已追加。确认问题已修复后，在下方选择失败任务并点击“授权恢复一次”；这里没有自动发起模型调用。");
      refresh();
    } catch (e) { setMessage(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return <section id="model-budget" style={{ scrollMarginTop: 24 }} className="surface workspace-panel budget-card" aria-label="本单模型用量授权"><div className="panel-heading"><h2>本单用量与恢复</h2><span>由负责人授权</span></div>
    {budget && <><div className="budget-meter"><span>本单已用额度</span><strong>{budget.used_tokens.toLocaleString()} <small>/ {budget.effective_limit.toLocaleString()} tokens</small></strong><progress max={budget.effective_limit} value={Math.min(budget.used_tokens, budget.effective_limit)} aria-label="本单模型用量" /></div><p className="field-help">追加仅影响本单，后续调用按实际用量计费；不会自动恢复任务。</p>{!budget.exhausted && <p className="field-help">当前额度尚未用尽，无需追加。技术失败请在下方选择任务恢复。</p>}{budget.exhausted && <p className="form-notice">已达到本单用量上限。先处理失败原因，再决定是否追加额度和恢复任务。</p>}<button className="button button-outline button-small" type="button" disabled={busy || !budget.exhausted || budget.additional_grant_available < 20000} onClick={() => void grant()}>{busy ? "正在授权…" : "授权本单追加 20000 token 上限"}</button></>}
    {message && <p role="status">{message}</p>}
  </section>;
}
