import Link from "next/link";

import { cn } from "@/lib/utils";

/** Vivekium's standalone product mark and wordmark, rendered from native UI. */
const ALT = "Vivekium";

export interface LogoProps {
  variant?: "full" | "mark";
  height?: number;
  href?: string;
  /**
   * Retained so the three call sites that pass it still compile, and it is
   * now a NO-OP: the mark is inline SVG, so there is no image request left to
   * prioritise. Kept rather than removed because a prop that silently stopped
   * existing would be a build break in someone else's branch.
   */
  priority?: boolean;
  className?: string;
}

export function Logo({
  variant = "full",
  height = 32,
  href,
  className,
}: LogoProps) {
  const mark = (
    // A sharp square tile (directive Part 1 section 7: zero corner radius,
    // brand mark included).
    //
    // THE MARK IS A V, AND IT USED TO BE AN R+P. The previous asset was the
    // old logo cropped tight, so it rendered the PREVIOUS PRODUCT'S INITIALS
    // beside the word Vivekium on every page: a leftover of the old name
    // rather than a design choice. Rename change 01 covers all screens, and
    // glyphs that spell ReadyPick are an instance of it.
    //
    // It is drawn here rather than shipped as an asset, which is what the
    // docstring above already claims the brand is ("rendered from native
    // UI"): it stays sharp at every height, needs no network fetch, and
    // carries the SAME navy-to-teal transition the wordmark makes across
    // "Vivek" and "ium". Navy is structure and teal is evidence (DESIGN.md),
    // and `teal-600` is correct here because this is a FILL and not text.
    //
    // GEOMETRY, NOT IDENTITY. This is a plain monogram standing in until the
    // owner commissions a real mark; replacing it is this one element.
    <span
      aria-hidden="true"
      className="relative block aspect-square shrink-0 overflow-hidden rounded-none bg-white ring-1 ring-black/5"
      style={{ height, width: height }}
    >
      <svg
        viewBox="0 0 32 32"
        className="absolute inset-0 h-full w-full"
        role="presentation"
      >
        <path
          d="M7 7.5 L16 25"
          stroke="#012654"
          strokeWidth="5"
          strokeLinecap="square"
          fill="none"
        />
        <path
          d="M25 7.5 L16 25"
          stroke="#00888A"
          strokeWidth="5"
          strokeLinecap="square"
          fill="none"
        />
      </svg>
    </span>
  );
  const content = (
    <span
      className={cn("inline-flex shrink-0 items-center gap-2.5", className)}
      style={{ height }}
    >
      {mark}
      {variant === "full" ? (
        <span
          className="font-black tracking-[-0.045em] text-foreground"
          style={{ fontSize: Math.max(18, Math.round(height * 0.72)) }}
        >
          {/* The wordmark keeps the two-tone treatment: the tail carries the
              TEAL the way "Pick" did before the Vivekium rename. `teal-700`
              rather than `teal-600` because this is TEXT and the brand teal
              measures 4.30:1 -- below AA. See DESIGN.md §2.

              NO DARK OVERRIDE. The token itself inverts in `.dark`, so
              `teal-700` is already the dark-theme teal text value, and
              `scripts/check-contrast.mjs` asserts teal-700 at the 4.5:1 TEXT
              bar in BOTH themes while asserting teal-600 only at the 3:1
              non-text bar. The dark-mode override this replaces was the one
              place in the product that printed words in the fill token. */}
          Vivek<span className="text-teal-700">ium</span>
        </span>
      ) : null}
    </span>
  );

  if (!href) return content;
  return (
    <Link
      href={href}
      aria-label={ALT}
      className="inline-flex shrink-0 items-center rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
    >
      {content}
    </Link>
  );
}
