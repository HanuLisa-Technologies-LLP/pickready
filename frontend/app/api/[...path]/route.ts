/**
 * Same-origin API proxy: every /api/* call the browser makes is forwarded from
 * here to the backend.
 *
 * WHY A ROUTE HANDLER AND NOT next.config.js `rewrites()`:
 * rewrites are resolved during `next build` and frozen into routes-manifest.json,
 * so a destination read from the environment is captured at BUILD time. That
 * would pin the image to one backend URL, and an unset variable at build time
 * silently produces no rewrite at all (the request then falls through to a 404
 * page). A route handler reads the environment on every REQUEST, so one image
 * is valid in every environment.
 *
 * WHY SAME-ORIGIN AT ALL:
 * the browser never learns the backend's address, so the auth cookies stay
 * same-site and COOKIE_SAMESITE can remain "strict". On Cloud Run the two
 * services land on *.a.run.app, which is on the Public Suffix List, so a split
 * origin is CROSS-site and the browser silently drops every auth cookie.
 * CORS also stops being involved at all.
 */
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Never prerendered, never cached: this is a live pass-through carrying
// per-user credentials, and a cached response would serve one user's data to
// the next.
export const dynamic = "force-dynamic";

/**
 * Hop-by-hop headers, plus the two that describe the wire encoding of the body
 * we are about to re-frame.
 *
 * `content-encoding` and `content-length` MUST be dropped from the backend's
 * response: the backend runs GZipMiddleware, and fetch has already decompressed
 * the payload by the time we read it. Forwarding a `content-encoding: gzip`
 * header alongside a body that is no longer gzipped makes the browser try to
 * inflate plain JSON and fail.
 */
const STRIPPED_RESPONSE_HEADERS = new Set([
  "content-encoding",
  "content-length",
  "transfer-encoding",
  "connection",
  "keep-alive",
]);

/** Headers that describe THIS hop and must not be replayed to the backend. */
const STRIPPED_REQUEST_HEADERS = new Set([
  "host",
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "content-length",
]);

function backendOrigin(): string | null {
  const raw = process.env.BACKEND_INTERNAL_URL;
  if (!raw) return null;
  return raw.replace(/\/+$/, "");
}

async function forward(request: NextRequest): Promise<Response> {
  const origin = backendOrigin();
  if (!origin) {
    // A misconfigured deployment must say so in the logs rather than answer
    // every API call with a confusing 404 page.
    console.error(
      "api-proxy: BACKEND_INTERNAL_URL is not set, cannot forward API requests"
    );
    return NextResponse.json(
      { detail: "The API is not reachable. Please try again shortly." },
      { status: 502 }
    );
  }

  const { pathname, search } = request.nextUrl;
  const target = `${origin}${pathname}${search}`;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!STRIPPED_REQUEST_HEADERS.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  });
  // Let the backend build correct absolute URLs and mark cookies Secure. Cloud
  // Run terminates TLS ahead of both services, so the inbound scheme here is
  // the one the user actually used.
  const forwardedProto =
    request.headers.get("x-forwarded-proto") ?? request.nextUrl.protocol.replace(":", "");
  headers.set("x-forwarded-proto", forwardedProto);
  const host = request.headers.get("host");
  if (host) headers.set("x-forwarded-host", host);

  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      // Streamed rather than buffered so a databank upload of up to 25 resumes
      // does not have to sit in this process's memory in full. `duplex: "half"`
      // is required by undici whenever the body is a stream.
      body: hasBody ? request.body : undefined,
      ...(hasBody ? { duplex: "half" } : {}),
      redirect: "manual",
      cache: "no-store",
    } as RequestInit & { duplex?: "half" });
  } catch (cause) {
    console.error("api-proxy: upstream request failed", cause);
    return NextResponse.json(
      { detail: "We couldn't reach the server. Check your connection and try again." },
      { status: 502 }
    );
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!STRIPPED_RESPONSE_HEADERS.has(key.toLowerCase()) && key.toLowerCase() !== "set-cookie") {
      responseHeaders.set(key, value);
    }
  });

  // Set-Cookie is the one header that legitimately repeats: the backend sets
  // pr_access, pr_refresh and pr_session on a single login response. Headers.set
  // would keep only the last, silently breaking the session, so each is appended
  // individually.
  for (const cookie of upstream.headers.getSetCookie()) {
    responseHeaders.append("set-cookie", cookie);
  }

  // 204 and 304 must not carry a body at all.
  const bodyless = upstream.status === 204 || upstream.status === 304;

  return new Response(bodyless ? null : upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
export const HEAD = forward;
export const OPTIONS = forward;
