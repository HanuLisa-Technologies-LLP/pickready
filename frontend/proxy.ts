import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Cookie-presence gate only, the JWT is verified server-side by the backend;
// the Next.js edge cannot validate the signature (no shared secret by design).

/**
 * Which cookies count as "this browser still has a session".
 *
 * Both `pr_access` and `pr_session` are browser-session cookies at path "/".
 * The refresh cookie stays scoped to /api/v1/auth. Presence grants nothing:
 * the page calls /auth/me, which checks the signed JWT and server-side idle
 * deadline. An expired access JWT can refresh while the browser stays open.
 */
const SESSION_COOKIES = ["pr_access", "pr_session"] as const;

function hasSession(request: NextRequest): boolean {
  return SESSION_COOKIES.some((name) => request.cookies.has(name));
}

const PUBLIC_PREFIXES = [
  "/login",
  "/register", // candidate self sign-up (register first, log in later)
  "/docs", // public product and technical documentation
  // THE REST OF THE PUBLIC SITE, WHICH WAS BEING REDIRECTED TO SIGN-IN.
  //
  // This list is a deny-by-default allowlist, and five genuinely public pages
  // were missing from it, so a signed-out visitor asking for any of them got a
  // 307 to /login. Two consequences, and the second is the serious one:
  //
  //  * The site footer links to /about and /insights on every public page, so
  //    the marketing site dead-ended at a sign-in form.
  //  * /privacy and /terms are LEGAL pages. A privacy policy nobody can read
  //    without an account is not a published privacy policy.
  //
  // It was also about to get worse rather than better: `app/robots.ts` now
  // allows all five and `app/sitemap.ts` lists them, so a crawler following the
  // sitemap would have been handed a redirect to a login form for every URL it
  // had just been invited to index.
  //
  // Found by probing the deployed site. Every route below was checked to exist
  // under `app/(public)/`.
  "/about",
  "/insights",
  "/privacy",
  "/terms",
  "/employers", // the public employer directory and each employer page
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
    // `robots.txt`, `sitemap.xml` and `opengraph-image` are EXCLUDED, and this
    // was found in production rather than in a test. They are GENERATED routes
    // rather than files under `public/`, so they fall inside the matcher, and
    // this middleware is deny-by-default: anything outside PUBLIC_PREFIXES
    // without a session is redirected to /login. Every one of them therefore
    // answered a crawler with a 307 to the sign-in page.
    //
    // That is worse than not shipping them at all. `robots.txt` is the one file
    // whose entire job is to be read by something that has no session and never
    // will, so the disallow rules protecting /org, /portal, /admin and the
    // tokenised links were never delivered to anybody.
    // All app routes except static assets and Next internals.
    //
    // `api` is excluded deliberately. Those paths are not pages: they are the
    // same-origin proxy to the backend (next.config.js rewrites), and the
    // backend is the only thing that can validate a token. Letting the
    // deny-by-default branch below see them would answer an unauthenticated
    // API call with a 307 to /login, so the browser would receive an HTML
    // redirect where it expected JSON and every 401-triggered silent refresh
    // would break instead of refreshing.
    //
    // `llms.txt` is excluded for exactly the same reason and was added with
    // the same care: it is a generated route, so it sits inside the matcher,
    // and a file whose only reader is an unauthenticated agent must never be
    // answered with a redirect to a sign-in form.
    //
    // `__/auth` and `__/firebase` are the Firebase Auth helper endpoints,
    // proxied to <project>.firebaseapp.com by the rewrites in next.config.js
    // so the sign-in popup can run on this origin. They are loaded by a
    // browser that BY DEFINITION has no session yet; answering them with a
    // 307 to /login would break every sign-in the moment the auth domain
    // moves to this host.
    // `icon` and `apple-icon` joined this list on 2026-09-20 with the icons
    // themselves. They are GENERATED routes (`app/icon.tsx`,
    // `app/apple-icon.tsx`), so they carry no file extension and the
    // extension clause below does not cover them, exactly like
    // `opengraph-image`. A browser requests a favicon with no session on the
    // very first paint, so without this every tab icon would 307 to /login
    // and render nothing. That is not hypothetical: `robots.txt`,
    // `sitemap.xml` and `opengraph-image` all shipped broken this precise way
    // while every local test passed.
    "/((?!api|__/auth|__/firebase|_next/static|_next/image|favicon.ico|icon|apple-icon|robots.txt|sitemap.xml|llms.txt|opengraph-image|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
