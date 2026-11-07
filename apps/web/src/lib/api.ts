export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number | null,
    public readonly kind: "http" | "network" | "timeout" | "invalid_response",
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type ApiRequestOptions = RequestInit & { timeoutMs?: number; idempotencyKey?: string };

function httpMessage(status: number): string {
  if (status === 401) return "登录已失效，请重新登录。";
  if (status === 403) return "你没有执行此操作的权限。";
  if (status === 404) return "未找到请求的内容。";
  if (status === 409) return "内容状态已经变化，请刷新后重试。";
  if (status === 422) return "提交的资料不符合要求，请检查后重试。";
  if (status === 429) return "请求较多，请稍后重试。";
  return "服务暂时不可用，请稍后重试。";
}

/** Same-origin API access. Never expose an upstream address or response diagnostics. */
export async function apiFetch<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("..")) {
    throw new Error("API paths must be relative to /api/v1.");
  }
  const { timeoutMs = 8000, idempotencyKey, signal, ...request } = options;
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    const headers = new Headers(request.headers);
    headers.set("Accept", "application/json");
    if (typeof request.body === "string" && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);
    const response = await fetch(`/api/v1${path}`, {
      ...request,
      headers,
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) throw new ApiError(httpMessage(response.status), response.status, "http");
    if (response.status === 204) return undefined as T;
    if (!response.headers.get("content-type")?.includes("application/json")) {
      throw new ApiError("服务返回的内容暂时无法读取。", response.status, "invalid_response");
    }
    try {
      return (await response.json()) as T;
    } catch {
      throw new ApiError("服务返回的内容暂时无法读取。", response.status, "invalid_response");
    }
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (signal?.aborted) throw error;
    throw new ApiError(
      timedOut ? "连接超时，请稍后重试。" : "暂时无法连接服务，请检查网络后重试。",
      null,
      timedOut ? "timeout" : "network",
    );
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "操作未完成，请稍后重试。";
}

export function csrfHeaders(csrfToken: string): Record<string, string> {
  return { "X-CSRF-Token": csrfToken };
}
