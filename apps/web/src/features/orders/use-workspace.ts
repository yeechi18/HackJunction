"use client";

import { useEffect, useState } from "react";
import { ApiError, apiFetch, errorMessage } from "@/lib/api";
import { getSellerSession } from "./session";
import { pollIntervalFor } from "./workflow-progress";
import type { InternalOrder, Message, Order, OrderEvent, OwnerSession, SellerSession } from "./types";

export type WorkspaceData<T extends Order> = { order: T; messages: Message[]; events: OrderEvent[]; csrfToken: string; checkedAt: string };

export function useWorkspace<T extends Order = Order>(orderId: string, internal = false) {
  const [data, setData] = useState<WorkspaceData<T> | null>(null);
  const [error, setError] = useState("");
  const [accessDenied, setAccessDenied] = useState(false);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let nextDelay = 5000;
    let blocked = false;
    async function load() {

      try {
        const session: OwnerSession | SellerSession = internal
          ? await apiFetch<OwnerSession>("/internal/auth/session", { signal: controller.signal })
          : await getSellerSession(orderId);
        const encodedId = encodeURIComponent(orderId);
        const [order, messages, events] = await Promise.all([
          apiFetch<T>(internal ? `/internal/orders/${encodedId}` : `/orders/${encodedId}`, { signal: controller.signal }),
          apiFetch<{ items: Message[] }>(`/orders/${encodedId}/messages`, { signal: controller.signal }),
          apiFetch<{ items: OrderEvent[] }>(`/orders/${encodedId}/events`, { signal: controller.signal }),
        ]);
        nextDelay = pollIntervalFor(order);
        if (active) {
          setData({ order, messages: messages.items, events: events.items, csrfToken: session.csrf_token, checkedAt: new Date().toLocaleTimeString("zh-CN", { hour12: false }) });
          setError("");
          setAccessDenied(false);
        }
      } catch (caught) {
        if (active) {
          setError(errorMessage(caught));
          const denied = caught instanceof ApiError && [401, 403, 404].includes(caught.status ?? 0);
          blocked = denied;
          setAccessDenied(denied);
          if (denied) setData(null);
        }
      } finally {
        if (active && !blocked) timer = setTimeout(() => void load(), nextDelay);
      }
    }
    void load();
    return () => { active = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [orderId, internal, revision]);

  return { data, error, accessDenied, pollIntervalMs: data ? pollIntervalFor(data.order) : 5000, refresh: () => setRevision(current => current + 1) };
}

export type InternalWorkspaceData = WorkspaceData<InternalOrder>;
