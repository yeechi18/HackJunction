import type { Metadata } from "next";
import { NewOrderForm } from "@/features/orders/new-order";
import styles from "@/features/orders/new-order.module.css";

export const metadata: Metadata = { title: "提交商品资料｜上架有方 Shelfwise" };

export default function NewOrderPage() {
  return (
    <div className="sw-home">
      <main className={styles.workspace} id="sw-main">
        <NewOrderForm />
      </main>
    </div>
  );
}
