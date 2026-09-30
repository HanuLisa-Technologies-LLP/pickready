"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowRight, Check } from "lucide-react";
import { apiGet } from "@/lib/api";
import { Button } from "@/components/ui/button";

interface MonthlyPlan {
  slug: string;
  name: string;
  assessments: number;
  price_inr: number;
  gst_inr: number;
  total_inr: number;
  rollover_months: number;
}

interface MonthlyPlansResponse {
  plans: MonthlyPlan[];
  gst_rate_percent: number;
  pilot_days: number;
  pilot_plan_slug: string;
  topup_plan_slug: string;
}

function inr(amount: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency", currency: "INR", maximumFractionDigits: 0,
  }).format(amount);
}

export function PricingCatalogue() {
  const [catalogue, setCatalogue] = React.useState<MonthlyPlansResponse | null>(null);
  const [error, setError] = React.useState(false);
  const load = React.useCallback(async () => {
    setError(false);
    try {
      setCatalogue(await apiGet<MonthlyPlansResponse>("/billing/public/plans"));
    } catch {
      setError(true);
    }
  }, []);
  React.useEffect(() => { void load(); }, [load]);

  if (error) {
    return (
      <div role="alert" className="rounded-xl border border-border bg-surface p-6">
        <p>The current prices could not be loaded.</p>
        <Button variant="outline" className="mt-4" onClick={() => void load()}>Try again</Button>
      </div>
    );
  }
  if (!catalogue) {
    return <p role="status" className="text-sm text-muted-foreground">Loading plans...</p>;
  }

  const starter = catalogue.plans.find((plan) => plan.slug === catalogue.pilot_plan_slug);
  const topup = catalogue.plans.find((plan) => plan.slug === catalogue.topup_plan_slug);
  return (
    <div>
      <div className="grid gap-4 sm:grid-cols-2">
        {catalogue.plans.map((plan) => (
          <article key={plan.slug} className="flex flex-col rounded-2xl border border-border bg-surface p-6">
            <h3 className="text-lg font-semibold">{plan.name}</h3>
            <p className="mt-4 text-3xl font-semibold tabular-nums">{inr(plan.price_inr)}<span className="ml-1 text-sm font-medium">/ month</span></p>
            <p className="mt-1 text-sm text-muted-foreground">Plus {catalogue.gst_rate_percent}% GST</p>
            <p className="mt-5 text-base font-semibold tabular-nums">{new Intl.NumberFormat("en-IN").format(plan.assessments)} completed assessments per month</p>
            <p className="mt-4 rounded-lg border border-teal-600/30 bg-teal-50 p-3 text-sm font-medium text-teal-950 dark:bg-teal-950/30 dark:text-teal-100">
              Unused monthly credits roll over for {plan.rollover_months} months, then expire.
            </p>
            <Button asChild variant="outline" className="mt-6 w-full">
              <Link href="/company/register">Start on Starter<ArrowRight className="ml-2 h-4 w-4" aria-hidden="true" /></Link>
            </Button>
          </article>
        ))}
      </div>
      <div className="mt-6 grid gap-5 lg:grid-cols-2">
        <div className="rounded-2xl border border-border bg-surface p-6">
          <h3 className="text-base font-semibold">Included in every plan</h3>
          <ul className="mt-4 space-y-2 text-sm">
            {[
              "Every platform feature and all 7 agents",
              "BGV reconfirm",
              "One credit pool across all open jobs",
              "No feature gates or seat limits",
            ].map((item) => (
              <li key={item} className="flex gap-2"><Check className="mt-0.5 h-4 w-4 shrink-0 text-teal-700" aria-hidden="true" />{item}</li>
            ))}
          </ul>
        </div>
        <div className="rounded-2xl border border-border bg-surface p-6 text-sm">
          <h3 className="text-base font-semibold">Start on Starter</h3>
          <p className="mt-4">The paid pilot runs for {catalogue.pilot_days} days on {starter?.name ?? "Starter"}. At the end, keep the monthly plan or cancel.</p>
          <p className="mt-3">No annual commitment or lock-in. Cancel anytime. Unused paid credits keep their {starter?.rollover_months ?? "three"}-month validity after cancellation.</p>
          {topup ? <p className="mt-3">Need more capacity? The only top-up is the {topup.name} pack: {topup.assessments} additional assessments for {inr(topup.price_inr)} plus GST.</p> : null}
          <p className="mt-3">Already registered? <Link href="/company/login" className="font-semibold underline underline-offset-2">Sign in</Link>.</p>
        </div>
      </div>
    </div>
  );
}
