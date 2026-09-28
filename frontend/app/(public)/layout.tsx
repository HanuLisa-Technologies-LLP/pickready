import { displayFont } from "@/app/display-font";
import { SiteJsonLd } from "@/components/site-json-ld";

import { SiteFooter } from "./site-footer";
import { SiteHeader } from "./site-header";

/**
 * The frame for every public page except `/`, which sits at the app root and
 * carries its own copy of this frame (see `landing-page.tsx`).
 *
 * The header and footer take no props: `/` always serves the landing page, so
 * their anchors into it (`/#how-it-works`, `/#features`, `/#pricing`) resolve
 * from every page that renders them.
 */
export default function PublicLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div
      className={`${displayFont.variable} flex min-h-screen flex-col overflow-x-clip bg-canvas text-ink`}
    >
      <SiteJsonLd />
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[60] focus:bg-brand-600 focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-white"
      >
        Skip to content
      </a>
      <SiteHeader />
      <div className="flex-1 pt-16">{children}</div>
      <SiteFooter />
    </div>
  );
}
