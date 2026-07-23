"use client";

// /org home — jobs are the shared entry point for every org role.

import * as React from "react";
import { useRouter } from "next/navigation";

export default function OrgHomePage() {
  const router = useRouter();

  React.useEffect(() => {
    router.replace("/org/jobs");
  }, [router]);

  return (
    <p className="text-sm text-muted-foreground">Loading your workspace…</p>
  );
}
