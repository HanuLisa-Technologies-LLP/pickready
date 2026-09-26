// The coding editor's colours, built from the product's own tokens.
//
// Monaco's stock themes colour keywords blue-violet and control keywords
// purple, which DESIGN.md forbids anywhere, and draw comments and line numbers
// in grey, which the product's text never is. So the theme is built at mount
// from the SAME CSS custom properties every other surface reads
// (`app/globals.css`), and rebuilt when the theme toggle swaps them, so the
// editor follows light and dark exactly like the page around it.
//
// Navy is structure and teal is evidence (DESIGN.md section 2): keywords are
// navy, and literals, the one thing in a program that states a fact rather
// than a structure, are teal. Every other token, comments included, is ink.
//
// `inherit: false` is deliberate. An inheriting theme keeps every stock rule
// this one does not override, and the stock light theme carries a purple rule
// for control-flow keywords that a narrower override would leave in place.

import type * as MonacoApi from "monaco-editor";

export const MONACO_THEME_NAME = "vivekium";

/** Every token the theme reads. `monaco-theme.test.ts` asserts each is
 *  declared in both the light and the dark block of `app/globals.css`, so a
 *  renamed token fails a test rather than the editor. */
export const THEME_TOKENS = [
  "ink",
  "surface",
  "muted",
  "border-token",
  "navy-50",
  "navy-100",
  "navy-400",
  "navy-500",
  "teal-100",
  "teal-600",
  "teal-700",
  "teal-900",
] as const;

export type ThemeToken = (typeof THEME_TOKENS)[number];

/**
 * `210 80% 38%`, the HSL triple `globals.css` stores, as `#rrggbb`.
 *
 * Throws on anything else. A token that cannot be read is a renamed or
 * deleted token, and an editor quietly drawn in some other colour would be
 * the silent fallback the product does not allow.
 */
export function hslTripletToHex(triplet: string): string {
  const match = /^\s*(-?[\d.]+)(?:deg)?\s+([\d.]+)%\s+([\d.]+)%\s*$/.exec(triplet);
  if (!match) {
    throw new Error(`Not an HSL token value: ${JSON.stringify(triplet)}`);
  }
  const hue = (((Number(match[1]) % 360) + 360) % 360) / 360;
  const saturation = Number(match[2]) / 100;
  const lightness = Number(match[3]) / 100;
  const q =
    lightness < 0.5
      ? lightness * (1 + saturation)
      : lightness + saturation - lightness * saturation;
  const p = 2 * lightness - q;
  const channel = (offset: number) => {
    let t = hue + offset;
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    let value: number;
    if (t < 1 / 6) value = p + (q - p) * 6 * t;
    else if (t < 1 / 2) value = q;
    else if (t < 2 / 3) value = p + (q - p) * (2 / 3 - t) * 6;
    else value = p;
    return Math.round(value * 255)
      .toString(16)
      .padStart(2, "0");
  };
  return `#${channel(1 / 3)}${channel(0)}${channel(-1 / 3)}`;
}

/**
 * The theme, from a token reader. `readToken("navy-400")` returns the raw
 * custom property value (`210 80% 38%`).
 */
export function buildMonacoTheme(
  readToken: (token: ThemeToken) => string,
  dark: boolean
): MonacoApi.editor.IStandaloneThemeData {
  const hex = (token: ThemeToken) => hslTripletToHex(readToken(token));
  // Token rules take a colour WITHOUT the leading #.
  const bare = (token: ThemeToken) => hex(token).slice(1);
  const ink = bare("ink");

  return {
    base: dark ? "vs-dark" : "vs",
    inherit: false,
    rules: [
      { token: "", foreground: ink },
      { token: "keyword", foreground: bare("navy-400"), fontStyle: "bold" },
      { token: "string", foreground: bare("teal-700") },
      { token: "regexp", foreground: bare("teal-700") },
      { token: "number", foreground: bare("navy-500") },
      { token: "constant", foreground: bare("navy-500") },
      // Comments in ink, italic. Never dimmed: text is never grey.
      { token: "comment", foreground: ink, fontStyle: "italic" },
      { token: "type", foreground: bare("teal-900") },
      { token: "invalid", foreground: ink, fontStyle: "underline" },
    ],
    colors: {
      "editor.background": hex("surface"),
      "editor.foreground": hex("ink"),
      "editorLineNumber.foreground": hex("ink"),
      "editorLineNumber.activeForeground": hex("ink"),
      "editorGutter.background": hex("muted"),
      "editor.lineHighlightBackground": hex("navy-50"),
      "editor.lineHighlightBorder": hex("navy-50"),
      "editor.selectionBackground": hex("navy-100"),
      "editor.inactiveSelectionBackground": hex("navy-100"),
      "editorCursor.foreground": hex("ink"),
      "editorBracketMatch.background": hex("teal-100"),
      "editorBracketMatch.border": hex("teal-600"),
      "editorWidget.background": hex("surface"),
      "editorWidget.foreground": hex("ink"),
      "editorWidget.border": hex("border-token"),
      "editorIndentGuide.background1": hex("border-token"),
      "editorWhitespace.foreground": hex("border-token"),
      focusBorder: hex("navy-400"),
    },
  };
}

/** Read a token off the document root, where `globals.css` declares both
 *  themes (`:root` and `.dark`). */
function documentToken(token: ThemeToken): string {
  return getComputedStyle(document.documentElement).getPropertyValue(`--${token}`);
}

function isDark(): boolean {
  return document.documentElement.classList.contains("dark");
}

/** Define the theme from the page's current tokens and make it current. */
export function applyMonacoTheme(monaco: typeof MonacoApi): void {
  monaco.editor.defineTheme(MONACO_THEME_NAME, buildMonacoTheme(documentToken, isDark()));
  monaco.editor.setTheme(MONACO_THEME_NAME);
}

let watchers = 0;
let observer: MutationObserver | null = null;

/**
 * Rebuild the theme whenever the page's theme changes, for as long as at
 * least one editor is mounted. Monaco's theme is global to the page, so one
 * observer serves every editor. Returns the release function.
 */
export function watchPageTheme(monaco: typeof MonacoApi): () => void {
  watchers += 1;
  if (observer === null) {
    observer = new MutationObserver(() => applyMonacoTheme(monaco));
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
  }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    watchers -= 1;
    if (watchers === 0 && observer !== null) {
      observer.disconnect();
      observer = null;
    }
  };
}
