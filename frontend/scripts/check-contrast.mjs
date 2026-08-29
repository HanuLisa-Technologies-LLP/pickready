#!/usr/bin/env node
/**
 * Contrast verification for the navy/teal brand tokens and the field
 * affordance.
 *
 * The acceptance criterion is that an interactive field is distinguishable AT
 * REST, not only on focus, and the brief says to verify the computed contrast
 * rather than eyeball it. So this reads the HSL triples straight out of
 * `app/globals.css` -- the same strings the browser resolves -- converts them
 * to sRGB, and reports WCAG ratios.
 *
 * Two different thresholds apply and conflating them is the usual mistake:
 *  - WCAG 1.4.11 (non-text contrast) asks for 3:1 on the boundary of an
 *    interactive control against its adjacent surface. That is the bar for a
 *    field BORDER.
 *  - WCAG 1.4.3 asks for 4.5:1 on text. That is the bar for anything we print
 *    IN the brand colour.
 *
 * Exits non-zero if any assertion fails, so CI can gate on it.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(here, "..", "app", "globals.css"), "utf8");

/** Pull `--name: h s% l%;` out of a given block (`:root` or `.dark`). */
function tokens(blockName) {
  const start = css.indexOf(blockName);
  if (start === -1) throw new Error(`block ${blockName} not found in globals.css`);
  const open = css.indexOf("{", start);
  let depth = 0;
  let end = open;
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    if (css[i] === "}") {
      depth -= 1;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }
  const block = css.slice(open, end);
  const found = {};
  const pattern = /--([a-z0-9-]+):\s*([0-9.]+)\s+([0-9.]+)%\s+([0-9.]+)%/g;
  let match;
  while ((match = pattern.exec(block)) !== null) {
    found[match[1]] = [Number(match[2]), Number(match[3]), Number(match[4])];
  }
  return found;
}

function hslToRgb([h, s, l]) {
  const sat = s / 100;
  const lum = l / 100;
  const c = (1 - Math.abs(2 * lum - 1)) * sat;
  const hp = h / 60;
  const x = c * (1 - Math.abs((hp % 2) - 1));
  const [r, g, b] =
    hp < 1 ? [c, x, 0]
    : hp < 2 ? [x, c, 0]
    : hp < 3 ? [0, c, x]
    : hp < 4 ? [0, x, c]
    : hp < 5 ? [x, 0, c]
    : [c, 0, x];
  const m = lum - c / 2;
  return [r + m, g + m, b + m];
}

function relativeLuminance(hsl) {
  const linear = hslToRgb(hsl).map((channel) =>
    channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  );
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function ratio(a, b) {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

const NON_TEXT = 3;
const TEXT = 4.5;

const failures = [];
const rows = [];

function assertRatio(label, fg, bg, minimum) {
  const value = ratio(fg, bg);
  const ok = value >= minimum;
  rows.push({ label, ratio: value.toFixed(2), min: minimum.toFixed(1), ok });
  if (!ok) failures.push(`${label}: ${value.toFixed(2)}:1 < ${minimum}:1`);
}

for (const theme of ["  :root", "  .dark"]) {
  const t = tokens(theme);
  const name = theme.trim();
  // Fields sit on a card in every form we ship, so the card surface is the
  // adjacent colour, not the page canvas.
  const surface = t["surface"];
  const canvas = t["canvas"];

  assertRatio(`${name} field border (idle) vs surface`, t["field-border"], surface, NON_TEXT);
  assertRatio(`${name} field border (idle) vs canvas`, t["field-border"], canvas, NON_TEXT);
  assertRatio(`${name} field border (hover) vs surface`, t["field-border-hover"], surface, NON_TEXT);
  assertRatio(`${name} focus ring vs surface`, t["navy-400"], surface, NON_TEXT);

  // Ink is every word on the screen. If this fails nothing else matters.
  assertRatio(`${name} ink vs surface`, t["ink"], surface, TEXT);
  assertRatio(`${name} ink vs canvas`, t["ink"], canvas, TEXT);

  // Primary action: white on navy in light, near-black on light navy in dark.
  assertRatio(`${name} primary fg vs primary`, t["ink-inverse"] ?? [0, 0, theme === "  :root" ? 100 : 8], t["navy-600"], TEXT);

  // THE ONE THIS FILE EXISTS FOR NOW.
  //
  // The brand teal measures 4.30:1 on white -- below the 4.5:1 WCAG AA asks of
  // normal text. That is a measured property of the colour the client chose,
  // not something to design around by feel, and "teal label on a white card" is
  // the first mistake anybody reaches for.
  //
  // So teal-600 is asserted only at the NON-TEXT bar (it is a fill, a rule and
  // an icon colour), and teal-700 is asserted at the TEXT bar, because that is
  // the token DESIGN.md §2 requires for teal words. If teal-700 ever stops
  // passing, the rule in DESIGN.md is wrong and this build should stop.
  assertRatio(`${name} teal-600 as a FILL/RULE vs surface`, t["teal-600"], surface, NON_TEXT);
  assertRatio(`${name} teal-700 as TEXT vs surface`, t["teal-700"], surface, TEXT);

  // And the negative assertion, stated rather than implied: teal-600 must NOT
  // reach the text bar. If it ever did -- because somebody "fixed" the brand
  // colour -- the DESIGN.md rule sending people to teal-700 would have become
  // a lie, and a rule nobody needs is a rule people stop trusting.
  if (name === ":root") {
    const tealText = ratio(t["teal-600"], surface);
    rows.push({
      label: `${name} teal-600 is BELOW the text bar (by design)`,
      ratio: tealText.toFixed(2),
      min: `<${TEXT.toFixed(1)}`,
      ok: tealText < TEXT,
    });
    if (tealText >= TEXT) {
      failures.push(
        `teal-600 now measures ${tealText.toFixed(2)}:1 and passes AA for text. ` +
          `The brand colour has changed. Update DESIGN.md §2, which currently ` +
          `tells everyone to use teal-700 for teal text.`
      );
    }
  }
}

const width = Math.max(...rows.map((row) => row.label.length));
for (const row of rows) {
  const mark = row.ok ? "PASS" : "FAIL";
  process.stdout.write(
    `${mark}  ${row.label.padEnd(width)}  ${row.ratio.padStart(6)}:1  (min ${row.min}:1)\n`
  );
}

if (failures.length > 0) {
  process.stdout.write(`\n${failures.length} contrast assertion(s) failed:\n`);
  for (const failure of failures) process.stdout.write(`  - ${failure}\n`);
  process.exit(1);
}
process.stdout.write(`\nAll ${rows.length} contrast assertions pass.\n`);
