"use client";

import * as React from "react";
import { useRouter } from "next/navigation";

import { useAuth, homePathForRole } from "@/lib/auth-context";

export default function RootPage() {
  const { user, loading } = useAuth();
  const router = useRouter();

  React.useEffect(() => {
    if (loading) return;
    if (user) {
      router.replace(homePathForRole(user.role));
    } else {
      router.replace("/login");
    }
  }, [user, loading, router]);

  return (
    <div className="flex min-h-screen items-center justify-center">
      <p className="text-sm text-muted-foreground">Loading PickReady…</p>
    </div>
  );
}
