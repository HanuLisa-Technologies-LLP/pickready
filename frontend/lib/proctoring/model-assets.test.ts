/**
 * The vendored models are on disk, pinned, and the client asks for exactly
 * those paths.
 *
 * WHY THIS IS A TEST AND NOT A DEPLOY CHECK. Every model file is served from
 * our own origin so an assessment does not depend on a third party being
 * reachable (proctoring spec 3). A missing or renamed file does not fail the
 * build, it fails silently in the candidate's browser mid-assessment, which
 * is exactly the failure the vendoring exists to prevent. So the paths the
 * worker names are compared against the files on disk here, where a rename
 * on either side fails in two seconds.
 *
 * It also holds the two tables that mirror the backend: the client-emittable
 * event identifiers and the client config field names. Both are read out of
 * the Python source rather than restated, because a restatement is a second
 * copy that drifts.
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath, URL } from "node:url";

import { describe, expect, it } from "vitest";

import { CLIENT_EVENTS } from "./catalog";
import { CLIENT_CONFIG_FIELDS } from "./config";
import { WORKER_SCRIPTS } from "./worker-client";
import { FACE_API_MODELS, MODEL_PATHS } from "./workers/protocol";

const FRONTEND = fileURLToPath(new URL("../..", import.meta.url));
const PUBLIC = join(FRONTEND, "public");
const REPO = join(FRONTEND, "..");

interface ManifestEntry {
  path: string;
  source: string;
  sha256: string;
  bytes: number;
}

function manifest(): { files: ManifestEntry[] } {
  return JSON.parse(readFileSync(join(PUBLIC, "models", "manifest.json"), "utf8"));
}

/** Every file the client can ask the network for, as a public path. */
function referencedPaths(): string[] {
  const cocoModel = readFileSync(join(PUBLIC, MODEL_PATHS.cocoSsdModel), "utf8");
  const cocoShards: string[] = [];
  for (const group of JSON.parse(cocoModel).weightsManifest) {
    for (const shard of group.paths) cocoShards.push(`/models/coco-ssd/${shard}`);
  }
  const faceApi: string[] = [];
  for (const model of FACE_API_MODELS) {
    const name = `${model}-weights_manifest.json`;
    faceApi.push(`${MODEL_PATHS.faceApiWeights}/${name}`);
    const groups = JSON.parse(readFileSync(join(PUBLIC, "models", "face-api", name), "utf8"));
    for (const group of groups) {
      for (const shard of group.paths) faceApi.push(`${MODEL_PATHS.faceApiWeights}/${shard}`);
    }
  }
  return [
    MODEL_PATHS.cocoSsdModel,
    ...cocoShards,
    MODEL_PATHS.faceLandmarkerModel,
    // FilesetResolver picks one of the two builds at runtime, so both have to
    // be present: which one is chosen depends on the candidate's browser.
    `${MODEL_PATHS.mediapipeWasm}/vision_wasm_internal.js`,
    `${MODEL_PATHS.mediapipeWasm}/vision_wasm_internal.wasm`,
    `${MODEL_PATHS.mediapipeWasm}/vision_wasm_nosimd_internal.js`,
    `${MODEL_PATHS.mediapipeWasm}/vision_wasm_nosimd_internal.wasm`,
    ...faceApi,
  ];
}

