"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import { overseasOrderPath } from "@/lib/service-packages";
import { ProductScene } from "./product-scene";
import { ShelfwiseHero } from "./shelfwise-hero";

const features = ["英文商品标题与五点卖点", "英文详情文案与买家 FAQ", "商品图片与图文一致性审校", "竞品研究与定价建议", "完整上架资料包与版本交付", "卖家确认后的模拟发布"];

export function ShelfwiseHome() {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const elements = root.current?.querySelectorAll("[data-reveal]");
    const observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) { entry.target.classList.add("sw-visible"); observer.unobserve(entry.target); }
    }), { threshold: 0.12 });
    elements?.forEach(element => observer.observe(element));
    return () => observer.disconnect();
  }, []);
  return <div className="sw-home" ref={root}>
    <main id="sw-main">
      <ShelfwiseHero />
      <section className="sw-about" id="about" data-reveal aria-labelledby="sw-about-title"><div className="sw-section-index">01 / MEET SHELFWISE <span>小卖家，也有大市场。</span></div><div className="sw-about-grid"><div><span className="sw-pill">上架有方 | Shelfwise</span><h2 id="sw-about-title">你专注好产品。<br/>上架的事，<br/><span>交给有方。</span></h2></div><div className="sw-about-copy"><span className="sw-big-star" aria-hidden="true">✳</span><h3>让出海的第一步，<br/>少一点繁琐，多一点从容。</h3><p>我们为中文跨境小卖家，把商品资料整理成面向 Amazon 美国站的美式英语文案、图片与完整上架资料包。</p><p>从市场研究到图文生成与审校，我们把上架准备串成一套流程。你可以查看每一版交付，确认适合你的最终版本。</p><a href="#packages" className="sw-text-link">找到适合你的上架方案 <span>↘</span></a></div></div><div className="sw-process" aria-label="服务流程">{["资料补全", "市场研究", "内容生成", "图文审校", "确认上架"].map((label, i) => <div key={label}><small>0{i + 1}</small><span>{label}</span><b aria-hidden="true">↗</b></div>)}</div></section>
      <section className="sw-packages" id="packages" aria-labelledby="sw-packages-title"><div className="sw-package-heading" data-reveal><div><p className="sw-eyebrow">02 / YOUR NEXT CHAPTER</p><h2 id="sw-packages-title">选好方案。<br/>迈出你的下一步。</h2></div><p>从一个 SKU 开始，<br/>为你的出海计划，找到刚刚好的支持。</p></div><div className="sw-plan-grid">
        {[false, true].map(plus => <article className={`sw-plan ${plus ? "sw-plan-plus" : ""}`} key={String(plus)} data-reveal><div className="sw-plan-visual"><div className="sw-plan-label"><span>{plus ? "更进一步" : "从这里开始"}</span><small>AMAZON US</small></div><h3>{plus ? "出海进阶包" : "一站式上架包"}</h3><p>{plus ? "LISTING & CUSTOMS" : "THE COMPLETE LISTING"}</p><div className="sw-plan-art"><div className="sw-mini-browser"><div className="sw-browser-bar"><i/><i/><i/><span>shelfwise · listing studio</span></div><div className="sw-browser-content"><ProductScene compact/><div><small>READY FOR YOUR NEXT CHAPTER</small><strong>Good products.<br/>Better listings.</strong><span>★★★★★</span><hr/><p>商品文案 / 图片 / 定价建议</p></div></div></div><div className="sw-art-badge">{plus ? <><b>↗</b><span>海关备案<br/>自助指南</span></> : <><b>✓</b><span>全流程自动化<br/>一站式上架</span></>}</div></div></div><div className="sw-plan-body"><span className="sw-plan-tag">{plus ? "包含一站式上架包的全部内容" : "一个 SKU，一套完整交付"}</span><h4>{plus ? "上架之外，再帮你多走一步。" : "从商品资料到上架，全流程衔接。"}</h4><ul>{(plus ? [...features, "海关备案自助指南"] : features).map(feature => <li key={feature}><span aria-hidden="true">✓</span>{feature}</li>)}</ul><p className="sw-plan-note">服务价格待定 · 当前开放本地模拟上架演示。</p><Link className="sw-button sw-button-dark" href={plus ? overseasOrderPath : "/orders/new"}>选择此服务，开始上架 <span>↗</span></Link><a className="sw-button sw-button-outline" href="#workflow-details">查看服务交付说明<span>↓</span></a></div></article>)}
      </div></section>
      <section className="sw-final" id="workflow-details" data-reveal><span className="sw-eyebrow">YOUR PRODUCT. OUR NEXT CHAPTER.</span><h2>好商品的下一站，<br/>从这里开始。</h2><p>文案、图片、FAQ、定价建议与完整资料包，一次整理到位。<br/>文案和图片都可反复修改，直到你满意为止。<br/>你确认最终版本后，再进入模拟发布。</p><Link href="/orders/new" className="sw-button">提交商品资料 <span>↗</span></Link></section>
    </main><footer className="sw-footer"><Link href="/" className="sw-footer-brand">上架有方 <span>Shelfwise</span></Link><span>© 2026 Shelfwise · 让每一件好商品，被世界看见。</span><a href="#sw-main">回到顶部 ↑</a></footer>
  </div>;
}
