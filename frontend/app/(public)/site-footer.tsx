import Link from "next/link";

import { Logo } from "@/components/brand";
import { REQUEST_ACCESS_HREF } from "@/lib/site";

/**
 * The footer's links. Every internal one is a route under `app/` that the
 * proxy admits without a session, or an anchor `landing-page.tsx` mounts;
 * `lib/landing-links.test.ts` holds that for every public link on the page.
 *
 * TWO LINKS LEFT, AND WHY:
 *
 *  - "Join with an invite" opened `/join` with no token, which is a page that
 *    can only say "This invitation link is incomplete." The invitation email
 *    carries the real link, token included, so the footer has nothing to
 *    offer there.
 *  - "For employers" named `/employers` as though it were a page for buyers.
 *    It is the public directory of companies hiring through Vivekium, which is
 *    a page for job seekers, so it is labelled as what it is.
 */
const COLUMNS = [
  {
    heading: "Product",
    links: [
      { label: "How it works", href: "/#how-it-works" },
      { label: "Platform", href: "/#features" },
      { label: "See pricing plans", href: "/pricing" },
      { label: "Docs", href: "/docs" },
    ],
  },
  {
    heading: "Get access",
    links: [
      { label: "Log in", href: "/login" },
      { label: "Request access for your company", href: REQUEST_ACCESS_HREF },
      { label: "Employers hiring now", href: "/employers" },
      { label: "Create a candidate account", href: "/register" },
    ],
  },
  {
    heading: "Legal",
    links: [
      { label: "Privacy", href: "/privacy" },
      { label: "Terms", href: "/terms" },
      { label: "About", href: "/about" },
    ],
  },
];

export function SiteFooter() {
  return (
    <footer className="border-t border-border bg-surface/60">
      <div className="mx-auto max-w-6xl px-6 py-14 lg:px-10">
        <div className="grid gap-10 sm:grid-cols-2 lg:grid-cols-[minmax(0,1.4fr)_repeat(3,minmax(0,1fr))]">
          <div>
            <Logo variant="full" height={38} href="/" />
          </div>

          {COLUMNS.map((column) => (
            <nav key={column.heading} aria-label={column.heading}>
              {/* Full ink at 13px. The heading used to be `opacity-70`, which
                  is grey text by another name, and DESIGN.md section 3 admits
                  no exception for a faked one. */}
              <h2 className="type-eyebrow">
                {column.heading}
              </h2>
              <ul className="mt-4 space-y-3">
                {column.links.map((link) => (
                  <li key={link.label}>
                    <Link
                      href={link.href}
                      className="text-sm underline-offset-4 transition-colors hover:text-brand-600 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                    >
                      {link.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </nav>
          ))}
        </div>

        <div className="mt-12 flex flex-col gap-2 border-t border-border pt-6 text-sm sm:flex-row sm:items-center sm:justify-between">
          <p>
            &copy; {new Date().getFullYear()} Vivekium. All rights reserved.
          </p>
          <p>A Varpitech LLP product.</p>
        </div>
      </div>
    </footer>
  );
}
