"use client";

import * as React from "react";
import { usePathname } from "next/navigation";
import {
  apiGet,
  apiPost,
  isAuthError,
  isNetworkError,
  onForbidden,
  resetRefreshBackoff,
} from "@/lib/api";
import { firebaseAuth } from "@/lib/firebase";
import { installInteractionTracking, markInteraction } from "@/lib/user-activity";
import type { Capability, Role, User } from "@/lib/types";

/**
 * How often a visible tab re-validates its session.
 *
 * A user action may validate the session at most once per interval. A passive
 * tab must never keep the server's thirty-minute idle deadline alive.
 */
const ACTIVITY_REVALIDATE_MS = 5 * 60 * 1000;

/**
 * The shortest gap between two capability revalidations triggered by
 * NAVIGATION rather than by the timer.
 *
 * Section 36 of the 2026-09-13 spec asks for permission refresh to be
 * reliable, and the ten-minute timer alone is not: an administrator who grants
 * somebody edit access and says "try it now" should not be met with "wait ten
 * minutes or reload". Revalidating when the person navigates covers that,
 * because the first thing they do is go to the page. Throttling it stops a
 * tab-happy user issuing one /auth/me per click.
 */
const NAVIGATION_REVALIDATE_MS = 60 * 1000;

/**
 * Routes that render signed-out. A dead session on one of these is normal and
 * must never trigger a redirect (bouncing /login to /login is a reload loop).
 *
 * THE SAME LIST AS `PUBLIC_PREFIXES` IN `proxy.ts`, and
 * `lib/public-routes.test.ts` compares the two. This one had fallen behind:
 * the proxy admitted /keep-profile, /employers and the legal pages signed-out,
 * and then this provider answered the 401 from /auth/me by sending the visitor
 * to /login anyway. For /keep-profile that is the renewal link in a letter to
 * somebody who has not signed in for six months, bounced to a password form.
 */
const PUBLIC_PREFIXES = [
  "/login",
  "/register",
  "/docs",
  "/about",
  "/insights",
  "/privacy",
  "/terms",
  "/employers",
  "/join",
  "/apply",
  "/verify-employment",
  "/keep-profile",
  // Assessment invitation landing. It MUST render signed-out: its whole
  // job is to resolve the token and then send the candidate through
  // /login carrying itself as `next`. Gating it here would bounce them
  // to a login with no destination, which is the bug it exists to fix.
  "/assessments/invite",
];

function isPublicPath(pathname: string): boolean {
  if (pathname === "/") return true;
  return PUBLIC_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(prefix + "/")
  );
}

interface AuthContextValue {
  user: User | null;
  /** Capabilities from the RBAC engine ("*" = owner/all). Empty until loaded. */
  capabilities: Capability[];
  loading: boolean;
  refresh: () => Promise<void>;
  /** Store the authenticated session (verify / select-context responses). */
  setSession: (user: User | null, capabilities?: Capability[]) => void;
  hasCapability: (capability: Capability) => boolean;
  logout: () => Promise<void>;
}

