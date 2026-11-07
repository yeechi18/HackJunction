import type { Metadata } from "next";
import { PortalShell } from "@/components/portal-shell";
import { InternalOrderList } from "@/features/internal/order-list";

export const metadata: Metadata = { title: "订单工作台｜出海工坊", robots: { index: false, follow: false } };

export default function InternalOrdersPage() {
  return <PortalShell internal><InternalOrderList /></PortalShell>;
}
