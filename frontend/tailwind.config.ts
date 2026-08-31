import type { Config } from "tailwindcss";

/**
 * ReadyPick Tailwind theme.
 *
 * Every colour here reads a CSS variable defined in `app/globals.css`, so the
 * light/dark toggle stays a variable swap and no component branches on theme.
 * The `<alpha-value>` placeholder lets utilities compose opacity, e.g.
 * `bg-navy-600/10` or `border-border/60`.
 *
 * TWO NAMES FOR ONE RAMP, ON PURPOSE. `navy-*` is the real scale; `brand-*` is
 * an ALIAS onto it, kept because 193 existing call sites say `bg-brand-600`
 * and rewriting all of them in the same change that recolours the palette
 * would mean one diff doing two jobs -- and a regression in either would be
 * indistinguishable from a regression in the other. New work uses `navy-*` and
 * `teal-*`, which say what they mean.
 *
 * NAVY IS STRUCTURE, TEAL IS EVIDENCE. See DESIGN.md §2. Teal is the one
 * colour in this system with a meaning; spending it on a primary button would
 * waste it on the element that needs none.
 *
 * Token map for other agents:
 *   bg-canvas / bg-surface       page background / card surface
 *   text-ink                     the only text colour (black light, white dark)
 *   border-border                borders and dividers
 *   bg-navy-600, ring-navy-400   primary action, active nav, focus
 *   bg-navy-50                   subtle fill, selected row
 *   bg-teal-50, border-teal-600  evidence: corroborated, cited
 *   text-teal-700                teal TEXT on white -- NEVER text-teal-600,
 *                                which measures 4.30:1 and fails AA
 *   text-rating-1 / bg-rating-1-bg .. 5   the word-label rating chips
 */
