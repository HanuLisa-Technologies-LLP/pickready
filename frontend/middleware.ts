import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Cookie-presence gate only — the JWT is verified server-side by the backend;
// the Next.js edge cannot validate the signature (no shared secret by design).

const PUBLIC_PREFIXES = [
  "/login",
  "/portal/outreach", // public tokenized outreach completion
  "/verify-employment", // public employer verification form
];

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
  if (isPublic || pathname === "/") {
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
