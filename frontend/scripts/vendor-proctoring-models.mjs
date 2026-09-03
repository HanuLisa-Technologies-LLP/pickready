#!/usr/bin/env node
/**
 * Vendor the proctoring models into `public/models/`.
 *
 * WHY THE MODELS ARE VENDORED AT ALL. The proctoring client loads three model
 * families in the candidate's browser (proctoring spec section 3): COCO-SSD
 * for objects, MediaPipe Face Landmarker for faces, face-api.js for the
 * identity descriptor. Loading them from their publishers' hosts at runtime
 * would make an assessment depend on a third party being reachable mid-session
 * and would tell that third party when a candidate was being assessed. So the
 * files are downloaded ONCE, here, checked against a pinned SHA-256, committed
 * under `public/models/`, and served from our own origin. Nothing loads from a
 * third-party host at runtime; `lib/proctoring/model-assets.test.ts` fails if
 * a path the client references is missing or has drifted from its pin.
 *
 * HOW THE PINS WORK. The URLs are fixed below. The SHA-256 of every file, its
 * size and where it came from live in `public/models/manifest.json`, which is
 * committed beside the files. The default run verifies: every file in the
 * manifest is present on disk with the pinned hash, downloading any that is
 * missing and refusing any download whose hash differs. `--record` is the ONE
 * way to change a pin: it downloads everything fresh, discovers the weight
 * shards from each model's own manifest, and rewrites `manifest.json`. That is
 * a reviewed change in a diff, never something a build does on its own.
 *
 *   node scripts/vendor-proctoring-models.mjs            verify, fill gaps
 *   node scripts/vendor-proctoring-models.mjs --record   re-pin everything
 *
 * The COCO-SSD URL is the one `@tensorflow-models/coco-ssd` itself uses for
 * `lite_mobilenet_v2` (BASE_PATH + "ssdlite_mobilenet_v2/model.json" in its
 * dist/index.js); the client passes `modelUrl` so the package never reaches for
 * it. The MediaPipe wasm files are copied from the installed
 * `@mediapipe/tasks-vision` package so the runtime and its wasm are the same
 * release. The face-api.js weights are the repository's own `weights/` files.
 */
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, posix } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const FRONTEND = join(here, "..");
const MODELS_DIR = join(FRONTEND, "public", "models");
const MANIFEST_PATH = join(MODELS_DIR, "manifest.json");
const RECORD = process.argv.includes("--record");

const COCO_SSD_MODEL_URL =
  "https://storage.googleapis.com/tfjs-models/savedmodel/ssdlite_mobilenet_v2/model.json";
const MEDIAPIPE_FACE_LANDMARKER_URL =
  "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task";
const FACE_API_WEIGHTS_BASE =
  "https://raw.githubusercontent.com/justadudewhohacks/face-api.js/master/weights/";
const FACE_API_MODELS = [
  "tiny_face_detector_model",
  "face_landmark_68_model",
  "face_recognition_model",
];
const MEDIAPIPE_WASM_DIR = join(FRONTEND, "node_modules", "@mediapipe", "tasks-vision", "wasm");
/** The files `FilesetResolver.forVisionTasks` chooses between at runtime:
 *  the SIMD build when the browser supports it, the nosimd build otherwise. */
const MEDIAPIPE_WASM_FILES = [
  "vision_wasm_internal.js",
  "vision_wasm_internal.wasm",
  "vision_wasm_nosimd_internal.js",
  "vision_wasm_nosimd_internal.wasm",
];

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

