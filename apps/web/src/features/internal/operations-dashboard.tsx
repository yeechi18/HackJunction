"use client";

import Link from "next/link";
import { useState } from "react";
import type { components } from "@/lib/generated/api";
import { formatTime, type Order } from "@/features/orders/types";
import { orderStatusLabel } from "@/features/orders/workflow-progress";
import { operationsHref, type OperationsView } from "./operations-views";
import { IntakeApproval, intakeApprovalStatus, isIntakeOrder } from "./intake-approval";

export type OperationsRun = components["schemas"]["OperationsRun"];
export type SupportTicket = components["schemas"]["SupportTicketView"];

const agents = [
  { key: "acquisition", label: "获客 Agent", duty: "审查卖家资料并处理交付后的修改反馈" },
  { key: "research", label: "研究 Agent", duty: "核实商品事实并整理市场证据" },
  { key: "generation", label: "生成 Agent", duty: "制作与修订上架文案和商品图片" },
  { key: "review", label: "审校 Agent", duty: "审查文案、声明依据和商品图片" },
  { key: "publishing", label: "上架 Agent", duty: "按卖家确认的版本和价格准备并执行上架" },
];

function agentKey(role: string) {
  if (role === "acquisition_feedback") return "acquisition";
  if (role === "images") return "generation";
  return role;
}

export function agentLabel(role: string) {
  return agents.find(agent => agent.key === agentKey(role))?.label ?? role;
}

function statusGroup(status: string) {
  const key = status.toLowerCase();
  if (["simulated", "succeeded"].includes(key)) return "succeeded";
  if (["failed", "plan_failed", "blocked"].includes(key)) return "failed";
  if (["running", "submitting", "planning"].includes(key)) return "running";
  return key;
}

const statusLabels: Record<string, string> = {
  succeeded: "成功", failed: "失败", running: "运行中", queued: "已排队",
  stale: "结果已过期", unknown: "结果待核对", ready: "待执行", prepared: "已生成计划",
};

function statusLabel(status: string) {
  return statusLabels[statusGroup(status)] ?? status;
}

const orderHref = (id: string, section?: string) => `/internal/orders/${id}${section ? "#" + section : ""}`;

function OrderQueue({ orders, title, section }: { orders: Order[]; title: string; section?: string }) {
  return <section className="ops-panel"><h2>{title} <small>{orders.length}</small></h2>
    {orders.length ? orders.map(order => <Link className="ops-row" href={orderHref(order.id, section)} key={order.id}>
      <span><strong>{order.product_name}</strong><small>{order.seller_name} · {order.id.slice(0, 8)}</small></span>
      <span>{orderStatusLabel(order)} ↗</span>
    </Link>) : <p className="panel-empty">暂无订单</p>}
  </section>;
}

function IntakeQueue({ orders }: { orders: Order[] }) {
  const [selectedId, setSelectedId] = useState("");
  const selected = orders.find(order => order.id === selectedId);
  const intake = orders.filter(isIntakeOrder);
  return <section className="ops-panel">
    <h2>接单审批 <small>{intake.length}</small></h2>
    {intake.length ? intake.map(order => <div key={order.id} className="ops-row">
      <Link href={orderHref(order.id)}>
        <strong>{order.product_name}</strong><small>{order.seller_name} · {order.id.slice(0, 8)}</small>
      </Link>
      <div className="ops-intake-actions">{intakeApprovalStatus(order)} <button type="button" className="text-button"
        aria-expanded={selectedId === order.id}
        onClick={() => setSelectedId(selectedId === order.id ? "" : order.id)}>接单决定</button></div>
    </div>) : <p className="panel-empty">暂无订单</p>}
    {selectedId && <div>
      <div className="panel-heading">
        <strong>{selected?.product_name ?? selectedId.slice(0, 8)}</strong>
        <button type="button" className="text-button" onClick={() => setSelectedId("")}>关闭接单决定</button>
      </div>
      <IntakeApproval key={selectedId} orderId={selectedId} />
    </div>}
  </section>;
}

