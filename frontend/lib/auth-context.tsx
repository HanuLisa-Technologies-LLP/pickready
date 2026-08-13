"use client";

import * as React from "react";
import {
  apiGet,
  apiPost,
  isAuthError,
  isNetworkError,
  resetRefreshBackoff,
  tryRefresh,
} from "@/lib/api";
import { firebaseAuth } from "@/lib/firebase";
import type { Capability, Role, User } from "@/lib/types";

/**
 * How often a visible tab re-validates its session.
 *
 * The access cookie lives 15 minutes and the refresh cookie 7 days. Without
 * this, a tab left open past the access TTL kept a stale `user` in memory and
 * only discovered the expiry on its next API call, and any transient failure
 * there read as "logged out". Re-validating well inside the access TTL keeps
 * the cookie rotated for as long as the tab is actually being used.
 */
const SESSION_POLL_MS = 10 * 60 * 1000;

/**
 * Routes that render signed-out. A dead session on one of these is normal and
 * must never trigger a redirect (bouncing /login to /login is a reload loop).
 * Kept in step with PUBLIC_PREFIXES in middleware.ts.
 */
const PUBLIC_PREFIXES = [
  "/login",
  "/register",
  "/docs",
  "/join",
  "/apply",
  "/portal/outreach",
  "/verify-employment",
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

  // Keep a live tab's session fresh: rotate on a timer, and again whenever the
  // tab is brought back to the foreground (a laptop that slept through the
  // access TTL comes back with a usable session instead of a dead one).
  React.useEffect(() => {
    if (typeof window === "undefined") return;

    const revalidate = () => {
      if (document.visibilityState !== "visible") return;
      // Nothing to keep alive when nobody is signed in. Polling regardless is
      // what filled the dev logs with an endless /auth/me 401 + /auth/refresh
      // 401 pair from every tab parked on the login page.
      if (!userRef.current) return;
      void tryRefresh().then(() => refreshRef.current());
    };

    const timer = window.setInterval(revalidate, SESSION_POLL_MS);
    document.addEventListener("visibilitychange", revalidate);
    window.addEventListener("online", revalidate);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", revalidate);
      window.removeEventListener("online", revalidate);
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
