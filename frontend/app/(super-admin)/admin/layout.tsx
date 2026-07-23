"use client";

import { Building2, ScrollText, Settings, ShieldCheck } from "lucide-react";

import { AppShell } from "@/components/app-shell";

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AppShell
      title="Super Admin Console"
      nav={[
        { href: "/admin", label: "Tenants", icon: Building2, exact: true },
        { href: "/admin/permissions", label: "Permissions", icon: ShieldCheck },
        { href: "/admin/audit", label: "Audit Log", icon: ScrollText },
        { href: "/admin/settings", label: "Settings", icon: Settings },
      ]}
    >
      {children}
    </AppShell>
  );
}
