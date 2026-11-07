import type { Metadata } from "next";
import { PortalShell } from "@/components/portal-shell";
import { BrowserCompatibility } from "@/components/browser-compatibility";
import { AccountForm } from "@/features/account/auth-form";

export const metadata: Metadata = { title: "注册卖家账号｜出海工坊" };

export default async function Page({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  return <PortalShell><BrowserCompatibility><AccountForm register next={next} /></BrowserCompatibility></PortalShell>;
}
