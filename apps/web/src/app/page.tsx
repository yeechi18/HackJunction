import Link from "next/link";
import { Brand } from "@/components/brand";
import { ProductScene } from "@/components/product-scene";
import { ServiceStatus } from "@/components/service-status";

const deliverables = [
  { number: "01", name: "一眼读懂的商品标题", english: "PRODUCT TITLE", detail: "用美式英语讲清商品是什么、适合谁，从卖家确认的事实出发。", mark: "Aa" },
  { number: "02", name: "有依据的卖点与详情", english: "BULLETS & DESCRIPTION", detail: "五点卖点与详情文案，让材质、尺寸和使用场景变成清楚的购买信息。", mark: "≡" },
  { number: "03", name: "图文一致的商品素材", english: "PRODUCT IMAGES", detail: "以核实过的商品参考图为基础制作素材，让图片和文案讲同一件事。", mark: "◩" },
  { number: "04", name: "多想一步的配套资料", english: "FAQ & PRICING NOTES", detail: "买家常见问题与带竞品依据的定价建议，整理成一个完整交付包。", mark: "+" },
];

const workflow = [
  ["获客", "把资料问清楚", "收集商品事实，缺什么就补什么。"],
  ["研究", "把依据找明白", "分清商品事实、图片观察与竞品信息。"],
  ["生成", "把价值说明白", "产出文案与图片，修改时保留版本。"],
  ["审校", "把每个细节核对好", "核查声明依据，问题退回后重新审校。"],
  ["上架", "由你确认，再发布", "确认具体版本和价格后进入发布流程。"],
];

