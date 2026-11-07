import type { Metadata } from "next";
import { PortalShell } from "@/components/portal-shell";
import { BrowserCompatibility } from "@/components/browser-compatibility";
import { SellerGate } from "@/features/account/session";
import { NewOrderForm } from "@/features/orders/new-order";

export const metadata: Metadata = { title: "提交商品资料｜出海工坊" };

export default function NewOrderPage() {
  return <PortalShell><BrowserCompatibility><SellerGate><NewOrderForm /></SellerGate></BrowserCompatibility></PortalShell>;
}
