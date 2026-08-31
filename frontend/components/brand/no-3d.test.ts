/**
 * No 3D model renders anywhere in the product. Ever.
 *
 * Master Implementation Directive §0 records the violation this guards
 * against: a floating, glossy 3D logomark was rendered over the hero headline,
 * in direct breach of Part 1 §1 ("not 3D-heavy"), §25 ("Do not introduce a 3D
 * model merely to make the application look futuristic") and §30 (3D models
 * are a named anti-pattern). The corrective action removed the Three.js scene
 * and its geometry factory entirely.
 *
 * This test is the check that keeps it removed. It walks the source tree and
 * fails on any import of three.js or any resurrection of the logomark scene
 * files, because the failure mode is not somebody deliberately ignoring the
 * directive; it is a well-meaning "signature moment" coming back in a future
 * redesign. A comment cannot stop that. A failing test can.
 */
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..", "..");

const SOURCE_DIRS = ["app", "components", "lib"];
const SKIP = new Set(["node_modules", ".next", ".turbo"]);

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.(ts|tsx)$/.test(entry)) out.push(full);
  }
  return out;
}

describe("directive §0: the 3D logomark stays removed", () => {
  it("no source file imports three.js", () => {
    const offenders: string[] = [];
    for (const dir of SOURCE_DIRS) {
      const abs = join(frontendRoot, dir);
      if (!existsSync(abs)) continue;
      for (const file of walk(abs)) {
        if (file === join(here, "no-3d.test.ts")) continue;
        const src = readFileSync(file, "utf8");
        if (/from\s+["']three["']|import\(["']three["']\)|require\(["']three["']\)/.test(src)) {
          offenders.push(relative(frontendRoot, file).split(sep).join("/"));
        }
      }
    }
    expect(offenders, "three.js import found; 3D is banned by directive §0/§25/§30").toEqual([]);
  });

  it("the logomark scene files do not exist", () => {
    for (const file of [
      "components/brand/logomark-3d.ts",
      "components/brand/logomark-hero.tsx",
    ]) {
      expect(existsSync(join(frontendRoot, file)), `${file} must stay deleted`).toBe(false);
    }
  });

  it("three.js is not a dependency", () => {
    const pkg = JSON.parse(readFileSync(join(frontendRoot, "package.json"), "utf8"));
    expect({ ...pkg.dependencies, ...pkg.devDependencies }).not.toHaveProperty("three");
    expect({ ...pkg.dependencies, ...pkg.devDependencies }).not.toHaveProperty("@types/three");
  });
});
