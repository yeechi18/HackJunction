import { OperationsNav } from "@/features/internal/operations-nav";
import "@/app/operations.css";
export function PortalShell({ children, internal = false, operations = false }: { children: React.ReactNode; internal?: boolean; operations?: boolean }) {
  if (operations) return <div className="portal ops-shell">
    <div className="ops-layout"><OperationsNav /><main id="sw-main" className="ops-main">{children}</main></div>
  </div>;
  return <div className="portal"><main id="sw-main" className="container portal-main">{children}</main><footer className="container portal-footer"><span>上架有方 · Shelfwise</span><span>{internal ? "负责人工作台" : "Amazon US · 美式英语 · 单 SKU"}</span></footer></div>;
}
