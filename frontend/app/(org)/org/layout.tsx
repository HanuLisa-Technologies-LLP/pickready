"use client";

// Unified client-org portal shell (PRD v1.0, FLAT STAFF ROLES). HR Manager,
// Recruiter and Hiring Manager are equal: they all share ONE nav, all can
// create jobs, and all share a single candidate pool. There is NO per-staff-
// role nav gating and NO approval-level configuration (the approval FSM was
// removed, staff-created jobs are published directly).
//
// The only capability-gated items are the two CLIENT-ADMIN functions (Company
// Page, Staff management), which belong to the company owner ("client") rather
// than to the three interchangeable staff roles. Everything staff actually work
// with, Jobs, Review Screen, Dashboard, is shown to every staff member.

import {
  Briefcase,
  Building,
  CreditCard,
  FileText,
  LayoutDashboard,
  Settings,
  ShieldCheck,
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
    // Compliance & legal records (Provider Portal spec §3). Capability-gated
    // to the Company Admin by default, a GSTIN certificate and a signed
    // agreement are the company's legal instruments, not recruitment data, so
    // this is the one surface the flat staff model deliberately does not share.
    hasCapability("manage_compliance_documents")
      ? { href: "/org/compliance", label: "Compliance", icon: ShieldCheck }
      : null,
    // Shared staff surface, identical for every staff role (flat).
    { href: "/org/jobs", label: "Jobs", icon: Briefcase },
    // Company Profile (2026-07-27 spec §3.2): the About / Work Life / Benefits
    // sections every new job snapshots into its JD.
    { href: "/org/profile", label: "Company Profile", icon: FileText },
    { href: "/org/dashboard", label: "Dashboard", icon: LayoutDashboard },
    // Billing. Gated on `view_billing`, which the three staff roles hold as
    // read-only: a recruiter whose assessment invitations have stopped sending
    // has to be able to SEE that the credit pool is in deficit. Only the
    // Company Admin also holds `manage_billing` and can change the plan.
    hasCapability("view_billing")
      ? { href: "/org/billing", label: "Billing", icon: CreditCard }
      : null,
    // Settings keeps ONLY the theme toggle and account controls (claude.md
    // rule 10). The Review Screen and the Email Templates builder are gone, 
    // candidates are reviewed inline on the job page, and all six lifecycle
    // emails are AI-drafted and edited at send time rather than pre-authored.
    { href: "/org/settings", label: "Settings", icon: Settings },
  ].filter((item) => item !== null) as NavItem[];

  return (
    <AppShell title="Client-Org Portal" nav={nav}>
      {children}
    </AppShell>
  );
}
