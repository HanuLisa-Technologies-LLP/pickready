// Typed fetch wrapper for the ReadyPick backend.
// Routes must match docs/API_CONTRACT.md verbatim.
import { apiErrorMessage } from "./validation-errors";

/**
 * Where the browser sends API calls.
 *
 * SAME-ORIGIN BY DEFAULT. The browser talks only to the Next.js server, which
 * forwards /api/* to the backend (see the rewrite in next.config.js, driven by
 * the runtime BACKEND_INTERNAL_URL). That matters for more than tidiness:
 *
 *   1. Auth cookies stay same-site, so COOKIE_SAMESITE can remain "strict".
 *      On Cloud Run the two services land on *.a.run.app, which is on the
 *      Public Suffix List, so a split origin is CROSS-site and every auth
 *      cookie is silently dropped by the browser.
 *   2. Nothing about the backend's address is baked into the bundle, so one
 *      frontend image is valid in every environment. A NEXT_PUBLIC_ value is
 *      inlined at BUILD time and would pin the image to one backend URL.
 *
 * An absolute NEXT_PUBLIC_API_URL still overrides this for a deliberate
 * split-origin deployment, which then also requires COOKIE_SAMESITE=none.
 */
export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "/api/v1";

/**
 * Origin to prepend for a path that already carries its own /api/... prefix.
 * Empty when API_BASE is relative, which is exactly what keeps the request
 * same-origin. Only an absolute base has an origin to strip back to.
 */
const API_ORIGIN = /^https?:\/\//i.test(API_BASE)
  ? new URL(API_BASE).origin
  : "";

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
  /** Multipart form data, takes precedence over body */
  formData?: FormData;
  /** Skip the automatic refresh-and-retry on 401 (used for auth endpoints) */
  skipRefresh?: boolean;
  signal?: AbortSignal;
}

/**
 * Status used for a request that never reached the server (DNS failure, the
 * backend restarting, the machine offline). Callers MUST treat this differently
 * from a 401: a transport failure says nothing about whether the session is
 * still valid, and treating it as "logged out" bounces a signed-in user to the
 * login screen every time the API blips.
 */
export const NETWORK_ERROR = 0;

export function isNetworkError(error: unknown): boolean {
  return error instanceof ApiError && error.status === NETWORK_ERROR;
}

/** A definite "your session is not valid" answer FROM the server. */
export function isAuthError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
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
  const base = path.startsWith("/api/") ? API_ORIGIN : API_BASE;
  try {
    return await fetch(`${base}${path}`, {
      method: opts.method ?? (body !== undefined ? "POST" : "GET"),
      headers,
      body,
      credentials: "include",
      // Authenticated resources are workspace-relative even when their URL is
      // identical. Never let the browser reuse tenant A's response after the
      // session cookie has switched to tenant B.
      cache: "no-store",
      signal: opts.signal,
    });
  } catch (cause) {
    // An aborted request is the caller's own doing, surface it unchanged so
    // cleanup logic can still recognise an AbortError.
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(
      NETWORK_ERROR,
      null,
      "We couldn't reach the server. Check your connection and try again."
    );
  }
}

let refreshPromise: Promise<boolean> | null = null;
/**
 * When the last refresh definitively failed. A failed refresh means the refresh
 * cookie is gone or expired, and nothing about that changes in the next few
 * seconds. Without this, a page that fires eight calls on mount answers eight
 * 401s with eight more doomed refreshes, and the user waits through all of them
 * before seeing the login screen.
 */
let refreshFailedAt = 0;
const REFRESH_FAILURE_COOLDOWN_MS = 5000;

/** Cleared by a successful sign-in so a new session is not held back. */
export function resetRefreshBackoff(): void {
  refreshFailedAt = 0;
}

/**
 * Rotate the access cookie using the refresh cookie. Concurrent callers share
 * ONE in-flight request: refresh-token rotation invalidates the previous token,
 * so N parallel refreshes would race and all but one would be rotating a token
 * that had already been replaced.
 */
export async function tryRefresh(): Promise<boolean> {
  if (
    refreshFailedAt &&
    Date.now() - refreshFailedAt < REFRESH_FAILURE_COOLDOWN_MS
  ) {
    return false;
  }
  if (!refreshPromise) {
    const inFlight = rawFetch("/auth/refresh", {
      method: "POST",
      skipRefresh: true,
    })
      .then((r) => {
        if (r.ok) {
          refreshFailedAt = 0;
        } else {
          refreshFailedAt = Date.now();
        }
        return r.ok;
      })
      .catch(() => {
        // A transport failure is NOT a dead session, so it does not arm the
        // cooldown; the caller simply gets "not refreshed" for this attempt.
        return false;
      })
      .finally(() => {
        // Release on the next microtask, not immediately: callers that already
        // awaited this promise must all observe the same result before a fresh
        // attempt can start.
        setTimeout(() => {
          refreshPromise = null;
        }, 0);
      });
    refreshPromise = inFlight;
  }
  return refreshPromise;
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  let res = await rawFetch(path, opts);

  // Silent refresh: one refresh, one retry, then give up. 401 is the only
  // status that means "your access token expired", a 403 is a permission
  // answer about a session that is perfectly valid, and refreshing it would
  // just burn a rotation and retry into the same 403.
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
    const error = new ApiError(res.status, detail);
    error.message = apiErrorMessage(error);
    throw error;
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
export const apiPatch = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "PATCH", body });
export const apiDelete = <T = void>(path: string) =>
  api<T>(path, { method: "DELETE" });
export const apiUpload = <T>(path: string, formData: FormData) =>
  api<T>(path, { method: "POST", formData });

/** Multipart upload with byte-level progress. Cookies remain the auth source. */
export function apiUploadWithProgress<T>(
  path: string,
  formData: FormData,
  onProgress: (percent: number) => void,
  method: "POST" | "PUT" = "POST",
): Promise<T> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open(method, `${API_BASE}${path}`);
    request.withCredentials = true;
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.min(99, Math.round((event.loaded / event.total) * 100)));
      }
    };
    request.onerror = () => reject(new ApiError(0, null, "Network error. Check your connection and retry."));
    request.onabort = () => reject(new ApiError(0, null, "Upload cancelled. Please retry."));
    request.onload = () => {
      let payload: unknown = null;
      try {
        payload = request.responseText ? JSON.parse(request.responseText) : undefined;
      } catch {
        payload = null;
      }
      if (request.status < 200 || request.status >= 300) {
        const error = new ApiError(request.status, payload);
        error.message = apiErrorMessage(error);
        reject(error);
        return;
      }
      onProgress(100);
      resolve(payload as T);
    };
    request.send(formData);
  });
}
