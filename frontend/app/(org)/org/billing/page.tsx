"use client";

// Customer Portal → Billing (killer-spec §2.4, §3.4).
//
// Four things this page has to get right, because each of them is a question a
// customer will otherwise ask by email:
//
//   * what plan am I on, and when does it renew;
//   * how many credits do I have, in CREDITS, not in the sub-units the ledger
//     stores (60 sub-units is one credit, and nobody should have to know that);
//   * where did this month's usage go, broken down by what caused it;
//   * why have my assessment invitations stopped, and what do I do about it.
//
// Reading is gated on `view_billing`, which the three staff roles hold, so a
// recruiter can answer the fourth question for themselves. Changing the plan
// needs `manage_billing`, which the Company Admin holds alone.

import * as React from "react";
import { AlertTriangle, ArrowUpRight, Loader2 } from "lucide-react";

import { ApiError, apiGet, apiPost } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { openCheckout } from "@/lib/razorpay";
import type {
  BillingOverview,
  CreditEventType,
  PricingPlan,
  SubscribeResponse,
} from "@/lib/types";
import { PageHeader } from "@/components/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DetailItem,
  ErrorState,
  LoadingRows,
  Section,
} from "@/components/page-primitives";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";

/** Human labels for ledger event types. A raw enum never reaches the page. */
const EVENT_LABELS: Record<CreditEventType, string> = {
  grant: "Monthly top up",
  completed_assessment: "Assessment completed",
  incomplete_assessment: "Assessment started, not finished",
  no_show: "Invitation never opened",
  old_profile_review: "Earlier applicant reviewed",
  adjustment: "Adjustment",
};

/** How many of each event make one credit. Shown so the rate is never a mystery. */
const EVENT_RATE: Record<string, string> = {
  completed_assessment: "1 per credit",
  incomplete_assessment: "3 per credit",
  no_show: "15 per credit",
  old_profile_review: "20 per credit",
};

const STATUS_LABELS: Record<string, string> = {
  active: "Active",
  past_due: "Payment pending",
  cancelled: "Cancelled",
  halted: "Halted",
};

function formatInr(value: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value);
}

