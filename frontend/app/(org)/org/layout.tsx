"use client";

// Unified client-org portal shell (PRD v1.0 — FLAT STAFF ROLES). HR Manager,
// Recruiter and Hiring Manager are equal: they all share ONE nav, all can
// create jobs, and all share a single candidate pool. There is NO per-staff-
// role nav gating and NO approval-level configuration (the approval FSM was
// removed — staff-created jobs are published directly).
//
// The only capability-gated items are the two CLIENT-ADMIN functions (Company
// Page, Staff management), which belong to the company owner ("client") rather
// than to the three interchangeable staff roles. Everything staff actually work
// with — Jobs, Review Screen, Dashboard — is shown to every staff member.

import {
  Briefcase,
  Building,
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
    // Client-admin functions (company owner), not part of the flat staff nav.
    hasCapability("create_company_page")
      ? { href: "/org/company", label: "Company Page", icon: Building }
      : null,
    hasCapability("manage_staff")
      ? { href: "/org/staff", label: "Staff", icon: Users }
      : null,
    hasCapability("manage_email_templates")
      ? { href: "/org/templates", label: "Email Templates", icon: Mail }
      : null,
    // Shared staff surface — identical for every staff role (flat).
    { href: "/org/jobs", label: "Jobs", icon: Briefcase },
    { href: "/org/review", label: "Review Screen", icon: ListChecks },
    { href: "/org/dashboard", label: "Dashboard", icon: LayoutDashboard },
    // Settings is always available; the theme toggle lives ONLY here.
    { href: "/org/settings", label: "Settings", icon: Settings },
  ].filter((item) => item !== null) as NavItem[];

  return (
    <AppShell title="Client-Org Portal" nav={nav}>
      {children}
    </AppShell>
  );
}
