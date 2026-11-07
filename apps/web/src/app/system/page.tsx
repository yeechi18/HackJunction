import type { Metadata } from "next";
import Link from "next/link";
import { Brand } from "@/components/brand";
import { ServiceStatus } from "@/components/service-status";

export const metadata: Metadata = { title: "基础服务状态｜出海工坊" };

export default function SystemPage() {
  return <main className="system-page"><div className="system-top"><Brand /><Link className="text-link" href="/">返回首页 <span aria-hidden="true">↗</span></Link></div><div className="system-content"><p className="eyebrow">SYSTEM STATUS</p><h1>基础服务状态<span>.</span></h1><p className="system-intro">这里显示当前页面与后端的实际连接情况。基础检查不会调用模型，也不代表完整业务流程已经通过验收。</p><ServiceStatus detailed /><p className="system-note">当前为模拟业务闭环，包含资料、生产、交付和模拟发布；真实 Amazon 发布尚未接入。基础检查反映服务连接状态，业务验收以实际运行记录为准。</p></div></main>;
}
