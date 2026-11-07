import type { Metadata } from "next";
import { PortalShell } from "@/components/portal-shell";
import { BrowserCompatibility } from "@/components/browser-compatibility";
import { MyOrders } from "@/features/account/order-list";

export const metadata: Metadata = { title: "我的订单｜出海工坊" };

export default function Page() {
  return <PortalShell><BrowserCompatibility><MyOrders /></BrowserCompatibility></PortalShell>;
}
