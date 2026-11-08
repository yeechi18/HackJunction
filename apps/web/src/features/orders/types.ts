import type { components } from "@/lib/generated/api";

type Schemas = components["schemas"];
export type ProductFact = Schemas["ProductFact"];
export type SellerSession = Schemas["SellerSessionView"];
export type OwnerSession = Schemas["OwnerSessionView"];
export type Order = Schemas["OrderSummary"];
export type CreatedOrder = Schemas["CreatedOrder"];
export type Message = Schemas["MessageView"];
export type OrderEvent = Schemas["EventView"];
export type DecisionCommand = Schemas["QueuedCommand"];
export type InternalOrder = Schemas["InternalOrder"];
export type AgentRun = Schemas["AgentRunView"];

export const factLabels: Record<string, string> = {
  brand: "品牌",
  product_type: "产品类别",
  net_volume: "单瓶净含量",
  material: "材质",
  dimensions: "尺寸",
  pack_quantity: "包装数量",
  use_cases: "适用范围",
  dimension_scope: "测量口径",
  cable_diameter: "适用线径",
  color: "颜色",
};

export function phaseLabel(phase: string): string {
  const labels: Record<string, string> = {
    INTAKE: "资料已保存",
    NEEDS_INFO: "待补充资料",
    AWAITING_INFO: "待补充资料",
    AWAITING_ACCEPTANCE: "待负责人审批",
    RESEARCH: "研究阶段",
    GENERATION: "生成中",
    REVIEW: "审校中",
    AWAITING_SELLER: "待卖家确认",
    READY_TO_PUBLISH: "待发布",
    NEEDS_PUBLISH_INFO: "待补发布资料",
    GENERATING: "生成中",
    REVIEWING: "审校中",
    AWAITING_RELEASE: "待交付审核",
    AWAITING_SELLER_CONFIRMATION: "待卖家确认",
    PUBLISHING: "发布处理中",
    COMPLETED: "已完成",
    REJECTED: "未接单",
    FAILED: "待处理故障",
  };
  return labels[phase] ?? "处理中";
}

export function formatTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? "时间暂不可用" : parsed.toLocaleString("zh-CN", { hour12: false });
}
