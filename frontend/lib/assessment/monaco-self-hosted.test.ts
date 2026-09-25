// The Monaco editor is served from THIS origin, and nothing lets it quietly
// fall back to a CDN.
//
// `@monaco-editor/react` defaults to loading the editor from a public CDN. The
// Content-Security-Policy refuses that, so a missing or broken self-hosted copy
// would not degrade to the CDN: it would fail in a candidate's browser in the
// middle of a timed coding question. Every link in the chain that keeps the
// copy present and the loader pointed at it is asserted here, because each one
// is a line in a different file that reads fine on its own.

import { createRequire } from "node:module";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { extname, join } from "node:path";
import { fileURLToPath, URL } from "node:url";

import { NextRequest } from "next/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("@monaco-editor/react", async () =>
  (await import("@/lib/assessment/monaco-test-double")).monacoReactModule()
);

import { monacoDouble } from "@/lib/assessment/monaco-test-double";
import { config as proxyConfig, proxy } from "@/proxy";
import {
  configureMonacoLoader,
  MONACO_VS_PATH,
  resetMonacoSetupForTests,
} from "@/lib/assessment/monaco-setup";

const FRONTEND = fileURLToPath(new URL("../..", import.meta.url));
const read = (relative: string) => readFileSync(join(FRONTEND, relative), "utf8");

async function contentSecurityPolicy(): Promise<Record<string, string[]>> {
  const config = createRequire(import.meta.url)(join(FRONTEND, "next.config.js")) as {
    headers: () => Promise<Array<{ headers: Array<{ key: string; value: string }> }>>;
  };
  const rules = await config.headers();
  const csp = rules
    .flatMap((rule) => rule.headers)
    .find((header) => header.key === "Content-Security-Policy");
  expect(csp, "the CSP header").toBeDefined();
  return Object.fromEntries(
    (csp as { value: string }).value.split(";").map((directive) => {
      const [name, ...sources] = directive.trim().split(/\s+/);
      return [name, sources];
    })
  );
}

