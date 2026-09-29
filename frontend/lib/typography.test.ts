/**
 * The type system's faces, and where each is allowed (DESIGN.md section 3).
 *
 * Mona Sans is the working face, Hubot Sans the rare marketing display face,
 * Geist Mono the reference face. The rules below are the ones that erode one
 * call site at a time: a display face creeping onto a dashboard heading, a
 * retired face left imported, a reference role that stops being monospace,
 * or a component inventing its own pixel size instead of using a role.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join, relative, sep } from "node:path";
import { fileURLToPath, URL } from "node:url";

import { describe, expect, it } from "vitest";

const ROOT = fileURLToPath(new URL("..", import.meta.url));

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...sourceFiles(full));
    } else if ([".ts", ".tsx", ".css"].includes(extname(full)) && !/\.test\.tsx?$/.test(full)) {
      out.push(full);
    }
  }
  return out;
}

const FILES = ["app", "components", "lib"].flatMap((dir) => sourceFiles(join(ROOT, dir)));
const rel = (file: string) => relative(ROOT, file).split(sep).join("/");
const read = (file: string) => readFileSync(file, "utf8");

describe("the faces", () => {
  it("the retired faces are imported nowhere", () => {
    for (const file of FILES) {
      expect(read(file), rel(file)).not.toMatch(/\b(Inter_Tight|Fraunces|JetBrains_Mono)\b/);
    }
  });

  it("the root layout binds Mona Sans and Geist Mono, and not the display face", () => {
    const layout = read(join(ROOT, "app/layout.tsx"));
    expect(layout).toMatch(/Mona_Sans\(/);
    expect(layout).toMatch(/Geist_Mono\(/);
    expect(layout).not.toMatch(/Hubot_Sans\(|from "@\/app\/display-font"/);
  });

  it("no face is fetched from a CDN at runtime", () => {
    for (const file of FILES) {
      expect(read(file), rel(file)).not.toMatch(/fonts\.(googleapis|gstatic)\.com|use\.typekit|fonts\.bunny/);
    }
  });
});

describe("Hubot Sans stays rare", () => {
  it("is bound only by the two public frames", () => {
    const importers = FILES.filter((file) => /from "@\/app\/display-font"/.test(read(file))).map(rel);
    expect(importers.sort()).toEqual(["app/(public)/landing-page.tsx", "app/(public)/layout.tsx"]);
  });

  it("is reached only through `.type-display`, and only on the public site", () => {
    for (const file of FILES.filter((f) => extname(f) === ".tsx")) {
      const source = read(file);
      expect(source, rel(file)).not.toMatch(/\bfont-display\b/);
      if (/\btype-display\b/.test(source)) {
        expect(rel(file), "a display headline outside the public site").toMatch(/^app\/\(public\)\//);
      }
    }
  });
});

describe("the component roles", () => {
  const css = read(join(ROOT, "app/globals.css"));
  const rule = (name: string) => css.match(new RegExp(`\\.${name}\\s*\\{([^}]*)\\}`))?.[1] ?? "";

  it("a reference code is monospace with tabular figures", () => {
    expect(rule("type-ref")).toMatch(/font-mono/);
    expect(rule("type-ref")).toMatch(/tabular-nums/);
  });

  it("the display role is the only one that uses the display face", () => {
    expect(rule("type-display")).toMatch(/font-display/);
    for (const name of ["type-page-title", "type-section-title", "type-lead", "type-prose-lg", "type-eyebrow"]) {
      expect(rule(name), name).not.toMatch(/font-display/);
    }
  });

  it("running text on the public site keeps a readable measure", () => {
    expect(rule("type-lead")).toMatch(/max-w-/);
    expect(rule("type-prose-lg")).toMatch(/max-w-/);
  });
});

describe("no invented sizes outside the roles", () => {
  // The workflow animation draws a miniature product UI at a fraction of its
  // real size, so its sub-role pixel sizes are geometry, not typography.
  const EXEMPT = ["components/workflow-animation/"];

  it("no component sets a pixel or rem font size by hand", () => {
    for (const file of FILES.filter((f) => extname(f) !== ".css")) {
      if (EXEMPT.some((prefix) => rel(file).startsWith(prefix))) continue;
      expect(read(file), rel(file)).not.toMatch(/\btext-\[\d+(\.\d+)?(px|rem)\]/);
    }
  });
});
