/**
 * "Credits never expire" is true of SOME credits, and the copy must say which.
 *
 * Change request 25 gave every NEW grant an expiry (the lot carries
 * `expires_at`); credits granted before that shipped keep the never-expire
 * promise printed on their GST invoices, stored as a NULL-expiry lot. A
 * surface that still says "Credits never expire." without the qualifier is
 * telling a customer buying today something the ledger will not honour.
 *
 * Swept over every client source file rather than checked at the two call
 * sites that had it, because a rule enforced at one call site is a rule the
 * next pricing card breaks.
 */
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..");
const SOURCE_DIRS = ["app", "components", "lib"];
const SKIP = new Set(["node_modules", ".next", ".turbo"]);
const QUALIFIER = "granted before expiry was introduced";

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    if (SKIP.has(name)) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      out.push(...sourceFiles(full));
    } else if (/\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name)) {
      out.push(full);
    }
  }
  return out;
}

describe("credit expiry copy", () => {
  it("never promises that every credit never expires", () => {
    const offenders: string[] = [];
    for (const dir of SOURCE_DIRS) {
      for (const file of sourceFiles(join(frontendRoot, dir))) {
        const text = readFileSync(file, "utf-8").replace(/\s+/g, " ");
        for (const match of text.matchAll(/credits never expire/gi)) {
          const start = match.index ?? 0;
          const window = text.slice(Math.max(0, start - 160), start + 40);
          if (!window.includes(QUALIFIER)) {
            offenders.push(relative(frontendRoot, file));
          }
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("states the term new grants carry on both surfaces that sell credits", () => {
    const billing = readFileSync(
      join(frontendRoot, "app", "(org)", "org", "billing", "page.tsx"),
      "utf-8",
    );
    const pricing = readFileSync(join(frontendRoot, "app", "(public)", "pricing.tsx"), "utf-8");
    expect(billing).toContain("credit_validity_months");
    expect(billing).toContain(QUALIFIER);
    expect(pricing).toContain(QUALIFIER);
  });
});
