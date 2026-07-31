import Image from "next/image";
import Link from "next/link";

import { cn } from "@/lib/utils";

/**
 * The Readypick.ai brand mark.
 *
 * Two variants exist:
 *
 *   `full` the horizontal lockup (mark plus wordmark). Use it in the public
 *          site header, the sign-in card and the footer.
 *   `mark` the square R mark on its own. Use it where width is tight: a
 *          collapsed sidebar, a mobile app bar, an avatar-sized slot.
 *
 * The full lockup is a static asset (`lockup-light.png` / `lockup-dark.png`,
 * transparent, theme-matched wordmark colour) so both themes stay legible;
 * the two are stacked and toggled with `dark:` visibility rather than
 * recoloured at runtime.
 *
 * ```tsx
 * <Logo variant="full" height={32} href="/" />
 * <Logo variant="mark" height={28} priority />
 * ```
 */

/** Intrinsic pixel dimensions of the shipped assets. */
const MARK = { width: 512, height: 512 } as const;
const LOCKUP = { width: 1774, height: 887 } as const;

const ALT = "Readypick.ai";

export interface LogoProps {
  /** `full` is the wordmark lockup, `mark` the square R. Defaults to `full`. */
  variant?: "full" | "mark";
  /** Rendered height in CSS pixels. Width follows the asset aspect ratio. */
  height?: number;
  /** Wraps the logo in a link. Omit for a decorative or non-navigating logo. */
  href?: string;
  /** Set on the logo in the first viewport (the public header, the hero). */
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
  const width =
    variant === "full"
      ? Math.round((height * LOCKUP.width) / LOCKUP.height)
      : height;
  // `sizes` matches the rendered CSS width so the browser never downloads a
  // larger candidate than it will paint.
  const sizes = `${width}px`;

  const image =
    variant === "mark" ? (
      <Image
        src="/brand/readypick-mark-transparent.png"
        alt={ALT}
        width={width}
        height={height}
        sizes={sizes}
        priority={priority}
        className="h-full w-auto"
        style={{ height: "100%", width: "auto" }}
      />
    ) : (
      <>
        <Image
          src="/brand/lockup-light.png"
          alt={ALT}
          width={width}
          height={height}
          sizes={sizes}
          priority={priority}
          className="block h-full w-auto dark:hidden"
          style={{ height: "100%", width: "auto" }}
        />
        <Image
          src="/brand/lockup-dark.png"
          alt={ALT}
          width={width}
          height={height}
          sizes={sizes}
          priority={priority}
          className="hidden h-full w-auto dark:block"
          style={{ height: "100%", width: "auto" }}
        />
      </>
    );

  const content = (
    <span
      className={cn("inline-flex shrink-0 items-center", className)}
      style={{ height }}
    >
      {image}
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
