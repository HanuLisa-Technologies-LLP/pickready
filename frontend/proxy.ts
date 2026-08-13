import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Cookie-presence gate only, the JWT is verified server-side by the backend;
// the Next.js edge cannot validate the signature (no shared secret by design).

/**
 * Which cookies count as "this browser still has a session".
 *
 * `pr_access` is deleted by the browser the moment its 15-minute Max-Age
 * lapses. `pr_refresh` is path-scoped to /api/v1/auth so it is never sent to a
 * page request like /org/jobs, which means this middleware CANNOT see it, the
 * old `cookies.has("pr_refresh")` check here was dead code that never once
 * returned true. The result: an idle user with a perfectly valid 7-day refresh
 * token was redirected to /login on their next click, before the API client
 * ever got the chance to refresh silently.
 *
 * `pr_session` fixes that. It is set and cleared by the backend alongside the
 * refresh token, lives at path "/", and holds no token material at all, it
 * only says a refresh token exists. Presence still grants nothing: the page it
 * admits calls /auth/me, and a session that cannot refresh is cleared there.
 */
const SESSION_COOKIES = ["pr_access", "pr_session"] as const;

function hasSession(request: NextRequest): boolean {
  return SESSION_COOKIES.some((name) => request.cookies.has(name));
}

const PUBLIC_PREFIXES = [
  "/login",
  "/register", // candidate self sign-up (register first, log in later)
  "/docs", // public product and technical documentation
  "/join", // tokenized staff invitation acceptance
  // Public job application link. The JD must be readable WITHOUT an account
  // (FR-3.5); the page itself gates submission on a verified candidate
  // session, so letting it render signed-out grants nothing.
  "/apply",
  "/portal/outreach", // public tokenized outreach completion
  "/verify-employment", // public employer verification form
  // Assessment invitation landing. It MUST render signed-out: its whole
  // job is to resolve the token and then send the candidate through
  // /login carrying itself as `next`. Gating it here would bounce them
  // to a login with no destination, which is the bug it exists to fix.
  "/assessments/invite",
];

const PORTAL_BY_ROLE: Record<string, string> = {
  super_admin: "/admin",
  // Business Development is the fourth portal. A bd token legitimately carries
  // the OWNER audience (it is a platform console, not a tenant one), so the
  // audience map below would send them to /admin. The ROLE lookup is tried
  // first in portalFromAccessToken and wins, which is what keeps them on /bd.
  bd: "/bd",
  candidate: "/portal",
  client: "/org",
  recruitment_manager: "/org",
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

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // ONE login page for every role (contract rev 2), the old candidate
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
    // Forward a signed-in visitor straight to their portal. This needs the
    // ACCESS cookie specifically, because the role lives in it; the presence
    // hint says a session exists but not whose. When only the hint is left, the
    // landing page renders as usual and its "Sign in" link resumes the session
    // through the normal refresh path. Guessing a portal here would send an
    // owner to /org and produce a 403 for no reason.
    const access = request.cookies.get("pr_access")?.value;
    if (!access) return NextResponse.next();

    const url = request.nextUrl.clone();
    url.pathname = portalFromAccessToken(access) ?? "/login";
    return NextResponse.redirect(url);
  }

  if (isPublic) {
    return NextResponse.next();
  }

  // /admin, /bd, /org and /portal all require an auth cookie. This is a
  // deny-by-default list: everything the matcher sees that is not in
  // PUBLIC_PREFIXES needs a session, so /bd is covered without an entry.
  if (!hasSession(request)) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    // Carry the QUERY too, not just the path. A destination like
    // /portal/assessments/<id>?from=email loses its meaning without it, and
    // the whole point of `next` is that the person lands where they were
    // going rather than on a generic dashboard.
    url.searchParams.set("next", pathname + request.nextUrl.search);
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    // All app routes except static assets and Next internals.
    //
    // `api` is excluded deliberately. Those paths are not pages: they are the
    // same-origin proxy to the backend (next.config.js rewrites), and the
    // backend is the only thing that can validate a token. Letting the
    // deny-by-default branch below see them would answer an unauthenticated
    // API call with a 307 to /login, so the browser would receive an HTML
    // redirect where it expected JSON and every 401-triggered silent refresh
    // would break instead of refreshing.
    "/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
