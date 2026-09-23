import type { Metadata } from "next";
import Link from "next/link";

import { SiteJsonLd } from "@/components/site-json-ld";
import { SITE_NAME } from "@/lib/site";

import { LandingPage } from "./(public)/landing-page";

/**
 * THE LAUNCH GATE.
 *
 * Production serves the holding page. The optimised landing page is finished
 * and composed in `app/(public)/landing-page.tsx`, and it goes live when this
 * one variable is set to the string "true" at build time, and at no other
 * moment. Anything else, unset included, keeps the holding page, so the
 * default is the state the owner asked for rather than the state a missing
 * variable happens to produce.
 *
 * THIS IS A LAUNCH GATE, NOT A DUAL CODE PATH. The distinction is the one the
 * repository already draws with `AWS_DEPLOY_ENABLED` and
 * `PILOT_DEPLOY_ENABLED`: there is exactly one landing page and exactly one
 * holding page, neither is a variant of the other, and the flag selects which
 * of the two is served rather than switching between two implementations of
 * the same behaviour. It is a one-way door with a date on it: when the owner
 * flips it, the holding page and this branch come out in the same change.
 *
 * `NEXT_PUBLIC_` is required because Next inlines the value at build time from
 * a literal reference. A computed lookup would resolve to undefined in the
 * browser bundle, which reads exactly like the flag being off.
 *
 * KNOWN AND ACCEPTED: the landing module is imported whichever way the flag
 * falls, so the holding page's route carries chunks it never renders. A lazy
 * import would remove that weight and add a loading state to a page whose
 * whole job is to appear at once, for a page that is days from being deleted.
 * The trade is recorded here rather than left for somebody to rediscover.
 */
const LANDING_LIVE = process.env.NEXT_PUBLIC_LANDING_LIVE === "true";

/**
 * THE ROOT SEGMENT DOES NOT GET ITS OWN TITLE TEMPLATE, which is why every
 * title below spells out the product name. `title.template` in `app/layout.tsx`
 * applies to CHILD segments; `app/page.tsx` is the same segment as that layout,
 * so it resolves against `title.default` instead and a bare "Site Under
 * Construction" would ship with no product name at all.
 *
 * `openGraph` and `twitter` are stated rather than inherited for the reason
 * `lib/site.ts` records: Next replaces the parent's Open Graph object rather
 * than merging into it, so a page that leaves them out serves the root
 * layout's card, and the root layout's card names the landing page. Here that
 * happens to be the same page, but the holding page's card was advertising a
 * product tour that is not what `/` is currently serving.
 */
const HOLDING_TITLE = `Site Under Construction | ${SITE_NAME}`;
const HOLDING_DESCRIPTION = "Vivekium is preparing something new. Check back soon.";
const LANDING_TITLE = "Vivekium, know every candidate before you meet them";
const LANDING_DESCRIPTION =
  "Vivekium reads every applicant against the role, runs a structured assessment built from the job itself, and returns one readable report per candidate.";

export const metadata: Metadata = LANDING_LIVE
  ? {
      title: LANDING_TITLE,
      description: LANDING_DESCRIPTION,
      alternates: { canonical: "/" },
      openGraph: {
        type: "website",
        siteName: SITE_NAME,
        url: "/",
        title: LANDING_TITLE,
        description: LANDING_DESCRIPTION,
      },
      twitter: {
        card: "summary_large_image",
        title: LANDING_TITLE,
        description: LANDING_DESCRIPTION,
      },
    }
  : {
      title: HOLDING_TITLE,
      description: HOLDING_DESCRIPTION,
      // Relative, resolved against `metadataBase` in the root layout. Stating
      // it is what stops a trailing slash, a query string or a preview host
      // from becoming a second URL for the same page.
      alternates: { canonical: "/" },
      openGraph: {
        type: "website",
        siteName: SITE_NAME,
        url: "/",
        title: HOLDING_TITLE,
        description: HOLDING_DESCRIPTION,
      },
      twitter: {
        card: "summary_large_image",
        title: HOLDING_TITLE,
        description: HOLDING_DESCRIPTION,
      },
    };

export default function RootPage() {
  // Site-level structured data belongs on the home page whichever of the
  // two it is serving: the holding page is still readypick.ai, and it is the
  // page a crawler reaches first. `app/(public)/layout.tsx` carries it for
  // every other public page, and this route does not use that layout, so
  // nothing renders it twice.
  return (
    <>
      <SiteJsonLd />
      {LANDING_LIVE ? <LandingPage /> : <SiteUnderConstruction />}
    </>
  );
}

/**
 * The holding page, byte for byte what production has been serving. It is
 * deliberate work, not a placeholder, so it is moved rather than edited: the
 * only change is that it is now a named component below the gate instead of
 * the default export.
 */
function SiteUnderConstruction() {
  return (
    <div className="relative min-h-screen bg-canvas">
      {/* Faint structural grid, decorative only. */}
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0"
        style={{
          backgroundImage:
            "linear-gradient(to right, rgba(10,37,64,0.05) 1px, transparent 1px), linear-gradient(to bottom, rgba(10,37,64,0.05) 1px, transparent 1px)",
          backgroundSize: "84px 84px",
        }}
      />

      <div className="relative mx-auto flex min-h-screen max-w-[1400px] flex-col p-3 sm:p-4">
        <header className="flex h-[68px] items-center border border-navy-200/60 bg-white px-5 sm:h-[78px] sm:px-9">
          <span className="text-2xl font-bold tracking-[-0.045em] text-ink sm:text-[28px]">
            Ready<span className="text-teal-700">Pick</span>
          </span>
        </header>

        <main className="relative flex flex-1 items-center justify-center overflow-hidden border border-t-0 border-navy-900 bg-navy-900">
          <div className="w-[min(980px,calc(100%-4rem))] px-6 py-20 text-center sm:py-24">
            <span className="mb-6 inline-block text-sm font-bold leading-tight text-teal-400">
              Vivekium
            </span>

            <h1 className="text-balance text-[clamp(3.5rem,10vw,8.9rem)] font-bold leading-[0.98] tracking-[-0.09em] text-white">
              Ready<span className="text-teal-400">Pick</span>
            </h1>

            <p className="mt-6 text-balance text-[clamp(1.3rem,3vw,2.5rem)] font-semibold leading-[1.15] tracking-[-0.03em] text-white">
              The candidate intelligence platform
            </p>

            <div className="mt-14">
              <p className="text-balance text-[clamp(2.1rem,5vw,4rem)] font-semibold lowercase leading-[1.05] tracking-[-0.05em] text-white">
                site under construction
              </p>
              <span
                aria-hidden="true"
                className="mx-auto mt-7 block h-[3px] w-[72px] bg-teal-400"
              />
            </div>
          </div>

          <footer className="absolute bottom-5 left-1/2 -translate-x-1/2">
            {/* Not a labeled control by design: same weight, color and
                decoration as static footer text, entry point to the
                platform for those who already know it is there. */}
            <Link
              href="/login"
              className="text-sm font-semibold leading-none text-white no-underline"
            >
              Vivekium
            </Link>
          </footer>
        </main>
      </div>
    </div>
  );
}
