"use client";

// /org home, jobs are the shared entry point for every org role.

import * as React from "react";
import { useRouter } from "next/navigation";

import { LoadingRows } from "@/components/page-primitives";

export default function OrgHomePage() {
  const router = useRouter();

  React.useEffect(() => {
    router.replace("/org/jobs");
  }, [router]);

  return <LoadingRows rows={3} label="Opening your workspace" />;
}
