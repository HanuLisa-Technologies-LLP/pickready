"use client";

import { Briefcase, ListChecks, UserRound } from "lucide-react";

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
        // Order and labels are the client's (2026-07-27): New Jobs, then
        // Applied Jobs, then the unified My Profile (formerly "Settings").
        { href: "/portal", label: "New Jobs", icon: Briefcase, exact: true },
        { href: "/portal/applications", label: "Applied Jobs", icon: ListChecks },
        { href: "/portal/profile", label: "My Profile", icon: UserRound },
      ]}
    >
      {children}
    </AppShell>
  );
}
