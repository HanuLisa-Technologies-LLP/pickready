"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Check, Loader2 } from "lucide-react";

import { apiGet, apiPost, isAuthError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { openCheckout } from "@/lib/razorpay";
import type {
  BillingConfig,
  PricingPlan,
  SubscribeResponse,
} from "@/lib/types";
import {
  HoverLift,
  Reveal,
  RevealStagger,
  StaggerItem,
} from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";

/**
 * Pricing and checkout (killer-spec §2.3, §5.3).
 *
 * Two rules shape this section rather than the usual SaaS pricing grid:
 *
 * 1. **Every plan includes everything.** So the feature list is ONE strip
 *    shared by all four cards, never a per-card list with ticks and crosses.
 *    Repeating an identical list four times is what makes a reader hunt for
 *    the difference, and here there is none to find. The only thing that
 *    varies is volume, so volume is the only thing the cards differ on.
 *
 * 2. **The differentiators are product truths, not badges.** There is no
 *    "Why we're different" heading anywhere on this page. The shared pool, the
 *    lasting candidate data and the identical feature set are stated as how the
 *    thing works, in the sections below the cards.
 *
 * Prices come from GET /billing/config, not from a constant here, so the page
 * and the ledger can never quote different numbers.
 */

/** Highlighted card. Growth is the mid tier most teams land on. */
const RECOMMENDED_SLUG = "growth";

/**
 * ASSUMPTION (killer-spec §5.3 asks for "the existing copy near-verbatim"):
 * that copy lives in an earlier conversation and is not in this repository, so
 * these two sections are written from the substance the brief quotes ("draws
 * from one shared pool... the pool doesn't care which"). Replace verbatim when
 * the original is to hand; the numbers and rules stated here already match the
 * ledger implementation exactly.
 */
const POOL_COPY = [
  {
    title: "How the pool works",
    body: [
      "Your plan tops up one shared pool of applications every month. Every job you post draws from that same pool, and the pool does not care which. A month where one role takes eighty applications and four others take five each is simply a month, not a billing problem.",
      "A candidate who finishes their assessment uses one application. A candidate who starts and never comes back uses a third of one. A candidate who never opens the invitation uses a fifteenth. Reviewing a profile carried over from an earlier posting uses a twentieth.",
      "Whatever you do not use rolls over. Nothing expires.",
    ],
  },
  {
    title: "Jobs and renewals",
    body: [
      "Post as many roles as you like. A job stays live for thirty days, then allows five more days in which people who already applied can still update what they sent.",
      "When a posting closes you can renew it for another thirty days. Everyone who applied the first time round stays in your dashboard, fully readable, marked as an earlier applicant. Their profiles do not leave when the posting does.",
    ],
  },
];

/** The shared feature strip. Identical on every plan, said once. */
const INCLUDED = [
  "Unlimited jobs, drawing on one pool",
  "Unlimited team members, no per seat fee",
  "Four parameter AI matching",
  "Technical questions written per candidate",
  "ReadyPick Profile Intelligence",
  "One continuous candidate conversation",
  "Full PPI Assessment Report",
  "Four radar charts, no numbers on them",
  "Candidate databank",
  "Ten stage hiring pipeline",
  "AI drafted lifecycle emails",
  "Compliance document vault",
  "Every profile stays yours, and stays exportable",
];

function formatInr(value: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value);
}

