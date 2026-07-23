"use client";

import { Building, GitBranch, Mail, Settings, Users } from "lucide-react";

import { AppShell } from "@/components/app-shell";

export default function ClientLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AppShell
      title="Client Console"
      nav={[
        { href: "/client", label: "Company Page", icon: Building, exact: true },
        { href: "/client/hiring-managers", label: "Hiring Managers", icon: Users },
        { href: "/client/approval-levels", label: "Approval Levels", icon: GitBranch },
        { href: "/client/email-templates", label: "Email Templates", icon: Mail },
        { href: "/client/settings", label: "Settings", icon: Settings },
      ]}
    >
      {children}
    </AppShell>
  );
}
