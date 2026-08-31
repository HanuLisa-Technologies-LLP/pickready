"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Check } from "lucide-react";

import { useAuth } from "@/lib/auth-context";
import {
  Reveal,
  RevealStagger,
  StaggerItem,
} from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Public pricing (Master Directive Part 5).
 *
 * The model this section sells is the credit model and nothing else: Rs. 600
 * per credit, purchased in packs, consumed per completed ReadyPick
 * Intelligence Report - 1.0 credit for a Non-STEM role, 1.5 for a STEM role,
 * classified by the platform. No monthly subscription exists, no annual plan
 * exists, and credits never expire (Rule 4 is a stated promise, so the page
 * states it).
 *
 * The figures here are the DIRECTIVE'S OWN fixed numbers, written as
 * constants: Part 5 fixes the price per credit, the pack sizes and the bonus
 * levels, so there is no server price list for this surface to disagree with.
 * The transactional truth (setup-fee waiver state, trial availability for the
 * signed-in account) lives on the portal's billing page, which is where every
 * card routes: purchase is an in-portal act, and a public page that opened a
 * checkout would have to guess at account state it cannot see.
 *
 * Bonus credits are a GIFT, never a discount (Rule 3): each card quotes the
 * same Rs. 600 rate and shows the bonus as extra credits, so the reader is
 * never shown a crossed-out price.
 */

/** Part 5 §1 / §3.2, verbatim. */
const PRICE_PER_CREDIT_INR = 600;

const PACKS = [
  {
    slug: "trial_20",
    name: "First purchase",
    credits: 20,
    bonus: 0,
    price: 12000,
    note: "One-time trial minimum for new accounts.",
    recommended: false,
  },
  {
    slug: "standard_50",
    name: "Standard",
    credits: 50,
    bonus: 0,
    price: 30000,
    note: "The standard minimum for every later top-up.",
    recommended: false,
  },
  {
    slug: "volume_100",
    name: "Volume",
    credits: 100,
    bonus: 5,
    price: 60000,
    note: "105 credits in your pool.",
    recommended: true,
  },
  {
    slug: "volume_200",
    name: "Scale",
    credits: 200,
    bonus: 15,
    price: 120000,
    note: "215 credits in your pool.",
    recommended: false,
  },
] as const;

