export const operationsViews = {
  overview: { title: "运营总览", icon: "◫", description: "查看订单、运行趋势和当前待办。" },
  orders: { title: "订单工作台", icon: "▤", description: "按商品、卖家和订单编号查找订单。" },
  agents: { title: "Agent 监控", icon: "◇", description: "查看各工作流角色及最近执行结果。" },
  runs: { title: "运行日志", icon: "≋", description: "按角色和状态查看模型、耗时、用量和错误。" },
  approvals: { title: "审批中心", icon: "✓", description: "集中检查待接单和待交付的订单。" },
  support: { title: "人工客服", icon: "◌", description: "查看待处理和已结束的卖家工单。" },
};
export type OperationsView = keyof typeof operationsViews;

export function operationsHref(view: OperationsView) {
  return view === "orders" ? "/internal/orders" : `/internal/operations/${view}`;
}
