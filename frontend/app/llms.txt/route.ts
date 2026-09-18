import { SITE_DESCRIPTION, SITE_NAME, SITE_URL } from "@/lib/site";

import sitemap from "../sitemap";

/**
 * llms.txt, for an assistant that has been pointed at readypick.ai and needs to
 * know which pages are worth reading.
 *
 * WHY IT EXISTS HERE, AND WHAT IT IS NOT. It is NOT a ranking mechanism and no
 * search engine reads it; it earns its place only because this site has a real
 * public documentation surface (/docs carries the product and technical
 * documentation, /insights carries seven articles) and nothing on the site
 * tells a reader which of the public URLs are the substantive ones. A sitemap
 * answers "what exists"; this answers "what to read first".
 *
 * IT CANNOT DRIFT FROM THE SITEMAP, AND THAT IS THE WHOLE DESIGN. The URL list
 * is DERIVED from `app/sitemap.ts` rather than typed out again. A second
 * hand-maintained list of public URLs is exactly the failure this repository
 * has already had once: robots.txt, the sitemap and the middleware allowlist
 * disagreed, and five public pages answered a crawler with a redirect to a
 * sign-in form. `lib/public-routes.test.ts` pins the three-way agreement, and
 * now pins this file with them.
 *
 * NOTHING BEHIND AUTH IS NAMED. The four portals, the tokenised assessment,
 * outreach and employment-verification links and the API proxy are absent by
 * construction: they are not in the sitemap, so they cannot reach this list.
 * There is no handwritten entry anywhere in this file that could add one.
 *
 * A path with no note below is still listed, with its URL and no description.
 * A complete list with a thin line is honest; a page silently dropped from the
 * list because somebody forgot a sentence is not, and the test is what makes
 * the missing sentence visible.
 */
const NOTES: Record<string, string> = {
  "/": "Home page.",
  "/employers":
    "Directory of companies hiring through ReadyPick, each with a public page and its currently open roles.",
  "/about": "Who builds ReadyPick and the principles the product is built on.",
  "/insights":
    "Articles on evidence-led candidate decisions, assessment design and consent.",
  "/docs":
    "Product and technical documentation: the workspaces, the hiring flow, the assessment, the architecture and the stated limitations.",
  "/privacy":
    "Privacy notice covering candidate, customer and visitor information.",
  "/terms": "Terms governing access to and use of ReadyPick.",
};

/** The path a sitemap entry addresses, with no host and no trailing slash. */
function pathOf(url: string): string {
  return url.slice(SITE_URL.length).replace(/\/$/, "") || "/";
}

export function llmsTxt(): string {
  const lines = [
    `# ${SITE_NAME}`,
    "",
    `> ${SITE_DESCRIPTION}`,
    "",
    "Everything listed here is public and needs no account. The customer,",
    "candidate, provider and business development workspaces are not listed",
    "because they require a sign-in, and links sent to one person (an",
    "assessment invitation, an employment verification, an outreach message)",
    "are not listed because they are addressed by a single-use token.",
    "",
    "## Pages",
    "",
  ];

  for (const entry of sitemap()) {
    const url = String(entry.url);
    const path = pathOf(url);
    const note = NOTES[path];
    lines.push(note ? `- [${path}](${url}): ${note}` : `- [${path}](${url})`);
  }

  lines.push(
    "",
    "## Optional",
    "",
    `- [/robots.txt](${SITE_URL}/robots.txt): what may be crawled.`,
    `- [/sitemap.xml](${SITE_URL}/sitemap.xml): the same public pages, as XML.`,
    "",
  );

  return lines.join("\n");
}

/**
 * Prerendered at build time, like robots.txt and sitemap.xml beside it. The
 * content is derived from source and from nothing per-request, so serving it
 * from a running handler would spend a Node process on a constant.
 */
export const dynamic = "force-static";

export function GET(): Response {
  return new Response(llmsTxt(), {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
}
