import Link from "next/link";

import { Logo } from "@/components/brand";

const COLUMNS = [
  {
    heading: "Product",
    links: [
      { label: "How it works", href: "/#how-it-works" },
      { label: "Platform", href: "/#features" },
      { label: "Product tour", href: "/#workflow" },
      { label: "Docs", href: "/docs" },
    ],
  },
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
              <h2 className="text-xs font-semibold uppercase tracking-wide opacity-70">
                {column.heading}
              </h2>
              <ul className="mt-4 space-y-3">
                {column.links.map((link) => (
                  <li key={link.label}>
                    <Link
                      href={link.href}
                      className="text-sm underline-offset-4 transition-colors hover:text-brand-600 hover:underline"
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
          <p className="opacity-70">
            A Hanulisa Technologies LLP product.
          </p>
        </div>
      </div>
    </footer>
  );
}
