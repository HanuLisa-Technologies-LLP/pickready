/**
 * The sitemap and the auth middleware must agree about what is public.
 *
 * WHY THIS EXISTS. `proxy.ts` is a deny-by-default allowlist: any path its
 * matcher sees that is not under `PUBLIC_PREFIXES` is redirected to /login when
 * there is no session cookie. Five genuinely public pages were missing from that
 * list, and the result was only visible on the deployed site:
 *
 *   /about      307 -> /login
 *   /insights   307 -> /login
 *   /privacy    307 -> /login
 *   /terms      307 -> /login
 *   /employers  307 -> /login
 *
 * The site footer links to /about and /insights from every public page, so the
 * marketing site dead-ended at a sign-in form. Worse, /privacy and /terms are
 * legal pages, and a privacy policy nobody can read without an account is not a
 * published privacy policy.
 *
 * It was about to get worse rather than better. `app/robots.ts` now invites
 * crawlers to all five and `app/sitemap.ts` lists them, so a crawler following
 * the sitemap would have been handed a redirect to a login form for every URL it
 * had just been told to index.
 *
 * WHY IT IS ASSERTED THIS WAY. Every local check passed while this was broken:
 * the pages build, they render, the sitemap is correct and the middleware is
 * correct in isolation. The defect only exists in the RELATIONSHIP between two
 * files, so that relationship is what this reads. The sitemap is the better
 * source of truth of the two, because it is the list the product publishes to
 * the outside world as "these are our public URLs".
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appDir = resolve(here, "..", "app");

/** Every path literal the sitemap publishes, as a route. */
function sitemapRoutes(): string[] {
  const source = readFileSync(join(appDir, "sitemap.ts"), "utf8");
  const found = new Set<string>();
  // Matches the `${BASE}/about` and `${BASE}` shapes a Next sitemap uses.
  for (const match of source.matchAll(
    /\$\{[A-Za-z_$][\w$]*\}(\/[a-z0-9\-/]*)?/g,
  )) {
    found.add(match[1] ? match[1].replace(/\/$/, "") || "/" : "/");
  }
  return [...found];
}

/** Every prefix the middleware treats as reachable without a session. */
function publicPrefixes(): string[] {
  const source = readFileSync(resolve(here, "..", "proxy.ts"), "utf8");
  const start = source.indexOf("const PUBLIC_PREFIXES");
  const block = source.slice(start, source.indexOf("];", start));
  return [...block.matchAll(/"(\/[^"]*)"/g)].map((m) => m[1]);
}

/** The middleware's own rule: an exact match, or a match on a path segment. */
function isAdmitted(route: string, prefixes: string[]): boolean {
  return prefixes.some(
    (prefix) => route === prefix || route.startsWith(prefix + "/"),
  );
}

describe("the public routes the product advertises", () => {
  it("finds both files and parses something out of each", () => {
    // A parser that silently matched nothing would make every assertion below
    // vacuously true, which is the failure mode this whole file exists to catch
    // one level up.
    expect(sitemapRoutes().length).toBeGreaterThan(3);
    expect(publicPrefixes().length).toBeGreaterThan(3);
  });

  it("admits every URL the sitemap publishes, without a session", () => {
    const prefixes = publicPrefixes();
    const refused = sitemapRoutes().filter(
      (route) => route !== "/" && !isAdmitted(route, prefixes),
    );
    expect(
      refused,
      `these are in app/sitemap.ts and are NOT in PUBLIC_PREFIXES, so a ` +
        `signed-out visitor and every crawler following the sitemap is ` +
        `redirected to /login: ${refused.join(", ")}`,
    ).toEqual([]);
  });

  it("keeps the legal pages reachable without an account", () => {
    // Called out separately from the sitemap sweep because these two are not a
    // marketing decision: a privacy policy that requires an account to read is
    // not published, whatever the sitemap happens to say that day.
    const prefixes = publicPrefixes();
    for (const legal of ["/privacy", "/terms"]) {
      expect(isAdmitted(legal, prefixes), `${legal} must be public`).toBe(true);
    }
  });

  it("does not admit an authenticated portal", () => {
    // The other direction. A fix for the above that widened the allowlist too
    // far would open the product, and would still pass the test above.
    const prefixes = publicPrefixes();
    for (const portal of ["/org", "/org/jobs", "/admin", "/bd", "/portal/me"]) {
      expect(
        isAdmitted(portal, prefixes),
        `${portal} must NOT be reachable without a session`,
      ).toBe(false);
    }
  });
});