export default function HomePage() {
  return <>
    <a className="skip-link" href="#main">跳到主要内容</a>
    <header className="site-header"><div className="container header-inner"><Brand /><nav aria-label="主要导航"><a href="#service">服务内容</a><Link href="/seller/orders">我的订单</Link><Link href="/seller/login">卖家登录 <span aria-hidden="true">↗</span></Link></nav><span className="preview-badge"><span aria-hidden="true" />资料受理预览</span></div></header>
    <main id="main">
      <section className="hero container" aria-labelledby="hero-title">
        <div className="hero-content"><div className="hero-kicker"><span className="tiny-square" />FOR SMALL SELLERS, BIG IDEAS.</div><h1 id="hero-title">让好商品，<br />说好<span className="hero-word">英语<svg viewBox="0 0 200 17" aria-hidden="true"><path d="M4 12C58 2 120 4 194 8" fill="none" stroke="currentColor" strokeWidth="6" strokeLinecap="round" /></svg></span>。</h1><p className="hero-description">你的产品，值得被世界读懂。<br />为跨境小卖家打造面向 Amazon 美国站的<br className="desktop-break" />商品文案与图片本地化上架包。</p><div className="hero-actions"><Link className="button button-dark" href="/orders/new">提交商品资料 <span aria-hidden="true">↗</span></Link><a className="button button-quiet" href="#example">查看演示样例 <span aria-hidden="true">→</span></a></div><div className="hero-scope"><span>AMAZON US</span><i /><span>美式英语</span><i /><span>单 SKU</span></div></div>
        <div className="hero-exhibit"><div className="exhibit-topline"><span>THE EVERYDAY, REIMAGINED.</span><span>NO. 001</span></div><ProductScene /><div className="floating-note"><span className="note-icon" aria-hidden="true">↗</span><span><strong>小商品，也有大表达。</strong><small>中文商品事实 → 英文上架内容</small></span></div><div className="exhibit-caption"><span>DESK ESSENTIALS</span><span>虚构商品 · 结构示意</span></div><span className="exhibit-stamp" aria-hidden="true">FROM<br />LOCAL<br /><b>TO GLOBAL.</b></span></div>
      </section>
      <div className="promise-strip"><div className="container promise-inner"><p><span aria-hidden="true">✳</span> 从产品事实出发，让每句话都有依据。</p><span>一名负责人 × 五个 MiMo Agent</span></div></div>
      <section className="section container" id="service" aria-labelledby="service-title"><div className="section-heading"><div><p className="eyebrow">01 / WHAT YOU GET</p><h2 id="service-title">不止翻译，<br className="mobile-break" />更是一次认真表达<span>.</span></h2></div><p className="section-description">首版聚焦非电子类桌面收纳用品。<br />从一件商品开始，把准备上架需要的内容整理好。</p></div><div className="deliverables-grid">{deliverables.map(item => <article className="deliverable" key={item.number}><div className="deliverable-top"><span>{item.number}</span><span className="deliverable-mark" aria-hidden="true">{item.mark}</span></div><p className="deliverable-english">{item.english}</p><h3>{item.name}</h3><p>{item.detail}</p></article>)}</div><div className="package-note"><span aria-hidden="true">↳</span><p>计划交付：可复制文案 + 实际商品图片 + 整包下载。FAQ 和定价建议作为配套资料。</p></div></section>
      <section className="example-section" id="example" aria-labelledby="example-title"><div className="container"><div className="section-heading"><div><p className="eyebrow">02 / A SMALL EXAMPLE</p><h2 id="example-title">一件小商品，<br />换一种被看见的方式<span>.</span></h2></div><div className="sample-label"><span className="tiny-square" /><span>虚构演示样例</span><span>内容结构示例，非真实卖家商品</span></div></div><div className="example-grid"><div className="sample-product"><div className="sample-product-label"><span>INPUT / 中文商品资料</span><span aria-hidden="true">↗</span></div><ProductScene compact /><div className="sample-product-bottom"><h3>桌面理线夹</h3><div><span>材质：硅胶</span><span>包装：3 个装</span><span>用途：桌面线缆整理</span></div><p>示例事实仅用于说明交付格式。</p></div></div><article className="sample-copy"><div className="sample-copy-header"><span>OUTPUT / 美式英语文案</span><span className="sample-copy-tag">EN-US</span></div><p className="copy-field-label">PRODUCT TITLE</p><h3>Silicone Desk Cable Clips,<br />3-Pack Cord Organizers<br />for a Tidy Workspace</h3><div className="copy-divider" /><p className="copy-field-label">BULLET POINT PREVIEW</p><div className="sample-bullet"><span aria-hidden="true">01</span><p><strong>A little order for your everyday.</strong><br />Keep desk cables organized with a set of three silicone cable clips.</p></div><div className="evidence-note"><span aria-hidden="true">✓</span> 表达对应示例事实：硅胶 / 3 个装 / 桌面理线</div><p className="sample-footnote">此处展示标题与一条卖点格式。完整交付计划包含五点卖点、详情、FAQ 与图片。</p></article></div></div></section>
      <section className="section container process-section" id="process" aria-labelledby="process-title"><div className="section-heading"><div><p className="eyebrow">03 / HOW WE WORK</p><h2 id="process-title">各司其职，<br className="mobile-break" />每一步都清楚<span>.</span></h2></div><p className="section-description">五个协作角色，一名负责人。<br />从资料补全到审校返修，让交付过程可追溯。</p></div><div className="workflow-label">服务流程设计</div><ol className="workflow">{workflow.map(([role, title, detail], index) => <li key={role}><div className="workflow-step"><span>0{index + 1}</span><span aria-hidden="true">↗</span></div><p className="workflow-role">{role} AGENT</p><h3>{title}</h3><p>{detail}</p></li>)}</ol><div className="human-gate"><div className="human-gate-icon" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><circle cx="16" cy="10" r="5" stroke="currentColor" strokeWidth="1.5" /><path d="M6 28v-4a10 10 0 0 1 20 0v4" stroke="currentColor" strokeWidth="1.5" /></svg></div><p><strong>重要决定，留给人。</strong><span>负责人审批接单并可否决待交付版本；卖家确认成品和价格后，再进入发布。</span></p></div></section>
      <section className="closing-section"><div className="container closing-inner"><div><p className="eyebrow">MAKE YOUR NEXT LISTING COUNT.</p><h2>把商品做好，<br />也把它说好。</h2></div><div className="closing-copy"><p>我们正在搭建从中文商品资料<br />到英文上架包的完整服务。</p><Link className="button button-dark" href="/orders/new">提交我的商品资料 <span aria-hidden="true">↗</span></Link><p className="opening-note">模拟业务闭环预览，真实 Amazon 发布尚未接入。</p></div><span className="closing-asterisk" aria-hidden="true">✳</span></div></section>
    </main>
    <footer className="site-footer"><div className="container"><div className="footer-top"><Brand /><p>认真对待每一件小商品。</p><a className="text-link" href="#main">回到顶部 <span aria-hidden="true">↑</span></a></div><div className="footer-bottom"><span>© 2026 出海工坊 · Listing Studio</span><span>JUNCTION CHINA 2026 项目</span><Link href="/internal/login">负责人入口 ↗</Link><ServiceStatus /></div></div></footer>
  </>;
}
