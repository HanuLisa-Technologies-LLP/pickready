"use client";

import { Briefcase, LayoutDashboard, Settings } from "lucide-react";

import { AppShell } from "@/components/app-shell";

export default function RecruiterLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AppShell
      title="Recruiter"
      nav={[
        { href: "/recruiter", label: "Jobs", icon: Briefcase, exact: true },
        { href: "/recruiter/jobs", label: "Job Workspace", icon: Briefcase },
        { href: "/recruiter/dashboard", label: "Dashboard", icon: LayoutDashboard },
        { href: "/recruiter/settings", label: "Settings", icon: Settings },
      ]}
    >
      {children}
    </AppShell>
  );
}