describe("the loader", () => {
  it("is pointed at the self-hosted copy, on this origin", () => {
    resetMonacoSetupForTests();
    monacoDouble.loaderConfigs = [];
    configureMonacoLoader();
    configureMonacoLoader();
    // Once, and a same-origin PATH: no scheme, no host.
    expect(monacoDouble.loaderConfigs).toEqual([{ paths: { vs: "/monaco/vs" } }]);
    expect(MONACO_VS_PATH.startsWith("/")).toBe(true);
    expect(MONACO_VS_PATH).not.toMatch(/^\/\//);
  });

  it("points where the copy script writes", () => {
    const script = read("scripts/copy-monaco.mjs");
    const output = /export const OUTPUT = join\(FRONTEND, ([^)]*)\)/.exec(script);
    expect(output, "copy-monaco.mjs OUTPUT").not.toBeNull();
    const segments = [...(output as RegExpExecArray)[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
    expect(segments[0]).toBe("public");
    expect(`/${segments.slice(1).join("/")}`).toBe(MONACO_VS_PATH);
  });
});

describe("the copy is produced by every build", () => {
  it("runs before both the dev server and the production build", () => {
    const scripts = JSON.parse(read("package.json")).scripts as Record<string, string>;
    expect(scripts.prebuild).toContain("node scripts/copy-monaco.mjs");
    expect(scripts.predev).toContain("node scripts/copy-monaco.mjs");
  });

  it("copies from the pinned package, and the package ships what the loader needs", () => {
    const pkg = JSON.parse(read("package.json")) as { dependencies: Record<string, string> };
    // Exact pins. The copy is whatever version resolved, so a caret would let
    // the served editor change under a lockfile refresh with no diff to read.
    expect(pkg.dependencies["monaco-editor"]).toMatch(/^\d+\.\d+\.\d+$/);
    expect(pkg.dependencies["@monaco-editor/react"]).toMatch(/^\d+\.\d+\.\d+$/);
    const source = join(FRONTEND, "node_modules", "monaco-editor", "min", "vs");
    for (const file of ["loader.js", "editor/editor.main.js", "editor/editor.main.css"]) {
      expect(existsSync(join(source, file)), file).toBe(true);
    }
  });

  it("keeps the copy out of git and out of the linter", () => {
    expect(read(".gitignore").split(/\r?\n/)).toContain("public/monaco/");
    expect(read("eslint.config.mjs")).toContain('"public/monaco/**"');
  });
});

describe("the Content-Security-Policy admits the self-hosted editor and no CDN", () => {
  it("allows the loader, its blob-bootstrapped worker, its stylesheet and its inlined font", async () => {
    const csp = await contentSecurityPolicy();
    expect(csp["script-src"]).toContain("'self'");
    // The editor worker is started from a blob that imports a same-origin script.
    expect(csp["worker-src"]).toEqual(expect.arrayContaining(["'self'", "blob:"]));
    expect(csp["style-src"]).toContain("'self'");
    // The icon font is a data: URL inside the editor's stylesheet.
    expect(csp["font-src"]).toContain("data:");
  });

  it("names no public package CDN anywhere a script could come from", async () => {
    const csp = await contentSecurityPolicy();
    for (const directive of ["script-src", "worker-src", "style-src", "font-src", "default-src"]) {
      for (const source of csp[directive] ?? []) {
        expect(source, directive).not.toMatch(/jsdelivr|unpkg|cdnjs|esm\.sh|skypack/);
      }
    }
  });

  it("is not relied on by any source file loading the editor from a CDN", () => {
    const offenders: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir)) {
        if (entry === "node_modules" || entry.startsWith(".next")) continue;
        const full = join(dir, entry);
        if (statSync(full).isDirectory()) walk(full);
        else if ([".ts", ".tsx", ".js", ".mjs"].includes(extname(entry))) {
          if (full.endsWith("monaco-self-hosted.test.ts")) continue;
          if (/cdn\.jsdelivr\.net|unpkg\.com/.test(readFileSync(full, "utf8"))) {
            offenders.push(full.slice(FRONTEND.length));
          }
        }
      }
    };
    for (const dir of ["app", "components", "lib", "scripts"]) walk(join(FRONTEND, dir));
    expect(offenders).toEqual([]);
  });
});

describe("the session proxy in front of the copy", () => {
  // `proxy.ts` excludes files by extension only for images, so a request for
  // `/monaco/vs/*.js`, `.css` or `.ttf` passes through its deny-by-default
  // branch like a page. That is deliberate and needs no carve-out: the editor
  // mounts only on the candidate's assessment page and the recruiter's
  // transcript, both of which require a session, and every file the loader
  // fetches (scripts, its worker's imports, the lazily loaded grammars) is a
  // same-origin request carrying the session cookie. What would break it is a
  // coding editor on a signed-out page, and this pins that answer.
  const LOADER = "http://localhost/monaco/vs/loader.js";

  it("sees the editor's files", () => {
    const [pattern] = proxyConfig.matcher;
    const matcher = new RegExp(`^${pattern}$`);
    expect(matcher.test("/monaco/vs/loader.js")).toBe(true);
    expect(matcher.test("/monaco/vs/editor/editor.main.css")).toBe(true);
    expect(matcher.test("/brand/logo.png")).toBe(false);
  });

  it("lets a signed-in page load them", () => {
    const response = proxy(new NextRequest(LOADER, { headers: { cookie: "pr_session=present" } }));
    expect(response.headers.get("location")).toBeNull();
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("would send a signed-out page to sign in, which is why no signed-out page mounts the editor", () => {
    const response = proxy(new NextRequest(LOADER));
    expect(response.status).toBe(307);
    expect(new URL(String(response.headers.get("location"))).pathname).toBe("/login");
  });
});
