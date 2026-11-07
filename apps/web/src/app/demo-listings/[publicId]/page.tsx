import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PortalShell } from "@/components/portal-shell";
import { DemoListingImage } from "@/features/orders/demo-listing-image";
import styles from "@/features/orders/demo-listing.module.css";

export const metadata: Metadata = { title: "模拟商品展示｜出海工坊", robots: { index: false, follow: false }, referrer: "no-referrer" };

type PublicDemo = {
  title: string; bullets: string[]; description: string;
  faqs: { question: string; answer: string }[];
  images: { id: string; width: number; height: number }[];
  listingPrice: string;
};

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function parseDemo(value: unknown, publicId: string): PublicDemo | null {
  if (!object(value) || value.public_id !== publicId || value.mode !== "simulated" || value.status !== "SIMULATED" || !object(value.listing) || value.currency !== "USD" || value.marketplace !== "AMAZON_US") return null;
  const { title, bullets, description } = value.listing;
  if (typeof title !== "string" || typeof description !== "string" || !Array.isArray(bullets) || !bullets.every(item => typeof item === "string") || typeof value.listing_price !== "string" || !/^(?:0|[1-9]\d*)\.\d{2}$/.test(value.listing_price)) return null;
  if (!Array.isArray(value.faqs) || !Array.isArray(value.images) || !value.images.length) return null;
  const faqs: PublicDemo["faqs"] = [];
  const images: PublicDemo["images"] = [];
  for (const item of value.faqs) {
    if (!object(item) || typeof item.question !== "string" || typeof item.answer !== "string") return null;
    faqs.push({ question: item.question, answer: item.answer });
  }
  for (const item of value.images) {
    if (!object(item) || typeof item.id !== "string" || !/^[0-9a-f-]{36}$/.test(item.id) || typeof item.width !== "number" || typeof item.height !== "number" || item.width <= 0 || item.height <= 0) return null;
    images.push({ id: item.id, width: item.width, height: item.height });
  }
  return { title, bullets, description, faqs, images, listingPrice: value.listing_price };
}

async function loadDemo(publicId: string): Promise<{ kind: "ready"; data: PublicDemo } | { kind: "missing" } | { kind: "unavailable" }> {
  try {
    const origin = new URL(process.env.API_ORIGIN ?? "http://127.0.0.1:8000");
    if (!["http:", "https:"].includes(origin.protocol) || origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash) return { kind: "unavailable" };
    const response = await fetch(new URL(`/api/v1/demo-listings/${publicId}`, origin), { cache: "no-store", headers: { Accept: "application/json" }, signal: AbortSignal.timeout(8000) });
    if (response.status === 404) return { kind: "missing" };
    if (!response.ok || !response.headers.get("content-type")?.includes("application/json")) return { kind: "unavailable" };
    const data = parseDemo(await response.json(), publicId);
    return data ? { kind: "ready", data } : { kind: "unavailable" };
  } catch { return { kind: "unavailable" }; }
}

export default async function DemoListingPage({ params }: { params: Promise<{ publicId: string }> }) {
  const { publicId } = await params;
  if (!/^[0-9a-f]{32}$/.test(publicId)) notFound();
  const result = await loadDemo(publicId);
  if (result.kind === "missing") notFound();
  if (result.kind === "unavailable") return <PortalShell><section className={styles.unavailable}><h1>模拟商品页暂时无法读取</h1><p>请稍后重新读取，或回到原订单查看实际发布状态。</p><a className="button button-outline" href={`/demo-listings/${publicId}`}>重新读取 ↻</a></section></PortalShell>;
  const { data } = result;
  return <PortalShell>
    <header className={styles.heading}><p>SIMULATED LISTING · AMAZON US FORMAT</p><h1>模拟商品展示</h1></header>
    <p className={styles.banner}>虚构样例的模拟刊登记录，未发布到 Amazon，不代表真实成交、付款或销量。</p>
    <div className={styles.grid}>
      <div className={styles.gallery}>{data.images.map((image, index) => <figure key={image.id} className={styles.image}><DemoListingImage src={`/api/v1/demo-listings/${publicId}/assets/${image.id}`} alt={`${data.title} — ${index === 0 ? "product image" : "product details"}`} width={image.width} height={image.height} /><figcaption lang="en-US">{index === 0 ? "Product image" : `Product details · ${index + 1}`} · DEMO</figcaption></figure>)}</div>
      <article className={styles.copy} lang="en-US"><p className={styles.eyebrow}>PRODUCT LISTING / EN-US</p><h2>{data.title}</h2><p className={styles.price}><small>USD</small>{data.listingPrice}</p><p className={styles.priceNote} lang="zh-CN">卖家确认的模拟展示售价，无购买或付款功能。</p><h3>Product highlights</h3><ul className={styles.bullets}>{data.bullets.map((bullet, index) => <li key={index}>{bullet}</li>)}</ul><h3>Product description</h3><p className={styles.description}>{data.description}</p>{data.faqs.length > 0 && <><h3>Questions &amp; answers</h3>{data.faqs.map((faq, index) => <details className={styles.faq} key={index}><summary>{faq.question}</summary><p>{faq.answer}</p></details>)}</>}</article>
    </div>
    <div className={styles.footer}><Link className="button button-outline button-small" href="/">返回服务首页</Link><Link className="text-link" href="/orders/new">提交自己的商品资料 ↗</Link></div>
  </PortalShell>;
}
