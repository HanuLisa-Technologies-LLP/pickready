/**
 * The hooks half of the permission layer. See `lib/permissions.ts` for the
 * rules; this file is the part that reads the live session, which is why it
 * is a client module and the other one is not.
 */
"use client";

import * as React from "react";

import { useAuth } from "@/lib/auth-context";
import type { Capability } from "@/lib/types";

export interface Permissions {
  /** True when the resolved capability set contains this capability. */
  can: (capability: Capability) => boolean;
  /** True while the capability set is still being fetched. */
  loading: boolean;
  /** The resolved set, for the rare component that needs to enumerate. */
  capabilities: Capability[];
}

/**
 * The hook every permission-aware component uses.
 *
 * One line thinner than calling `useAuth().hasCapability` directly, and the
 * thinness is the point: components import from here, so the day a capability
 * needs a resource-scoped answer there is one file to change rather than
 * forty call sites to find.
 */
export function usePermissions(): Permissions {
  const { hasCapability, capabilities, loading } = useAuth();
  return React.useMemo(
    () => ({ can: hasCapability, loading, capabilities }),
    [hasCapability, loading, capabilities]
  );
}

/** `usePermissions().can`, for a component that needs nothing else. */
export function useCan(): (capability: Capability) => boolean {
  return usePermissions().can;
}