function formatDate(value: string | null): string {
  if (!value) return "Not scheduled";
  return new Date(value).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/**
 * Sub-units to a credit figure for DISPLAY.
 *
 * The server already sends the rounded string for the balance; this is for the
 * per-event usage numbers, which arrive as raw sub-units so the page can total
 * them without re-parsing decimals.
 */
function toCredits(subunits: number, perCredit: number): string {
  return (subunits / perCredit).toFixed(2);
}

export default function BillingPage() {
  const { user, hasCapability } = useAuth();
  const { toast } = useToast();
  const [data, setData] = React.useState<BillingOverview | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [forbidden, setForbidden] = React.useState(false);
  const [busySlug, setBusySlug] = React.useState<string | null>(null);

  const canManage = hasCapability("manage_billing");

  const load = React.useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setData(await apiGet<BillingOverview>("/billing/overview"));
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) {
        setForbidden(true);
      } else {
        setLoadError(
          error instanceof Error ? error.message : "Could not load billing."
        );
      }
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const choosePlan = React.useCallback(
    async (plan: PricingPlan) => {
      setBusySlug(plan.slug);
      const hasSubscription = Boolean(data?.subscription.razorpay_subscription_id);
      try {
        if (hasSubscription) {
          // Already subscribed: this is a plan CHANGE. Razorpay computes the
          // proration; we never compute a second opinion that could disagree
          // with the customer's card statement.
          await apiPost("/billing/change-plan", { plan_slug: plan.slug });
          toast({
            title: "Plan changed",
            description: `You are now on ${plan.name}. Razorpay will settle the difference.`,
          });
          await load();
          return;
        }
        const session = await apiPost<SubscribeResponse>("/billing/subscribe", {
          plan_slug: plan.slug,
        });
        const opened = await openCheckout({
          keyId: session.razorpay_key_id,
          subscriptionId: session.subscription_id,
          planName: session.plan.name,
          prefill: {
            email: user?.email ?? undefined,
            name: user?.full_name ?? undefined,
          },
          onSuccess: async (payload) => {
            try {
              await apiPost("/billing/checkout/verify", payload);
              toast({
                title: "Subscription active",
                description: `Your ${session.plan.name} credits are in your pool.`,
              });
            } catch (error) {
              toast({
                variant: "destructive",
                title: "We could not confirm that payment",
                description:
                  error instanceof Error
                    ? error.message
                    : "Reload this page in a moment to check its status.",
              });
            }
            await load();
          },
        });
        if (!opened) {
          if (session.short_url) {
            window.location.href = session.short_url;
            return;
          }
          toast({
            variant: "destructive",
            title: "Checkout could not open",
            description:
              "Check that your browser is not blocking payment scripts, then retry.",
          });
        }
      } catch (error) {
        toast({
          variant: "destructive",
          title: "That did not go through",
          description:
            error instanceof Error ? error.message : "Try again in a moment.",
        });
      } finally {
        setBusySlug(null);
      }
    },
    [data, load, toast, user]
  );

  if (forbidden) {
    return (
      <>
        <PageHeader
          title="Billing"
          description="Your plan, your credit pool and what used it."
        />
        <ErrorState
          title="Billing is not part of your access"
          description="Ask your Company Admin to open this page, or to grant you billing visibility."
        />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Billing"
        description="Your plan, your credit pool and what used it."
      />

      {loading ? (
        <LoadingRows rows={5} label="Loading billing" />
      ) : loadError ? (
        <ErrorState
          title="Could not load billing"
          description={loadError}
          action={
            <Button variant="outline" onClick={() => void load()}>
              Retry
            </Button>
          }
        />
      ) : data ? (
        <div className="space-y-6">
          {data.credits.in_deficit ? (
            <div
              role="alert"
              className="flex items-start gap-3 rounded-xl border border-destructive/40 bg-destructive/5 p-5"
            >
              <AlertTriangle
                className="mt-0.5 h-5 w-5 shrink-0 text-destructive"
                aria-hidden="true"
              />
              <div className="min-w-0">
                <p className="font-semibold">New assessment invitations are paused</p>
                <p className="mt-1 text-pretty leading-7">
                  {data.credits.deficit_message}
                </p>
                <p className="mt-1 text-sm leading-6">
                  Assessments already in progress are unaffected, and every
                  candidate profile stays exactly where it is.
                </p>
              </div>
            </div>
          ) : null}

          {/* ── Plan + balance ─────────────────────────────────────────── */}
          <Section
            title="Your plan"
            description={
              data.subscription.plan
                ? "Billed monthly. Unused credits roll over and nothing expires."
                : "No plan yet. Pick one below to start running assessments."
            }
          >
            <dl className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
              <DetailItem label="Plan">
                {data.subscription.plan ? (
                  <span className="font-semibold">
                    {data.subscription.plan.name}
                  </span>
                ) : (
                  "Not subscribed"
                )}
              </DetailItem>
              <DetailItem label="Status">
                {data.subscription.status ? (
                  <Badge
                    variant={
                      data.subscription.status === "active" ? "brand" : "outline"
                    }
                  >
                    {STATUS_LABELS[data.subscription.status] ??
                      data.subscription.status}
                  </Badge>
                ) : (
                  "Not subscribed"
                )}
              </DetailItem>
              <DetailItem label="Next billing date">
                {formatDate(data.subscription.current_end)}
              </DetailItem>
              <DetailItem label="Credit balance">
                <span className="text-2xl font-bold">
                  {data.credits.balance_credits}
                </span>{" "}
                credits
                <span className="mt-1 block text-sm font-medium">
                  {data.credits.balance_inr !== null
                    ? `${formatInr(Number(data.credits.balance_inr))} at your current plan rate`
                    : "INR value available after a plan is selected"}
                </span>
              </DetailItem>
            </dl>
          </Section>

          {/* ── Usage this month ───────────────────────────────────────── */}
          <Section
            title="This month"
            description="What drew from the pool, and at what rate."
          >
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {(
                [
                  "completed_assessment",
                  "incomplete_assessment",
                  "no_show",
                  "old_profile_review",
                ] as const
              ).map((event) => {
                const subunits =
                  data.credits.usage_this_month_subunits[event] ?? 0;
                return (
                  <div
                    key={event}
                    className="rounded-xl border border-border bg-surface p-4"
                  >
                    <p className="text-sm font-medium leading-6">
                      {EVENT_LABELS[event]}
                    </p>
                    <p className="mt-2 text-2xl font-bold">
                      {toCredits(subunits, data.credits.subunits_per_credit)}
                    </p>
                    <p className="mt-1 text-xs leading-5">
                      credits used, at {EVENT_RATE[event]}
                    </p>
                  </div>
                );
              })}
            </div>
            <div className="mt-6 grid gap-6 sm:grid-cols-3">
              <DetailItem label="Carried over from last month">
                {data.credits.rollover_credits} credits
              </DetailItem>
              <DetailItem label="Granted to date">
                {toCredits(
                  data.credits.granted_subunits,
                  data.credits.subunits_per_credit
                )}{" "}
                credits
              </DetailItem>
              <DetailItem label="Used to date">
                {toCredits(
                  data.credits.consumed_subunits,
                  data.credits.subunits_per_credit
                )}{" "}
                credits
              </DetailItem>
            </div>
          </Section>

          {/* ── Plans ──────────────────────────────────────────────────── */}
          <Section
            title={data.subscription.plan ? "Change plan" : "Choose a plan"}
            description={
              canManage
                ? "Every plan is the whole product. Only the monthly application volume changes."
                : "Every plan is the whole product. Ask your Company Admin to change it."
            }
          >
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {data.plans.map((plan) => {
                const current = plan.id === data.subscription.plan?.id;
                return (
                  <div
                    key={plan.id}
                    className={
                      "flex flex-col rounded-xl border p-5 " +
                      (current
                        ? "border-brand-600 ring-1 ring-brand-600/30"
                        : "border-border")
                    }
                  >
                    <div className="flex items-center justify-between gap-2">
                      <p className="font-semibold">{plan.name}</p>
                      {current ? <Badge variant="brand">Current</Badge> : null}
                    </div>
                    <p className="mt-3 text-2xl font-bold">
                      {formatInr(plan.price_inr)}
                      <span className="ml-1 text-sm font-medium">/ month</span>
                    </p>
                    <p className="mt-2 text-sm leading-6">
                      {plan.applications_per_month} applications, at{" "}
                      {formatInr(plan.rate_per_application_inr)} each
                    </p>
                    <div className="flex-1" />
                    <Button
                      className="mt-5 w-full"
                      variant={current ? "outline" : "default"}
                      disabled={
                        !canManage ||
                        current ||
                        !plan.checkout_ready ||
                        busySlug !== null
                      }
                      onClick={() => void choosePlan(plan)}
                    >
                      {busySlug === plan.slug ? (
                        <>
                          <Loader2
                            className="h-4 w-4 animate-spin"
                            aria-hidden="true"
                          />
                          Working
                        </>
                      ) : current ? (
                        "Your plan"
                      ) : data.subscription.razorpay_subscription_id ? (
                        "Switch to this"
                      ) : (
                        "Subscribe"
                      )}
                    </Button>
                  </div>
                );
              })}
            </div>
            <p className="mt-5 text-sm leading-6">
              Need more than 200 applications a month?{" "}
              <a
                className="inline-flex items-center gap-1 underline"
                href="mailto:hello@pickready.app?subject=Enterprise%20plan"
              >
                Talk to us about Enterprise
                <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
              </a>
            </p>
          </Section>

          {/* ── Statement ──────────────────────────────────────────────── */}
          <Section
            title="Recent activity"
            description="Every credit movement, newest first."
          >
            {data.recent_ledger.length === 0 ? (
              <p className="leading-7">
                Nothing yet. Activity appears here as soon as your first
                assessment invitation goes out.
              </p>
            ) : (
              <>
                <div className="hidden md:block">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>When</TableHead>
                        <TableHead>What happened</TableHead>
                        <TableHead className="text-right">Credits</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.recent_ledger.map((entry) => (
                        <TableRow key={entry.id}>
                          <TableCell>{formatDate(entry.created_at)}</TableCell>
                          <TableCell>
                            {EVENT_LABELS[entry.event_type] ?? entry.event_type}
                          </TableCell>
                          <TableCell className="text-right font-medium">
                            {entry.subunits_delta > 0 ? "+" : ""}
                            {entry.credits_delta}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <ul className="space-y-3 md:hidden">
                  {data.recent_ledger.map((entry) => (
                    <li
                      key={entry.id}
                      className="flex items-start justify-between gap-3 rounded-xl border border-border bg-surface p-4"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-medium">
                          {EVENT_LABELS[entry.event_type] ?? entry.event_type}
                        </p>
                        <p className="mt-1 text-xs leading-5">
                          {formatDate(entry.created_at)}
                        </p>
                      </div>
                      <span className="shrink-0 text-sm font-semibold">
                        {entry.subunits_delta > 0 ? "+" : ""}
                        {entry.credits_delta}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Section>

          {data.transactions.length > 0 ? (
            <Section title="Payments" description="What was charged, and when.">
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Date</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="text-right">Amount</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.transactions.map((row) => (
                      <TableRow key={row.id}>
                        <TableCell>{formatDate(row.created_at)}</TableCell>
                        <TableCell>
                          {row.transaction_type === "subscription_charge"
                            ? "Subscription"
                            : row.transaction_type === "plan_change"
                              ? "Plan change"
                              : "Refund"}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              row.status === "success" ? "brand" : "outline"
                            }
                          >
                            {row.status === "success"
                              ? "Paid"
                              : row.status === "failed"
                                ? "Failed"
                                : "Refunded"}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right font-medium">
                          {formatInr(row.amount_inr)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </Section>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
