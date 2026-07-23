"use client";

import { Briefcase, CheckSquare, Settings, Users } from "lucide-react";

import { AppShell } from "@/components/app-shell";

export default function HiringManagerLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AppShell
      title="Hiring Manager"
      nav={[
        { href: "/hm", label: "Jobs", icon: Briefcase, exact: true },
        { href: "/hm/approvals", label: "My Approvals", icon: CheckSquare },
        { href: "/hm/profiles", label: "Granted Profiles", icon: Users },
        { href: "/hm/settings", label: "Settings", icon: Settings },
      ]}
    >
      {children}
    </AppShell>
  );
}
