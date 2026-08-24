/**
 * Every authenticated request must carry the silent refresh.
 *
 * THE BUG THIS PINS. `api()` refreshes the access cookie once on a 401 and
 * retries, so the ordinary JSON path survives an expired session invisibly.
 * Anything binary or multipart could not use `api()`, it parses JSON, so
 * those call sites reached for a bare `fetch(..., { credentials: "include" })`
 * and each one silently opted out of the refresh.
 *
 * Three of them did, and they were the worst three to lose: the resume upload,
 * the databank bulk upload and the resume preview. The access cookie has a
 * 15-minute Max-Age; a recruiter reading a job description or working down a
 * candidate list is idle for longer than that as a matter of course. The next
 * thing they clicked answered 401 against a session that was perfectly
 * refreshable, and because all three of those actions kick off resume parsing
 * and matching, it was reported as "the AI features return 401", not as an
 * expired login.
 *
 * `apiFetch` is that same call with the refresh attached. This test exists
 * because the gap is invisible in review: a bare `fetch` with credentials is
 * indistinguishable from a correct one until a cookie happens to lapse.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join } from "node:path";
import { fileURLToPath, URL } from "node:url";

import { describe, expect, it } from "vitest";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const SEARCH_DIRS = ["app", "components", "lib"];
const SKIP_DIRS = new Set(["node_modules", ".next", ".next-dev"]);

/**
 * Files allowed to call `fetch` with credentials directly.
 *
 *   lib/api.ts               defines the wrapper; the raw calls ARE the wrapper.
 *   app/api/[...path]/route.ts  runs on the SERVER. It is the proxy hop itself,
 *                            forwards whatever cookies arrived, and has no
 *                            client-side session to refresh.
 */
const ALLOWED = new Set([
  join("lib", "api.ts"),
  join("app", "api", "[...path]", "route.ts"),
]);

function sourceFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIRS.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      found.push(...sourceFiles(full));
    } else if (
      [".ts", ".tsx"].includes(extname(entry)) &&
      // Test files describe the shape they are looking for, so scanning them
      // makes every such test match itself.
      !entry.includes(".test.")
    ) {
      found.push(full);
    }
  }
  return found;
}

function relative(file: string): string {
  return file.slice(ROOT.length).replace(/^[\\/]/, "");
}

describe("authenticated fetch", () => {
  it("never sends credentials through a bare fetch", () => {
    const offenders: string[] = [];

    for (const dir of SEARCH_DIRS) {
      for (const file of sourceFiles(join(ROOT, dir))) {
        const rel = relative(file);
        if (ALLOWED.has(rel)) continue;
        const source = readFileSync(file, "utf8");
        // `fetch(` followed, within the same call, by a credentials option.
        // Deliberately textual: the point is to catch the shape a developer
        // reaches for, before it ever runs.
        // `[\s\S]` rather than the `s` flag: the tsconfig target predates it,
        // and the same TS1501 already sits unfixed in another test file.
        const pattern =
          /\bfetch\s*\([\s\S]{0,400}?credentials\s*:\s*["']include["']/g;
        if (pattern.test(source)) offenders.push(rel);
      }
    }

    expect(
      offenders,
      "These call sites bypass the silent refresh and will answer 401 to any " +
        "user whose access cookie lapsed. Use apiFetch from lib/api instead."
    ).toEqual([]);
  });

  it("exposes one raw-response helper rather than open-coded retries", () => {
    const source = readFileSync(join(ROOT, "lib", "api.ts"), "utf8");
    expect(source).toContain("export async function apiFetch");
    // The retry has to be conditional on the refresh actually succeeding.
    // Retrying regardless just doubles the 401s and the wait.
    expect(source).toMatch(/res\.status === 401 && \(await tryRefresh\(\)\)/);
  });

  it("retries a progress upload after a refresh", () => {
    const source = readFileSync(join(ROOT, "lib", "api.ts"), "utf8");
    // The XHR path cannot share `apiFetch` (fetch reports no upload progress),
    // so it carries its own copy and this asserts the copy exists. It is the
    // path a candidate's resume travels.
    const upload = source.slice(source.indexOf("export function apiUploadWithProgress"));
    expect(upload).toContain("await tryRefresh()");
    expect(upload).toMatch(/error\.status !== 401/);
  });
});
