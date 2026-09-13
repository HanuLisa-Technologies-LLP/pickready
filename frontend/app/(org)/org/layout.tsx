"use client";

// Customer portal shell. Navigation and every backend action are capability
// driven; the four-level hierarchy controls those capabilities per person.

import * as React from "react";
import Link from "next/link";

import {
  Briefcase,
  CreditCard,
  FileText,
  Gauge,
  LayoutDashboard,
  LifeBuoy,
  MailCheck,
  Settings,
  ShieldCheck,
  Users,
  Users2,
  AlertTriangle,
} from "lucide-react";

import { CAP } from "@/lib/permissions";
import { usePermissions } from "@/lib/use-permissions";
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
  const { can } = usePermissions();
  const canView = can(CAP.viewBilling);
  const canPurchase = can(CAP.manageBilling);
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
              {credits.exhausted
                ? "Credit pool exhausted"
                : (credits.warning_level ?? 0) >= 2
                  ? "Critical: credit balance at or below 10 credits"
                  : "Credits running low"}
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
  const { can: hasCapability } = usePermissions();

  const nav = [
    hasCapability(CAP.manageStaff)
      ? { href: "/org/staff", label: "Staff", icon: Users }
      : null,
    // Compliance & legal records (Provider Portal spec §3). Capability-gated
    // to the Company Admin by default, a GSTIN certificate and a signed
    // agreement are the company's legal instruments, not recruitment data, so
    // this is the one surface the flat staff model deliberately does not share.
    hasCapability(CAP.manageComplianceDocuments)
      ? { href: "/org/compliance", label: "Compliance", icon: ShieldCheck }
      : null,
    // Shared staff surface, identical for every staff role (flat).
    { href: "/org/jobs", label: "Jobs", icon: Briefcase },
    // The Candidate Dashboard, the client's daily working surface. NOT gated
    // on a capability here, and deliberately: the route is authorized by
    // `view_candidate_ratings`, which is one of the RBAC 24 grants the engine
    // resolves server-side and which is not in the client-side
    // `ALL_CAPABILITIES` list, so `hasCapability` cannot see it. A nav item
    // hidden by a stale client-side list is a page somebody is told does not
    // exist. The page itself asks the server what this person may do and
    // renders scoped or full accordingly, which is the more honest
    // arrangement in any case.
    { href: "/org/candidates", label: "Candidates", icon: Users2 },
    // Company Profile (2026-07-27 spec §3.2): the About / Work Life / Benefits
    // sections every new job snapshots into its JD.
    { href: "/org/profile", label: "Company Profile", icon: FileText },
    { href: "/org/dashboard", label: "Dashboard", icon: LayoutDashboard },
    // Talent Intelligence (2026-09-05 spec): the 18 operational dashboards.
    // Gated on view_intelligence_dashboards, which the RBAC engine resolves
    // server-side and /auth/me returns because the capability is in
    // ALL_CAPABILITIES (seeded by migration 0082).
    hasCapability(CAP.viewIntelligenceDashboards)
      ? { href: "/org/intelligence", label: "Intelligence", icon: Gauge }
      : null,
    // The AI Dashboard was REMOVED from the customer portal (spec 30, client
    // instruction). Deleted rather than hidden: a nav entry behind a flag is a
    // page that comes back, and the route, the component and its API handler
    // all went with it.
    // Billing. Gated on `view_billing`, which the three staff roles hold as
    // read-only: a recruiter whose assessment invitations have stopped sending
    // has to be able to SEE that the credit pool is in deficit. Only the
    // Company Admin also holds `manage_billing` and can change the plan.
    hasCapability(CAP.viewBilling)
      ? { href: "/org/billing", label: "Billing", icon: CreditCard }
      : null,
    // Settings keeps ONLY the theme toggle and account controls (claude.md
    // rule 10). The Review Screen and the Email Templates builder are gone, 
    // candidates are reviewed inline on the job page, and all six lifecycle
    // emails are AI-drafted and edited at send time rather than pre-authored.
    // Sender Authorization. The Super Admin's decision on which addresses may
    // speak for this company, and the ONLY surface in the portal that makes
    // one. Gated on `authorize_email_senders`, which only the client Super
    // Admin holds: a Recruitment Manager can propose a sender from Settings
    // but must never be shown a control that approves one.
    //
    // A sidebar item rather than a settings card because it is a decision
    // QUEUE somebody comes back to, not configuration set once. Registering a
    // sender stays in Settings, where managing the list belongs.
    hasCapability("authorize_email_senders")
      ? { href: "/org/senders", label: "Sender Authorization", icon: MailCheck }
      : null,
    // Support (2026-09-10). Gated on `open_support_threads`, which every
    // customer role holds by default and a tenant Super Admin can revoke; the
    // capability is in ALL_CAPABILITIES so /auth/me returns it and
    // `hasCapability` can actually see it. That last part is not incidental:
    // the Candidates entry above is deliberately ungated precisely because its
    // capability is NOT in that list, and a nav item hidden by a stale
    // client-side list is a page somebody is told does not exist.
    hasCapability("open_support_threads")
      ? { href: "/org/support", label: "Support", icon: LifeBuoy }
      : null,
    { href: "/org/settings", label: "Settings", icon: Settings },
  ].filter((item) => item !== null) as NavItem[];

  return (
    <AppShell title="Client-Org Portal" nav={nav}>
      <CreditStatusAlert />
      {children}
    </AppShell>
  );
}