async function download(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${url}: HTTP ${response.status}`);
  }
  return Buffer.from(await response.arrayBuffer());
}

function packageVersion(name) {
  const file = join(FRONTEND, "node_modules", ...name.split("/"), "package.json");
  return JSON.parse(readFileSync(file, "utf8")).version;
}

function writeAsset(relativePath, bytes) {
  const target = join(MODELS_DIR, ...relativePath.split("/"));
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, bytes);
}

function readAsset(relativePath) {
  const target = join(MODELS_DIR, ...relativePath.split("/"));
  return existsSync(target) ? readFileSync(target) : null;
}

/**
 * Discover every file the three model families need, download each, and
 * return manifest entries. Only `--record` calls this; the default run works
 * from the committed manifest and never re-derives the file list.
 */
async function discoverAndFetch() {
  const entries = [];
  const add = (path, source, bytes) => {
    entries.push({ path, source, sha256: sha256(bytes), bytes: bytes.length });
    writeAsset(path, bytes);
    process.stdout.write(`  ${path}  ${bytes.length} bytes\n`);
  };

  process.stdout.write("COCO-SSD lite_mobilenet_v2\n");
  const cocoModel = await download(COCO_SSD_MODEL_URL);
  add("coco-ssd/model.json", COCO_SSD_MODEL_URL, cocoModel);
  const cocoBase = COCO_SSD_MODEL_URL.slice(0, COCO_SSD_MODEL_URL.lastIndexOf("/") + 1);
  for (const group of JSON.parse(cocoModel.toString("utf8")).weightsManifest) {
    for (const shard of group.paths) {
      add(posix.join("coco-ssd", shard), cocoBase + shard, await download(cocoBase + shard));
    }
  }

  process.stdout.write("MediaPipe Face Landmarker\n");
  add(
    "mediapipe/face_landmarker.task",
    MEDIAPIPE_FACE_LANDMARKER_URL,
    await download(MEDIAPIPE_FACE_LANDMARKER_URL)
  );
  const mediapipeVersion = packageVersion("@mediapipe/tasks-vision");
  for (const file of MEDIAPIPE_WASM_FILES) {
    add(
      posix.join("mediapipe", "wasm", file),
      `npm:@mediapipe/tasks-vision@${mediapipeVersion}/wasm/${file}`,
      readFileSync(join(MEDIAPIPE_WASM_DIR, file))
    );
  }

  process.stdout.write("face-api.js weights\n");
  for (const model of FACE_API_MODELS) {
    const manifestName = `${model}-weights_manifest.json`;
    const manifestBytes = await download(FACE_API_WEIGHTS_BASE + manifestName);
    add(posix.join("face-api", manifestName), FACE_API_WEIGHTS_BASE + manifestName, manifestBytes);
    for (const group of JSON.parse(manifestBytes.toString("utf8"))) {
      for (const shard of group.paths) {
        add(
          posix.join("face-api", shard),
          FACE_API_WEIGHTS_BASE + shard,
          await download(FACE_API_WEIGHTS_BASE + shard)
        );
      }
    }
  }
  return entries;
}

/** Fetch one manifest entry from where the manifest says it came from. */
async function fetchPinned(entry) {
  if (entry.source.startsWith("npm:")) {
    // npm:<package>@<version>/<file within the package>
    const spec = entry.source.slice("npm:".length);
    const at = spec.lastIndexOf("@");
    const name = spec.slice(0, at);
    const rest = spec.slice(at + 1);
    const slash = rest.indexOf("/");
    const version = rest.slice(0, slash);
    const file = rest.slice(slash + 1);
    const installed = packageVersion(name);
    if (installed !== version) {
      throw new Error(
        `${entry.path} is pinned to ${name}@${version} but ${installed} is installed; ` +
          "run with --record after deciding the upgrade is wanted"
      );
    }
    return readFileSync(join(FRONTEND, "node_modules", ...name.split("/"), ...file.split("/")));
  }
  return download(entry.source);
}

async function verify(manifest) {
  let fetched = 0;
  for (const entry of manifest.files) {
    let bytes = readAsset(entry.path);
    if (bytes === null) {
      process.stdout.write(`  fetching ${entry.path}\n`);
      bytes = await fetchPinned(entry);
      fetched += 1;
      if (sha256(bytes) !== entry.sha256) {
        throw new Error(
          `${entry.path}: downloaded content does not match the pinned SHA-256. ` +
            "The upstream file changed; nothing was written. Review it, then --record."
        );
      }
      writeAsset(entry.path, bytes);
    } else if (sha256(bytes) !== entry.sha256) {
      throw new Error(
        `${entry.path}: the file on disk does not match the pinned SHA-256. ` +
          "Delete it to re-fetch, or --record to re-pin deliberately."
      );
    }
    if (bytes.length !== entry.bytes) {
      throw new Error(`${entry.path}: size ${bytes.length} differs from the pinned ${entry.bytes}`);
    }
  }
  process.stdout.write(
    `verified ${manifest.files.length} files (${fetched} fetched, ` +
      `${manifest.files.reduce((sum, entry) => sum + entry.bytes, 0)} bytes)\n`
  );
}

async function main() {
  if (RECORD) {
    const files = await discoverAndFetch();
    const manifest = {
      recorded_at: new Date().toISOString(),
      sources: {
        coco_ssd: COCO_SSD_MODEL_URL,
        mediapipe_face_landmarker: MEDIAPIPE_FACE_LANDMARKER_URL,
        mediapipe_wasm: `@mediapipe/tasks-vision@${packageVersion("@mediapipe/tasks-vision")}`,
        face_api_weights: FACE_API_WEIGHTS_BASE,
      },
      files,
    };
    writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n");
    process.stdout.write(`recorded ${files.length} files into ${MANIFEST_PATH}\n`);
    return;
  }
  if (!existsSync(MANIFEST_PATH)) {
    throw new Error(`${MANIFEST_PATH} is missing; run with --record to create it`);
  }
  await verify(JSON.parse(readFileSync(MANIFEST_PATH, "utf8")));
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(1);
});
