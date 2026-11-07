import type { Metadata } from "next";
import Link from "next/link";
import { headers } from "next/headers";
import { PortalShell } from "@/components/portal-shell";
import { NewOrderForm } from "@/features/orders/new-order";

export const metadata: Metadata = { title: "提交商品资料｜出海工坊" };

export default async function NewOrderPage() {
  const agent = (await headers()).get("user-agent") ?? "";
  const safari = !/Chrome\/|Chromium\/|Edg\/|OPR\//.test(agent) && agent.match(/Version\/(\d+)\.(\d+).*Safari\//);
  if (safari && (Number(safari[1]) < 16 || (Number(safari[1]) === 16 && Number(safari[2]) < 4))) {
    return <PortalShell><section className="surface empty-panel" role="alert"><h1>请更新浏览器后再提交资料</h1><p>当前 Safari 版本无法正常运行此表单。请使用 Safari 16.4 或更新版本，或使用新版 Chrome / Edge，再打开此页面。</p><Link className="button button-dark" href="/">返回首页 ↗</Link></section></PortalShell>;
  }
  return <PortalShell><NewOrderForm /></PortalShell>;
}
