"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { operationsHref, operationsViews, type OperationsView } from "./operations-views";

export function OperationsNav() {
  const pathname = usePathname();
  return <aside className="ops-sidebar"><strong>负责人工作台</strong><nav aria-label="负责人功能">
    {(Object.keys(operationsViews) as OperationsView[]).map(view => {
      const href = operationsHref(view);
      const current = pathname === href || pathname.startsWith(href + "/");
      return <Link href={href} aria-current={current ? "page" : undefined} key={view}>
        <span aria-hidden="true">{operationsViews[view].icon}</span>{operationsViews[view].title}
      </Link>;
    })}
  </nav><Link className="ops-home" href="/">服务首页 ↗</Link></aside>;
}