function RunLog({ runs }: { runs: OperationsRun[] }) {
  const [role, setRole] = useState("");
  const [status, setStatus] = useState("");
  const filtered = runs.filter(run => (!role || agentKey(run.role) === role) && (!status || statusGroup(run.status) === status));

  return <section className="ops-panel"><h2>最近运行 <small>{filtered.length} / {runs.length}</small></h2>
    <div className="ops-filters field">
      <select aria-label="筛选 Agent" value={role} onChange={event => setRole(event.target.value)}>
        <option value="">全部 Agent</option>
        {Array.from(new Set([...agents.map(agent => agent.key), ...runs.map(run => agentKey(run.role))])).map(key => <option value={key} key={key}>{agentLabel(key)}</option>)}
      </select>
      <select aria-label="筛选运行状态" value={status} onChange={event => setStatus(event.target.value)}>
        <option value="">全部状态</option>
        {Array.from(new Set(runs.map(run => statusGroup(run.status)))).map(key => <option value={key} key={key}>{statusLabel(key)}</option>)}
      </select>
    </div>
    {filtered.length ? <div className="ops-table-scroll"><table><thead><tr>
      <th>Agent / 订单</th><th>记录时间</th><th>状态</th><th>模型 / Token</th><th>耗时</th><th>诊断</th>
    </tr></thead><tbody>{filtered.map(run => <tr key={run.id}>
      <td><strong>{agentLabel(run.role)}</strong><br /><Link href={orderHref(run.order_id)}>{run.order_id.slice(0, 8)} ↗</Link></td>
      <td>{formatTime(run.created_at)}</td><td><span className={`ops-badge ${statusGroup(run.status)}`}>{statusLabel(run.status)}</span></td>
      <td>{run.model ?? "—"}<br />{typeof run.usage.total_tokens === "number" ? run.usage.total_tokens.toLocaleString("zh-CN") : "—"}</td>
      <td>{run.elapsed_seconds == null ? "—" : `${run.elapsed_seconds.toFixed(1)} 秒`}</td><td>{run.error_summary ?? "—"}</td>
    </tr>)}</tbody></table></div> : <p className="panel-empty">暂无符合条件的运行记录</p>}
  </section>;
}

const dayKey = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" });

function RunTrend({ runs, checkedAt }: { runs: OperationsRun[]; checkedAt: number }) {
  const [days, setDays] = useState(7);
  const series = Array.from({ length: days }, (_, index) => {
    const date = dayKey.format(checkedAt - (days - index - 1) * 86400000);
    const daily = runs.filter(run => dayKey.format(new Date(run.created_at)) === date);
    return { date, success: daily.filter(run => statusGroup(run.status) === "succeeded").length, failed: daily.filter(run => statusGroup(run.status) === "failed").length };
  });
  const max = Math.max(1, ...series.map(day => day.success + day.failed));
  return <section className="ops-panel"><div className="ops-section-heading"><h2>Agent 运行趋势</h2>
    <select aria-label="趋势时间范围" value={days} onChange={event => setDays(Number(event.target.value))}>
      <option value={7}>最近 7 天</option><option value={14}>最近 14 天</option>
    </select>
  </div><p className="ops-muted">最近 {runs.length} 条记录 · 绿色成功 / 红色失败</p>
    <div className="ops-chart">{series.map(day => <div className="ops-chart-day" key={day.date} title={`${day.date} 成功 ${day.success} 次，失败 ${day.failed} 次`}>
      <div className="ops-chart-stack" role="img" aria-label={`${day.date} 成功 ${day.success} 次，失败 ${day.failed} 次`}>
        <i style={{ height: `${day.failed / max * 100}%` }} /><i style={{ height: `${day.success / max * 100}%` }} />
      </div><small>{day.date.slice(5)}</small>
    </div>)}</div>
  </section>;
}

const traceLabels: Record<string, string> = {
  summary: "结论", ready_for_acceptance: "可进入接单审批", missing_fields: "待补字段",
  questions: "待确认问题", conflicts: "资料冲突", category: "反馈分类", reason: "判断依据",
  requires_research: "需要重新研究", market_notes: "市场观察", buyer_questions: "买家问题",
  missing_facts: "缺失事实", follow_up_questions: "后续问题", pricing_notes: "定价依据",
  suggested_listing_price_range: "建议价格区间", title: "商品标题", bullets: "卖点",
  description: "详情文案", faqs: "常见问题", claims: "声明与依据", image_plan: "图片方案",
  program_issues: "自动校验结果", scope: "审校范围", passed: "通过审校", issues: "发现的问题",
  buyer_question_coverage: "买家问题覆盖", requires_images: "需要图片",
  marketplace: "目标市场", currency: "币种", listing_price: "确认售价", seller_sku: "卖家 SKU",
  version_id: "确认版本", question: "问题", answer: "回答", applicable: "适用",
  evidence_ids: "证据编号", fact_ids: "事实编号", target_fields: "关联字段",
  field_path: "字段", severity: "严重程度", suggested_fix: "修改建议", rule_id: "规则编号",
  low: "下限", high: "上限", is_demo: "演示数据", text: "声明", status: "状态",
  purpose: "用途", layout: "版式", text_overlay: "图片文案", message: "说明",
};

