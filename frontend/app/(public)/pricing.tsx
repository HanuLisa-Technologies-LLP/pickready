"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Check } from "lucide-react";

import { homePathForRole, useAuth } from "@/lib/auth-context";
import { REQUEST_ACCESS_HREF } from "@/lib/site";
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
 * per credit, purchased in packs, consumed per completed PRISM Report, 1.0
 * credit for a Non-STEM role and 1.5 for a STEM role, classified by the
 * platform. No monthly subscription exists, no annual plan
 * exists. Credits bought now stay valid for three months from purchase
 * (change request 25, new grants only); credits granted before expiry was
 * introduced keep the never-expire promise their invoices printed, and the
 * page states both so neither reads as a promise the product breaks.
 *
 * The figures here are the DIRECTIVE'S OWN fixed numbers, written as
 * constants: Part 5 fixes the price per credit, the pack sizes and the bonus
 * levels, so there is no server price list for this surface to disagree with.
 * The transactional truth (setup-fee waiver state, trial availability for the
 * signed-in account) lives on the portal's billing page, which is where every
 * card routes: purchase is an in-portal act, and a public page that opened a
 * checkout would have to guess at account state it cannot see. A signed-out
 * visitor is sent to sign in with billing as the destination, never to
 * `/register`: that is candidate sign-up, and a company is given its
 * workspace by Vivekium (the request-access line under the packs).
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
      "One credit costs Rs. 600, plus 18% GST. A completed PRISM Report consumes 1.0 credit for a Non-STEM role and 1.5 credits for a STEM role. Technical roles run a deeper AI assessment, and the platform classifies each role itself from the job description. The headline price never changes either way.",
      "A candidate who starts an assessment and never finishes consumes a third of the role's rate. A candidate who never opens the invitation consumes a fifteenth of a credit. Reviewing a profile carried over from an earlier posting uses a twentieth.",
      "Credits you buy stay valid for three months from purchase, and credits granted before expiry was introduced never expire. There is no monthly plan, no annual contract and no minimum usage: buy credits when you hire, and use them on any role while they are valid.",
    ],
  },
  {
    title: "Jobs and renewals",
    body: [
      "Post as many roles as you like. A job stays live for thirty days, then allows five more days in which people who already applied can still update what they sent.",
      // "When a posting closes" read as the Close action, which withholds a
      // job's assessment records and has no reopen. Renewal is what follows
      // the end of the thirty day window, so that is what this says.
      "When the thirty day window ends you can renew the posting for another thirty days. Everyone who applied the first time round stays in your dashboard, fully readable, marked as an earlier applicant. Their profiles do not leave when the window does.",
    ],
  },
];

/**
 * The shared feature strip. Identical whatever you buy, said once.
 *
 * REWIRED 2026-09-28. Four parameter matching, the per-candidate technical
 * bank, the "Profile Intelligence" name, the continuous conversation and the
 * ten stage count all described a product that no longer ships, and "stays
 * exportable" named an export no screen offers. Each line is now something a
 * customer can find in the product.
 */
const INCLUDED = [
  "Unlimited jobs, drawing on one credit pool",
  "Unlimited team members, no per seat fee",
  "Skills drafted from your JD, decided by your team",
  "AI Match on resume evidence, in words",
  "Questions written per candidate from the job's skills",
  "One proctored assessment, every skill asked",
  "Coding questions run in a sandbox",
  "Full PRISM Report",
  "Three radar charts, no numbers on them",
  "Proctoring Report",
  "Candidate databank",
  "Validated hiring pipeline",
  "Compliance document vault",
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
  // setup-fee state are known. A customer's team goes straight to billing;
  // anybody else signs in first with billing as the destination. It used to
  // send a signed-out visitor through `/register`, which creates a CANDIDATE
  // account, and send a signed-in candidate to a portal they cannot open.
  const goToBilling = React.useCallback(() => {
    const onCustomerTeam = user ? homePathForRole(user.role) === "/org" : false;
    router.push(onCustomerTeam ? "/org/billing" : "/login?next=%2Forg%2Fbilling");
  }, [router, user]);

  return (
    <section
      id="pricing"
      className="relative scroll-mt-24 py-20 lg:py-24"
      aria-labelledby="pricing-title"
    >
      <div className="mx-auto max-w-6xl px-6 lg:px-10">
        <Reveal className="max-w-2xl">
          <Badge variant="brand" className="px-3 py-1 text-xs font-semibold">
            Pricing
          </Badge>
          <h2
            id="pricing-title"
            className="mt-5 text-balance text-2xl font-bold tracking-[-0.015em] sm:text-3xl"
          >
            One rate. {formatInr(PRICE_PER_CREDIT_INR)} per credit.
          </h2>
          {/* It said "no expiry" while the card below it states the three
              month validity of new credits; the qualified promise lives in
              "How credits work", where lib/credit-expiry-copy.test.ts reads
              it. */}
          <p className="mt-5 text-pretty text-lg leading-8">
            Buy credits when you hire and spend them per completed candidate
            report. No subscription and no per-seat fees.
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
                // Flat at rest, and the pointer is answered with the border
                // rather than with a lift: DESIGN.md section 6 keeps cards at
                // level 0, and a price is not a thing to be playful about.
                "flex h-full flex-col border bg-surface transition-colors duration-150 p-6 hover:border-field-hover",
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
                <p className="mt-1 text-sm">{pack.note}</p>
              )}

              <dl className="mt-5 space-y-2 text-sm">
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
                Buy credits
                <ArrowRight
                  className="transition-transform duration-150 group-hover:translate-x-0.5"
                  aria-hidden="true"
                />
              </Button>
            </Reveal>
          ))}
        </div>

        <Reveal delay={0.05} className="mt-5 text-sm">
          <p>
            Prices exclude 18% GST. A one-time account setup fee of{" "}
            {formatInr(5000)} applies to your first purchase and is currently
            waived for early accounts. One report consumes 1.0 credit for a
            Non-STEM role and 1.5 credits for a STEM role. New to Vivekium?{" "}
            <a
              href={REQUEST_ACCESS_HREF}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium underline underline-offset-4 hover:text-brand-600"
            >
              Request access
            </a>{" "}
            and we set up your company&apos;s workspace.
          </p>
        </Reveal>

        {/* Enterprise: a full-width banner, not a fifth column. It has no
            self-serve checkout, so giving it a buy-shaped card would be
            promising a button that cannot exist. */}
        <Reveal
          delay={0.05}
          className="mt-5 flex flex-col gap-5 border border-border bg-surface p-7 sm:flex-row sm:items-center sm:justify-between"
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
              rel="noopener noreferrer"
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
                <span className="flex items-start gap-2.5 text-sm">
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
              className="border border-border bg-surface p-7"
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
