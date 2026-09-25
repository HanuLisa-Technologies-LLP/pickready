/**
 * The candidate's assessment surface has ONE mode, and the retired pieces of
 * the other one stay gone (Appendix B section 1; acceptance criterion 1: "no
 * video-interview or mode-selection code paths remain reachable").
 *
 * The screens, the client-measured pause time, the answer edit, the advisory
 * time phrase and the retake explanation were deleted with this release.
 * Deleting a file does not stop the next change from reaching for its name,
 * so the assessment surface is swept for every one of them. Whitespace is
 * normalised before matching (the 2026-09-23 lesson: a sweep that reads one
 * line at a time cannot see a name wrapped across two, and a sweep with a
 * blind spot is worse than none because its green is what stops anyone
 * looking), and a hit is mapped back to its line.
 *
 * The backend removal sweeps cover the whole tree, including the proctoring
 * half another package owns; this one pins the files this package owns, so a
 * regression here is named here.
 */
import { describe, expect, it } from "vitest";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..", "..");

/** The assessment surface: the player and its formats, their shared
 *  contracts, the candidate's assessment page and the invitation landing. */
const ROOTS = [
  "components/assessment",
  "lib/assessment",
  join("app", "(candidate)", "portal", "(app)", "assessments"),
  join("app", "assessments"),
  "lib/types.ts",
];

/** Each built from parts, so this file's own text is never a hit. */
const RETIRED: Array<[string, RegExp]> = [
  ["the client-measured pause field", new RegExp(["paused", "ms"].join("_"))],
  ["the bridge's pause reader", new RegExp(["consume", "PausedMs"].join(""))],
  ["the pause stopwatch", new RegExp(["use", "PausedTime"].join(""))],
  ["the mode choice screen", new RegExp(["Mode", "Selection"].join("") + "|" + ["mode", "selection"].join("-"))],
  ["the video interview screen", new RegExp(["Video", "Interview"].join("") + "|" + ["video", "interview"].join("-"))],
  ["the mode state type", new RegExp(["Assessment", "ModeState"].join(""))],
  ["the mode routes", new RegExp("/" + "mode[\"'`]")],
  ["the video interview routes", new RegExp("/video/" + "(start|mark|upload|finalize|status)")],
  ["the answer edit route", new RegExp("/answers/" + "\\$\\{")],
  ["the advisory time phrase", new RegExp(["time", "guidance"].join("-") + "|" + ["time", "AllocationPhrase"].join(""))],
  ["the retake explanation", new RegExp(["recent", "prior", "report"].join("_"))],
];

const SELF = "single-mode-surface.test.ts";

function walk(path: string, out: string[] = []): string[] {
  if (!existsSync(path)) return out;
  if (statSync(path).isFile()) {
    if (/\.(ts|tsx)$/.test(path)) out.push(path);
    return out;
  }
  for (const entry of readdirSync(path)) {
    if (entry === "node_modules") continue;
    walk(join(path, entry), out);
  }
  return out;
}

function sweptFiles(): string[] {
  return ROOTS.flatMap((root) => walk(join(frontendRoot, root))).filter(
    (file) => !file.endsWith(`${sep}${SELF}`)
  );
}

/** Hits over whitespace-normalised text, each named by file and line. */
function hits(file: string): string[] {
  const text = readFileSync(file, "utf-8");
  // Map every normalised offset back to the line it came from.
  const lineAt: number[] = [];
  let normalised = "";
  let line = 1;
  let previousSpace = false;
  for (const char of text) {
    if (char === "\n") line += 1;
    const space = /\s/.test(char);
    if (space && previousSpace) continue;
    normalised += space ? " " : char;
    lineAt.push(line);
    previousSpace = space;
  }
  const found: string[] = [];
  for (const [name, pattern] of RETIRED) {
    const global = new RegExp(pattern.source, "g");
    for (const match of normalised.matchAll(global)) {
      found.push(
        `${relative(frontendRoot, file)}:${lineAt[match.index ?? 0]} names ${name}`
      );
    }
  }
  return found;
}

describe("the single-mode assessment surface", () => {
  it("sweeps a real tree, not an empty one", () => {
    // A sweep over nothing passes every time; the floor is what keeps this
    // one honest if the directories move.
    expect(sweptFiles().length).toBeGreaterThan(20);
  });

  it("names none of the retired dual-mode pieces", () => {
    expect(sweptFiles().flatMap(hits)).toEqual([]);
  });

  it("no longer ships the deleted files", () => {
    for (const deleted of [
      `components/assessment/${["mode", "selection"].join("-")}.tsx`,
      `components/assessment/${["video", "interview"].join("-")}.tsx`,
      `lib/assessment/${["time", "guidance"].join("-")}.ts`,
    ]) {
      expect(existsSync(join(frontendRoot, deleted)), deleted).toBe(false);
    }
  });
});
