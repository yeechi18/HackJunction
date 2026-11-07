import type { Metadata } from "next";
import "./globals.css";
import "./portal.css";

export const metadata: Metadata = {
  title: "出海工坊 Listing Studio｜让好商品，说好英语",
  description: "为跨境小卖家设计的 Amazon 美国站商品本地化服务：从中文商品事实，到美式英语文案与图片上架包。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
