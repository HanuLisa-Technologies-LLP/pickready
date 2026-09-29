"use client";

import * as React from "react";
import Link from "next/link";
import { Menu, X } from "lucide-react";

import { Logo } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { REQUEST_ACCESS_HREF } from "@/lib/site";
import { cn } from "@/lib/utils";

/**
 * The header's sections. The first two are ANCHORS INTO THE LANDING PAGE,
 * and each names a section id that `landing-page.tsx` mounts;
 * `lib/landing-links.test.ts` fails if one of them stops resolving. The rest
 * are routes under `app/(public)/` that the proxy admits without a session.
 *
 * Pricing is its own page (owner spec, 2026-09-29, section 4.1): the landing
 * page no longer carries the price list inline, and "See pricing plans" opens
 * `/pricing`, which renders the server's published catalogue.
 */
const NAV = [
  { href: "/#how-it-works", label: "How it works" },
  { href: "/#features", label: "Platform" },
  { href: "/pricing", label: "See pricing plans" },
  { href: "/about", label: "About" },
  { href: "/insights", label: "Insights" },
  { href: "/docs", label: "Docs" },
];

/**
 * Public site header. Glass is used here and in the hero only, per
 * the public design system, and only once the page has scrolled so the top of
 * the page reads as one uninterrupted surface.
 *
 * TWO ACTIONS, NOT THREE. It carried Contact, Log in and "Get started", and
 * "Get started" opened CANDIDATE sign-up: an employer who clicked it came out
 * the other side as a candidate with no workspace. A company is given a
 * workspace by Vivekium, so the primary action is Request access, and it is
 * the same mailbox Contact opened, which is why Contact is not repeated beside
 * it.
 */
export function SiteHeader() {
  const [scrolled, setScrolled] = React.useState(false);
  const [open, setOpen] = React.useState(false);

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
          {NAV.map((item) => (
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
            <Link href="/login">Log in</Link>
          </Button>
          <Button asChild size="sm">
            <a href={REQUEST_ACCESS_HREF} target="_blank" rel="noopener noreferrer">
              Request access
            </a>
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
            {NAV.map((item) => (
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
            <Button asChild variant="outline">
              <Link href="/login">Log in</Link>
            </Button>
            <Button asChild>
              <a href={REQUEST_ACCESS_HREF} target="_blank" rel="noopener noreferrer">
                Request access
              </a>
            </Button>
          </div>
        </div>
      ) : null}
    </header>
  );
}
