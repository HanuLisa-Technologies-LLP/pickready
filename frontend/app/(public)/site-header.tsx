"use client";

import * as React from "react";
import Link from "next/link";
import { Menu, X } from "lucide-react";

import { Logo } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Nav items that are ANCHORS INTO THE LANDING PAGE. Every one of these needs
 * a section mounted at `/` to land on.
 *
 * THE BUG THIS FIXES: the header is rendered on about, insights, docs,
 * employers, privacy and terms, and it linked to `/#workflow`, `/#features`
 * and `/#pricing` while `/` served the holding page. Every one of those items
 * navigated to the construction screen and stopped there, site wide, with
 * nothing to tell the visitor that the target did not exist. They now render
 * only when the landing page is the thing being served.
 *
 * `#workflow` is not in this list even though the section exists: it is not
 * composed into the page (see workflow-showcase.tsx for why), so linking to it
 * would be the same dead end in a new place. A nav item is added back here
 * when something mounts its target, never in anticipation of one.
 */
const LANDING_NAV = [
  { href: "/#how-it-works", label: "How it works" },
  { href: "/#features", label: "Platform" },
  { href: "/#pricing", label: "Pricing" },
];

/** Items that resolve to a real route whatever `/` is currently serving. */
const PAGE_NAV = [
  { href: "/about", label: "About" },
  { href: "/insights", label: "Insights" },
  { href: "/docs", label: "Docs" },
];

const CONTACT_HREF = "mailto:manjuchro@gmail.com?subject=Vivekium%20enquiry";

export interface SiteHeaderProps {
  /**
   * Whether `/` is serving the landing page rather than the holding page.
   * Resolved from `NEXT_PUBLIC_LANDING_LIVE` by whoever renders the header,
   * and defaulting to false so a caller that forgets it renders no dead link.
   */
  landingLive?: boolean;
}

/**
 * Public site header. Glass is used here and in the hero only, per
 * the public design system, and only once the page has scrolled so the top of
 * the page reads as one uninterrupted surface.
 */
export function SiteHeader({ landingLive = false }: SiteHeaderProps) {
  const [scrolled, setScrolled] = React.useState(false);
  const [open, setOpen] = React.useState(false);

  const nav = React.useMemo(
    () => (landingLive ? [...LANDING_NAV, ...PAGE_NAV] : PAGE_NAV),
    [landingLive]
  );

  React.useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={cn(
        "fixed inset-x-0 top-0 z-50 w-full transition-[background-color,border-color,box-shadow] duration-200",
        scrolled
          ? "glass border-b shadow-card"
          : "border-b border-transparent bg-transparent"
      )}
    >
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-6 lg:px-10">
        <Logo variant="full" height={40} href="/" priority />

        <nav
          className="hidden items-center gap-1 md:flex"
          aria-label="Site sections"
        >
          {nav.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className="px-3 py-2 text-sm font-medium transition-colors hover:bg-brand-100/70 hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-canvas"
            >
              {item.label}
            </a>
          ))}
        </nav>

        <div className="hidden items-center gap-2 md:flex">
          <Button asChild variant="ghost" size="sm">
            <a href={CONTACT_HREF} target="_blank" rel="noopener noreferrer">
              Contact
            </a>
          </Button>
          <Button asChild variant="ghost" size="sm">
            <Link href="/login?initial_context=all">Log in</Link>
          </Button>
          <Button asChild size="sm">
            <Link href="/register?role=candidate">Get started</Link>
          </Button>
        </div>

        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          aria-controls="site-nav-mobile"
          aria-label={open ? "Close menu" : "Open menu"}
          className="inline-flex h-10 w-10 items-center justify-center border border-border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 md:hidden"
        >
          {open ? (
            <X className="h-5 w-5" aria-hidden="true" />
          ) : (
            <Menu className="h-5 w-5" aria-hidden="true" />
          )}
        </button>
      </div>

      {open ? (
        <div
          id="site-nav-mobile"
          className="glass border-t px-6 pb-6 pt-2 md:hidden"
        >
          <nav className="flex flex-col" aria-label="Site sections">
            {nav.map((item) => (
              <a
                key={item.href}
                href={item.href}
                onClick={() => setOpen(false)}
                className="border-b border-border px-2 py-3 text-base font-medium last:border-b-0"
              >
                {item.label}
              </a>
            ))}
          </nav>
          <div className="mt-4 flex flex-col gap-2">
            <Button asChild variant="ghost">
              <a href={CONTACT_HREF} target="_blank" rel="noopener noreferrer">
                Contact us
              </a>
            </Button>
            <Button asChild variant="outline">
              <Link href="/login?initial_context=all">Log in</Link>
            </Button>
            <Button asChild>
              <Link href="/register?role=candidate">Get started</Link>
            </Button>
          </div>
        </div>
      ) : null}
    </header>
  );
}
