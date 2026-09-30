import Link from "next/link";

import { displayFont } from "@/app/display-font";
import { Button } from "@/components/ui/button";

import { SiteFooter } from "./site-footer";
import { SiteHeader } from "./site-header";
import { LandingView } from "./landing-view";
import { PricingCatalogue } from "./pricing/pricing-catalogue";

/**
 * The landing page introduces the product and places monthly pricing inside
 * Start beside the Open a role action. The header and footer are rendered here because
 * `app/page.tsx` sits at the app root and does not get `app/(public)/layout.tsx`.
 */
export function LandingPage() {
  return (
    <div
      className={`${displayFont.variable} flex min-h-dvh flex-col bg-canvas text-ink`}
    >
      <LandingView />
      <SiteHeader />

      <main
        id="main"
        className="flex flex-1 flex-col items-center justify-center px-6 pb-20 pt-24 text-center"
      >
        <h1 className="type-display">
          Hiring, made simple.
        </h1>
        <p className="mt-4 max-w-md text-base sm:text-lg">
          ReadyPick helps companies hire with confidence.
        </p>
        <div className="mt-8 flex flex-col gap-3 sm:flex-row">
          <Button asChild size="lg">
            <Link href="#start">See pricing plans</Link>
          </Button>
          <Button asChild size="lg" variant="outline">
            <Link href="/company/login">Company login</Link>
          </Button>
        </div>
      </main>

      <section id="start" aria-labelledby="start-title" className="border-t border-border px-6 py-20">
        <div className="mx-auto grid max-w-7xl gap-12 xl:grid-cols-[18rem_1fr]">
          <div>
            <p className="type-eyebrow">Start</p>
            <h2 id="start-title" className="mt-4 type-section-title">Open a role.</h2>
            <p className="mt-5 type-prose-lg">Start with a paid 30-day Starter pilot. Keep the monthly plan or cancel anytime.</p>
            <Button asChild size="lg" className="mt-8">
              <Link href="/company/register">Open a role</Link>
            </Button>
          </div>
          <PricingCatalogue />
        </div>
      </section>

      <SiteFooter />
    </div>
  );
}
