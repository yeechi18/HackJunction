import type { Metadata } from "next";
import { PortalShell } from "@/components/portal-shell";
import { SellerWorkspace } from "@/features/orders/seller-workspace";

export const metadata: Metadata = { title: "我的订单｜出海工坊", robots: { index: false, follow: false }, referrer: "no-referrer" };

export default async function SellerOrderPage({ params }: { params: Promise<{ orderId: string }> }) {
  const { orderId } = await params;
  return <PortalShell><SellerWorkspace key={orderId} orderId={orderId} /></PortalShell>;
}
