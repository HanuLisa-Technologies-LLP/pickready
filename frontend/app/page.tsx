import type { Metadata } from "next";

import { SiteJsonLd } from "@/components/site-json-ld";
import {
  LANDING_TITLE,
  SHARE_CARD,
  SITE_DESCRIPTION,
  SITE_NAME,
} from "@/lib/site";

import { LandingPage } from "./(public)/landing-page";

/**
 * `/` serves the landing page, unconditionally.
 *
 * THE LAUNCH GATE IS GONE (owner, 2026-09-28: remove the holding page and
 * rewire the landing page for readypick.ai). This file
 * used to choose between the landing page and a holding page on a build-time
 * variable, and its own docstring called that a one-way door: when the owner
 * flipped it, the holding page and the branch would come out in the same
 * change. This is that change. There is one page at `/` now and no variable
 * that can put anything else there; `tests/test_holding_page_removed.py`
 * keeps it so.
 *
 * THE ROOT SEGMENT DOES NOT GET ITS OWN TITLE TEMPLATE, which is why the title
 * spells out the product name. `title.template` in `app/layout.tsx` applies to
 * CHILD segments; `app/page.tsx` is the same segment as that layout.
 *
 * `openGraph` and `twitter` are stated in full, image included, for the reason
 * `lib/site.ts` records: Next replaces a parent's Open Graph object rather than
 * merging into it, so a page that restates the object without its image ships
 * a card with no picture. The title, description and card are the same
 * constants the root layout serves, so the home page and the site default
 * cannot describe the product two ways.
 */
export const metadata: Metadata = {
  title: LANDING_TITLE,
  description: SITE_DESCRIPTION,
  // Relative, resolved against `metadataBase` in the root layout. Stating it is
  // what stops a trailing slash, a query string or a preview host from
  // becoming a second URL for the same page.
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    siteName: SITE_NAME,
    url: "/",
    title: LANDING_TITLE,
    description: SITE_DESCRIPTION,
    images: [SHARE_CARD],
  },
  twitter: {
    card: "summary_large_image",
    title: LANDING_TITLE,
    description: SITE_DESCRIPTION,
    images: [SHARE_CARD],
  },
};

export default function RootPage() {
  // Site-level structured data belongs on the home page: it is the page a
  // crawler reaches first. `app/(public)/layout.tsx` carries it for every
  // other public page, and this route does not use that layout, so nothing
  // renders it twice.
  return (
    <>
      <SiteJsonLd />
      <LandingPage />
    </>
  );
}