const config: Config = {
  darkMode: ["class"],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    container: {
      center: true,
      padding: { DEFAULT: "1.5rem", lg: "2.5rem" },
      screens: { "2xl": "1280px" },
    },
    extend: {
      colors: {
        // --- brand: NAVY (structure) --------------------------------------
        navy: {
          50: "hsl(var(--navy-50) / <alpha-value>)",
          100: "hsl(var(--navy-100) / <alpha-value>)",
          200: "hsl(var(--navy-200) / <alpha-value>)",
          400: "hsl(var(--navy-400) / <alpha-value>)",
          500: "hsl(var(--navy-500) / <alpha-value>)",
          600: "hsl(var(--navy-600) / <alpha-value>)",
          700: "hsl(var(--navy-700) / <alpha-value>)",
          900: "hsl(var(--navy-900) / <alpha-value>)",
          DEFAULT: "hsl(var(--navy-600) / <alpha-value>)",
        },

        // --- brand: TEAL (evidence) ---------------------------------------
        // `text-teal-600` is a mistake waiting to be made: the brand teal
        // measures 4.30:1 on white and fails AA for body text. Use
        // `text-teal-700`. `scripts/check-contrast.mjs` fails the build on it.
        teal: {
          50: "hsl(var(--teal-50) / <alpha-value>)",
          100: "hsl(var(--teal-100) / <alpha-value>)",
          400: "hsl(var(--teal-400) / <alpha-value>)",
          500: "hsl(var(--teal-500) / <alpha-value>)",
          600: "hsl(var(--teal-600) / <alpha-value>)",
          700: "hsl(var(--teal-700) / <alpha-value>)",
          900: "hsl(var(--teal-900) / <alpha-value>)",
          DEFAULT: "hsl(var(--teal-600) / <alpha-value>)",
        },

        // --- brand-* : ALIAS onto navy, for the 193 existing call sites ----
        // Kept so the recolour is one diff rather than two. The 100/500/600/700
        // steps map onto the navy steps that carry the same ROLE, not onto the
        // same numbers: the old brand-100 was a subtle fill, which is navy-50
        // here, and the old brand-500 was an accent, which is navy-400.
        brand: {
          100: "hsl(var(--navy-50) / <alpha-value>)",
          500: "hsl(var(--navy-400) / <alpha-value>)",
          600: "hsl(var(--navy-600) / <alpha-value>)",
          700: "hsl(var(--navy-700) / <alpha-value>)",
          DEFAULT: "hsl(var(--navy-600) / <alpha-value>)",
        },

        // --- held for review ----------------------------------------------
        // Not `destructive`. A flag is not a rejection, and colouring it like
        // one would make the platform look as though it had decided.
        warning: {
          DEFAULT: "hsl(var(--warning) / <alpha-value>)",
          foreground: "hsl(var(--warning-foreground) / <alpha-value>)",
        },

        // --- semantic surfaces -------------------------------------------
        ink: "hsl(var(--ink) / <alpha-value>)",
        canvas: "hsl(var(--canvas) / <alpha-value>)",
        surface: "hsl(var(--surface) / <alpha-value>)",

        // --- interactive field affordance ---------------------------------
        // `border-field` is the idle boundary of anything you can click or
        // type into; `border-field-hover` is the saturation step-up. `input`
        // already resolves to the idle one, so a primitive that reads
        // `border-input` gets it without any call-site change.
        field: {
          DEFAULT: "hsl(var(--field-border) / <alpha-value>)",
          hover: "hsl(var(--field-border-hover) / <alpha-value>)",
        },

        // --- shadcn aliases (kept so existing primitives keep working) ----
        border: "hsl(var(--border) / <alpha-value>)",
        input: "hsl(var(--input) / <alpha-value>)",
        ring: "hsl(var(--ring) / <alpha-value>)",
        background: "hsl(var(--background) / <alpha-value>)",
        foreground: "hsl(var(--foreground) / <alpha-value>)",
        primary: {
          DEFAULT: "hsl(var(--primary) / <alpha-value>)",
          foreground: "hsl(var(--primary-foreground) / <alpha-value>)",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary) / <alpha-value>)",
          foreground: "hsl(var(--secondary-foreground) / <alpha-value>)",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive) / <alpha-value>)",
          foreground: "hsl(var(--destructive-foreground) / <alpha-value>)",
        },
        muted: {
          DEFAULT: "hsl(var(--muted) / <alpha-value>)",
          foreground: "hsl(var(--muted-foreground) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "hsl(var(--accent) / <alpha-value>)",
          foreground: "hsl(var(--accent-foreground) / <alpha-value>)",
        },
        popover: {
          DEFAULT: "hsl(var(--popover) / <alpha-value>)",
          foreground: "hsl(var(--popover-foreground) / <alpha-value>)",
        },
        card: {
          DEFAULT: "hsl(var(--card) / <alpha-value>)",
          foreground: "hsl(var(--card-foreground) / <alpha-value>)",
        },

        // --- rating ramp: five word labels, one shared ramp ---------------
        rating: {
          "1": "hsl(var(--rating-1-fg) / <alpha-value>)",
          "1-bg": "hsl(var(--rating-1-bg) / <alpha-value>)",
          "2": "hsl(var(--rating-2-fg) / <alpha-value>)",
          "2-bg": "hsl(var(--rating-2-bg) / <alpha-value>)",
          "3": "hsl(var(--rating-3-fg) / <alpha-value>)",
          "3-bg": "hsl(var(--rating-3-bg) / <alpha-value>)",
          "4": "hsl(var(--rating-4-fg) / <alpha-value>)",
          "4-bg": "hsl(var(--rating-4-bg) / <alpha-value>)",
          "5": "hsl(var(--rating-5-fg) / <alpha-value>)",
          "5-bg": "hsl(var(--rating-5-bg) / <alpha-value>)",
        },
      },
      // ZERO CORNER RADIUS EVERYWHERE (Master directive Part 1 §7): every
      // container, card, button and badge is a sharp rectangle. The whole
      // scale is pinned to 0 so a `rounded-2xl` left in a call site renders
      // square rather than reintroducing soft geometry. `rounded-full` keeps
      // its default and is reserved for genuinely circular marks (avatars,
      // typing dots) — never for pill containers.
      borderRadius: {
        sm: "0px",
        DEFAULT: "0px",
        md: "0px",
        lg: "0px",
        xl: "0px",
        "2xl": "0px",
        "3xl": "0px",
      },
      fontSize: {
        // The scale from the brief: 12 / 13 / 15 / 18 / 24 / 32 / 48 / 64.
        "2xs": ["0.75rem", { lineHeight: "1rem" }],
        xs: ["0.8125rem", { lineHeight: "1.125rem" }],
        sm: ["0.9375rem", { lineHeight: "1.5rem" }],
        base: ["1rem", { lineHeight: "1.625rem" }],
        lg: ["1.125rem", { lineHeight: "1.75rem" }],
        xl: ["1.5rem", { lineHeight: "2rem" }],
        "2xl": ["2rem", { lineHeight: "2.375rem" }],
        "3xl": ["2.5rem", { lineHeight: "2.875rem" }],
        "4xl": ["3rem", { lineHeight: "3.25rem" }],
        "5xl": ["4rem", { lineHeight: "4.25rem" }],
      },
      boxShadow: {
        // VERY MINIMAL shadows (Master directive Part 1 §7): structure comes
        // from borders, not elevation. `pop` and `brand` are kept as names so
        // existing call sites keep compiling, but both now resolve to the
        // same restrained values — no deep drops, no coloured glow.
        card: "0 1px 2px hsl(var(--ink) / 0.04), 0 1px 3px hsl(var(--ink) / 0.03)",
        "card-hover":
          "0 2px 6px hsl(var(--ink) / 0.06), 0 1px 3px hsl(var(--ink) / 0.04)",
        pop: "0 2px 6px hsl(var(--ink) / 0.06), 0 1px 3px hsl(var(--ink) / 0.04)",
        brand: "0 1px 2px hsl(var(--ink) / 0.06)",
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
        // --- Magic UI keyframes, expressed for Tailwind v3 ----------------
        marquee: {
          from: { transform: "translateX(0)" },
          to: { transform: "translateX(calc(-100% - var(--gap)))" },
        },
        "marquee-vertical": {
          from: { transform: "translateY(0)" },
          to: { transform: "translateY(calc(-100% - var(--gap)))" },
        },
        "aurora-drift": {
          "0%, 100%": { transform: "translate3d(0,0,0) scale(1)" },
          "50%": { transform: "translate3d(4%, -3%, 0) scale(1.08)" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        marquee: "marquee var(--duration) infinite linear",
        "marquee-vertical": "marquee-vertical var(--duration) linear infinite",
        "aurora-drift": "aurora-drift 18s ease-in-out infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
