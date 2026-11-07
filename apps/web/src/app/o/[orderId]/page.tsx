import type { Metadata } from "next";
import styles from "@/features/orders/seller-workspace.module.css";
import { SellerWorkspace } from "@/features/orders/seller-workspace";

export const metadata: Metadata = { title: "订单会话｜上架有方 Shelfwise", robots: { index: false, follow: false }, referrer: "no-referrer" };

export default async function SellerOrderPage({ params }: { params: Promise<{ orderId: string }> }) {
  const { orderId } = await params;
  return (
    <div className="sw-home">
      <main className={styles.workspace} id="sw-main">
        <SellerWorkspace key={orderId} orderId={orderId} />
      </main>
    </div>
  );
}
