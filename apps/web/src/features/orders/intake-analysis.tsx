import type { Order } from "./types";

const statusLabels: Record<string, string> = { ok: "已读取页面", unavailable: "暂时无法读取", restricted: "页面需要登录或验证", blocked: "链接无法安全读取", empty: "页面没有可用正文", too_large: "页面超出读取范围", unsupported: "暂不支持此页面格式", redirect_limit: "页面跳转过多", offline_test: "离线验收样例" };

export function IntakeAnalysisPanel({ order }: { order: Order }) {
  const analysis = order.intake_analysis;
  if (!analysis) return <div className="order-notes"><h3>获客初审与文案方向</h3><p>{order.workflow_status === "failed" ? "初审暂未完成，请查看运行记录或联系负责人恢复。" : "顾问正在结合商品描述、图片和参考链接整理初审意见。完成后可批准或拒绝接单。"}</p></div>;
  const brief = analysis.content_brief;
  return <article className="order-notes" aria-label="获客初审与文案方向">
    <h3>获客初审与文案方向</h3>
    <p>{analysis.summary}</p>
    <p>资料一致性：{({ consistent: "整体一致", uncertain: "部分内容尚不能判断", inconsistent: "存在需要留意的差异" } as Record<string, string>)[analysis.assessment ?? "uncertain"]}。此判断不等于商品真伪鉴定。</p>
    {Boolean(analysis.issues?.length) && <ul>{analysis.issues?.map((issue, index) => <li key={index}><strong>{issue.description}</strong><p>依据：{issue.basis}</p></li>)}</ul>}
    {Boolean(analysis.image_observations?.length) && <div><h4>图片观察</h4><ul>{analysis.image_observations?.map((item, index) => <li key={index}>{item}</li>)}</ul></div>}
    {analysis.page_observation && <details><summary>商品链接读取结果：{statusLabels[analysis.page_observation.status] ?? "读取结果已记录"}</summary><p>{analysis.page_observation.title}</p>{analysis.page_observation.excerpt && <p>{analysis.page_observation.excerpt.slice(0, 1200)}</p>}</details>}
    {brief && <div><h4>内容方向（初稿）</h4>{brief.product_type && <p>商品：{brief.product_type}</p>}{brief.audience && <p>适合面向：{brief.audience}</p>}{Boolean(brief.selling_points?.length) && <ul>{brief.selling_points?.map((item, index) => <li key={index}>{item}</li>)}</ul>}{brief.english_title && <p lang="en-US"><strong>{brief.english_title}</strong></p>}{brief.english_description && <p lang="en-US">{brief.english_description}</p>}</div>}
    {Boolean(analysis.source_facts?.length) && <details><summary>已采用的商品描述与原文依据</summary><ul>{analysis.source_facts?.map((fact, index) => <li key={index}>{fact.key}：{fact.value}{fact.unit ? ` ${fact.unit}` : ""}<p>原文：{fact.source_quote}</p></li>)}</ul></details>}
  </article>;
}
