export function PortalShell({ children, internal = false }: { children: React.ReactNode; internal?: boolean }) {
  return <div className="portal"><main id="sw-main" className="container portal-main">{children}</main><footer className="container portal-footer"><span>上架有方 · Shelfwise</span><span>{internal ? "负责人工作台" : "Amazon US · 美式英语 · 单 SKU"}</span></footer></div>;
}
