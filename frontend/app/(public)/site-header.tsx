"use client";

import * as React from "react";
import Link from "next/link";
import { Menu, X } from "lucide-react";

import { Logo } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/#workflow", label: "Product tour" },
  { href: "/#features", label: "Platform" },
  { href: "/about", label: "About" },
  { href: "/insights", label: "Insights" },
  { href: "/docs", label: "Docs" },
  { href: "/#pricing", label: "Pricing" },
];

/**
 * Public site header. Glass is used here and in the hero only, per
 * the public design system, and only once the page has scrolled so the top of
 * the page reads as one uninterrupted surface.
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
              className="rounded-md px-3 py-2 text-sm font-medium transition-colors hover:bg-brand-100/70 hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {item.label}
            </a>
          ))}
        </nav>

        <div className="hidden items-center gap-2 md:flex">
          <Button asChild variant="ghost" size="sm">
            <a
              href="mailto:manjuchro@gmail.com?subject=PickReady%20enquiry"
              target="_blank"
              rel="noreferrer"
            >
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
          className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-border md:hidden"
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
                className="rounded-md px-2 py-3 text-base font-medium"
              >
                {item.label}
              </a>
            ))}
          </nav>
          <div className="mt-3 flex flex-col gap-2">
            <Button asChild variant="ghost">
              <a
                href="mailto:manjuchro@gmail.com?subject=PickReady%20enquiry"
                target="_blank"
                rel="noreferrer"
              >
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
