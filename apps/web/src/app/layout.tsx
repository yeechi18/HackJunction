import type { Metadata } from "next";
import "./globals.css";
import "./portal.css";

export const metadata: Metadata = {
  title: "上架有方 Shelfwise｜让好商品，走向世界",
  description: "上架有方 Shelfwise，为跨境小卖家提供 Amazon 美国站商品本地化上架服务，从中文资料到英文文案、图片与完整上架资料包。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