const MODEL_COPY = [
  {
    title: "How credits work",
    body: [
      "One credit costs Rs. 600, plus 18% GST. A completed ReadyPick Intelligence Report consumes 1.0 credit for a Non-STEM role and 1.5 credits for a STEM role - technical roles run a deeper AI assessment, and the platform classifies each role itself from the job description. The headline price never changes either way.",
      "A candidate who starts an assessment and never finishes consumes a third of the role's rate. A candidate who never opens the invitation consumes a fifteenth of a credit. Reviewing a profile carried over from an earlier posting uses a twentieth.",
      "Credits never expire. There is no monthly plan, no annual contract and no minimum usage: buy credits when you hire, and whatever you do not use waits for the next role.",
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

/** The shared feature strip. Identical whatever you buy, said once. */
const INCLUDED = [
  "Unlimited jobs, drawing on one credit pool",
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

  // Purchase happens inside the portal, where the account's trial and
  // setup-fee state are known. Signed out, the card carries the visitor
  // through sign-up and lands them on billing.
  const goToBilling = React.useCallback(() => {
    router.push(user ? "/org/billing" : "/register?next=/org/billing");
  }, [router, user]);

  return (
    <section id="pricing" className="relative scroll-mt-24 py-24 sm:py-28">
      <div className="mx-auto max-w-6xl px-6 lg:px-10">
        <Reveal className="max-w-2xl">
          <Badge variant="brand" className="px-3 py-1 text-xs font-semibold">
            Pricing
          </Badge>
          <h2 className="mt-5 text-balance text-3xl font-bold leading-tight sm:text-4xl">
            One rate. {formatInr(PRICE_PER_CREDIT_INR)} per credit.
          </h2>
          <p className="mt-5 text-pretty text-lg leading-8">
            Buy credits when you hire, spend one per candidate report, and keep
            what you do not use. No subscription, no expiry, no per-seat fees.
          </p>
        </Reveal>

        {/* Pack cards. `sm:grid-cols-2 xl:grid-cols-4` so they STACK on a
            phone and pair on a tablet rather than squeezing into 375px. */}
        <div className="mt-12 grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
          {PACKS.map((pack, index) => (
            <Reveal
              key={pack.slug}
              delay={0.04 * index}
              className={cn(
                "flex h-full flex-col border bg-surface p-6 shadow-card transition-transform duration-200 motion-safe:hover:-translate-y-1",
                pack.recommended
                  ? "border-brand-600 ring-1 ring-brand-600/30"
                  : "border-border",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <p className="text-base font-semibold">{pack.name}</p>
                {pack.recommended ? (
                  <Badge variant="brand">Most chosen</Badge>
                ) : null}
              </div>

              <p className="mt-5 text-3xl font-bold tracking-tight">
                {pack.credits}
                <span className="ml-1.5 align-baseline text-sm font-medium">
                  credits
                </span>
              </p>
              {pack.bonus > 0 ? (
                <p className="mt-1 text-sm font-semibold text-teal-700">
                  + {pack.bonus} bonus credits free
                </p>
              ) : (
                <p className="mt-1 text-sm leading-6 opacity-70">{pack.note}</p>
              )}

              <dl className="mt-5 space-y-2 text-sm leading-6">
                <div className="flex items-baseline justify-between gap-3">
                  <dt>Price</dt>
                  <dd className="font-semibold">
                    {formatInr(pack.price)} + GST
                  </dd>
                </div>
                <div className="flex items-baseline justify-between gap-3">
                  <dt>Rate</dt>
                  <dd className="font-semibold">
                    {formatInr(PRICE_PER_CREDIT_INR)} per credit
                  </dd>
                </div>
                {pack.bonus > 0 ? (
                  <div className="flex items-baseline justify-between gap-3">
                    <dt>In your pool</dt>
                    <dd className="font-semibold">
                      {pack.credits + pack.bonus} credits
                    </dd>
                  </div>
                ) : null}
              </dl>

              <div className="mt-6 flex-1" />

              <Button
                className="w-full group"
                variant={pack.recommended ? "default" : "outline"}
                onClick={goToBilling}
              >
                Get started
                <ArrowRight
                  className="transition-transform duration-150 group-hover:translate-x-0.5"
                  aria-hidden="true"
                />
              </Button>
            </Reveal>
          ))}
        </div>

        <Reveal delay={0.05} className="mt-5 text-sm leading-6 opacity-80">
          <p>
            Prices exclude 18% GST. A one-time account setup fee of{" "}
            {formatInr(5000)} applies to your first purchase and is currently
            waived for early accounts. One report consumes 1.0 credit for a
            Non-STEM role and 1.5 credits for a STEM role.
          </p>
        </Reveal>

        {/* Enterprise: a full-width banner, not a fifth column. It has no
            self-serve checkout, so giving it a buy-shaped card would be
            promising a button that cannot exist. */}
        <Reveal
          delay={0.05}
          className="mt-5 flex flex-col gap-5 border border-border bg-surface p-7 shadow-card sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="max-w-2xl">
            <p className="text-lg font-semibold">Enterprise</p>
            <p className="mt-2 text-pretty leading-7">
              Hiring at a volume the packs do not fit, or across several
              entities. Same product, credits priced to your agreement, with
              onboarding support.
            </p>
          </div>
          <Button asChild size="lg" variant="outline" className="shrink-0">
            <a
              href="mailto:manjuchro@gmail.com?subject=Enterprise%20credits"
              target="_blank"
              rel="noreferrer"
            >
              Contact us
            </a>
          </Button>
        </Reveal>

        {/* The shared feature strip. Said once, for every pack. */}
        <Reveal
          delay={0.08}
          className="mt-10 border border-border bg-brand-100/40 p-7"
        >
          <p className="text-base font-semibold">
            Everything, whatever you buy
          </p>
          <p className="mt-2 max-w-3xl text-pretty leading-7">
            The list below is not a comparison table. Every item comes with the
            20-credit first purchase and with the 200-credit pack alike. The
            only thing a bigger pack buys is more assessments.
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

        {/* How credits work / Jobs and renewals. */}
        <div className="mt-10 grid gap-5 lg:grid-cols-2">
          {MODEL_COPY.map((block, index) => (
            <Reveal
              key={block.title}
              delay={0.05 * index}
              className="border border-border bg-surface p-7 shadow-card"
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
