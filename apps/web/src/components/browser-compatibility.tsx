import { headers } from "next/headers";
import Link from "next/link";

export async function BrowserCompatibility({ children }: { children: React.ReactNode }) {
  const agent = (await headers()).get("user-agent") ?? "";
  const safari = !/Chrome\/|Chromium\/|Edg\/|OPR\//.test(agent) && agent.match(/Version\/(\d+)\.(\d+).*Safari\//);
  if (safari && (Number(safari[1]) < 16 || (Number(safari[1]) === 16 && Number(safari[2]) < 4))) {
    return <section className="surface empty-panel" role="alert"><h1>请更新浏览器后再提交资料</h1><p>当前 Safari 版本无法正常运行账号和订单表单。请使用 Safari 16.4 或更新版本，或使用新版 Chrome / Edge，再打开此页面。</p><Link className="button button-dark" href="/">返回首页 ↗</Link></section>;
  }
  return <>{children}</>;
}
