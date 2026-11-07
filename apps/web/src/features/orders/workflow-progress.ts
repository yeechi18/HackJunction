type ProgressOrder = { phase: string; workflow_status: string; pending_command_id: string | null };

export function pollIntervalFor(order: Pick<ProgressOrder, "workflow_status">): number {
  return ["queued", "running", "processing"].includes(order.workflow_status) ? 2000 : 5000;
}

export function workflowProgress(order: ProgressOrder): { title: string; description: string; tone: string } {
  const stage: Record<string, string> = {
    INTAKE: "资料审查", NEEDS_INFO: "资料审查", AWAITING_ACCEPTANCE: "资料审查",
    RESEARCH: "商品研究", GENERATION: "内容生成", REVIEW: "内容审校", NEEDS_IMAGE: "商品图片制作",
    AWAITING_RELEASE: "成品交付审核", AWAITING_SELLER: "成品确认", READY_TO_PUBLISH: "发布准备", PUBLISHING: "商品发布",
  };
  const task = order.phase === "AWAITING_ACCEPTANCE" && order.pending_command_id ? "接单决定" : stage[order.phase] ?? "订单任务";
  switch (order.workflow_status) {
    case "queued": return { title: `${task}已排队`, description: "任务已保存，等待工作流开始处理。新的进度会自动出现在本页。", tone: "waiting" };
    case "running": return { title: `${task}正在处理`, description: "工作流正在处理本轮任务，最新动态与结果会在本页自动更新。", tone: "active" };
    case "waiting_for_seller": return order.phase === "NEEDS_IMAGE"
      ? { title: "需要补充商品参考图", description: "请上传能够准确呈现本商品的参考图，图片制作和图文审校会继续处理。", tone: "waiting" }
      : order.phase === "AWAITING_SELLER"
      ? { title: "成品已交付，等待你的确认", description: "请查看已放行的交付版本，可提出修改，或核对售价与 SKU 后确认模拟发布。", tone: "waiting" }
      : { title: "需要你补充或核对资料", description: "请查看获客 Agent 的回复，补齐缺失项并确认商品事实。保存后会重新审查。", tone: "waiting" };
    case "waiting_for_owner": return order.phase === "AWAITING_RELEASE"
      ? { title: "候选版本等待负责人核对", description: "负责人将核对文案、图片与审校记录。放行后，成品才会出现在卖家的交付包中。", tone: "waiting" }
      : { title: "资料审查通过，等待负责人接单", description: "负责人将查看本版资料并决定是否接单，结果会在这里更新。", tone: "waiting" };
    case "research_pending": return { title: "已批准接单，等待开展研究", description: "下一步根据卖家确认的商品事实与已保存证据开展商品和市场研究。", tone: "waiting" };
    case "failed": return { title: `${task}遇到问题`, description: "当前处理暂时中断，需要负责人检查运行记录并处理后恢复。已保存的资料与消息仍然保留。", tone: "failed" };
    case "finished": return order.phase === "REJECTED"
      ? { title: "负责人未接单，订单已结束", description: "本单资料与沟通记录已保留，供你查看。", tone: "finished" }
      : { title: "订单处理已结束", description: "请查看本单的最新动态与已保存记录。", tone: "finished" };
    default: return { title: "订单正在处理", description: "本页展示服务端实际进度，最新结果会通过订单动态与沟通记录更新。", tone: "active" };
  }
}

export function orderStatusLabel(order: ProgressOrder): string {
  const statuses: Record<string, string> = {
    queued: "任务排队中",
    running: "工作流处理中",
    waiting_for_seller: order.phase === "NEEDS_IMAGE" ? "待补参考图" : order.phase === "AWAITING_SELLER" ? "待成品确认" : "待卖家补充",
    waiting_for_owner: order.phase === "AWAITING_RELEASE" ? "待放行交付" : "待负责人审批",
    research_pending: "待开展研究",
    finished: order.phase === "REJECTED" ? "未接单" : "已结束",
    failed: "处理遇到问题",
    processing: "处理中",
  };
  return statuses[order.workflow_status] ?? "状态更新中";
}
