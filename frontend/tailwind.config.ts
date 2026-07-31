import type { Config } from "tailwindcss";

/**
 * PickReady Tailwind theme.
 *
 * Every colour here reads a CSS variable defined in `app/globals.css`, so the
 * light/dark toggle stays a variable swap and no component branches on theme.
 * The `<alpha-value>` placeholder lets utilities compose opacity, e.g.
 * `bg-brand-600/10` or `border-border/60`.
 *
 * Token map for other agents:
 *   bg-canvas / bg-surface       page background / card surface
 *   text-ink                     the only text colour (black light, white dark)
 *   border-border                borders and dividers
 *   bg-brand-600, text-brand-600, ring-brand-600   primary action, active nav, focus
 *   bg-brand-100                 subtle brand fill, selected row
 *   text-rating-1 / bg-rating-1-bg .. 5   the five word-label rating chips
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
        // --- brand -------------------------------------------------------
        brand: {
          100: "hsl(var(--brand-100) / <alpha-value>)",
          500: "hsl(var(--brand-500) / <alpha-value>)",
          600: "hsl(var(--brand-600) / <alpha-value>)",
          700: "hsl(var(--brand-700) / <alpha-value>)",
          DEFAULT: "hsl(var(--brand-600) / <alpha-value>)",
        },

        // --- semantic surfaces -------------------------------------------
        ink: "hsl(var(--ink) / <alpha-value>)",
        canvas: "hsl(var(--canvas) / <alpha-value>)",
        surface: "hsl(var(--surface) / <alpha-value>)",

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
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        xl: "calc(var(--radius) + 4px)",
        "2xl": "calc(var(--radius) + 10px)",
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
        // Soft, low-contrast elevation. Cards get `shadow-card`.
        card: "0 1px 2px hsl(var(--ink) / 0.04), 0 1px 3px hsl(var(--ink) / 0.03)",
        "card-hover":
          "0 4px 12px hsl(var(--ink) / 0.07), 0 2px 4px hsl(var(--ink) / 0.04)",
        pop: "0 12px 32px hsl(var(--ink) / 0.10), 0 2px 8px hsl(var(--ink) / 0.05)",
        brand: "0 6px 20px hsl(var(--brand-600) / 0.28)",
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
        "shimmer-slide": {
          to: { transform: "translate(calc(100cqw - 100%), 0)" },
        },
        "spin-around": {
          "0%": { transform: "translateZ(0) rotate(0)" },
          "15%, 35%": { transform: "translateZ(0) rotate(90deg)" },
          "65%, 85%": { transform: "translateZ(0) rotate(270deg)" },
          "100%": { transform: "translateZ(0) rotate(360deg)" },
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
        "shimmer-slide":
          "shimmer-slide var(--speed) ease-in-out infinite alternate",
        "spin-around": "spin-around calc(var(--speed) * 2) infinite linear",
        "aurora-drift": "aurora-drift 18s ease-in-out infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
