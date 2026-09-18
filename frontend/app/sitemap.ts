import type { MetadataRoute } from "next";

import { SITE_URL } from "@/lib/site";

/**
 * The sitemap lists ONLY the genuinely public marketing and legal pages.
 *
 * It deliberately does NOT fetch from the backend and does NOT enumerate
 * employer slugs. That would require a network call at build time against an
 * API that may be down, and would leak the customer list.
 *
 * An employer page is still reachable and still indexable: it is linked from
 * the /employers directory, which is listed here, and robots.txt allows the
 * subtree. What is absent is a machine-readable roster of every company that
 * has signed with ReadyPick, published from our own domain.
 *
 * Individual /apply pages are absent for a related reason: a job lives for
 * exactly thirty days, so a build-time snapshot of them would advertise dead
 * URLs for as long as the deployment stands. Crawlers reach them through the
 * employer pages, which are generated live.
 */

export default function sitemap(): MetadataRoute.Sitemap {
  // One timestamp for the whole build. These pages are edited in the source
  // tree, so the deploy IS their last modification.
  const lastModified = new Date();

  return [
    { url: `${SITE_URL}/`, lastModified, changeFrequency: "monthly", priority: 1 },
    {
      url: `${SITE_URL}/employers`,
      lastModified,
      changeFrequency: "daily",
      priority: 0.9,
    },
    {
      url: `${SITE_URL}/about`,
      lastModified,
      changeFrequency: "monthly",
      priority: 0.7,
    },
    {
      url: `${SITE_URL}/insights`,
      lastModified,
      changeFrequency: "weekly",
      priority: 0.7,
    },
    {
      url: `${SITE_URL}/docs`,
      lastModified,
      changeFrequency: "monthly",
      priority: 0.6,
    },
    {
      url: `${SITE_URL}/privacy`,
      lastModified,
      changeFrequency: "yearly",
      priority: 0.3,
    },
    {
      url: `${SITE_URL}/terms`,
      lastModified,
      changeFrequency: "yearly",
      priority: 0.3,
    },
  ];
}
