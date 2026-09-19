import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

// Sign-in is POPUP-ONLY, and this test is what keeps that a decision rather
// than an accident. signInWithRedirect would put the Firebase helper URL
// (`__/auth/handler?apiKey=...`) in the MAIN browser bar and would need
// getRedirectResult plumbing on every flow; none of that exists, and a stray
// import of it would ship a sign-in that can never complete. The apiKey in
// the popup's own URL is the public Web API key and is not a leak.

const ROOT = join(__dirname, "..");
const SCAN_DIRS = ["app", "components", "lib"];
const SKIP = new Set(["node_modules", ".next", ".next-dev"]);

function* sourceFiles(dir: string): Generator<string> {
  for (const entry of readdirSync(dir)) {
    if (SKIP.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      yield* sourceFiles(full);
    } else if (/\.(ts|tsx)$/.test(entry) && !entry.endsWith(".test.ts")) {
      yield full;
    }
  }
}

describe("firebase sign-in stays popup-only", () => {
  it("no source file uses the redirect flow", () => {
    const offenders: string[] = [];
    for (const dir of SCAN_DIRS) {
      for (const file of sourceFiles(join(ROOT, dir))) {
        const text = readFileSync(file, "utf8");
        if (/signInWithRedirect|getRedirectResult/.test(text)) {
          offenders.push(file);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("the same-origin auth helper proxy exists in next.config.js", () => {
    // The rewrite is what lets NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN point at the
    // site's own host so the popup stops showing the vendor domain. It is
    // inert until that env var is flipped, but removing it would silently
    // strand any deployment that has flipped it.
    const config = readFileSync(join(ROOT, "next.config.js"), "utf8");
    expect(config).toContain('source: "/__/auth/:path*"');
    expect(config).toContain(".firebaseapp.com/__/auth/:path*");
  });

  it("the middleware matcher does not gate the auth helper paths", () => {
    // The helper is loaded by a browser that has no session yet; if the
    // deny-by-default middleware saw it, sign-in on a same-origin auth domain
    // would 307 to /login before the popup could complete.
    const proxy = readFileSync(join(ROOT, "proxy.ts"), "utf8");
    const matcher = proxy.match(/matcher:\s*\[([\s\S]*?)\]/);
    expect(matcher).not.toBeNull();
    expect(matcher![1]).toContain("__/auth");
  });
});
