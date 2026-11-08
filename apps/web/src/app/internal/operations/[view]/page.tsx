import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PortalShell } from "@/components/portal-shell";
import { InternalOrderList } from "@/features/internal/order-list";
import { operationsViews, type OperationsView } from "@/features/internal/operations-views";

export const metadata: Metadata = { title: "负责人工作台｜出海工坊", robots: { index: false, follow: false }, referrer: "no-referrer" };

export default async function OperationsPage({ params }: { params: Promise<{ view: string }> }) {
  const { view } = await params;
  if (!Object.hasOwn(operationsViews, view) || view === "orders") notFound();
  return <PortalShell internal operations><InternalOrderList key={view} view={view as OperationsView} /></PortalShell>;
}
