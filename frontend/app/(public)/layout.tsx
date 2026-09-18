import { SiteJsonLd } from "@/components/site-json-ld";

import { SiteFooter } from "./site-footer";
import { SiteHeader } from "./site-header";

/**
 * The frame for every public page except `/`, which sits at the app root and
 * carries its own copy of this frame (see `landing-page.tsx`).
 *
 * THE LAUNCH GATE IS READ HERE, NOT INSIDE THE HEADER. `NEXT_PUBLIC_*` is
 * inlined at build time from a literal reference, and reading it in a Server
 * Component keeps the decision on the server: the header and footer take a
 * plain boolean prop, which is testable, defaults to the safe value, and does
 * not force a shared constant across the client boundary, where a Server
 * Component importing from a `"use client"` module would get a client
 * reference rather than the value.
 */
const LANDING_LIVE = process.env.NEXT_PUBLIC_LANDING_LIVE === "true";

export default function PublicLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div className="flex min-h-screen flex-col overflow-x-clip bg-canvas text-ink">
      <SiteJsonLd />
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[60] focus:bg-brand-600 focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-white"
      >
        Skip to content
      </a>
      <SiteHeader landingLive={LANDING_LIVE} />
      <div className="flex-1 pt-16">{children}</div>
      <SiteFooter landingLive={LANDING_LIVE} />
    </div>
  );
}
