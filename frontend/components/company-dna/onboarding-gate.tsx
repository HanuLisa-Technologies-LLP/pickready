"use client";

// The entry point a client with no Company DNA cannot miss.
//
// It sits in the portal shell rather than on one page, because the person who
// needs it is not looking for it: they signed in to post a job. The banner
// states the consequence in the terms that will actually bite them, links
// straight to the intake, and disappears the moment the artifact exists.
//
// WHAT IT SAYS IS A REQUIREMENT, NOT A CLAIM ABOUT ENFORCEMENT. Sutra cannot
// compile a scorecard without a Layer 2 artifact, so a job's scorecard cannot
// be locked without one. The message comes from the server so the two cannot
// drift, and the server's wording is careful not to promise that an evaluation
// is currently being refused, because Part A is not on the live path yet.

import * as React from "react";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Fingerprint } from "lucide-react";

import { apiGet } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { Button } from "@/components/ui/button";

import type { CompanyDnaStatus } from "./types";
import { SCORECARD_BLOCK_SENTENCE, companyDnaPath } from "./types";

export const COMPANY_DNA_ROUTE = "/org/company-dna";

export function CompanyDnaGate() {
  const { user } = useAuth();
  const pathname = usePathname();
  const tenantId = user?.tenant_id ?? null;
  const [status, setStatus] = React.useState<CompanyDnaStatus | null>(null);
  // Silent on the page it points at. A banner telling somebody to go and do the
  // thing they are already looking at is noise, and noise above the first
  // question is noise on the surface this feature is judged by.
  const onTheIntake = pathname?.startsWith(COMPANY_DNA_ROUTE) ?? false;

  React.useEffect(() => {
    if (!tenantId || onTheIntake) return;
    let live = true;
    apiGet<CompanyDnaStatus>(`${companyDnaPath(tenantId)}/status`)
      .then((result) => {
        if (live) setStatus(result);
      })
      .catch(() => {
        // A caller without the capability, or a request that did not land.
        // Neither is a reason to tell somebody their Company DNA is missing:
        // the banner claims a fact about their account, and a failed read is
        // not evidence of that fact. The page itself is the diagnostic surface.
      });
    return () => {
      live = false;
    };
  }, [tenantId, onTheIntake]);

  if (onTheIntake || !status || status.status === "complete") return null;

  return (
    <div className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-navy-200 bg-navy-50 p-4">
      <Fingerprint className="h-5 w-5" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold">
          {status.draft_open
            ? "Your Company DNA session is part of the way through"
            : "Set up your Company DNA"}
        </p>
        <p className="mt-1 text-sm leading-6">
          {SCORECARD_BLOCK_SENTENCE} It is answered once, and every job you post
          afterwards is evaluated against it.
        </p>
      </div>
      <Button asChild size="sm">
        <Link href={COMPANY_DNA_ROUTE}>
          {status.draft_open ? "Carry on" : "Start the intake"}
        </Link>
      </Button>
    </div>
  );
}
