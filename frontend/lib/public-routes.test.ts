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
import { llmsTxt } from "../app/llms.txt/route";
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

/**
 * `llms.txt` is a SECOND published list of this site's public URLs, and a
 * second list is how the first one starts lying. It is derived from the
 * sitemap in code rather than typed out, and these assertions are what keep it
 * derived: they fail if somebody replaces the derivation with a literal list
 * and then adds a page to only one of the two.
 */
describe("llms.txt", () => {
  const body = llmsTxt();

  it("lists exactly the URLs the sitemap publishes", () => {
    const listed = [...body.matchAll(/^- \[(\/[^\]]*)\]/gm)]
      .map((m) => m[1])
      .filter((path) => path !== "/robots.txt" && path !== "/sitemap.xml");
    expect([...listed].sort()).toEqual([...sitemapRoutes()].sort());
  });

  it("describes every page it lists", () => {
    // A bare link is allowed by the route handler on purpose, so that a
    // forgotten sentence cannot drop a page off the list. This is where the
    // forgotten sentence becomes visible instead.
    const bare = [...body.matchAll(/^- \[(\/[^\]]*)\]\([^)]*\)$/gm)].map(
      (m) => m[1],
    );
    expect(bare, `these llms.txt entries carry no description: ${bare.join(", ")}`).toEqual([]);
  });

  it("names nothing that sits behind a sign-in or a token", () => {
    for (const secret of [
      "/org",
      "/portal",
      "/admin",
      "/bd",
      "/assessments",
      "/verify-employment",
      "/api",
      "/login",
      "/register",
      "/join",
    ]) {
      expect(body, `llms.txt must not name ${secret}`).not.toContain(
        `(https://readypick.ai${secret}`,
      );
    }
  });

  it("is excluded from the middleware matcher, like robots.txt", () => {
    // The generated text routes sit INSIDE the matcher's path space, so an
    // omission here does not fail loudly: the file simply answers every
    // crawler and every agent with a 307 to /login, which is what robots.txt
    // and sitemap.xml did in production until it was found by probing the
    // deployed site.
    const source = readFileSync(resolve(here, "..", "proxy.ts"), "utf8");
    const matcher = source.slice(source.indexOf("matcher: ["));
    for (const generated of ["robots.txt", "sitemap.xml", "llms.txt", "opengraph-image"]) {
      expect(
        matcher.includes(`|${generated}|`) || matcher.includes(`?!${generated}|`),
        `${generated} is a generated route and must be excluded from the proxy matcher`,
      ).toBe(true);
    }
  });
});

/**
 * The client half of the same allowlist.
 *
 * `lib/auth-context.tsx` redirects to /login when /auth/me answers 401 on any
 * path it does not recognise as public. So a route the proxy admits signed-out
 * and the provider does not is still a redirect to a sign-in form, one render
 * later. /keep-profile, the renewal link in a letter to somebody who has not
 * signed in for months, was exactly that.
 */
describe("the auth provider and the proxy agree about what is public", () => {
  function providerPrefixes(): string[] {
    const source = readFileSync(resolve(here, "auth-context.tsx"), "utf8");
    const start = source.indexOf("const PUBLIC_PREFIXES");
    const block = source.slice(start, source.indexOf("];", start));
    return [...block.matchAll(/"(\/[^"]*)"/g)].map((m) => m[1]);
  }

  it("holds exactly the same prefixes", () => {
    const provider = providerPrefixes();
    expect(provider.length).toBeGreaterThan(3);
    expect([...provider].sort()).toEqual([...publicPrefixes()].sort());
  });
});
