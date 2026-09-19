import Link from "next/link";

import { Logo } from "@/components/brand";

/**
 * Product links that are anchors into the landing page. Same rule as the
 * header: they render only when `/` serves the landing page, because on the
 * holding page every one of them is a link to nowhere. See site-header.tsx for
 * the full note.
 */
const LANDING_LINKS = [
  { label: "How it works", href: "/#how-it-works" },
  { label: "Platform", href: "/#features" },
  { label: "Pricing", href: "/#pricing" },
];

/** Product links that resolve to a real route in either state. */
const PRODUCT_LINKS = [
  { label: "For employers", href: "/employers" },
  { label: "Docs", href: "/docs" },
];

const COLUMNS = [
  {
    heading: "Get access",
    links: [
      { label: "Log in", href: "/login?initial_context=all" },
      { label: "Create an account", href: "/register?role=candidate" },
      { label: "Join with an invite", href: "/join" },
      { label: "Contact us", href: "mailto:manjuchro@gmail.com" },
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

export interface SiteFooterProps {
  /**
   * Whether `/` is serving the landing page. Defaults to false so a caller
   * that forgets it renders no dead anchor.
   */
  landingLive?: boolean;
}

export function SiteFooter({ landingLive = false }: SiteFooterProps) {
  const columns = [
    {
      heading: "Product",
      links: landingLive
        ? [...LANDING_LINKS, ...PRODUCT_LINKS]
        : PRODUCT_LINKS,
    },
    ...COLUMNS,
  ];

  return (
    <footer className="border-t border-border bg-surface/60">
      <div className="mx-auto max-w-6xl px-6 py-14 lg:px-10">
        <div className="grid gap-10 sm:grid-cols-2 lg:grid-cols-[minmax(0,1.4fr)_repeat(3,minmax(0,1fr))]">
          <div>
            <Logo variant="full" height={38} href="/" />
          </div>

          {columns.map((column) => (
            <nav key={column.heading} aria-label={column.heading}>
              {/* Full ink at 13px. The heading used to be `opacity-70`, which
                  is grey text by another name, and DESIGN.md section 3 admits
                  no exception for a faked one. */}
              <h2 className="text-xs font-semibold uppercase tracking-[0.12em]">
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
            &copy; {new Date().getFullYear()} ReadyPick. All rights reserved.
          </p>
          <p>A Hanulisa Technologies LLP product.</p>
        </div>
      </div>
    </footer>
  );
}
