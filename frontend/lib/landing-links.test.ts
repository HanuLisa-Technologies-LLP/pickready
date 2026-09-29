/**
 * Every link on the landing page goes somewhere real.
 *
 * WHY THIS EXISTS. When `/` went live (2026-09-28) the page it served had
 * been written for a launch that kept not happening, and its links had drifted
 * while nobody could see them: "Get started" opened CANDIDATE sign-up for an
 * employer, "Join with an invite" opened `/join` with no token (a page that can
 * only say the link is incomplete), two query parameters (`initial_context`,
 * `role=candidate`) were read by nothing, and the pricing cards sent a
 * signed-out visitor through candidate sign-up to a customer billing page.
 * Every one of those rendered, built and passed; the defect was only in where
 * a click landed. So this reads where every click lands.
 *
 * What it asserts, per link in the landing page and the frame around it:
 *
 *   * an internal path names a page that exists under `app/` (route groups
 *     removed, a dynamic segment matching anything);
 *   * the proxy admits it WITHOUT a session, so a visitor is never bounced to
 *     /login by a link on a public page (the one signed-in destination is
 *     declared below with its reason);
 *   * an anchor names a section id the landing page actually mounts;
 *   * a `mailto:` is the one request-access address or the enterprise line;
 *   * no token-addressed route is linked bare, and no dead parameter returns.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = resolve(here, "..");
const appDir = join(frontend, "app");
const publicDir = join(appDir, "(public)");

/** The landing page, its sections and the frame it renders. */
const LANDING_FILES = [
  "landing-page.tsx",
  "site-header.tsx",
  "site-footer.tsx",
].map((name) => join(publicDir, name));

/**
 * Destinations a landing link may send somebody to that need a session, each
 * with the reason. Nothing else may. EMPTY since 2026-09-29: the inline
 * pricing cards that sent a signed-in customer to `/org/billing` left the
 * landing page for the public `/pricing` page.
 */
const SIGNED_IN_TARGETS: Record<string, string> = {};

/** Routes addressed by a single-use token: a bare link to one is a dead end. */
const TOKEN_ROUTES = ["/join", "/assessments/invite", "/keep-profile", "/verify-employment"];

const DEAD_PARAMETERS = ["initial_context", "role=candidate"];

function read(path: string): string {
  return readFileSync(path, "utf8");
}

/** The request-access mailbox, read from its one definition. */
function requestAccessHref(): string {
  const match = read(join(frontend, "lib", "site.ts")).match(
    /REQUEST_ACCESS_HREF =\s*"([^"]+)"/,
  );
  if (!match) throw new Error("REQUEST_ACCESS_HREF not found in lib/site.ts");
  return match[1];
}

/** Every literal destination a landing file links or navigates to. */
function destinations(): Array<{ file: string; target: string }> {
  const found: Array<{ file: string; target: string }> = [];
  for (const file of LANDING_FILES) {
    const source = read(file);
    const patterns = [
      /href=\s*"([^"]+)"/g,
      /href:\s*"([^"]+)"/g,
      /router\.push\(([^)]*)\)/g,
    ];
    for (const pattern of patterns) {
      for (const match of source.matchAll(pattern)) {
        if (pattern.source.startsWith("router")) {
          for (const literal of match[1].matchAll(/"([^"]+)"/g)) {
            found.push({ file: relative(frontend, file), target: literal[1] });
          }
        } else {
          found.push({ file: relative(frontend, file), target: match[1] });
        }
      }
    }
  }
  return found;
}

/** Every page route under `app/`, with route groups removed. */
function pageRoutes(): string[] {
  const routes: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) {
        walk(full);
      } else if (entry === "page.tsx") {
        const segments = relative(appDir, dir)
          .split(sep)
          .filter((segment) => segment && !/^\(.*\)$/.test(segment));
        routes.push("/" + segments.join("/"));
      }
    }
  };
  walk(appDir);
  return routes;
}

function routeExists(path: string, routes: string[]): boolean {
  const wanted = path.split("/").filter(Boolean);
  return routes.some((route) => {
    const have = route.split("/").filter(Boolean);
    return (
      have.length === wanted.length &&
      have.every((segment, index) => /^\[.*\]$/.test(segment) || segment === wanted[index])
    );
  });
}

function publicPrefixes(): string[] {
  const source = read(join(frontend, "proxy.ts"));
  const start = source.indexOf("const PUBLIC_PREFIXES");
  const block = source.slice(start, source.indexOf("];", start));
  return [...block.matchAll(/"(\/[^"]*)"/g)].map((m) => m[1]);
}

function admittedSignedOut(path: string, prefixes: string[]): boolean {
  if (path === "/") return true;
  return prefixes.some((prefix) => path === prefix || path.startsWith(prefix + "/"));
}

/** Every `id="..."` the landing sections mount. */
function mountedIds(): Set<string> {
  const ids = new Set<string>();
  for (const file of LANDING_FILES) {
    for (const match of read(file).matchAll(/\bid="([^"]+)"/g)) ids.add(match[1]);
  }
  return ids;
}

