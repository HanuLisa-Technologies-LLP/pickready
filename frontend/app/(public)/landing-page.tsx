import Link from "next/link";

import { displayFont } from "@/app/display-font";
import { Button } from "@/components/ui/button";

import { SiteFooter } from "./site-footer";
import { SiteHeader } from "./site-header";

/**
 * The landing page: one screen, no scrolling (owner, 2026-09-29).
 *
 * The public site deliberately says almost nothing about the product: a name,
 * one line, and the ways in (pricing, company sign in, company registration,
 * candidate sign in). It fits the viewport at every width, so there is nothing
 * below the fold to find. The header and footer are rendered here because
 * `app/page.tsx` sits at the app root and does not get `app/(public)/layout.tsx`.
 */
export function LandingPage() {
  return (
    <div
      className={`${displayFont.variable} flex h-dvh min-h-[32rem] flex-col overflow-hidden bg-canvas text-ink`}
    >
      <SiteHeader />

      <main
        id="main"
        className="flex flex-1 flex-col items-center justify-center px-6 pt-16 text-center"
      >
        <h1 className="type-display">
          Hiring, made simple.
        </h1>
        <p className="mt-4 max-w-md text-base sm:text-lg">
          ReadyPick helps companies hire with confidence.
        </p>
        <div className="mt-8 flex flex-col gap-3 sm:flex-row">
          <Button asChild size="lg">
            <Link href="/pricing">See pricing plans</Link>
          </Button>
          <Button asChild size="lg" variant="outline">
            <Link href="/company/login">Company login</Link>
          </Button>
        </div>
      </main>

      <SiteFooter />
    </div>
  );
}
