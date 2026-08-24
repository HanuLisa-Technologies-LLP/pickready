/**
 * No em dash anywhere in the frontend source.
 *
 * WHY A SECOND COPY OF A RULE THE BACKEND SUITE ALREADY SWEEPS FOR.
 * `backend/tests/test_platform_audit.py::test_no_em_dash_in_frontend_source`
 * checks exactly this, and it cannot run where it is most useful. The backend
 * dev container mounts `backend/` alone, so the frontend tree is absent, the
 * sweep finds zero files, and it PASSES. That is the worst shape a guard can
 * take: green locally, and the violation is only discovered by CI after a push.
 *
 * That is not hypothetical. It happened on the change that added this file: two
 * source comments carried em dashes, every local run was green, and the deploy
 * pipeline stopped at the test gate.
 *
 * The backend copy stays. It is the one that runs in CI over both trees, and
 * deleting it would trade a redundant check for a missing one. This copy exists
 * so `npm test` fails in the two seconds before a commit rather than the two
 * minutes after a push.
 *
 * THE RULE ITSELF: no em dash in labels, helper text, empty states, toasts,
 * emails, page titles, or generated content, and none in source comments
 * either. A comment is where it survives long enough to be copied into a
 * string.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join } from "node:path";
import { fileURLToPath, URL } from "node:url";

import { describe, expect, it } from "vitest";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const SEARCH_DIRS = ["app", "components", "lib"];
const SKIP_DIRS = new Set(["node_modules", ".next", ".next-dev"]);

/**
 * Built from the code point, never typed literally. A character class that
 * MATCHES a dash is data, not prose: written as a literal, a repo-wide sweep
 * would rewrite the very code that detects it.
 */
const EM_DASH = String.fromCharCode(8212);

function sourceFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIRS.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      found.push(...sourceFiles(full));
    } else if ([".ts", ".tsx", ".css"].includes(extname(entry))) {
      found.push(full);
    }
  }
  return found;
}

describe("no em dash", () => {
  const files = SEARCH_DIRS.flatMap((dir) => sourceFiles(join(ROOT, dir)));

  it("has something to sweep", () => {
    // The guard on the guard, and the whole reason this file exists: a sweep
    // over an empty list passes forever and protects nothing.
    expect(files.length).toBeGreaterThan(50);
  });

  it("appears in no frontend source file", () => {
    const offenders = files
      .filter((file) => readFileSync(file, "utf8").includes(EM_DASH))
      .map((file) => file.slice(ROOT.length).replace(/^[\/]/, ""));

    expect(
      offenders,
      "Em dash is forbidden in frontend source, including in comments. " +
        "Use a comma, a full stop, or a double hyphen."
    ).toEqual([]);
  });
});
