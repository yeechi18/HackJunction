import Link from "next/link";

export function ShelfwiseHeader({ intake = false }: { intake?: boolean }) {
  return (
    <header className="sw-header">
      <Link href="/" className="sw-brand">
        <span className="sw-monogram" aria-hidden="true">S↗</span>
        <b>上架有方<span>Shelfwise</span></b>
      </Link>
      <nav aria-label="主要导航">
        <Link href="/#about">认识我们</Link>
        <Link href="/#packages">服务套餐</Link>
        <Link href="/internal/login">负责人入口 ↗</Link>
      </nav>
      <Link className="sw-nav-cta" href="/orders/new" aria-current={intake ? "page" : undefined}>
        开始上架 <span aria-hidden="true">↗</span>
      </Link>
    </header>
  );
}
