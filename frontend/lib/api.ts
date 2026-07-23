// Typed fetch wrapper for the PickReady backend.
// Routes must match docs/API_CONTRACT.md verbatim.

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown, message?: string) {
    super(message ?? `API error ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  body?: unknown;
  /** Multipart form data — takes precedence over body */
  formData?: FormData;
  /** Skip the automatic refresh-and-retry on 401 (used for auth endpoints) */
  skipRefresh?: boolean;
  signal?: AbortSignal;
}

async function rawFetch(path: string, opts: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;
  if (opts.formData) {
    body = opts.formData;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  return fetch(`${API_BASE}${path}`, {
    method: opts.method ?? (body !== undefined ? "POST" : "GET"),
    headers,
    body,
    credentials: "include",
    signal: opts.signal,
  });
}

let refreshPromise: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = rawFetch("/auth/refresh", { method: "POST", skipRefresh: true })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        setTimeout(() => {
          refreshPromise = null;
        }, 0);
      });
  }
  return refreshPromise;
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  let res = await rawFetch(path, opts);

  if (res.status === 401 && !opts.skipRefresh) {
    const refreshed = await tryRefresh();
    if (refreshed) {
      res = await rawFetch(path, opts);
    }
  }

  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      /* non-JSON error body */
    }
    const message =
      detail && typeof detail === "object" && "detail" in (detail as object)
        ? typeof (detail as { detail: unknown }).detail === "string"
          ? ((detail as { detail: string }).detail as string)
          : `Request failed (${res.status})`
        : `Request failed (${res.status})`;
    throw new ApiError(res.status, detail, message);
  }

  if (res.status === 204) {
    return undefined as T;
  }
  const text = await res.text();
  if (!text) {
    return undefined as T;
  }
  return JSON.parse(text) as T;
}

export const apiGet = <T>(path: string) => api<T>(path, { method: "GET" });
export const apiPost = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body });
export const apiPut = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "PUT", body });
export const apiDelete = <T = void>(path: string) =>
  api<T>(path, { method: "DELETE" });
export const apiUpload = <T>(path: string, formData: FormData) =>
  api<T>(path, { method: "POST", formData });