const AuthContext = React.createContext<AuthContextValue>({
  user: null,
  capabilities: [],
  loading: true,
  refresh: async () => {},
  setSession: () => {},
  hasCapability: () => false,
  logout: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = React.useState<User | null>(null);
  const [capabilities, setCapabilities] = React.useState<Capability[]>([]);

  const [loading, setLoading] = React.useState(true);

  const refresh = React.useCallback(async () => {
    try {
      // GET /auth/me returns {user, capabilities} (contract rev 2). `api()`
      // already retries once through /auth/refresh on a 401, so reaching the
      // catch below means the refresh cookie could not save us either.
      const res = await apiGet<{ user: User; capabilities?: Capability[] }>(
        "/auth/me"
      );
      setUser(res.user);
      setCapabilities(res.capabilities ?? []);
    } catch (error) {
      // Only a definite answer FROM the server ends the session. A network
      // failure, the API restarting, a dropped connection, the laptop waking
      // up, says nothing about whether the session is still valid, and
      // clearing `user` here is what bounced signed-in users to a login screen
      // and made them think they had been logged out.
      if (isNetworkError(error) && user) return;
      if (!isAuthError(error) && user) return;
      setUser(null);
      setCapabilities([]);
      // The server has definitively refused the session and the silent refresh
      // inside api() could not save it. On a protected route there is nothing
      // left to render, so send them to sign in once, remembering where they
      // were. Public routes render signed-out and are left alone.
      if (
        typeof window !== "undefined" &&
        !isPublicPath(window.location.pathname)
      ) {
        const next = encodeURIComponent(
          window.location.pathname + window.location.search
        );
        window.location.replace(`/login?next=${next}`);
      }
    } finally {
      setLoading(false);
    }
  }, [user]);

  // `refresh` closes over `user`, so keep a stable handle for the effects below
  //, otherwise every session change would tear down and restart the timer.
  const refreshRef = React.useRef(refresh);
  refreshRef.current = refresh;
  const userRef = React.useRef(user);
  userRef.current = user;

  React.useEffect(() => {
    void refreshRef.current();
  }, []);

  // ── Keeping the capability snapshot honest (spec section 36) ─────────────
  //
  // The server resolves permissions per request, so it is never stale. The
  // CLIENT's copy is, between polls, and the two disagreeing is what produces
  // a control that is offered and then refused. Two cheap triggers close most
  // of that window: a navigation (the upgrade direction, where somebody has
  // just been granted access and goes to use it) and a 403 (the revocation
  // direction, where the server has just told us we are out of date).
  const lastRevalidateRef = React.useRef(0);
  const revalidateCapabilities = React.useCallback((force: boolean) => {
    if (!userRef.current) return;
    const now = Date.now();
    if (!force && now - lastRevalidateRef.current < NAVIGATION_REVALIDATE_MS) {
      return;
    }
    lastRevalidateRef.current = now;
    void refreshRef.current();
  }, []);

  const pathname = usePathname();
  React.useEffect(() => {
    revalidateCapabilities(false);
  }, [pathname, revalidateCapabilities]);

  React.useEffect(
    () => onForbidden(() => revalidateCapabilities(true)),
    [revalidateCapabilities]
  );

  // Only actual interaction renews the idle deadline. A timer or a bare
  // visibility event would keep a forgotten tab signed in indefinitely.
  //
  // The server renews only for a request carrying the activity header, which
  // `lib/api.ts` attaches within a few seconds of a recorded interaction. The
  // tracking is installed ONCE here, in the capture phase, so the mark lands
  // before any page handler fires its request. The revalidation below marks
  // explicitly as well: it runs on an interaction by construction, and its
  // /auth/me is what keeps a person who is reading or typing (and so sending
  // no other request) signed in.
  React.useEffect(() => installInteractionTracking(), []);

  React.useEffect(() => {
    if (typeof window === "undefined") return;
    let lastValidation = Date.now();
    const onActivity = () => {
      if (!userRef.current || document.visibilityState !== "visible") return;
      const now = Date.now();
      if (now - lastValidation < ACTIVITY_REVALIDATE_MS) return;
      lastValidation = now;
      markInteraction(now);
      void refreshRef.current();
    };
    window.addEventListener("pointerdown", onActivity);
    window.addEventListener("keydown", onActivity);
    return () => {
      window.removeEventListener("pointerdown", onActivity);
      window.removeEventListener("keydown", onActivity);
    };
  }, []);

  const setSession = React.useCallback(
    (u: User | null, caps: Capability[] = []) => {
      // A fresh sign-in means new cookies: drop any cooldown left over from the
      // expired session, or the first call after login would skip its refresh.
      if (u) resetRefreshBackoff();
      setUser(u);
      setCapabilities(u ? caps : []);
    },
    []
  );

  const hasCapability = React.useCallback(
    (capability: Capability) =>
      capabilities.includes("*") || capabilities.includes(capability),
    [capabilities]
  );

  const logout = React.useCallback(async () => {
    try {
      await apiPost("/auth/logout");
    } catch {
      /* ignore */
    }
    // Also clear the Firebase session so a subsequent login starts clean.
    try {
      await firebaseAuth.signOut();
    } catch {
      /* ignore */
    }
    setUser(null);
    setCapabilities([]);
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        capabilities,
        loading,
        refresh,
        setSession,
        hasCapability,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return React.useContext(AuthContext);
}

/**
 * Four portals, one login (contract rev 2, BD added 2026-07-28):
 * Owner → /admin, Business Development → /bd, Candidate → /portal,
 * every client-org role → /org.
 *
 * A bd session carries the OWNER token audience because the BD console is a
 * platform console, so the ROLE is the only thing that separates it from the
 * Provider Portal here. Keep the two cases distinct.
 */
export function homePathForRole(role: Role): string {
  switch (role) {
    case "super_admin":
      return "/admin";
    case "bd":
      return "/bd";
    case "candidate":
      return "/portal";
    case "client":
    case "recruitment_manager":
    case "hr_manager":
    case "recruiter":
    case "hiring_manager":
      return "/org";
    default:
      return "/login";
  }
}
