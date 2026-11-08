import type { Metadata } from "next";
import { PortalShell } from "@/components/portal-shell";
import { InternalOrderWorkspace } from "@/features/internal/order-workspace";

export const metadata: Metadata = { title: "订单详情｜出海工坊", robots: { index: false, follow: false }, referrer: "no-referrer" };

export default async function InternalOrderDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PortalShell internal operations><InternalOrderWorkspace key={id} orderId={id} /></PortalShell>;
}
