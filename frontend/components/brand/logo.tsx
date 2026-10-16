import Link from "next/link";

import { cn } from "@/lib/utils";

/** Vivekium's standalone product mark and wordmark, rendered as vector UI. */
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
    // Four directional chevrons surround an evidence point. The supplied
    // mark used purple for the centre; Vivekium's product palette assigns the
    // point to teal so the mark contains no purple in either theme.
    <span
      aria-hidden="true"
      className="relative block aspect-square shrink-0 text-ink"
      style={{ height, width: height }}
    >
      <svg
        viewBox="0 0 48 48"
        className="absolute inset-0 h-full w-full"
        role="presentation"
      >
        <path
          d="M18 14 L24 4 L30 14 M34 18 L44 24 L34 30 M30 34 L24 44 L18 34 M14 30 L4 24 L14 18"
          stroke="currentColor"
          strokeWidth="4"
          strokeLinecap="butt"
          strokeLinejoin="miter"
          fill="none"
        />
        <circle cx="24" cy="24" r="6" className="fill-teal-600" />
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
          className="font-display font-normal tracking-[-0.03em] text-ink"
          style={{ fontSize: Math.max(20, Math.round(height * 0.76)) }}
        >
          Vivekium
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
