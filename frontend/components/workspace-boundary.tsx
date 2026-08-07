"use client";

import type { ReactNode } from "react";

import type { User } from "@/lib/types";

export function workspaceSessionKey(user: User | null): string {
  if (!user) return "signed-out";
  return `${user.id}:${user.tenant_id ?? "global"}:${user.role}`;
}

/**
 * Remount every page-local state container when the selected user/tenant
 * changes. This is deliberately independent of navigation: a workspace switch
 * inside the same portal and route still destroys tenant A's pending requests,
 * state and rendered DOM before tenant B is shown.
 */
export function WorkspaceContentBoundary({
  user,
  children,
}: {
  user: User | null;
  children: ReactNode;
}) {
  const sessionKey = workspaceSessionKey(user);
  return (
    <div key={sessionKey} data-workspace-session={sessionKey}>
      {children}
    </div>
  );
}