function pathOf(target: string): string {
  return target.split("#")[0].split("?")[0] || "/";
}

describe("the landing page's links", () => {
  const links = destinations();

  it("reads a real number of links from the page and the header", () => {
    // A parser that matched nothing would make every assertion below pass.
    expect(links.length).toBeGreaterThanOrEqual(5);
    for (const name of ["landing-page.tsx", "site-header.tsx"]) {
      expect(links.some((link) => link.file.endsWith(name)), name).toBe(true);
    }
  });

  it("sends every internal link to a page that exists", () => {
    const routes = pageRoutes();
    expect(routes).toContain("/");
    const missing = links
      .filter(({ target }) => target.startsWith("/"))
      .filter(({ target }) => !routeExists(pathOf(target), routes));
    expect(missing).toEqual([]);
  });

  it("never bounces a signed-out visitor to the sign-in form", () => {
    const prefixes = publicPrefixes();
    const refused = links
      .filter(({ target }) => target.startsWith("/"))
      .filter(({ target }) => !(pathOf(target) in SIGNED_IN_TARGETS))
      .filter(({ target }) => !admittedSignedOut(pathOf(target), prefixes));
    expect(refused).toEqual([]);
  });

  it("sends pricing to its own page, from the header and the page", () => {
    // Owner spec 2026-09-29, section 4.1: no inline price list and no
    // `/#pricing` anchor; "See pricing plans" opens `/pricing`.
    expect(links.filter(({ target }) => target.includes("#pricing"))).toEqual([]);
    for (const name of ["site-header.tsx", "landing-page.tsx"]) {
      const source = read(join(publicDir, name));
      expect(source, name).toContain('"/pricing"');
      expect(source, name).toContain("See pricing plans");
    }
  });

  it("carries no in-page anchor, because the page is one screen", () => {
    expect(links.filter(({ target }) => target.includes("#"))).toEqual([]);
  });

  it("writes to the request-access mailbox and nowhere else by mail", () => {
    const allowed = new Set([
      requestAccessHref(),
      "mailto:manjuchro@gmail.com?subject=Enterprise%20credits",
    ]);
    const mail = links.filter(({ target }) => target.startsWith("mailto:"));
    expect(mail.filter(({ target }) => !allowed.has(target))).toEqual([]);
  });

  it("offers company login and company registration from the header", () => {
    const header = read(join(publicDir, "site-header.tsx"));
    expect(header).toContain('"/company/login"');
    expect(header).toContain('"/company/register"');
  });

  it("names no company and no previous product name (owner, 2026-09-29)", () => {
    for (const file of LANDING_FILES) {
      expect(read(file), file).not.toMatch(/Vivekium|Varpitech|HanuLisa/i);
    }
  });

  it("links no token-addressed route bare, and no dead parameter", () => {
    const bare = links.filter(({ target }) =>
      TOKEN_ROUTES.some((route) => pathOf(target) === route),
    );
    expect(bare).toEqual([]);
    const dead = links.filter(({ target }) =>
      DEAD_PARAMETERS.some((parameter) => target.includes(parameter)),
    );
    expect(dead).toEqual([]);
  });
});

/**
 * The public /pricing page (owner spec 2026-09-29, sections 4.2 and 4.3). Its
 * calls to action are named constants rather than inline literals, so they are
 * read here by their declaration. Until company self-registration existed,
 * "Register company" pointed at a page that was not there yet; it exists now,
 * and so does the rule that it must.
 */
describe("the pricing page's links", () => {
  const source = read(join(publicDir, "pricing", "pricing-catalogue.tsx"));
  const targets = [
    ...[...source.matchAll(/const [A-Z_]+_HREF\s*=\s*"([^"]+)"/g)].map((m) => m[1]),
    ...[...source.matchAll(/href=\s*"([^"]+)"/g)].map((m) => m[1]),
  ];

  it("reads the calls to action", () => {
    expect(targets).toContain("/company/register");
    expect(targets).toContain("/company/login");
  });

  it("sends every internal link to a page a signed-out visitor may open", () => {
    const routes = pageRoutes();
    const prefixes = publicPrefixes();
    const internal = targets.filter((target) => target.startsWith("/"));
    expect(internal.filter((target) => !routeExists(pathOf(target), routes))).toEqual([]);
    expect(
      internal.filter((target) => !admittedSignedOut(pathOf(target), prefixes)),
    ).toEqual([]);
  });

  it("writes to the enterprise line and nowhere else by mail", () => {
    const mail = targets.filter((target) => target.startsWith("mailto:"));
    expect(mail.length).toBeGreaterThan(0);
    expect(
      mail.filter(
        (target) => target !== "mailto:manjuchro@gmail.com?subject=Enterprise%20credits",
      ),
    ).toEqual([]);
  });
});
