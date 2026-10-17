import Link from "next/link";

import { Logo } from "@/components/brand";
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
    <div className="flex min-h-dvh flex-col bg-canvas text-ink">
      <LandingView />
      <SiteHeader />

      <main id="main" className="flex-1 px-6 pb-20 pt-28 lg:px-10 lg:pt-36">
        <div className="mx-auto grid min-h-[34rem] max-w-6xl items-center gap-16 lg:grid-cols-[minmax(0,1fr)_24rem]">
          <div>
            <h1 className="type-display">Hiring, made simple.</h1>
            <p className="mt-6 type-lead">
              Vivekium helps companies hire with confidence.
            </p>
            <div className="mt-9 flex flex-col gap-3 sm:flex-row">
              <Button asChild size="lg">
                <Link href="#start">See pricing plans</Link>
              </Button>
              <Button asChild size="lg" variant="outline">
                <Link href="/company/login">Company login</Link>
              </Button>
            </div>
          </div>
          <div
            aria-hidden="true"
            className="hidden aspect-square place-items-center border border-border bg-surface shadow-hero lg:grid"
          >
            <Logo variant="mark" height={190} />
          </div>
        </div>
      </main>

      <section id="start" aria-labelledby="start-title" className="border-t border-border bg-surface/45 px-6 py-20 lg:px-10 lg:py-28">
        <div className="mx-auto grid max-w-6xl gap-12 xl:grid-cols-[18rem_1fr]">
          <div>
            <p className="type-eyebrow text-teal-700">Start</p>
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