describe("vendored model assets", () => {
  it("has every path the client loads on disk", () => {
    const missing = referencedPaths().filter((path) => !existsSync(join(PUBLIC, path)));
    expect(missing, "run: node scripts/vendor-proctoring-models.mjs").toEqual([]);
  });

  it("pins every one of those paths in the manifest", () => {
    const pinned = new Set(manifest().files.map((entry) => `/models/${entry.path}`));
    const unpinned = referencedPaths().filter((path) => !pinned.has(path));
    expect(unpinned).toEqual([]);
  });

  it("names only our own origin, never a third-party host", () => {
    // The whole point of vendoring: nothing the client loads at runtime may
    // be an absolute URL to somebody else's server.
    for (const path of Object.values(MODEL_PATHS)) {
      expect(path.startsWith("/models/"), path).toBe(true);
    }
  });

  it("loads the worker bundles the build script actually writes", () => {
    // The bundles are build output, so they are not on disk in a checkout and
    // cannot be asserted by existence. What CAN drift is the pair of names:
    // the client asking for a path the build never writes is a worker that
    // 404s in a candidate's browser mid-assessment.
    const script = readFileSync(join(FRONTEND, "scripts", "build-proctoring-workers.mjs"), "utf8");
    const outputs = [...script.matchAll(/"(public\/proctoring\/[a-z.]+\.js)"/g)].map(
      (match) => `/${match[1].slice("public/".length)}`
    );
    expect(outputs.length).toBe(Object.keys(WORKER_SCRIPTS).length);
    expect(Object.values(WORKER_SCRIPTS).sort()).toEqual(outputs.sort());
  });

  it("keeps the built worker bundles out of version control", () => {
    // Build output from our own source. A committed bundle goes stale against
    // the code it was built from and nothing says so; the vendored MODELS are
    // the opposite case and are committed deliberately.
    const rules = readFileSync(join(FRONTEND, ".gitignore"), "utf8")
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0 && !line.startsWith("#"));
    expect(rules).toContain("public/proctoring/");
    expect(rules.filter((rule) => rule.includes("public/models"))).toEqual([]);
  });

  it("carries the licences and provenance beside the files", () => {
    const readme = readFileSync(join(PUBLIC, "models", "README.md"), "utf8");
    expect(readme).toContain("Apache License 2.0");
    expect(readme).toContain("MIT");
    expect(readme).toContain("storage.googleapis.com");
  });
});

describe("the client and the server share one vocabulary", () => {
  const catalogSource = readFileSync(
    join(REPO, "backend", "app", "services", "proctoring", "catalog.py"),
    "utf8"
  );
  const configSource = readFileSync(
    join(REPO, "backend", "app", "services", "proctoring", "config.py"),
    "utf8"
  );

  it("emits exactly the events the backend catalog marks client-emittable", () => {
    const emittable = new Set<string>();
    const entry = /EventSpec\(\s*"([A-Z_]+)",\s*(PATH_[ABC])[\s\S]*?client_emittable=(True|False)/g;
    const paths: Record<string, string> = {};
    let match: RegExpExecArray | null;
    while ((match = entry.exec(catalogSource)) !== null) {
      if (match[3] !== "True") continue;
      emittable.add(match[1]);
      paths[match[1]] = match[2].slice("PATH_".length);
    }
    expect(emittable.size).toBeGreaterThan(0);
    expect(Object.keys(CLIENT_EVENTS).sort()).toEqual([...emittable].sort());
    // And each one on the same consequence path, because the path is what
    // decides whether this client flushes the batch at once.
    expect(CLIENT_EVENTS).toEqual(paths);
  });

  it("reads exactly the config fields the backend projects to the client", () => {
    // `\r?\n` rather than `\n`. The closing paren has to sit at the end of a
    // line so an inner `)` cannot end the match early, but `.gitattributes`
    // pins LF for shell and YAML only, so a Windows checkout hands this test
    // CRLF Python and `\)\n` matches nothing. It failed for that reason and
    // for no other, which reads as a broken contract rather than a broken
    // regex. Line endings are not part of what this test checks.
    const block = configSource.match(/CLIENT_FIELDS: tuple\[str, \.\.\.\] = \(([\s\S]*?)\)\r?\n/);
    expect(block, "CLIENT_FIELDS is no longer a plain tuple literal").toBeTruthy();
    const fields = [...(block as RegExpMatchArray)[1].matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]);
    expect(fields.length).toBeGreaterThan(0);
    expect([...CLIENT_CONFIG_FIELDS].sort()).toEqual([...fields].sort());
  });
});
