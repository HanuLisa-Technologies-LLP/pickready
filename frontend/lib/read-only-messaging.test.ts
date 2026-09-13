/**
 * One place may tell somebody they have read-only access, and one only.
 *
 * The 2026-09-13 spec's section 7 is a rule about a SENTENCE: it may appear
 * when a user can view a resource and genuinely cannot edit it, and never
 * otherwise. The product broke that rule on the company profile page, and the
 * break was not a permission bug. `canEdit` was computed correctly; the
 * sentence sat in the else-branch of `canEdit && editing`, so a user who held
 * the capability and had not clicked Edit was told they did not hold it.
 *
 * A rule about where a sentence may live is enforceable by looking at where
 * the sentence lives. `<ReadOnlyNotice>` returns null when `canEdit` is true,
 * so a surface that renders restriction copy through it cannot contradict the
 * server; a surface that writes the copy itself can, and this test fails on
 * one that does.
 */
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..");

const SOURCE_DIRS = ["app", "components", "lib"];
const SKIP = new Set(["node_modules", ".next", ".turbo"]);

/**
 * The two files that are ALLOWED to carry the copy: the component that owns
 * the rule, and the module that owns its wording. Everything else asks.
 */
const OWNERS = new Set([
  "components/permission-notice.tsx",
  "lib/permissions.ts",
]);

/**
 * Phrases that constitute a permission restriction addressed to the reader.
 * Deliberately narrow: "read-only" appears legitimately in plenty of other
 * senses (a closed application is read-only, the Provider's view of a
 * customer's compliance file is read-only), and a test that flagged those
 * would be turned off rather than obeyed.
 */
const RESTRICTION_PHRASES = [
  /you\s+have\s+read-only\s+access/i,
  /you\s+have\s+only\s+read-only\s+access/i,
  /ask\s+an\s+administrator\s+if\s+you\s+need/i,
];

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.(ts|tsx)$/.test(entry)) out.push(full);
  }
  return out;
}

function sourceFiles(): string[] {
  const files: string[] = [];
  for (const dir of SOURCE_DIRS) {
    const abs = join(frontendRoot, dir);
    if (existsSync(abs)) walk(abs, files);
  }
  return files;
}

function relativePath(file: string): string {
  return relative(frontendRoot, file).split(sep).join("/");
}

describe("the read-only restriction has exactly one author", () => {
  it("no screen writes its own permission-restriction copy", () => {
    const offenders: string[] = [];
    for (const file of sourceFiles()) {
      const rel = relativePath(file);
      if (OWNERS.has(rel) || rel.endsWith(".test.ts") || rel.endsWith(".test.tsx")) {
        continue;
      }
      const src = readFileSync(file, "utf8");
      if (RESTRICTION_PHRASES.some((phrase) => phrase.test(src))) {
        offenders.push(rel);
      }
    }
    expect(
      offenders,
      "Render <ReadOnlyNotice canEdit={...}> instead. It shows nothing to a " +
        "user who may edit, which is the whole rule."
    ).toEqual([]);
  });

  it("the company profile offers Edit rather than explaining a restriction", () => {
    const src = readFileSync(
      join(frontendRoot, "app/(org)/org/profile/page.tsx"),
      "utf8"
    );
    // The regression in one line: the Save control may be gated on the edit
    // MODE, but the restriction copy may not be the else-branch of it.
    expect(src).toContain("<ReadOnlyNotice");
    expect(src).toMatch(/canEdit\s*&&\s*!editing/);
  });
});
