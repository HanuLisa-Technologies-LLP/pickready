import Link from "next/link";
import Image from "next/image";

import { cn } from "@/lib/utils";

/** ReadyPick's standalone product mark and wordmark, rendered from native UI. */
const ALT = "ReadyPick";

export interface LogoProps {
  variant?: "full" | "mark";
  height?: number;
  href?: string;
  priority?: boolean;
  className?: string;
}

export function Logo({
  variant = "full",
  height = 32,
  href,
  priority = false,
  className,
}: LogoProps) {
  const mark = (
    // A sharp square tile (directive Part 1 section 7: zero corner radius,
    // brand mark included). The icon asset is the navy/teal R+P mark cropped
    // tight from the source logo, so it renders contained, never zoomed.
    <span
      aria-hidden="true"
      className="relative block aspect-square shrink-0 overflow-hidden rounded-none bg-white ring-1 ring-black/5"
      style={{ height, width: height }}
    >
      <Image
        src="/brand-mark-2026.png"
        alt=""
        fill
        priority={priority}
        sizes={`${height}px`}
        className="object-contain"
      />
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
          {/* "Pick" carries the TEAL, and the split is the wordmark's own:
              navy Ready, teal Pick, exactly as the mark is drawn. `teal-700`
              rather than `teal-600` because this is TEXT on a light surface and
              the brand teal measures 4.30:1 -- below AA. See DESIGN.md §2. */}
          Ready<span className="text-teal-700 dark:text-teal-600">Pick</span>
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
