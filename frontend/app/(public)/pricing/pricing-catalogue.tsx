"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowRight, Check } from "lucide-react";

import { apiGet } from "@/lib/api";
import type {
  PublishedCatalogue,
  PublishedConsumption,
  PublishedPack,
} from "@/lib/types";
import { Reveal, RevealStagger, StaggerItem } from "@/components/motion";
import { ErrorState, LoadingCards } from "@/components/page-primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The public price list, rendered from the server's catalogue.
 *
 * ONE SOURCE OF TRUTH (owner spec, section 4.2). This file holds no
 * price, pack size, bonus, GST rate, validity term or setup fee: every figure
 * below comes from `GET /billing/public/credit-packs`, which is built from the
 * same constants and the same arithmetic checkout and the GST invoice use. The
 * copy this page replaced carried its own array of packs, which is exactly how
 * a public price drifts from the price charged.
 *
 * NO FALLBACK. When the catalogue cannot be read the page says so and offers a
 * retry. A cached or hardcoded price shown during an outage would be a price
 * nobody has confirmed checkout still charges.
 *
 * ACCOUNT-DEPENDENT ITEMS ARE MARKED AS SUCH. A visitor has no account, so the
 * packs are quoted at the standard price and the one-time setup fee is stated
 * as its rule (charged on the first purchase, waived for early accounts), with
 * the note that checkout shows whether it applies. The trial pack is marked
 * as a new account's first purchase only.
 *
 * Bonus credits are a GIFT, never a discount: every card quotes the same rate
 * per credit and shows the bonus as extra credits, never a crossed-out price.
 */

/** Where a company goes next. Built by the company onboarding and sign-in work. */
const REGISTER_COMPANY_HREF = "/company/register";
const COMPANY_LOGIN_HREF = "/company/login";
const ENTERPRISE_HREF = "mailto:manjuchro@gmail.com?subject=Enterprise%20credits";

/**
 * The shared feature strip. Identical whatever you buy, said once. Product
 * description, not pricing: nothing here is a figure the catalogue owns.
 */
