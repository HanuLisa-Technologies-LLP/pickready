import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Cookie-presence gate only — the JWT is verified server-side by the backend;
// the Next.js edge cannot validate the signature (no shared secret by design).

const PUBLIC_PREFIXES = [
  "/login",
  "/portal/login",
  "/portal/outreach", // public tokenized outreach completion
  "/verify-employment", // public employer verification form
];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  const isPublic = PUBLIC_PREFIXES.some(
    (p) => pathname === p || pathname.startsWith(p + "/")
  );
  if (isPublic || pathname === "/") {
    return NextResponse.next();
  }

  const hasSession =
    request.cookies.has("pr_access") || request.cookies.has("pr_refresh");
  if (!hasSession) {
    const login = pathname.startsWith("/portal") ? "/portal/login" : "/login";
    const url = request.nextUrl.clone();
    url.pathname = login;
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
