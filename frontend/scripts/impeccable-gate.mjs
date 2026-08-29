#!/usr/bin/env node
/**
 * CI gate over `npx impeccable detect --json .`
 *
 * spec-doc5 §C acceptance: "`npx impeccable detect --json .` runs clean (or
 * documented, deliberate, reasoned exceptions only) in CI."
 *
 * WHY A WRAPPER RATHER THAN THE RAW COMMAND
 * ------------------------------------------
 * `detect` reports findings and exits 0 either way, which makes it a thing that
 * prints warnings CI is free to ignore -- and warnings CI ignores are warnings
 * everybody ignores. This exits NON-ZERO on any finding that is not listed in
 * `.impeccable-exceptions.md`, which turns the detector into a gate.
 *
 * The exception list is a MARKDOWN TABLE rather than a JSON config, and that is
 * deliberate: the file's second column is a REASON, in prose, and a reason is
 * the whole thing that makes an exception legitimate. A JSON array of rule ids
 * would let somebody silence a detector without writing down why, which is
 * exactly the outcome this is written against.
 *
 *   node scripts/impeccable-gate.mjs            # runs detect itself
 *   node scripts/impeccable-gate.mjs findings.json   # gates a saved run
 */
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
const exceptionsPath = join(repoRoot, ".impeccable-exceptions.md");

/**
 * Parse the exception table.
 *
 * Reads ONLY the first table -- the one under "| Antipattern | File | Reason |".
 * The file also carries a second table of findings that were FIXED, and
 * treating those as exceptions would silently re-permit everything the team
 * decided to correct.
 */
function loadExceptions() {
  if (!existsSync(exceptionsPath)) return [];
  const text = readFileSync(exceptionsPath, "utf8");
  const marker = text.indexOf("| Antipattern | File | Reason |");
  if (marker === -1) return [];
  const stop = text.indexOf("## Findings that were FIXED", marker);
  const block = text.slice(marker, stop === -1 ? undefined : stop);
  const rows = [];
  for (const line of block.split("\n")) {
    const cells = line.split("|").map((cell) => cell.trim());
    // | antipattern | file | reason |  -> ["", a, f, r, ""]
    if (cells.length < 5) continue;
    const [, antipattern, file, reason] = cells;
    if (!antipattern || antipattern === "Antipattern" || antipattern.startsWith("-")) continue;
    const cleaned = {
      antipattern: antipattern.replace(/`/g, ""),
      file: file.replace(/`/g, ""),
      reason,
    };
    // An exception with no reason is not an exception, it is a silenced
    // detector. Refused here rather than accepted quietly.
    if (!cleaned.reason || cleaned.reason.length < 40) {
      process.stdout.write(
        `\nRefusing the exception for ${cleaned.antipattern} in ${cleaned.file}: ` +
          `its reason is missing or too short to be one.\n`
      );
      process.exit(2);
    }
    rows.push(cleaned);
  }
  return rows;
}

function loadFindings() {
  const saved = process.argv[2];
  if (saved) return JSON.parse(readFileSync(saved, "utf8"));
  // `shell: true` on Windows because Node 18+ refuses to spawn a `.cmd`
  // directly (EINVAL), and `npx` IS a `.cmd` there. The arguments are all
  // literals in this file -- nothing user-supplied reaches the shell.
  const isWindows = process.platform === "win32";
  let raw;
  try {
    raw = execFileSync(
      isWindows ? "npx.cmd" : "npx",
      ["--yes", "impeccable@latest", "detect", "--json", "."],
      {
        cwd: repoRoot,
        encoding: "utf8",
        maxBuffer: 32 * 1024 * 1024,
        shell: isWindows,
      }
    );
  } catch (error) {
    // `detect` EXITS NON-ZERO WHEN IT FINDS ANYTHING, which is a reasonable
    // thing for it to do and the reason this cannot be a bare execFileSync:
    // the findings we came for are on stdout of the "failed" call. A genuine
    // failure -- the binary missing, the network down -- produces no stdout,
    // and that is what is re-thrown.
    if (!error.stdout) throw error;
    raw = error.stdout;
  }
  const start = raw.indexOf("[");
  if (start === -1) return [];
  return JSON.parse(raw.slice(start));
}

const exceptions = loadExceptions();
const findings = loadFindings();

function isExcepted(finding) {
  const rel = relative(repoRoot, finding.file).split(sep).join("/");
  return exceptions.some(
    (row) => row.antipattern === finding.antipattern && rel.endsWith(row.file)
  );
}

const unexcused = findings.filter((finding) => !isExcepted(finding));
const excused = findings.length - unexcused.length;

process.stdout.write(
  `impeccable: ${findings.length} finding(s), ${excused} documented exception(s), ` +
    `${unexcused.length} to answer for.\n`
);

// An exception that no longer matches anything is worse than useless: it is a
// standing permission for a pattern nobody is using, and the next person to
// introduce that pattern inherits it silently. Reported, not fatal -- a stale
// row is a tidy-up, not a broken build.
for (const row of exceptions) {
  const stillFiring = findings.some(
    (finding) =>
      finding.antipattern === row.antipattern &&
      relative(repoRoot, finding.file).split(sep).join("/").endsWith(row.file)
  );
  if (!stillFiring) {
    process.stdout.write(
      `  STALE exception: ${row.antipattern} in ${row.file} no longer fires. ` +
        `Remove the row so it cannot silently permit a future one.\n`
    );
  }
}

if (unexcused.length === 0) {
  process.stdout.write("Clean.\n");
  process.exit(0);
}

for (const finding of unexcused) {
  const rel = relative(repoRoot, finding.file).split(sep).join("/");
  process.stdout.write(
    `\n  ${finding.severity.toUpperCase()}  ${finding.antipattern}\n` +
      `    ${rel}:${finding.line ?? "?"}\n` +
      `    ${finding.snippet ?? ""}\n` +
      `    ${finding.description ?? ""}\n`
  );
}
process.stdout.write(
  `\nFix these, or add a row to .impeccable-exceptions.md with a reason naming ` +
    `what would be LOST by fixing it.\n`
);
process.exit(1);
