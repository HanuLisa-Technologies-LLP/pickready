// The coding editor's theme is built from the product's tokens: navy is
// structure, teal is evidence, text is ink, and nothing is purple or grey.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath, URL } from "node:url";

import { describe, expect, it } from "vitest";

import { buildMonacoTheme, hslTripletToHex, THEME_TOKENS, type ThemeToken } from "./monaco-theme";

const FRONTEND = fileURLToPath(new URL("../..", import.meta.url));
const GLOBALS = readFileSync(join(FRONTEND, "app", "globals.css"), "utf8");

/** The custom properties declared inside the first block opened by `selector`. */
function block(selector: string): Record<string, string> {
  const start = GLOBALS.indexOf(`${selector} {`);
  if (start === -1) throw new Error(`${selector} is not in globals.css`);
  const end = GLOBALS.indexOf("\n  }", start);
  const tokens: Record<string, string> = {};
  for (const match of GLOBALS.slice(start, end).matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) {
    tokens[match[1]] = match[2].trim();
  }
  return tokens;
}

const LIGHT = block(":root");
const DARK = block(".dark");

/** A theme built the way the editor builds it, against one CSS block. */
function themeFor(tokens: Record<string, string>, dark: boolean) {
  return buildMonacoTheme((token: ThemeToken) => {
    // `.dark` redefines most tokens; anything it leaves alone is inherited.
    const value = tokens[token] ?? LIGHT[token];
    if (value === undefined) throw new Error(`--${token} is not declared`);
    return value;
  }, dark);
}

/** Hue in degrees and saturation, from `#rrggbb`. */
function hueAndSaturation(hex: string): { hue: number; saturation: number } {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const delta = max - min;
  const lightness = (max + min) / 2;
  const saturation = delta === 0 ? 0 : delta / (1 - Math.abs(2 * lightness - 1));
  let hue = 0;
  if (delta !== 0) {
    if (max === r) hue = 60 * (((g - b) / delta) % 6);
    else if (max === g) hue = 60 * ((b - r) / delta + 2);
    else hue = 60 * ((r - g) / delta + 4);
  }
  return { hue: (hue + 360) % 360, saturation };
}

describe("the tokens the theme reads", () => {
  it("are declared in the light theme, and redefined or inherited in the dark one", () => {
    for (const token of THEME_TOKENS) {
      expect(LIGHT[token], `--${token} in :root`).toBeDefined();
    }
    // The four the dark theme must redefine, or the editor would be drawn
    // dark-on-dark: the text, the background and the two text accents.
    for (const token of ["ink", "surface", "navy-400", "teal-700"] as const) {
      expect(DARK[token], `--${token} in .dark`).toBeDefined();
      expect(DARK[token]).not.toBe(LIGHT[token]);
    }
  });
});

describe("the conversion", () => {
  it("turns the stored HSL triple into the colour globals.css documents", () => {
    // Worked by hand: hsl(210, 80%, 38%) is rgb(19, 97, 174) and
    // hsl(175, 84%, 23%) is rgb(9, 108, 100). The hex comments beside the
    // tokens in globals.css are rounded approximations and are not the source.
    expect(hslTripletToHex("210 80% 38%").toUpperCase()).toBe("#1361AE");
    expect(hslTripletToHex("175 84% 23%").toUpperCase()).toBe("#096C64");
    expect(hslTripletToHex("0 0% 100%")).toBe("#ffffff");
    expect(hslTripletToHex("212 55% 7%")).toBe("#08111c");
  });

  it("refuses a token it cannot read rather than drawing some other colour", () => {
    expect(() => hslTripletToHex("")).toThrow(/Not an HSL token value/);
    expect(() => hslTripletToHex("var(--navy-400)")).toThrow(/Not an HSL token value/);
  });
});

describe.each([
  ["light", LIGHT, false],
  ["dark", DARK, true],
] as const)("the %s theme", (_name, tokens, dark) => {
  const theme = themeFor(tokens, dark);
  const ink = hslTripletToHex(tokens.ink ?? LIGHT.ink).slice(1);

  it("inherits nothing from the stock theme, whose control keywords are purple", () => {
    expect(theme.inherit).toBe(false);
    expect(theme.base).toBe(dark ? "vs-dark" : "vs");
  });

  it("is never purple", () => {
    const colours = [
      ...theme.rules.map((rule) => `#${rule.foreground}`),
      ...Object.values(theme.colors),
    ];
    for (const colour of colours) {
      const { hue, saturation } = hueAndSaturation(colour);
      if (saturation < 0.15) continue;
      expect(hue < 250 || hue > 330, `${colour} has hue ${hue}`).toBe(true);
    }
  });

  it("draws text, comments and line numbers in ink, never grey", () => {
    const byToken = Object.fromEntries(theme.rules.map((rule) => [rule.token, rule.foreground]));
    expect(byToken[""]).toBe(ink);
    expect(byToken.comment).toBe(ink);
    expect(theme.colors["editor.foreground"]).toBe(`#${ink}`);
    expect(theme.colors["editorLineNumber.foreground"]).toBe(`#${ink}`);
  });

  it("colours keywords navy and literals teal", () => {
    const byToken = Object.fromEntries(theme.rules.map((rule) => [rule.token, rule.foreground]));
    const navy = hueAndSaturation(`#${byToken.keyword}`).hue;
    const teal = hueAndSaturation(`#${byToken.string}`).hue;
    expect(navy).toBeGreaterThan(200);
    expect(navy).toBeLessThan(225);
    expect(teal).toBeGreaterThan(165);
    expect(teal).toBeLessThan(185);
  });
});
