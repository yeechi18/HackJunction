import Link from "next/link";
import { factLabels, type InternalOrder } from "@/features/orders/types";
import styles from "./owner-actions.module.css";
import { operationsHref } from "./operations-views";

export function OwnerActions({ order }: { order: InternalOrder }) {
  const delivery = order.phase === "AWAITING_RELEASE";
  const missing = order.missing_delivery_fields ?? [];
  const blocked = order.delivery_blockers ?? [];
  const accepting = ["INTAKE", "NEEDS_INFO", "AWAITING_ACCEPTANCE"].includes(order.phase);
  const failed = order.workflow_status === "failed";
  return <section className={styles.panel} aria-label="负责人操作导航">
    <div><h2>{failed ? "任务已暂停，尚不能放行" : delivery ? "交付审批" : accepting ? "接单审批" : "负责人操作"}</h2>
      <p>{delivery ? missing.length ? `暂不能放行：请卖家确认${missing.map(k => factLabels[k] ?? k).join("、")}及适用单位。` : blocked.length ? blocked.join(" ") : "请先核对本版文案、图片与审校记录，再决定是否放行交付。" : accepting ? "接单和最终交付是两次独立审批；接单后系统才会开展正式研究与制作。" : "这里可以快速定位本单的审批、研究与制作入口。"}</p>
    </div>
    <nav className={styles.actions} aria-label="审批与制作入口">
      {failed && <a href="#model-budget" className="button button-dark button-small">查看用量授权与恢复 ↓</a>}
      {failed && <a href="#release-review" className="text-link">查看未通过的成品审校 ↓</a>}
      {delivery && <a href="#release-review" className="button button-dark button-small">查看放行审批 ↓</a>}
      {accepting && <Link href={operationsHref("approvals")} className="button button-dark button-small">前往接单审批 ↗</Link>}
      {missing.length > 0 && <Link href={`/o/${order.id}#delivery-facts`} className="button button-outline button-small">查看卖家待补资料 ↗</Link>}
      <a href="#research-integrations" className="text-link">研究资料与图片制作 ↓</a>
    </nav>
  </section>;
}