function TraceValue({ value }: { value: unknown }) {
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.length ? <ul>{value.map((item, index) => <li key={index}><TraceValue value={item} /></li>)}</ul> : "无";
  if (value && typeof value === "object") return <dl>{Object.entries(value).map(([key, item]) => <div key={key}>
    <dt>{traceLabels[key] ?? key}</dt><dd><TraceValue value={item} /></dd>
  </div>)}</dl>;
  return value == null ? "—" : String(value);
}

function runTokens(run: OperationsRun) {
  return typeof run.usage.total_tokens === "number" ? run.usage.total_tokens : null;
}

function runStep(run: OperationsRun) {
  if (run.role === "acquisition_feedback") return "分析卖家反馈";
  if (run.role === "review" && run.result?.scope === "text_and_images") return "完整图文审校";
  if (run.role === "review" && run.result?.scope === "text_review_only") return "文案审校";
  return ({
    acquisition: "审查商品资料", research: "研究商品与市场证据", generation: "生成上架内容",
    review: "审校候选版本", publishing: "处理卖家确认的上架请求", images: "制作商品图",
  } as Record<string, string>)[run.role] ?? run.role;
}

function AgentMonitor({ runs }: { runs: OperationsRun[] }) {
  const [selected, setSelected] = useState("");
  const [orderId, setOrderId] = useState("");
  const orderIds = [...new Set(runs.map(run => run.order_id))];
  const filtered = runs.filter(run => (!selected || agentKey(run.role) === selected) && (!orderId || run.order_id === orderId));
  const tokens = filtered.flatMap(run => runTokens(run) ?? []);
  const entries = filtered.flatMap(run => {
    const end = run.finished_at;
    return end && end !== run.created_at
      ? [{ run, time: run.created_at, started: true }, { run, time: end, started: false }]
      : [{ run, time: run.created_at, started: false }];
  }).sort((a, b) => Date.parse(a.time) - Date.parse(b.time));
  const currentAgent = agents.find(agent => agent.key === selected);

  return <section className="ops-panel ops-monitor" aria-label="Agent 执行会话">
    <aside className="ops-sessions" aria-label="选择 Agent">
      <h2>Agent 会话</h2>
      {[{ key: "", label: "全部 Agent" }, ...agents].map(agent => <button
        type="button" key={agent.key} aria-pressed={selected === agent.key} onClick={() => setSelected(agent.key)}>
        <span aria-hidden="true">◇</span>{agent.label}
      </button>)}
    </aside>
    <div className="ops-conversation">
      <header className="ops-conversation-heading">
        <div><h2>{currentAgent?.label ?? "全部 Agent"} · 执行会话</h2><p>{currentAgent?.duty ?? "按时间查看订单在各 Agent 之间的执行记录。"}</p></div>
        <label className="field">订单筛选<select value={orderId} onChange={event => setOrderId(event.target.value)}>
          <option value="">全部订单</option>
          {Array.from(new Set(orderId ? [orderId, ...orderIds] : orderIds)).map(id => <option key={id} value={id}>{id.slice(0, 8)}</option>)}
        </select></label>
      </header>
      <dl className="ops-conversation-stats">
        <div><dt>运行记录</dt><dd>{filtered.length}</dd></div>
        <div><dt>失败次数</dt><dd>{filtered.filter(run => statusGroup(run.status) === "failed").length}</dd></div>
        <div><dt>累计 Tokens</dt><dd>{tokens.length ? tokens.reduce((sum, value) => sum + value, 0).toLocaleString("zh-CN") : "—"}</dd></div>
      </dl>
      <p className="ops-trace-note">根据最近 {runs.length} 条工作流记录展示执行步骤、已保存的判断依据和产物。累计 Tokens 以筛选范围内已记录的用量为准。</p>
      {entries.length ? <ol className="ops-thread" aria-label="执行时间线">{entries.map(({ run, time, started }) => {
        const tokens = runTokens(run);
        const status = started ? "开始执行" : run.status === "SIMULATED" ? "模拟上架完成" : statusLabel(run.status);
        return <li className="ops-message" key={`${run.id}-${started}`}>
          <span className="ops-avatar" aria-hidden="true">◇</span>
          <div className="ops-message-content">
            <div className="ops-message-meta"><strong>{agentLabel(run.role)}</strong><time dateTime={time}>{formatTime(time)}</time></div>
            <div className="ops-bubble">
              <p><strong>{runStep(run)}</strong> <span className={`ops-badge ${started ? "running" : statusGroup(run.status)}`}>{status}</span></p>
              {!started && typeof run.result?.summary === "string" && <p>{run.result.summary}</p>}
              {!started && run.error_summary && <p>{run.error_summary}</p>}
            </div>
            {!started && <details className="ops-activity"><summary>执行详情</summary>
              <p><Link href={orderHref(run.order_id)}>订单 {run.order_id.slice(0, 8)} ↗</Link> · {run.model ?? "模型未记录"} · {tokens == null ? "Token 未记录" : `${tokens.toLocaleString("zh-CN")} tokens`}{run.elapsed_seconds == null ? "" : ` · ${run.elapsed_seconds.toFixed(1)} 秒`}</p>
              {run.result ? <TraceValue value={run.result} /> : <p>该步骤暂无已保存的结构化结果。</p>}
            </details>}
          </div>
        </li>;
      })}</ol> : <p className="panel-empty">当前筛选范围内暂无执行记录。</p>}
    </div>
  </section>;
}

