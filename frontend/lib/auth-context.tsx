"use client";

import * as React from "react";
import { apiGet, apiPost } from "@/lib/api";
import { firebaseAuth } from "@/lib/firebase";
import type { Capability, Role, User } from "@/lib/types";

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
      // GET /auth/me returns {user, capabilities} (contract rev 2).
      const res = await apiGet<{ user: User; capabilities?: Capability[] }>(
        "/auth/me"
      );
      setUser(res.user);
      setCapabilities(res.capabilities ?? []);
    } catch {
      setUser(null);
      setCapabilities([]);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void refresh();
  }, [refresh]);

  const setSession = React.useCallback(
    (u: User | null, caps: Capability[] = []) => {
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
 * Three portals, one login (contract rev 2):
 * Owner → /admin, Candidate → /portal, every client-org role → /org.
 */
export function homePathForRole(role: Role): string {
  switch (role) {
    case "super_admin":
      return "/admin";
    case "candidate":
      return "/portal";
    case "client":
    case "hr_manager":
    case "recruiter":
    case "hiring_manager":
      return "/org";
    default:
      return "/login";
  }
}
