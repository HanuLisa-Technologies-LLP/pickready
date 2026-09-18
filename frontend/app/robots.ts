import type { MetadataRoute } from "next";

import { SITE_URL } from "@/lib/site";

/**
 * robots.txt, generated rather than kept as a static file so the canonical
 * host is stated once in code and cannot drift from the sitemap beside it.
 * That host is `SITE_URL` in `lib/site.ts`, which this file, the sitemap, the
 * root layout's `metadataBase` and the site-level structured data all read.
 *
 * The canonical domain is readypick.ai (RBAC section 15). Neither picready.com
 * nor pickready.app is the product's address, and both have appeared in this
 * tree before.
 *
 * WHAT IS ALLOWED: the marketing and legal surface, plus the public employer
 * directory and each employer's own page. Everything else on this platform is
 * either an authenticated workspace or a link somebody was sent personally.
 *
 * /apply IS CRAWLABLE, DELIBERATELY. It is the public job application page: it
 * renders a full job description to an unauthenticated visitor, it carries
 * JobPosting structured data, and its layout sets `robots: { index: true }`.
 * Disallowing it here would block the crawl that the structured data exists to
 * feed, and would leave the meta tag and this file disagreeing about the same
 * URL. Every other unauthenticated route is token-addressed (an assessment
 * invitation, an employment verification, an outreach link), which is exactly
 * what must never be crawled.
 */

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: [
          "/",
          "/about",
          "/insights",
          "/docs",
          "/privacy",
          "/terms",
          "/employers",
          "/employers/*",
        ],
        disallow: [
          // Authenticated workspaces: the four portals.
          "/org",
          "/portal",
          "/admin",
          "/bd",
          // Token-addressed routes. A crawled token is a leaked token.
          "/assessments",
          "/verify-employment",
          // Sign-in surfaces. Nothing here is content.
          "/login",
          "/register",
          "/join",
          // The API proxy.
          "/api",
        ],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  };
}