export function OperationsDashboard({ view, orders, runs, tickets, checkedAt }: {
  view: Exclude<OperationsView, "orders">; orders: Order[]; runs: OperationsRun[]; tickets: SupportTicket[]; checkedAt: number;
}) {
  const intake = orders.filter(order => order.allowed_actions.includes("accept") || order.allowed_actions.includes("reject"));
  const delivery = orders.filter(order => order.allowed_actions.includes("release") || order.allowed_actions.includes("veto"));
  const open = tickets.filter(ticket => ticket.status === "open");

  if (view === "runs") return <RunLog runs={runs} />;
  if (view === "agents") return <AgentMonitor runs={runs} />;
  if (view === "approvals") return <div className="ops-stack">
    <IntakeQueue orders={orders} />
    <OrderQueue title="待交付检查" orders={delivery} section="production" />
  </div>;
  if (view === "support") return <div className="ops-stack">{["open", "resolved"].map(status => {
    const items = tickets.filter(ticket => ticket.status === status);
    return <section className="ops-panel" key={status}><h2>{status === "open" ? "待处理工单" : "已结束工单"} <small>{items.length}</small></h2>
      {items.length ? items.map(ticket => <Link key={ticket.id} className="ops-row" href={orderHref(ticket.order_id, "support")}>
        <span><strong>{ticket.messages[0]?.content ?? "卖家请求人工协助"}</strong><small>{ticket.order_id.slice(0, 8)} · {formatTime(ticket.created_at)}</small></span>
        <span>{status === "open" ? "处理工单" : "查看记录"} ↗</span>
      </Link>) : <p className="panel-empty">暂无工单</p>}
    </section>;
  })}</div>;

  const stats = [
    { title: "进行中订单", value: orders.filter(order => !["COMPLETED", "REJECTED"].includes(order.phase)).length, view: "orders" },
    { title: "待审批订单", value: intake.length + delivery.length, view: "approvals" },
    { title: "失败运行", value: runs.filter(run => statusGroup(run.status) === "failed").length, view: "runs" },
    { title: "待处理工单", value: open.length, view: "support" },
  ] as const;
  return <div className="ops-stack">
    <div className="ops-stat-grid">{stats.map(stat => <Link className="ops-panel ops-stat" href={operationsHref(stat.view)} key={stat.view}>
      <span>{stat.title} <b aria-hidden="true">↗</b></span><strong>{stat.value}</strong>
    </Link>)}</div>
    <div className="ops-overview-grid"><RunTrend runs={runs} checkedAt={checkedAt} /><OrderQueue title="待办事项" orders={[...intake, ...delivery].slice(0, 5)} /></div>
    <OrderQueue title="最近更新的订单" orders={[...orders].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 5)} />
    <p className="ops-muted">订单统计与队列基于最近读取的订单，运行统计基于最近读取的执行记录。</p>
  </div>;
}
