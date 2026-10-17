/**
 * The type system's faces, and where each is allowed (DESIGN.md section 3).
 *
 * Outfit is the working face, Fraunces the heading and brand face, and Geist
 * Mono the reference face. The rules below are the ones that erode one
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
      expect(read(file), rel(file)).not.toMatch(/\b(Inter_Tight|Mona_Sans|Hubot_Sans|JetBrains_Mono)\b/);
    }
  });

  it("the root layout binds Outfit, Fraunces and Geist Mono", () => {
    const layout = read(join(ROOT, "app/layout.tsx"));
    expect(layout).toMatch(/Outfit\(/);
    expect(layout).toMatch(/Fraunces\(/);
    expect(layout).toMatch(/Geist_Mono\(/);
    expect(layout).not.toMatch(/DM_Serif_Display|Mona_Sans|Hubot_Sans/);
  });

  it("no face is fetched from a CDN at runtime", () => {
    for (const file of FILES) {
      expect(read(file), rel(file)).not.toMatch(/fonts\.(googleapis|gstatic)\.com|use\.typekit|fonts\.bunny/);
    }
  });
});

describe("Fraunces is the single heading and brand face", () => {
  it("has no route-local font loader", () => {
    for (const file of FILES) {
      expect(read(file), rel(file)).not.toMatch(/from "@\/app\/display-font"/);
    }
  });

  it("applies to semantic headings and the wordmark", () => {
    const css = read(join(ROOT, "app/globals.css"));
    const logo = read(join(ROOT, "components/brand/logo.tsx"));
    expect(css).toMatch(/h1,[\s\S]*h6\s*\{[\s\S]*font-display/);
    expect(logo).toMatch(/font-display/);
  });
});

describe("the component roles", () => {
  const css = read(join(ROOT, "app/globals.css"));
  const rule = (name: string) => css.match(new RegExp(`\\.${name}\\s*\\{([^}]*)\\}`))?.[1] ?? "";

  it("a reference code is monospace with tabular figures", () => {
    expect(rule("type-ref")).toMatch(/font-mono/);
    expect(rule("type-ref")).toMatch(/tabular-nums/);
  });

  it("title roles use the display face and prose roles do not", () => {
    expect(rule("type-display")).toMatch(/font-display/);
    expect(rule("type-section-title")).toMatch(/font-editorial/);
    for (const name of ["type-lead", "type-prose-lg", "type-eyebrow"]) {
      expect(rule(name), name).not.toMatch(/font-display/);
    }
  });

  it("the section-title role uses the editorial face", () => {
    expect(rule("type-section-title")).toMatch(/font-editorial/);
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
