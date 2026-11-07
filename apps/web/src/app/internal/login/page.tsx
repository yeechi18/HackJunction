import type { Metadata } from "next";
import { PortalShell } from "@/components/portal-shell";
import { InternalLoginForm } from "@/features/internal/auth";

export const metadata: Metadata = { title: "负责人登录｜出海工坊", robots: { index: false, follow: false } };

export default function LoginPage() {
  return <PortalShell internal><InternalLoginForm /></PortalShell>;
}