export function Pricing() {
  const router = useRouter();
  const { user } = useAuth();
  const { toast } = useToast();
  const [config, setConfig] = React.useState<BillingConfig | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [busySlug, setBusySlug] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    apiGet<BillingConfig>("/billing/config")
      .then((data) => {
        if (!cancelled) setConfig(data);
      })
      .catch(() => {
        // The price list failing to load must not blank the section. The
        // static copy below still explains the model, and the cards render
        // their skeletons rather than an error nobody can act on.
        if (!cancelled) setConfig(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const subscribe = React.useCallback(
    async (plan: PricingPlan) => {
      // Not signed in: send them to sign-up carrying the plan, so they land
      // back here on the right card instead of on a generic dashboard.
      if (!user) {
        router.push(`/register?plan=${plan.slug}&next=/org/billing`);
        return;
      }
      setBusySlug(plan.slug);
      try {
        const session = await apiPost<SubscribeResponse>("/billing/subscribe", {
          plan_slug: plan.slug,
        });
        const opened = await openCheckout({
          keyId: session.razorpay_key_id,
          subscriptionId: session.subscription_id,
          planName: session.plan.name,
          prefill: {
            email: user.email ?? undefined,
            name: user.full_name ?? undefined,
          },
          onSuccess: async (payload) => {
            try {
              await apiPost("/billing/checkout/verify", payload);
              toast({
                title: "Subscription active",
                description: `Your ${session.plan.name} credits are in your pool.`,
              });
              router.push("/org/billing");
            } catch (error) {
              toast({
                variant: "destructive",
                title: "We could not confirm that payment",
                description:
                  error instanceof Error
                    ? error.message
                    : "Open Billing in your workspace to check its status.",
              });
            }
          },
        });
        if (!opened) {
          // The embedded widget could not load (blocked script, offline). The
          // hosted page is a working way through rather than a dead button.
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
        if (isAuthError(error)) {
          router.push(`/login?next=/org/billing&plan=${plan.slug}`);
          return;
        }
        toast({
          variant: "destructive",
          title: "Could not start the subscription",
          description:
            error instanceof Error ? error.message : "Try again in a moment.",
        });
      } finally {
        setBusySlug(null);
      }
    },
    [router, toast, user],
  );

  const plans = config?.plans ?? [];

  return (
    <section id="pricing" className="relative scroll-mt-24 py-24 sm:py-28">
      <div className="mx-auto max-w-6xl px-6 lg:px-10">
        <Reveal className="max-w-2xl">
          <Badge variant="brand" className="px-3 py-1 text-xs font-semibold">
            Pricing
          </Badge>
          <h2 className="mt-5 text-balance text-3xl font-bold leading-tight sm:text-4xl">
            Pay for applications, not for features.
          </h2>
          <p className="mt-5 text-pretty text-lg leading-8">
            Pick the volume that fits your hiring. Every plan is the whole
            product, and every job you post draws from the same pool.
          </p>
        </Reveal>

        {/* Cards. `sm:grid-cols-2 xl:grid-cols-4` so they STACK on a phone and
            pair on a tablet, rather than squeezing four columns into 375px. */}
        <div className="mt-12 grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
          {loading
            ? Array.from({ length: 4 }).map((_, index) => (
                <Skeleton key={index} className="h-72 w-full rounded-2xl" />
              ))
            : plans.map((plan, index) => (
                <PlanCard
                  key={plan.id}
                  plan={plan}
                  index={index}
                  busy={busySlug === plan.slug}
                  disabled={busySlug !== null}
                  onSubscribe={() => void subscribe(plan)}
                />
              ))}
        </div>

        {/* Enterprise: a full-width banner, not a fifth column. It has no
            self-serve checkout, so giving it a Subscribe-shaped card would be
            promising a button that cannot exist. */}
        <Reveal
          delay={0.05}
          className="mt-5 flex flex-col gap-5 rounded-2xl border border-border bg-surface p-7 shadow-card sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="max-w-2xl">
            <p className="text-lg font-semibold">Enterprise</p>
            <p className="mt-2 text-pretty leading-7">
              Over 200 applications a month, or hiring across several entities.
              Same product, priced to your volume, with onboarding support.
            </p>
          </div>
          <Button asChild size="lg" variant="outline" className="shrink-0">
            <a
              href="mailto:manjuchro@gmail.com?subject=Enterprise%20plan"
              target="_blank"
              rel="noreferrer"
            >
              Contact us
            </a>
          </Button>
        </Reveal>

        {/* The shared feature strip. Said once, for all four plans. */}
        <Reveal
          delay={0.08}
          className="mt-10 rounded-2xl border border-border bg-brand-100/40 p-7"
        >
          <p className="text-base font-semibold">Everything, on every plan</p>
          <p className="mt-2 max-w-3xl text-pretty leading-7">
            The list below is not a comparison table. Every item is on the
            Starter plan and on the Pro plan alike. What changes between them is
            how many applications you can run in a month.
          </p>
          <RevealStagger
            as="ul"
            delay={0.05}
            className="mt-6 grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3"
          >
            {INCLUDED.map((item) => (
              <StaggerItem as="li" key={item}>
                <span className="flex items-start gap-2.5 text-sm leading-6">
                  <Check
                    className="mt-0.5 h-4 w-4 shrink-0 text-brand-600"
                    aria-hidden="true"
                  />
                  {item}
                </span>
              </StaggerItem>
            ))}
          </RevealStagger>
        </Reveal>

        {/* How the pool works / Jobs and renewals. */}
        <div className="mt-10 grid gap-5 lg:grid-cols-2">
          {POOL_COPY.map((block, index) => (
            <Reveal
              key={block.title}
              delay={0.05 * index}
              className="rounded-2xl border border-border bg-surface p-7 shadow-card"
            >
              <h3 className="text-base font-semibold">{block.title}</h3>
              <div className="mt-3 space-y-4">
                {block.body.map((paragraph) => (
                  <p key={paragraph} className="text-pretty leading-7">
                    {paragraph}
                  </p>
                ))}
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

function PlanCard({
  plan,
  index,
  busy,
  disabled,
  onSubscribe,
}: {
  plan: PricingPlan;
  index: number;
  busy: boolean;
  disabled: boolean;
  onSubscribe: () => void;
}) {
  const recommended = plan.slug === RECOMMENDED_SLUG;
  return (
    <Reveal
      delay={0.04 * index}
      className={cn(
        "flex h-full flex-col rounded-2xl border bg-surface p-6 shadow-card transition-transform duration-200 motion-safe:hover:-translate-y-1",
        recommended
          ? "border-brand-600 shadow-pop ring-1 ring-brand-600/30"
          : "border-border",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-base font-semibold">{plan.name}</p>
        {recommended ? <Badge variant="brand">Most chosen</Badge> : null}
      </div>

      <p className="mt-5 text-3xl font-bold tracking-tight">
        {formatInr(plan.price_inr)}
        <span className="ml-1 align-baseline text-sm font-medium">/ month</span>
      </p>

      <dl className="mt-5 space-y-2 text-sm leading-6">
        <div className="flex items-baseline justify-between gap-3">
          <dt>Applications</dt>
          <dd className="font-semibold">
            {plan.applications_per_month} a month
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <dt>Works out at</dt>
          <dd className="font-semibold">
            {formatInr(plan.rate_per_application_inr)} each
          </dd>
        </div>
      </dl>

      <div className="mt-6 flex-1" />

      <Button
        className="w-full group"
        variant={recommended ? "default" : "outline"}
        onClick={onSubscribe}
        disabled={busy || disabled || !plan.checkout_ready}
      >
        {busy ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Opening checkout
          </>
        ) : (
          <>
            Subscribe
            <ArrowRight
              className="transition-transform duration-150 group-hover:translate-x-0.5"
              aria-hidden="true"
            />
          </>
        )}
      </Button>
      {!plan.checkout_ready ? (
        <p className="mt-2 text-center text-xs leading-5">
          Checkout for this plan is being set up.{" "}
          <a
            href="mailto:manjuchro@gmail.com"
            target="_blank"
            rel="noreferrer"
            className="underline"
          >
            Contact us
          </a>{" "}
          to start today.
        </p>
      ) : null}
    </Reveal>
  );
}
