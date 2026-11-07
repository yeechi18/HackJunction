import type { Metadata } from "next";
import { ShelfwiseHeader } from "@/components/shelfwise-header";
import { NewOrderForm } from "@/features/orders/new-order";
import styles from "@/features/orders/new-order.module.css";
import "../../shelfwise.css";

export const metadata: Metadata = { title: "提交商品资料｜上架有方 Shelfwise" };

export default function NewOrderPage() {
  return (
    <div className="sw-home">
      <a className="skip-link" href="#sw-main">跳到主要内容</a>
      <ShelfwiseHeader intake />
      <main className={styles.workspace} id="sw-main">
        <NewOrderForm />
      </main>
    </div>
  );
}
