/**
 * The frontend must call each router at the prefix it is actually mounted on.
 *
 * This bug has now shipped twice and both times it was invisible in review:
 *
 *   2026-08-09  `resume_file` 307ed to `/api/v2/candidates/...`; the candidates
 *               router is mounted at /api/v1 only, so every resume view 404ed.
 *   2026-08-11  the new invitation page called `/assessments/invitations/...`
 *               relative to `API_BASE`, which defaults to /api/v1; the
 *               assessments router is mounted at /api/v2 ONLY, so the one page
 *               standing between a candidate and their assessment 404ed.
 *
 * Neither is a type error, neither is a lint error, and both read perfectly in
 * a diff. The only thing that catches them cheaply is asserting the prefix at
 * the call site, which is what this does: it greps the source for API paths
 * mentioning a v2-only router and fails on any that is not written in full.
 *
 * When a router gains a /api/v1 mount, delete its entry here rather than
 * loosening the check.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join } from "node:path";
import { fileURLToPath, URL } from "node:url";

import { describe, expect, it } from "vitest";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const SEARCH_DIRS = ["app", "components", "lib"];
const SKIP_DIRS = new Set(["node_modules", ".next", ".next-dev"]);

/**
 * Routers mounted at /api/v2 and NOT at /api/v1 (backend/app/main.py). A path
 * naming one of these must be written with the full prefix, because
 * `API_BASE` is /api/v1 and a relative path silently resolves to a 404.
 */
const V2_ONLY = ["assessments"];

function sourceFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIRS.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      found.push(...sourceFiles(full));
    } else if ([".ts", ".tsx"].includes(extname(entry))) {
      found.push(full);
    }
  }
  return found;
}

describe("API mount parity", () => {
  it("never calls a v2-only router through a v1-relative path", () => {
    const offenders: string[] = [];
    for (const file of SEARCH_DIRS.flatMap((dir) => sourceFiles(join(ROOT, dir)))) {
      if (file.endsWith("api-mount-parity.test.ts")) continue;
      const source = readFileSync(file, "utf8");
      for (const router of V2_ONLY) {
        // Anchored on the API HELPER, not on the path alone. `/assessments/...`
        // is also a legitimate page route (the invitation landing page lives
        // at one), so matching the path by itself would flag Link hrefs and
        // the public-prefix list and teach the next reader to ignore this
        // test. Only a path handed to api*() is an API call.
        const relative = new RegExp(
          `api(?:Get|Post|Put|Patch|Delete)\\s*(?:<[^>]*>)?\\s*\\(\\s*["'\`]/${router}/`,
          "g"
        );
        if (relative.test(source)) {
          offenders.push(`${file.slice(ROOT.length)} -> /${router}/...`);
        }
      }
    }
    expect(offenders).toEqual([]);
  }, 15_000);

  it("every SWOT API call carries the /api/v2/assessments mount", () => {
    // The SWOT surfaces live on the assessments router but their paths START
    // with /jobs/..., so the router-name grep above cannot see them. The Job
    // SWOT Analysis panel shipped calling `/jobs/{id}/swot-analysis` relative
    // to API_BASE, which resolved to /api/v1 and 404ed every call. A SWOT
    // path is legal in exactly two spellings: written in full, or composed
    // from a BASE constant that IS the full mount.
    const call =
      /api(?:Get|Post|Put|Patch|Delete)\s*(?:<[^>]*>)?\s*\(\s*["'`]([^"'`]*swot[^"'`]*)["'`]?/g;
    const FULL = "/api/v2/assessments";
    const VIA_BASE = `const BASE = "${FULL}/jobs"`;

    // Guard on the guard: the regex must see both spellings it rules on.
    expect(
      [...`apiGet(\`\${BASE}/\${jobId}/swot\`)`.matchAll(call)][0]?.[1]
    ).toContain("swot");
    expect(
      [...`apiPut("/jobs/j1/swot-analysis", body)`.matchAll(call)][0]?.[1]
    ).toBe("/jobs/j1/swot-analysis");

    const offenders: string[] = [];
    for (const file of SEARCH_DIRS.flatMap((dir) => sourceFiles(join(ROOT, dir)))) {
      if (/\.test\.tsx?$/.test(file)) continue;
      const source = readFileSync(file, "utf8");
      for (const match of source.matchAll(call)) {
        const path = match[1];
        const composed = path.startsWith("${BASE}") && source.includes(VIA_BASE);
        if (!path.startsWith(FULL) && !composed) {
          offenders.push(`${file.slice(ROOT.length)} -> ${path}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  }, 15_000);

  it("recognises the shape it is looking for, and only that shape", () => {
    // A guard on the guard: a regex that matched nothing would pass the test
    // above forever and prove nothing. The negative cases matter just as
    // much -- this test is worthless if it cries wolf on a page route.
    const pattern = () =>
      /api(?:Get|Post|Put|Patch|Delete)\s*(?:<[^>]*>)?\s*\(\s*["'`]\/assessments\//g;

    expect(pattern().test(`apiGet("/assessments/invitations/x")`)).toBe(true);
    expect(
      pattern().test(`apiGet<Thing>(\`/assessments/invitations/\${token}\`)`)
    ).toBe(true);

    expect(pattern().test(`apiGet("/api/v2/assessments/invitations/x")`)).toBe(
      false
    );
    // Page routes, which are not API calls and must not be flagged.
    expect(pattern().test(`<Link href="/assessments/invite/abc" />`)).toBe(false);
    expect(pattern().test(`const PUBLIC = ["/assessments/invite"];`)).toBe(false);
  });
});
