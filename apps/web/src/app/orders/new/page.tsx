import type { Metadata } from "next";
import { overseasOrderPath, overseasPackage } from "@/lib/service-packages";
import { CustomsFilingGuide } from "@/features/orders/customs-filing-guide";
import { NewOrderForm } from "@/features/orders/new-order";
import { BrowserCompatibility } from "@/components/browser-compatibility";
import { SellerGate } from "@/features/account/session";
import styles from "@/features/orders/new-order.module.css";

export const metadata: Metadata = { title: "提交商品资料｜上架有方 Shelfwise" };

export default async function NewOrderPage({ searchParams }: { searchParams: Promise<{ package?: string | string[] }> }) {
  const overseas = (await searchParams).package === overseasPackage;
  return (
    <div className="sw-home">
      <main className={styles.workspace} id="sw-main">
        {overseas && <CustomsFilingGuide />}
        <BrowserCompatibility>
          <SellerGate next={overseas ? overseasOrderPath : undefined}>
            <NewOrderForm overseas={overseas} />
          </SellerGate>
        </BrowserCompatibility>
      </main>
    </div>
  );
}