const INCLUDED = [
  "Unlimited jobs, drawing on one credit pool",
  "Unlimited team members, no per seat fee",
  "Skills drafted from your JD, decided by your team",
  "AI Match on resume evidence, in words",
  "Questions written per candidate from the job's skills",
  "One proctored assessment, every skill asked",
  "Typed or spoken answers, multiple choice, fill in the blank",
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

function greatestCommonDivisor(a: number, b: number): number {
  return b === 0 ? a : greatestCommonDivisor(b, a % b);
}

/**
 * A consumption rate in credits, EXACT. The server states rates in integer
 * sub-units so a third of a credit is never a rounded 0.33: a rate that is a
 * whole or half credit reads as a decimal, and anything finer as a fraction.
 */
export function creditsText(subunits: number, perCredit: number): string {
  if ((subunits * 2) % perCredit === 0) {
    const value = subunits / perCredit;
    return `${value} ${value === 1 ? "credit" : "credits"}`;
  }
  const divisor = greatestCommonDivisor(subunits, perCredit);
  return `${subunits / divisor}/${perCredit / divisor} of a credit`;
}

function PackCard({
  pack,
  catalogue,
  index,
}: {
  pack: PublishedPack;
  catalogue: PublishedCatalogue;
  index: number;
}) {
  return (
    <Reveal
      delay={0.04 * index}
      className={cn(
        // Flat at rest, and the pointer is answered with the border rather
        // than with a lift: DESIGN.md section 6 keeps cards at level 0, and a
        // price is not a thing to be playful about.
        "flex h-full flex-col border border-border bg-surface p-6 transition-colors duration-150 hover:border-field-hover",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-base font-semibold">{pack.label}</p>
        {pack.new_accounts_only ? (
          <Badge variant="outline">New accounts only</Badge>
        ) : null}
      </div>

      <p className="mt-5 text-3xl font-semibold tabular-nums">
        {pack.credits_total}
        <span className="ml-1.5 align-baseline text-sm font-medium">credits</span>
      </p>
      {pack.bonus_credits > 0 ? (
        <p className="mt-1 text-sm font-semibold text-teal-700">
          {pack.credits} purchased + {pack.bonus_credits} bonus credits free
        </p>
      ) : pack.new_accounts_only ? (
        <p className="mt-1 text-sm">A new account&apos;s first purchase only.</p>
      ) : null}

      <dl className="mt-5 space-y-2 text-sm">
        <div className="flex items-baseline justify-between gap-3">
          <dt>Price</dt>
          <dd className="font-semibold tabular-nums">
            {formatInr(pack.subtotal_inr)}
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <dt>GST at {catalogue.gst_rate_percent}%</dt>
          <dd className="font-semibold tabular-nums">{formatInr(pack.gst_inr)}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-3 border-t border-border pt-2">
          <dt className="font-semibold">Total</dt>
          <dd className="text-base font-semibold tabular-nums">
            {formatInr(pack.total_inr)}
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <dt>Valid for</dt>
          <dd className="font-semibold">{pack.validity_months} months</dd>
        </div>
      </dl>
    </Reveal>
  );
}

function ConsumptionTable({ catalogue }: { catalogue: PublishedCatalogue }) {
  return (
    <div className="mt-4 overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">
          What each billable event draws from your credit pool
        </caption>
        <thead>
          <tr className="border-b border-border">
            <th scope="col" className="py-2 pr-4 font-semibold">
              Event
            </th>
            <th scope="col" className="py-2 pr-4 font-semibold">
              Non-STEM role
            </th>
            <th scope="col" className="py-2 font-semibold">
              STEM role
            </th>
          </tr>
        </thead>
        <tbody>
          {catalogue.consumption.map((rate: PublishedConsumption) => (
            <tr key={rate.event_type} className="border-b border-border last:border-b-0">
              <th scope="row" className="py-2 pr-4 font-medium">
                {rate.label}
              </th>
              <td className="py-2 pr-4 tabular-nums">
                {creditsText(rate.non_stem_subunits, catalogue.subunits_per_credit)}
              </td>
              <td className="py-2 tabular-nums">
                {creditsText(rate.stem_subunits, catalogue.subunits_per_credit)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CompanyActions({ className }: { className?: string }) {
  return (
    <div className={cn("flex flex-col gap-3 sm:flex-row", className)}>
      <Button asChild size="lg" className="group">
        <Link href={REGISTER_COMPANY_HREF}>
          Register company
          <ArrowRight
            className="transition-transform duration-150 group-hover:translate-x-0.5"
            aria-hidden="true"
          />
        </Link>
      </Button>
      <Button asChild size="lg" variant="outline">
        <Link href={COMPANY_LOGIN_HREF}>Company login</Link>
      </Button>
    </div>
  );
}

function Catalogue({ catalogue }: { catalogue: PublishedCatalogue }) {
  const bonusLevels = catalogue.bonus_levels
    .map(
      (level) =>
        `${level.bonus_credits} bonus credits at ${level.min_credits} credits or more`
    )
    .join(", and ");

  return (
    <>
      <Reveal className="max-w-2xl">
        <Badge variant="brand" className="px-3 py-1 text-xs font-semibold">
          Pricing
        </Badge>
        <h1 id="pricing-title" className="mt-5 type-section-title">
          One rate. {formatInr(catalogue.price_per_credit_inr)} per credit.
        </h1>
        <p className="mt-5 type-lead">
          Buy credits when you hire and spend them per completed candidate
          report. No subscription, no plan to renew and no per-seat fees.
        </p>
        <CompanyActions className="mt-8" />
      </Reveal>

      {/* Pack cards. `sm:grid-cols-2 xl:grid-cols-3` so they STACK on a
          phone and pair on a tablet rather than squeezing into 375px. */}
      <div className="mt-12 grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
        {catalogue.packs.map((pack, index) => (
          <PackCard
            key={pack.slug}
            pack={pack}
            catalogue={catalogue}
            index={index}
          />
        ))}
      </div>

      <Reveal delay={0.05} className="mt-5 space-y-3 text-sm">
        <p>
          Prices are per credit purchased, at{" "}
          {formatInr(catalogue.price_per_credit_inr)} each, plus{" "}
          {catalogue.gst_rate_percent}% GST. A custom purchase is at least{" "}
          {catalogue.min_custom_credits} credits
          {bonusLevels ? `, and volume adds ${bonusLevels}, free` : ""}.
        </p>
        <p>
          <span className="font-semibold">Depends on your account:</span> a
          one-time account setup fee of {formatInr(catalogue.setup_fee_inr)}{" "}
          plus {formatInr(catalogue.setup_fee_gst_inr)} GST applies to a
          company&apos;s first purchase, and is waived for the first{" "}
          {catalogue.setup_fee_waiver_limit} client accounts. Checkout shows
          whether it applies to yours before you pay.
        </p>
        <p>
          Credits bought now stay valid for {catalogue.credit_validity_months}{" "}
          months from purchase. Credits granted before expiry was introduced
          never expire.
        </p>
      </Reveal>

      <div className="mt-10 grid gap-5 lg:grid-cols-2">
        <Reveal className="border border-border bg-surface p-7">
          <h2 className="text-base font-semibold">How credits are used</h2>
          <p className="mt-3 type-prose-lg">
            A completed PRISM Report draws the role&apos;s full rate, and the
            platform classifies each role as STEM or Non-STEM from its job
            description. The price per credit never changes either way.
          </p>
          <ConsumptionTable catalogue={catalogue} />
        </Reveal>

        <Reveal delay={0.05} className="border border-border bg-surface p-7">
          <h2 className="text-base font-semibold">Jobs and renewals</h2>
          <div className="mt-3 space-y-4">
            <p className="type-prose-lg">
              Post as many roles as you like. A job stays live for thirty days,
              then allows five more days in which people who already applied
              can still update what they sent.
            </p>
            <p className="type-prose-lg">
              When the thirty day window ends you can renew the posting for
              another thirty days. Everyone who applied the first time round
              stays in your dashboard, fully readable, marked as an earlier
              applicant.
            </p>
          </div>
        </Reveal>
      </div>

      {/* The shared feature strip. Said once, for every pack. */}
      <Reveal delay={0.08} className="mt-10 border border-border bg-brand-100/40 p-7">
        <h2 className="text-base font-semibold">Everything, whatever you buy</h2>
        <p className="mt-2 type-prose-lg">
          The list below is not a comparison table. Every item comes with the
          smallest pack and the largest alike. The only thing a bigger pack
          buys is more assessments.
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

      {/* Enterprise: a full-width banner, not a card. It has no self-serve
          checkout, so giving it a buy-shaped card would be promising a button
          that cannot exist. */}
      <Reveal
        delay={0.05}
        className="mt-5 flex flex-col gap-5 border border-border bg-surface p-7 sm:flex-row sm:items-center sm:justify-between"
      >
        <div className="max-w-2xl">
          <h2 className="text-lg font-semibold">Enterprise</h2>
          <p className="mt-2 text-pretty leading-7">
            Hiring at a volume the packs do not fit, or across several
            entities. Same product, credits priced to your agreement, with
            onboarding support.
          </p>
        </div>
        <Button asChild size="lg" variant="outline" className="shrink-0">
          <a href={ENTERPRISE_HREF} target="_blank" rel="noopener noreferrer">
            Contact us
          </a>
        </Button>
      </Reveal>

      <Reveal delay={0.05} className="mt-10 flex flex-col items-start gap-4">
        <p className="text-base font-semibold">Ready to start hiring?</p>
        <CompanyActions />
      </Reveal>
    </>
  );
}

export function PricingCatalogue() {
  const [catalogue, setCatalogue] = React.useState<PublishedCatalogue | null>(
    null
  );
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [reloadCount, setReloadCount] = React.useState(0);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiGet<PublishedCatalogue>("/billing/public/credit-packs")
      .then((result) => {
        if (!cancelled) setCatalogue(result);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setCatalogue(null);
        setError(e instanceof Error ? e.message : "The price list could not be loaded.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadCount]);

  return (
    <section
      className="mx-auto max-w-6xl px-6 py-16 lg:px-10 lg:py-24"
      aria-labelledby={catalogue ? "pricing-title" : undefined}
      aria-busy={loading}
    >
      {loading ? (
        <LoadingCards count={3} label="Loading the price list" />
      ) : error || !catalogue ? (
        <div className="mx-auto max-w-3xl">
          <ErrorState
            title="The price list could not be loaded"
            description={`Prices are shown only as the server states them, so nothing is shown while it cannot be reached. ${error ?? ""}`.trim()}
            action={
              <Button
                variant="outline"
                onClick={() => setReloadCount((count) => count + 1)}
              >
                Try again
              </Button>
            }
          />
        </div>
      ) : (
        <Catalogue catalogue={catalogue} />
      )}
    </section>
  );
}
