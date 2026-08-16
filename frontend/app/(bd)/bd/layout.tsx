"use client";

// Business Development Portal shell, the FOURTH portal (CLAUDE.md 2026-07-28).
//
//   Provider Portal   /admin   the ReadyPick owner's console
//   Customer Portal   /org     a client company's dashboard
//   Candidate Portal  /portal  the candidate surface
//   BD Portal         /bd      this one: ReadyPick's own sales team
//
// A `bd` user is PLATFORM staff (tenant_id NULL, OWNER audience token), so this
// shell looks like the Provider console rather than the org one.
//
// Every nav item is gated on the capability that guards ITS OWN endpoints, so a
// BD rep without a grant never sees a link that answers 403. Gating is the same
// data-driven `hasCapability` the other portals use, never a role branch
// (CLAUDE.md rule 3).
//
// There is no theme toggle here. It lives only in Settings (CLAUDE.md rule 10).

import {
  Building2,
  Settings,
  Sparkles,
  UserRoundSearch,
} from "lucide-react";

import { useAuth } from "@/lib/auth-context";
import { BD_CAPABILITIES } from "@/lib/bd-types";
import { AppShell, type NavItem } from "@/components/app-shell";

export default function BDLayout({ children }: { children: React.ReactNode }) {
  const { hasCapability } = useAuth();

  const nav = [
    // One reach section since 2026-08-09. Personal Reach and Social Reach were
    // the same funnel over the same table; where a lead came from is a column
    // on this page, not a second nav item.
    hasCapability(BD_CAPABILITIES.manageLeads)
      ? {
          href: "/bd",
          label: "BD Reach",
          icon: UserRoundSearch,
          exact: true,
        }
      : null,
    hasCapability(BD_CAPABILITIES.useAIReach)
      ? { href: "/bd/ai-reach", label: "AI Reach", icon: Sparkles }
      : null,
    hasCapability(BD_CAPABILITIES.viewCustomers)
      ? { href: "/bd/customers", label: "Customers", icon: Building2 }
      : null,
    { href: "/bd/settings", label: "Settings", icon: Settings },
  ].filter((item) => item !== null) as NavItem[];

  return (
    <AppShell title="Business Development" nav={nav}>
      {children}
    </AppShell>
  );
}
