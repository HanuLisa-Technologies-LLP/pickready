import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Cookie-presence gate only — the JWT is verified server-side by the backend;
// the Next.js edge cannot validate the signature (no shared secret by design).

const PUBLIC_PREFIXES = [
  "/login",
  "/register", // candidate self sign-up (register first, log in later)
  "/portal/outreach", // public tokenized outreach completion
  "/verify-employment", // public employer verification form
];

const PORTAL_BY_ROLE: Record<string, string> = {
  super_admin: "/admin",
  candidate: "/portal",
  client: "/org",
  hr_manager: "/org",
  recruiter: "/org",
  hiring_manager: "/org",
};

const PORTAL_BY_AUDIENCE: Record<string, string> = {
  "pickready:owner": "/admin",
  "pickready:org": "/org",
  "pickready:candidate": "/portal",
};

/**
 * Routing hint only: the backend remains the sole JWT verifier. Decoding here
 * selects one fixed, safe destination and never grants access to a route.
 */
function portalFromAccessToken(token: string | undefined): string | undefined {
  if (!token) return undefined;
  try {
    const encoded = token.split(".")[1];
    if (!encoded) return undefined;
    const base64 = encoded.replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(
      atob(base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "="))
    ) as { role?: string; aud?: string };
    return (
      (payload.role ? PORTAL_BY_ROLE[payload.role] : undefined) ??
      (payload.aud ? PORTAL_BY_AUDIENCE[payload.aud] : undefined)
    );
  } catch {
    return undefined;
  }
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // ONE login page for every role (contract rev 2) — the old candidate
  // login URL permanently redirects to /login.
  if (pathname === "/portal/login" || pathname.startsWith("/portal/login/")) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }

  const isPublic = PUBLIC_PREFIXES.some(
    (p) => pathname === p || pathname.startsWith(p + "/")
  );
  if (pathname === "/") {
    const hasSession =
      request.cookies.has("pr_access") || request.cookies.has("pr_refresh");
    if (!hasSession) return NextResponse.next();

    const url = request.nextUrl.clone();
    url.pathname = portalFromAccessToken(request.cookies.get("pr_access")?.value) ?? "/login";
    return NextResponse.redirect(url);
  }

  if (isPublic) {
    return NextResponse.next();
  }

  // /admin, /org and /portal all require an auth cookie.
  const hasSession =
    request.cookies.has("pr_access") || request.cookies.has("pr_refresh");
  if (!hasSession) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    // All app routes except static assets and Next internals
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
