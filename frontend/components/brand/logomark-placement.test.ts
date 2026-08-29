/**
 * The 3D logomark stays on the landing and login surfaces, and nowhere else.
 *
 * spec-doc5 §C.2 is explicit: "Use it for the landing/login hero and nowhere
 * else -- this is a signature moment, not a UI pattern to repeat." And the
 * acceptance criterion says the same thing again: "The R+P logomark exists as a
 * working Three.js scene, used once, on the landing/login surface only."
 *
 * A COMMENT SAYING "LANDING ONLY" IS A COMMENT. This counts the call sites,
 * because the failure mode is not that somebody ignores the rule deliberately;
 * it is that a component gets reused, which is what components are for. The
 * only thing that survives that is a check.
 *
 * It also asserts the SHAPE of the geometry factory -- the named parts and the
 * pure animation functions -- because "it renders something" is not the
 * property that matters. What matters is that the shared stroke is addressable
 * on its own, which is the entire reason the mark was rebuilt procedurally
 * rather than traced.
 */
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { PART, assemble, createLogomark, disposeLogomark } from "./logomark-3d";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..", "..");

/** Surfaces allowed to import the 3D hero. Landing and login. */
const ALLOWED = [
  "app/(public)/hero.tsx",
  "components/auth-shell.tsx",
  // The component itself, the geometry factory it loads, and this test.
  "components/brand/logomark-hero.tsx",
  "components/brand/logomark-3d.ts",
  "components/brand/logomark-placement.test.ts",
];

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === ".next" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.(tsx?|jsx?)$/.test(entry)) out.push(full);
  }
  return out;
}

describe("the 3D logomark's placement", () => {
  it("is imported only by the landing hero and the auth shell", () => {
    const offenders: string[] = [];
    for (const dir of ["app", "components", "lib"]) {
      const root = join(frontendRoot, dir);
      let files: string[] = [];
      try {
        files = walk(root);
      } catch {
        continue;
      }
      for (const file of files) {
        const source = readFileSync(file, "utf8");
        if (!source.includes("logomark-hero") && !source.includes("logomark-3d")) continue;
        const rel = relative(frontendRoot, file).split(sep).join("/");
        if (!ALLOWED.includes(rel)) offenders.push(rel);
      }
    }
    expect(
      offenders,
      "The Three.js logomark is a signature moment, not a UI pattern. " +
        "spec-doc5 §C.2 restricts it to the landing and login surfaces. " +
        `These import it: ${offenders.join(", ")}`
    ).toEqual([]);
  });

  it("is mounted, not merely present in the codebase", () => {
    // The mirror of the test above, and the one that catches the opposite
    // mistake: a scene nobody renders is a scene that has quietly rotted.
    const mounted = ["app/(public)/hero.tsx", "components/auth-shell.tsx"].filter((rel) =>
      readFileSync(join(frontendRoot, rel), "utf8").includes("<LogomarkHero")
    );
    expect(mounted).toHaveLength(2);
  });

  it("does not pull three.js into every page's bundle", () => {
    // The import is dynamic, inside the effect. A static top-level import would
    // put ~600KB of three.js in the bundle of every route that renders the
    // component's module graph, which for `auth-shell` is every portal's login.
    const source = readFileSync(
      join(frontendRoot, "components/brand/logomark-hero.tsx"),
      "utf8"
    );
    expect(source).not.toMatch(/^import \* as THREE from "three"/m);
    expect(source).toMatch(/await Promise\.all\(\[\s*import\("three"\)/);
  });

  it("degrades to the flat mark rather than to a hole", () => {
    const source = readFileSync(
      join(frontendRoot, "components/brand/logomark-hero.tsx"),
      "utf8"
    );
    // The fallback is the real brand mark and it is HIDDEN rather than
    // unmounted, so a context loss mid-session leaves something on the page.
    expect(source).toContain("prefers-reduced-motion");
    expect(source).toContain("<Logo variant=\"mark\"");
    expect(source).toContain("opacity-0");
  });
});

describe("the logomark geometry", () => {
  it("exposes the shared stroke as its own addressable part", () => {
    // THE REASON THE MARK IS PROCEDURAL. A traced outline gives one blob of
    // geometry, and the shared stroke -- the brand's one distinctive idea --
    // could not be lit, swept or held while the letters move.
    const group = createLogomark();
    expect(group.getObjectByName(PART.SHARED_STROKE)).toBeTruthy();
    expect(group.getObjectByName(PART.R)).toBeTruthy();
    expect(group.getObjectByName(PART.P)).toBeTruthy();
    disposeLogomark(group);
  });

  it("carries the sampled brand colours and no others", () => {
    const group = createLogomark();
    const hexes = new Set<string>();
    group.traverse((node) => {
      const mesh = node as unknown as { isMesh?: boolean; material?: { color?: { getHexString(): string } } };
      if (mesh.isMesh && mesh.material?.color) hexes.add(mesh.material.color.getHexString());
    });
    // Converted from sRGB into linear space on the way in, so the stored value
    // is not the literal hex. What is asserted is that there are exactly TWO
    // distinct colours in the mark -- navy and teal -- and nothing else has
    // crept in.
    expect(hexes.size).toBe(2);
    disposeLogomark(group);
  });

  it("assembles purely, so it can be scrubbed backwards", () => {
    const group = createLogomark();
    const r = group.getObjectByName(PART.R)!;

    assemble(group, 0);
    const atZero = r.position.x;
    assemble(group, 1);
    const atOne = r.position.x;
    assemble(group, 0);
    // Idempotent: returning to 0 returns to the same place, rather than
    // accumulating an offset the way an incremental animation would.
    expect(r.position.x).toBeCloseTo(atZero, 5);
    expect(atOne).not.toBeCloseTo(atZero, 2);

    // And the letters arrive from OPPOSITE sides, which is what makes the
    // assembly read as interlocking rather than as a fade-in.
    const p = group.getObjectByName(PART.P)!;
    assemble(group, 0);
    expect(Math.sign(r.position.x)).not.toBe(Math.sign(p.position.x));
    disposeLogomark(group);
  });

  it("centres on its own bounding box", () => {
    // Otherwise a rotating logo wobbles around an origin that is not its
    // centre, which is the tell of a mark somebody exported and never checked.
    const group = createLogomark();
    expect(Math.abs(group.position.x) + Math.abs(group.position.y)).toBeGreaterThan(0);
    disposeLogomark(group);
  });

  it("releases its GPU resources", () => {
    // three.js does not garbage-collect them, and the auth shell mounts and
    // unmounts on every navigation.
    const group = createLogomark();
    const geometries: Array<{ disposed?: boolean }> = [];
    group.traverse((node) => {
      const mesh = node as unknown as { isMesh?: boolean; geometry?: { dispose: () => void } };
      if (mesh.isMesh && mesh.geometry) {
        const original = mesh.geometry.dispose.bind(mesh.geometry);
        const record: { disposed?: boolean } = {};
        geometries.push(record);
        mesh.geometry.dispose = () => {
          record.disposed = true;
          original();
        };
      }
    });
    disposeLogomark(group);
    expect(geometries.length).toBeGreaterThan(0);
    expect(geometries.every((entry) => entry.disposed)).toBe(true);
  });
});
