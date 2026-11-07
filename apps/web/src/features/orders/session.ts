import { ApiError, apiFetch } from "@/lib/api";
import type { SellerSession } from "./types";

// Deduplicate the initial exchange across React Strict Mode effect replays.
// Tokens are held in memory only; the server's HttpOnly cookie restores reloads.
const exchanges = new Map<string, Promise<SellerSession>>();

export async function getSellerSession(orderId: string): Promise<SellerSession> {
  const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
  let session: SellerSession;
  if (token) {
    const key = `${orderId}:${token}`;
    let pending = exchanges.get(key);
    if (!pending) {
      pending = apiFetch<SellerSession>("/order-access/exchange", {
        method: "POST", body: JSON.stringify({ order_id: orderId, token }),
      });
      exchanges.set(key, pending);
      void pending.finally(() => { exchanges.delete(key); }).catch(() => undefined);
    }
    session = await pending;
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}`);
  } else {
    session = await apiFetch<SellerSession>("/order-access/session");
  }
  if (session.order_id !== orderId) throw new ApiError("请使用此订单的完整私有链接。", 401, "http");
  return session;
}
