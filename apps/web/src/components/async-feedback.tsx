import Link from "next/link";

export function ErrorNotice({ children }: { children: React.ReactNode }) {
  return <div className="form-notice notice-error" role="alert">{children}</div>;
}

export function LoadingPanel({ message = "正在读取最新资料…" }: { message?: string }) {
  return <div className="empty-panel" role="status"><span className="loading-mark" aria-hidden="true" /><p>{message}</p></div>;
}

export function AccessError({ internal = false, message }: { internal?: boolean; message?: string }) {
  return <div className="empty-panel access-panel"><span className="eyebrow">PRIVATE WORKSPACE</span><h2>{internal ? "请先登录工作台" : "暂时无法打开这份订单"}</h2><p>{message ?? (internal ? "内部订单仅对已登录的负责人开放。" : "请使用创建订单后获得的完整私有链接；链接或会话可能已失效。")}</p><Link className="button button-dark" href={internal ? "/internal/login" : "/orders/new"}>{internal ? "前往登录" : "提交新的商品资料"}<span aria-hidden="true">↗</span></Link></div>;
}
