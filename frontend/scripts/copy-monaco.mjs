#!/usr/bin/env node
/**
 * Copy the Monaco editor's AMD build into `public/monaco/vs`.
 *
 * WHY MONACO IS SELF-HOSTED. `@monaco-editor/react` loads the editor at
 * runtime through an AMD loader, and its default source is a public CDN. The
 * Content-Security-Policy in `next.config.js` names no CDN in `script-src`, so
 * that default would be refused in a candidate's browser in the middle of a
 * timed coding question, and widening the policy to a third-party origin to
 * make it work would hand the assessment page's script supply to somebody
 * else. The files are served from this origin instead, which the policy
 * already admits: `script-src 'self'` for the loader and its modules,
 * `worker-src 'self' blob:` for the editor worker (the build bootstraps it
 * from a blob that imports a same-origin script) and `font-src data:` for the
 * icon font, which the stylesheet inlines.
 *
 * WHY A COPY RATHER THAN A BUNDLER FEATURE. The same reason as
 * `build-proctoring-workers.mjs`: Next 16 builds with Turbopack, and the
 * editor's ESM build needs worker and CSS handling this project does not
 * configure. The AMD build is complete as shipped and needs nothing but a
 * static path.
 *
 * WHAT IS COPIED. `node_modules/monaco-editor/min/vs`, whole. The loader asks
 * for `vs/editor/editor.main`, which pulls its chunks, and each language's
 * grammar is fetched lazily by the loader when a model in that language is
 * first created. Pruning the tree by hand would turn a missing file into a
 * language that silently stops highlighting in one browser session, so the
 * directory is copied as the package ships it and the copy is VERIFIED below.
 *
 * The output is a BUILD ARTIFACT and is gitignored (`public/monaco/`).
 * `prebuild` and `predev` run this, so `npm run build`, the Docker image
 * (whose builder stage runs `npm run build`) and `npm run dev` all produce it.
 * A committed copy would go stale against the pinned package version with
 * nothing saying so.
 *
 * Written against the Node 20 standard library the Docker builder runs, with
 * no experimental API (`fs.cp` is still flagged experimental there).
 */
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
} from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const FRONTEND = join(here, "..");

/** Where the package ships its AMD build. */
export const SOURCE = join(FRONTEND, "node_modules", "monaco-editor", "min", "vs");

/** Where the page loads it from. `lib/assessment/monaco-setup.ts` points the
 *  loader at `/monaco/vs`, and `lib/assessment/monaco-self-hosted.test.ts`
 *  reads this literal out of this source and asserts the two agree. */
export const OUTPUT = join(FRONTEND, "public", "monaco", "vs");

/** The files the loader cannot start without. A copy missing any of them is
 *  refused here, at build time, rather than discovered by a candidate. */
const REQUIRED = ["loader.js", "editor/editor.main.js", "editor/editor.main.css"];

function copyTree(from, to) {
  mkdirSync(to, { recursive: true });
  let files = 0;
  for (const entry of readdirSync(from)) {
    const source = join(from, entry);
    const target = join(to, entry);
    if (statSync(source).isDirectory()) {
      files += copyTree(source, target);
    } else {
      copyFileSync(source, target);
      files += 1;
    }
  }
  return files;
}

function main() {
  if (!existsSync(SOURCE)) {
    throw new Error(
      `copy-monaco: ${relative(FRONTEND, SOURCE)} does not exist. Run npm ci first; ` +
        "the coding editor cannot load without it."
    );
  }
  const version = JSON.parse(
    readFileSync(join(FRONTEND, "node_modules", "monaco-editor", "package.json"), "utf8")
  ).version;

  rmSync(join(FRONTEND, "public", "monaco"), { recursive: true, force: true });
  const files = copyTree(SOURCE, OUTPUT);

  const missing = REQUIRED.filter((file) => !existsSync(join(OUTPUT, file)));
  if (missing.length > 0) {
    throw new Error(
      `copy-monaco: monaco-editor ${version} no longer ships ${missing.join(", ")} in min/vs. ` +
        "The AMD build the editor loads has changed shape; read the package changelog before upgrading."
    );
  }
  console.log(
    `copy-monaco: monaco-editor ${version}, ${files} files -> ${relative(FRONTEND, OUTPUT)}`
  );
}

main();
