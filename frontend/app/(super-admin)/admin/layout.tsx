"use client";

// Provider Portal shell, the PickReady owner's console.
//
// The nav is deliberately minimal (Provider Portal spec §1.1). Team
// Management, Permissions and the Audit Log were removed: the owner's job here
// is managing CUSTOMERS, and three cross-tenant admin surfaces alongside it
// made the one thing that matters the fourth item in a list. Staff are managed
// by each customer in their own portal, and the audit trail keeps being written
// (get_superadmin_db records every request), it simply has no page.
//
// Settings stays: it is where the theme toggle lives, and claude.md rule 10
// says the toggle appears nowhere else.
//
// 2026-07-28: Business Development is the ONE addition to that nav, by explicit
// client decision ("the provider can add BDs and their details too in the
// provider portal"). It is not a fourth cross-tenant admin surface, it is the
// only place a `bd` account can be created at all: every invite path in the
// product is tenant-scoped and a BD user has no tenant. This supersedes the
// "Customers + Settings, nothing else" rule in claude.md (2026-07-27).
//
// 2026-07-28 (killer-spec §4.1): Billing joins it, read-only. The Provider must
// be able to see which customer is on which plan and who is out of credits;
// it writes nothing, in keeping with read-only-by-absence.

import { Building2, Briefcase, CreditCard, Settings } from "lucide-react";

import { AppShell } from "@/components/app-shell";

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AppShell
      title="Provider Portal"
      nav={[
        { href: "/admin", label: "Customers", icon: Building2, exact: true },
        // Business Development is PickReady's OWN team, not a customer's, and
        // no other screen can create one: every invite path in the product is
        // tenant-scoped and a bd user has no tenant.
        { href: "/admin/bd", label: "Business Development", icon: Briefcase },
        // Billing is a natural provider need once customers pay: which plan
        // each is on, whether it is charging, and who is out of credits. It is
        // READ-ONLY, like the rest of the Provider's view of customer data, and
        // there is no route here that writes a subscription.
        { href: "/admin/billing", label: "Billing", icon: CreditCard },
        { href: "/admin/settings", label: "Settings", icon: Settings },
      ]}
    >
      {children}
    </AppShell>
  );
}
