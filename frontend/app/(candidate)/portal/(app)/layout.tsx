"use client";

import { Briefcase, ListChecks, Settings } from "lucide-react";

import { AppShell } from "@/components/app-shell";

export default function PortalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AppShell
      title="Candidate Portal"
      nav={[
        { href: "/portal", label: "New Jobs", icon: Briefcase, exact: true },
        { href: "/portal/applications", label: "My Applications", icon: ListChecks },
        { href: "/portal/settings", label: "Settings", icon: Settings },
      ]}
    >
      {children}
    </AppShell>
  );
}
