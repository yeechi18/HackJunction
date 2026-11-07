import Link from "next/link";
import { PortalShell } from "@/components/portal-shell";
import styles from "@/features/orders/demo-listing.module.css";

export default function DemoListingNotFound() {
  return <PortalShell><section className={styles.unavailable}><h1>未找到可公开的模拟商品页</h1><p>链接可能不完整，或模拟刊登尚未完成。请回到原订单检查实际发布状态；私人订单不会通过这个公开地址展示。</p><Link className="button button-outline" href="/">返回服务首页 ↗</Link></section></PortalShell>;
}
