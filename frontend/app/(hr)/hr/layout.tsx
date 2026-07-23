"use client";

import { Briefcase, LayoutDashboard, ListChecks, Settings } from "lucide-react";

import { AppShell } from "@/components/app-shell";

export default function HrLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AppShell
      title="HR Manager"
      nav={[
        { href: "/hr", label: "Jobs", icon: Briefcase, exact: true },
        { href: "/hr/jobs", label: "Job Workspace", icon: Briefcase },
        { href: "/hr/review", label: "Review Screen", icon: ListChecks },
        { href: "/hr/dashboard", label: "Dashboard", icon: LayoutDashboard },
        { href: "/hr/settings", label: "Settings", icon: Settings },
      ]}
    >
      {children}
    </AppShell>
  );
}
