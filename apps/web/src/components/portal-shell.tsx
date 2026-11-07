import Link from "next/link";
import { Brand } from "./brand";

export function PortalShell({ children, internal = false }: { children: React.ReactNode; internal?: boolean }) {
  return <div className="portal"><header className="portal-header"><div className="container portal-header-inner"><Brand /><nav aria-label={internal ? "内部导航" : "服务导航"}>{internal ? <><Link href="/internal/orders">订单工作台</Link><Link href="/">服务首页 ↗</Link></> : <><Link href="/">服务介绍</Link><Link href="/orders/new">提交商品资料 ↗</Link></>}</nav></div></header><main className="container portal-main">{children}</main><footer className="container portal-footer"><span>出海工坊 · Listing Studio</span><span>{internal ? "负责人工作台" : <><Link href="/internal/login">负责人入口 ↗</Link> · Amazon US · 美式英语 · 单 SKU</>}</span></footer></div>;
}
