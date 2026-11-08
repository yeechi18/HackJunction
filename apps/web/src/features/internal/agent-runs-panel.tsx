import { factLabels, formatTime, type AgentRun } from "@/features/orders/types";

const roleLabels: Record<string, string> = {
  copy_preview: "生成 Agent · 英文初稿", acquisition: "获客 Agent", research: "研究 Agent", generation: "生成 Agent", review: "审校 Agent", publishing: "上架 Agent",
};
const statusLabels: Record<string, string> = {
  needs_review: "待核对", queued: "等待执行", running: "运行中", succeeded: "已完成", failed: "失败", stale: "结果已过期",
};

function recordedNumber(value: number | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN") : "—";
}

function stringItems(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function RunResult({ run }: { run: AgentRun }) {
  if (!run.result) return null;
  const summary = typeof run.result.summary === "string" ? run.result.summary : null;
  const questions = stringItems(run.result.questions);
  const missing = stringItems(run.result.missing_fields);
  return <div className="agent-result">
    {summary && <p className="agent-result-summary">{summary}</p>}
    {missing.length > 0 && <p className="agent-result-missing">待补充：{missing.map(key => factLabels[key] ?? (key === "product_name" ? "商品名" : key)).join("、")}</p>}
    {questions.length > 0 && <ul>{questions.map((question, index) => <li key={index}>{question}</li>)}</ul>}
    <details className="agent-result-details"><summary>查看结构化结果 <span aria-hidden="true">＋</span></summary><pre>{JSON.stringify(run.result, null, 2)}</pre></details>
  </div>;
}

export function AgentRunsPanel({ runs }: { runs: AgentRun[] }) {
  const recordedTokens = runs.map(run => run.usage.total_tokens).filter(value => typeof value === "number" && Number.isFinite(value));
  const recordedDurations = runs.map(run => run.elapsed_seconds).filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  return <section className="surface workspace-panel agent-runs-panel" aria-labelledby="agent-runs-title">
    <div className="panel-heading"><h2 id="agent-runs-title">Agent 运行轨迹</h2><span>实际调用与执行记录</span></div>
    {runs.length === 0 ? <p className="panel-empty">尚无运行记录。任务开始执行后，将显示模型、用量、耗时和处理结果。</p> : <>
      <div className="agent-run-totals">
        <div><span>运行记录</span><strong>{runs.length}<small>次</small></strong></div>
        <div><span>已记录 Token</span><strong>{recordedTokens.length ? recordedNumber(recordedTokens.reduce((sum, value) => sum + value, 0)) : "—"}</strong></div>
        <div><span>已记录耗时</span><strong>{recordedDurations.length ? recordedDurations.reduce((sum, value) => sum + value, 0).toFixed(1) : "—"}<small>秒</small></strong></div>
      </div>
      <ol className="agent-run-list">{runs.map(run => <li key={run.id} className={`agent-run run-${run.status}`}>
        <div className="agent-run-header"><div><strong>{roleLabels[run.role] ?? run.role}</strong><span>资料版本 {run.input_revision}</span></div><span className="agent-run-status">{statusLabels[run.status] ?? "已记录"}</span></div>
        <div className="agent-run-meta"><span>{run.model ?? "模型信息待记录"}</span><time dateTime={run.created_at}>{formatTime(run.created_at)}</time></div>
        <dl className="agent-run-usage"><div><dt>输入 Token</dt><dd>{recordedNumber(run.usage.input_tokens)}</dd></div><div><dt>输出 Token</dt><dd>{recordedNumber(run.usage.output_tokens)}</dd></div><div><dt>总 Token</dt><dd>{recordedNumber(run.usage.total_tokens)}</dd></div><div><dt>耗时</dt><dd>{typeof run.elapsed_seconds === "number" ? `${run.elapsed_seconds.toFixed(2)}s` : "—"}</dd></div></dl>
        {run.error_summary && <div className="agent-run-error"><strong>失败原因</strong><p>{run.error_summary}</p></div>}
        {run.status === "stale" && <p className="agent-run-stale">本次结果对应旧资料，未应用于当前版本。</p>}
        <RunResult run={run} />
      </li>)}</ol>
    </>}
  </section>;
}
