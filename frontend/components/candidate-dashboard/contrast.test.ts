/**
 * Contrast assertions for the dashboard's band colours, in BOTH themes.
 *
 * WHY THIS EXISTS SEPARATELY FROM `scripts/check-contrast.mjs`
 * -------------------------------------------------------------
 * That script asserts the BRAND tokens: that navy is readable, that teal-700
 * is the text teal and that teal-600 is still below the bar. This asserts the
 * pairs THIS surface composes, which is a different question: a token can be
 * fine on white and fail against the background it is actually painted on.
 *
 * spec-doc6 §8.3: "Accessibility is a gate, not a polish item... contrast
 * assertions passing in both themes." Both themes is the operative half. The
 * rating ramp INVERTS between them (the light theme's `-fg` is a dark ink and
 * the dark theme's is a pale tint), so a pair checked in one theme tells you
 * nothing about the other, and the failure mode is a pale green pill on a pale
 * green background that nobody notices until somebody with the dark theme on
 * tries to read a score.
 *
 * The triples are read out of `app/globals.css`, the same strings the browser
 * resolves, so this cannot pass against a copy that has drifted.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(here, "..", "..", "app", "globals.css"), "utf8");

/** Pull `--name: h s% l%;` out of one block of the stylesheet. */
function tokens(blockName: string): Record<string, [number, number, number]> {
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
  const found: Record<string, [number, number, number]> = {};
  const pattern = /--([a-z0-9-]+):\s*([0-9.]+)\s+([0-9.]+)%\s+([0-9.]+)%/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(block)) !== null) {
    found[match[1]] = [Number(match[2]), Number(match[3]), Number(match[4])];
  }
  return found;
}

function hslToRgb([h, s, l]: [number, number, number]): [number, number, number] {
  const sat = s / 100;
  const lum = l / 100;
  const c = (1 - Math.abs(2 * lum - 1)) * sat;
  const hp = h / 60;
  const x = c * (1 - Math.abs((hp % 2) - 1));
  const [r1, g1, b1] =
    hp < 1
      ? [c, x, 0]
      : hp < 2
        ? [x, c, 0]
        : hp < 3
          ? [0, c, x]
          : hp < 4
            ? [0, x, c]
            : hp < 5
              ? [x, 0, c]
              : [c, 0, x];
  const m = lum - c / 2;
  return [r1 + m, g1 + m, b1 + m];
}

function luminance(rgb: [number, number, number]): number {
  const [r, g, b] = rgb.map((channel) =>
    channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(
  a: [number, number, number],
  b: [number, number, number]
): number {
  const la = luminance(hslToRgb(a));
  const lb = luminance(hslToRgb(b));
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

const LIGHT = tokens(":root");
const DARK = tokens(".dark");

/** Dark redefines only what changes, so an absent token falls through. */
function darkToken(name: string): [number, number, number] {
  return DARK[name] ?? LIGHT[name];
}

//: The pairs column 4 actually paints. `bg-rating-N-bg` with `text-rating-N`.
const BAND_PAIRS: Array<[string, string, string]> = [
  ["Ready to Pick, Strong", "rating-1", "rating-1-bg"],
  ["Ready to Pick", "rating-2", "rating-2-bg"],
  ["Consider with Reservations", "rating-3", "rating-3-bg"],
  ["Not Recommended", "rating-5", "rating-5-bg"],
];

//: WCAG 1.4.3. The band label is 11px bold, which is not "large text" under
//: 1.4.3's definition (18.66px bold / 24px regular), so the full 4.5 applies.
const TEXT_MINIMUM = 4.5;

describe("the Ready Pick Score band, light theme", () => {
  it.each(BAND_PAIRS)("%s is readable", (_label, fg, bg) => {
    expect(ratio(LIGHT[`${fg}-fg`], LIGHT[bg])).toBeGreaterThanOrEqual(
      TEXT_MINIMUM
    );
  });

  it("Under Review is readable and is not the Not Recommended red", () => {
    expect(
      ratio(LIGHT["warning-foreground"] ?? [0, 0, 100], LIGHT.warning)
    ).toBeGreaterThanOrEqual(TEXT_MINIMUM);
    expect(LIGHT.warning).not.toEqual(LIGHT["rating-5-fg"]);
  });

  it("the pending band paints ink on the muted surface, never grey on grey", () => {
    // Text is never grey in this product, enforced at the token: `--ink` is
    // what `--muted-foreground` resolves to. This asserts the CONSEQUENCE.
    expect(ratio(LIGHT.ink, LIGHT.muted)).toBeGreaterThanOrEqual(TEXT_MINIMUM);
  });
});

describe("the Ready Pick Score band, dark theme", () => {
  it.each(BAND_PAIRS)("%s is readable", (_label, fg, bg) => {
    expect(
      ratio(darkToken(`${fg}-fg`), darkToken(bg))
    ).toBeGreaterThanOrEqual(TEXT_MINIMUM);
  });

  it("Under Review is readable", () => {
    expect(
      ratio(darkToken("warning-foreground"), darkToken("warning"))
    ).toBeGreaterThanOrEqual(TEXT_MINIMUM);
  });

  it("the pending band paints ink on the muted surface", () => {
    expect(ratio(darkToken("ink"), darkToken("muted"))).toBeGreaterThanOrEqual(
      TEXT_MINIMUM
    );
  });

  it("the ramp actually inverts, which is why both themes are checked", () => {
    // If this ever stopped being true, the dark-theme assertions above would
    // be checking the light theme's numbers and reporting a pass for a pair
    // nobody had looked at.
    expect(darkToken("rating-1-fg")).not.toEqual(LIGHT["rating-1-fg"]);
  });
});

describe("the Pre-Screen Grade cell", () => {
  it("is readable as plain ink on the page, which is all it ever is", () => {
    expect(ratio(LIGHT.ink, LIGHT.canvas)).toBeGreaterThanOrEqual(TEXT_MINIMUM);
    expect(ratio(darkToken("ink"), darkToken("canvas"))).toBeGreaterThanOrEqual(
      TEXT_MINIMUM
    );
  });
});
