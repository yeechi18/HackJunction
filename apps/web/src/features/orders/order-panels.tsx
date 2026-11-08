import { factLabels, formatTime, type Message, type Order, type OrderEvent } from "./types";
import { orderStatusLabel, workflowProgress } from "./workflow-progress";

export function OrderHeading({ order, internal = false }: { order: Order; internal?: boolean }) {
  return <div className="order-heading"><div><p className="eyebrow">{internal ? "ORDER WORKSPACE" : "YOUR PRODUCT WORKSPACE"}</p><h1>{order.product_name}<span>.</span></h1><p className="order-identity"><span>订单 {order.id.slice(0, 8)}</span><span>资料版本 {order.revision}</span></p></div><span className="phase-badge"><span aria-hidden="true" />{orderStatusLabel(order)}</span></div>;
}

export function WorkflowNotice({ order, internal = false }: { order: Order; internal?: boolean }) {
  const progress = internal && order.phase === "AWAITING_SELLER"
    ? { title: "已放行交付，等待卖家确认", description: "负责人审批已完成。下一步由卖家在卖家端查看交付包，提出修改或填写售价与 SKU 后提交成品确认。", tone: "waiting" }
    : workflowProgress(order);
  return <div className={`workflow-notice workflow-${progress.tone}`} role="status" aria-label="订单进度" data-workflow-status={order.workflow_status}><span className="workflow-notice-mark" aria-hidden="true">↳</span><div><strong>{progress.title}</strong><p>{progress.description}</p>{order.pending_command_id && <p className="mono-small">命令 {order.pending_command_id.slice(0, 8)} · 等待实际处理结果</p>}</div></div>;
}

export function FactsPanel({ order, action }: { order: Order; action?: React.ReactNode }) {
  return <section className="surface workspace-panel"><div className="panel-heading"><h2>商品资料</h2>{action ?? <span>当前保存版本</span>}</div>{order.product_details && <div className="order-notes"><h3>商品明细信息</h3><p>{order.product_details}</p></div>}{order.domestic_product_url && <div className="order-notes"><h3>国内平台商品链接</h3><a className="text-link product-source-link" href={order.domestic_product_url} target="_blank" rel="noopener noreferrer">{order.domestic_product_url}</a></div>}{order.facts.length ? <dl className="facts-list">{order.facts.map((fact, index) => <div key={`${fact.key}-${index}`}><dt>{factLabels[fact.key] ?? fact.key}</dt><dd><span>{fact.value}{fact.unit ? ` ${fact.unit}` : ""}</span><span className={`fact-state ${fact.confirmed ? "confirmed" : "unconfirmed"}`}>{fact.confirmed ? "已确认" : "待确认"}</span></dd></div>)}</dl> : null}{order.notes && <div className="order-notes"><h3>补充说明</h3><p>{order.notes}</p></div>}{order.conflicting_fact_keys.length > 0 && <div className="form-notice notice-warning">存在需要核对的资料：{order.conflicting_fact_keys.map(key => factLabels[key] ?? key).join("、")}。初审会标出差异，负责人可结合依据判断。</div>}</section>;
}

export function MessagesPanel({ messages, children }: { messages: Message[]; children?: React.ReactNode }) {
  return <section className="surface workspace-panel"><div className="panel-heading"><h2>沟通记录</h2><span>{messages.length} 条记录</span></div><div className="message-list">{messages.length ? messages.map(message => <article className={`message-item message-${message.role}`} key={message.id}><div><strong>{({ seller: "卖家", owner: "负责人", agent: "服务 Agent", assistant: "获客 Agent" } as Record<string, string>)[message.role] ?? "服务消息"}</strong><time dateTime={message.created_at}>{formatTime(message.created_at)}</time></div><p>{message.content}</p></article>) : <div className="panel-empty">还没有沟通记录。</div>}</div>{children}</section>;
}

export function EventsPanel({ events }: { events: OrderEvent[] }) {
  return <section className="surface workspace-panel"><div className="panel-heading"><h2>订单动态</h2><span>实际事件记录</span></div><ol className="event-list">{events.length ? events.map(event => <li key={event.id}><span className="event-dot" aria-hidden="true" /><p>{event.message}</p><time dateTime={event.created_at}>{formatTime(event.created_at)}</time></li>) : <li className="panel-empty">暂无动态记录。</li>}</ol></section>;
}
