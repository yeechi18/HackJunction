"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";

export function ShelfwiseHeader() {
  const pathname = usePathname();
  const inOrderDesk = pathname.startsWith("/internal/orders");

  return (
    <div className="sw-home">
      <a className="skip-link" href="#sw-main">跳到主要内容</a>
      <header className="sw-header">
        <Link href="/" className="sw-brand">
          <Image src="/shelfwise-logo.png" width={48} height={38} alt="" className="sw-brand-logo" />
          <b>上架有方<span>Shelfwise</span></b>
        </Link>
        <nav aria-label="主要导航">
          <Link href="/#about">认识我们</Link>
          <Link href="/#packages">服务套餐</Link>
          <Link href="/seller/orders">我的订单</Link>
          <Link href="/seller/login">卖家登录</Link>
          <Link href={inOrderDesk ? "/internal/orders" : "/internal/login"}>
            {inOrderDesk ? "订单工作台" : "负责人入口"} ↗
          </Link>
        </nav>
        <Link className="sw-nav-cta" href="/orders/new" aria-current={pathname === "/orders/new" ? "page" : undefined}>
          开始上架 <span aria-hidden="true">↗</span>
        </Link>
      </header>
      <div className="sw-mobile-account-nav" aria-label="卖家账号导航">
        <Link href="/seller/orders">我的订单</Link><Link href="/seller/login">卖家登录</Link><Link href="/internal/login">负责人入口 ↗</Link>
      </div>
    </div>
  );
}
