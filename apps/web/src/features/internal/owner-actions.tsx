import Link from "next/link";
import { factLabels, type InternalOrder } from "@/features/orders/types";
import styles from "./owner-actions.module.css";


export function OwnerActions({ order }: { order: InternalOrder }) {
  const awaitingSeller = order.phase === "AWAITING_SELLER";
  const delivery = order.phase === "AWAITING_RELEASE";
  const missing = order.missing_delivery_fields ?? [];
  const blocked = order.delivery_blockers ?? [];
  const accepting = ["INTAKE", "NEEDS_INFO", "AWAITING_ACCEPTANCE"].includes(order.phase);
  const failed = order.workflow_status === "failed";
  return <section className={styles.panel} aria-label="负责人操作导航">
    <div><h2>{failed ? "发现待处理问题" : delivery ? "交付审批" : accepting ? "接单审批" : awaitingSeller ? "下一步：卖家确认成品" : "负责人操作"}</h2>
      <p>{delivery ? missing.length ? `暂不能放行：请卖家确认${missing.map(k => factLabels[k] ?? k).join("、")}及适用单位。` : blocked.length ? blocked.join(" ") : "请先核对本版文案、图片与审校记录，再决定是否放行交付。" : accepting ? "接单和最终交付是两次独立审批；接单后系统才会开展正式研究与制作。" : awaitingSeller ? "本单已完成负责人放行。请卖家打开交付包确认；负责人无需重复审批。" : "这里可以快速定位本单的审批、研究与制作入口。"}</p>
    </div>
    <nav className={styles.actions} aria-label="审批与制作入口" onClick={event => {
      const link = (event.target as HTMLElement).closest("a");
      const href = link?.getAttribute("href");
      if (!href?.startsWith("#")) return;
      const target = document.getElementById(href.slice(1));
      if (!target) return;
      let parent: HTMLElement | null = target;
      while (parent) {
        if (parent instanceof HTMLDetailsElement) parent.open = true;
        parent = parent.parentElement;
      }
    }}>
      {failed && <Link href={`/internal/operations/runs?order_id=${order.id}`} className="button button-dark button-small">定位问题并处理 ↗</Link>}
      {failed && <a href="#release-review" className="text-link">查看未通过的成品审校 ↓</a>}
      {delivery && <a href="#release-review" className="button button-dark button-small">审核成品并放行 ↓</a>}
      {accepting && <a href="#acceptance-review" className="button button-dark button-small">查看接单决定 ↓</a>}
      {awaitingSeller && <Link href={`/o/${order.id}#seller-confirmation`} className="button button-dark button-small">打开卖家端确认成品 ↗</Link>}
      {missing.length > 0 && <Link href={`/o/${order.id}#delivery-facts`} className="button button-outline button-small">查看卖家待补资料 ↗</Link>}
      <a href="#research-integrations" className="text-link">研究资料与图片制作 ↓</a>
    </nav>
  </section>;
}
