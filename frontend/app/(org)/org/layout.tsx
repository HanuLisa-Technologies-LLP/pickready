"use client";

// Unified client-org portal shell (contract rev 2): ONE URL space (/org) for
// Client, HR Manager, Recruiter and Hiring Manager. Nav items are shown per
// CAPABILITY (from auth context), never per role.

import {
  Briefcase,
  Building,
  CheckSquare,
  GitBranch,
  LayoutDashboard,
  ListChecks,
  Mail,
  Settings,
  Users,
} from "lucide-react";

import { useAuth } from "@/lib/auth-context";
import { AppShell, type NavItem } from "@/components/app-shell";

export default function OrgLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { hasCapability } = useAuth();

  const nav = [
    hasCapability("create_company_page")
      ? { href: "/org/company", label: "Company Page", icon: Building }
      : null,
    hasCapability("manage_staff")
      ? { href: "/org/staff", label: "Staff", icon: Users }
      : null,
    hasCapability("configure_approval_levels")
      ? { href: "/org/approval-levels", label: "Approval Levels", icon: GitBranch }
      : null,
    hasCapability("manage_email_templates")
      ? { href: "/org/templates", label: "Email Templates", icon: Mail }
      : null,
    // Jobs are visible to every org user; the create action inside is gated.
    { href: "/org/jobs", label: "Jobs", icon: Briefcase },
    hasCapability("approve_job")
      ? { href: "/org/approvals", label: "Approvals", icon: CheckSquare }
      : null,
    hasCapability("view_review_screen")
      ? { href: "/org/review", label: "Review Screen", icon: ListChecks }
      : null,
    hasCapability("view_dashboard")
      ? { href: "/org/dashboard", label: "Dashboard", icon: LayoutDashboard }
      : null,
    // Settings is always available; the theme toggle lives ONLY here.
    { href: "/org/settings", label: "Settings", icon: Settings },
  ].filter((item) => item !== null) as NavItem[];

  return (
    <AppShell title="Client-Org Portal" nav={nav}>
      {children}
    </AppShell>
  );
}
