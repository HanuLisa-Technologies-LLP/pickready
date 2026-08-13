"use client";

// Customer portal shell. Navigation and every backend action are capability
// driven; the four-level hierarchy controls those capabilities per person.

import * as React from "react";
import Link from "next/link";

import {
  Briefcase,
  Building,
  CreditCard,
  FileText,
  LayoutDashboard,
  Settings,
  ShieldCheck,
  Users,
  AlertTriangle,
} from "lucide-react";

import { useAuth } from "@/lib/auth-context";
import { apiGet } from "@/lib/api";
import type { BillingOverview } from "@/lib/types";
import { AppShell, type NavItem } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

function CreditStatusAlert() {
  const { hasCapability } = useAuth();
  const canView = hasCapability("view_billing");
  const canPurchase = hasCapability("manage_billing");
  const [credits, setCredits] = React.useState<BillingOverview["credits"] | null>(null);
  const [acknowledged, setAcknowledged] = React.useState(false);

  React.useEffect(() => {
    if (!canView) return;
    let active = true;
    apiGet<BillingOverview>("/billing/overview")
      .then((result) => {
        if (!active || result.credits.unlimited) return;
        setCredits(result.credits);
        const key = `pickready-credit-alert:${result.credits.balance_subunits}`;
        setAcknowledged(window.sessionStorage.getItem(key) === "acknowledged");
      })
      .catch(() => {
        // The billing page remains the explicit diagnostic surface. A failed
        // alert fetch never invents a healthy balance.
      });
    return () => {
      active = false;
    };
  }, [canView]);

  if (!credits || (!credits.low_balance && !credits.exhausted)) return null;
  const acknowledge = () => {
    window.sessionStorage.setItem(
      `pickready-credit-alert:${credits.balance_subunits}`,
      "acknowledged"
    );
    setAcknowledged(true);
  };
  return (
    <>
      <div className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-amber-600 bg-amber-50 p-4 text-amber-950 dark:bg-amber-950/50 dark:text-amber-50">
        <AlertTriangle className="h-5 w-5" aria-hidden="true" />
        <p className="min-w-0 flex-1 text-sm font-medium">
          {credits.alert_message}
        </p>
        <Button asChild size="sm" variant="outline">
          <Link href="/org/billing">{canPurchase ? "Purchase credits" : "View credit status"}</Link>
        </Button>
      </div>
      <Dialog open={!acknowledged} onOpenChange={(open) => !open && acknowledge()}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-600" aria-hidden="true" />
              {credits.exhausted ? "Credit pool exhausted" : "Credit balance below 30%"}
            </DialogTitle>
            <DialogDescription>{credits.alert_message}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={acknowledge}>Acknowledge</Button>
            <Button asChild>
              <Link href="/org/billing">{canPurchase ? "Purchase credits" : "View billing"}</Link>
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

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
    // The AI Dashboard was REMOVED from the customer portal (spec 30, client
    // instruction). Deleted rather than hidden: a nav entry behind a flag is a
    // page that comes back, and the route, the component and its API handler
    // all went with it.
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
      <CreditStatusAlert />
      {children}
    </AppShell>
  );
}
