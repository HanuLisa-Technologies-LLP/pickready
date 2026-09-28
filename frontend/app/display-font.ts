import { Hubot_Sans } from "next/font/google";

/**
 * Hubot Sans, the marketing DISPLAY face (DESIGN.md section 3).
 *
 * It is in its own module on purpose. next/font preloads a face on the routes
 * whose modules import it, so binding it in the root layout would make every
 * dashboard, form and assessment download a face none of them may use. Only
 * the two public frames import this file (`landing-page.tsx` for `/` and
 * `(public)/layout.tsx` for everything else public), and each puts
 * `displayFont.variable` on its wrapper so `font-display` resolves beneath it.
 *
 * Reached only through `.type-display`: the one hero headline of a marketing
 * page. Never a dashboard heading, a table, a form, a card, a dialog or a
 * report. Outside the public frames `--font-display` is unset and
 * `font-display` falls back to Mona Sans, so a stray use degrades to the
 * working face rather than to a browser default.
 *
 * Variable, weight axis only. SIL Open Font License 1.1. Downloaded at build
 * time and served from this origin; nothing is fetched from a CDN at runtime.
 */
export const displayFont = Hubot_Sans({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-display",
});
