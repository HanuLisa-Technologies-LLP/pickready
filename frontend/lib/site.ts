import type { Metadata } from "next";

/**
 * The canonical origin, defined once for the whole frontend.
 *
 * The canonical domain is readypick.ai (RBAC section 15). Neither picready.com
 * nor pickready.app is the product's address, and both have appeared in this
 * tree before, which is why this is a constant rather than a string typed out
 * at each call site. `app/robots.ts`, `app/sitemap.ts`, the root layout's
 * `metadataBase` and the site-level structured data all read THIS, so a host
 * change is one edit and cannot leave two files disagreeing about where the
 * product lives.
 */
export const SITE_URL = "https://readypick.ai";

/** The product name, as it appears in a title, a share card and a schema. */
export const SITE_NAME = "ReadyPick";

/**
 * The one-sentence description of the product, used as the site-wide meta
 * description and as the Organization's `description` in structured data.
 *
 * IT SAYS PRISM REPORT, AND IT USED TO SAY "PPI Assessment Report". The naming
 * rule (2026-08-23) is that the Tatva Assessment is the PROCESS and the PRISM
 * Report is the DOCUMENT, and that user-visible copy never calls either of
 * them PPI. This string is the default meta description for every page in the
 * product that does not declare one, so the forbidden name was being served on
 * /login, /register and /apply, which is about as user-visible as copy gets.
 * The `ppi` identifiers in CODE stay exactly as they are; this is copy.
 */
export const SITE_DESCRIPTION =
  "ReadyPick helps companies hire with confidence.";

/**
 * How a company gets a ReadyPick workspace: it asks for one.
 *
 * There is no employer self sign-up. A customer IS a tenant, and a tenant is
 * created when ReadyPick's own team records a signed agreement (the Business
 * Development portal), after which the customer's first user is invited.
 * `/register` creates a CANDIDATE account (or binds an already invited staff
 * email), so a public "Get started" pointing there turned an employer into a
 * candidate. Every employer call to action on the public site points here
 * instead: the mailbox the site already publishes as its contact, with a
 * subject line that says what the message is.
 */
export const REQUEST_ACCESS_HREF =
  "mailto:manjuchro@gmail.com?subject=ReadyPick%20access%20request";

/**
 * The home page's title, which is also the site-wide default title and the
 * default share title. One constant, read by `app/layout.tsx` and
 * `app/page.tsx`, so the tab, the search result and the share card cannot
 * name the product three ways. It is the product tagline the hero sets as
 * its h1.
 */
export const LANDING_TITLE = "ReadyPick";

/**
 * The generated share card, `app/opengraph-image.tsx`, named explicitly.
 *
 * Any page that sets its own `openGraph` REPLACES the inherited object, and
 * the file-convention image goes with it (see `publicPageMetadata` below), so
 * every page that restates Open Graph restates this too. The path is the
 * generated route, a real 200, and the size is the one that file exports.
 */
export const SHARE_CARD = {
  url: "/opengraph-image",
  width: 1200,
  height: 630,
  alt: "ReadyPick",
};

/**
 * Metadata for one public page.
 *
 * WHAT THIS FIXES. Every public page declared its own title, description and
 * canonical and inherited the ROOT layout's Open Graph object unchanged, so
 * measured on the live site, /about, /docs, /insights, /privacy, /terms and
 * /employers all served:
 *
 *     og:url          https://readypick.ai
 *     og:title        ReadyPick, know every candidate before you meet them
 *     og:description  Rank every applicant against the role, ...
 *
 * A share of the privacy notice therefore advertised the home page, and every
 * network that treats og:url as the canonical for a share consolidated all six
 * pages onto one URL. The page title and the card title described different
 * pages.
 *
 * Open Graph is NOT deep-merged by Next: a page that sets `openGraph` replaces
 * the parent object rather than extending it, so `type` and `siteName` are
 * restated here and `twitter.card` with them.
 *
 * `images` IS RESTATED TOO, AND THAT WAS FOUND BY BUILDING RATHER THAN BY
 * READING. `app/opengraph-image.tsx` supplies the card through the file
 * convention, and while a page inherited the root layout's Open Graph object
 * the generated card was merged in for free. The first build of this helper
 * served /about, /docs and /privacy with og:url finally correct and og:image
 * GONE, because the replacement took the merged-in image with it. A card with
 * no image is a worse share than a card pointing at the wrong URL, so the
 * image is named here rather than relied on. The path is the generated route,
 * which is a real 200, and the dimensions are the ones
 * `app/opengraph-image.tsx` exports.
 *
 * `path` is relative and is resolved against `metadataBase`, so a page states
 * its path and never its host.
 */
export function publicPageMetadata({
  path,
  title,
  description,
}: {
  /** Absolute path on this site, leading slash, no host and no trailing slash. */
  path: string;
  /** The page name only. The root layout appends "| ReadyPick" to the tab title. */
  title: string;
  description: string;
}): Metadata {
  const shareTitle = `${title} | ${SITE_NAME}`;
  const card = SHARE_CARD;
  return {
    title,
    description,
    alternates: { canonical: path },
    openGraph: {
      type: "website",
      siteName: SITE_NAME,
      url: path,
      title: shareTitle,
      description,
      images: [card],
    },
    twitter: {
      card: "summary_large_image",
      title: shareTitle,
      description,
      images: [card],
    },
  };
}
