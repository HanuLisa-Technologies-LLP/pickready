import Link from "next/link";
import Image from "next/image";

import { cn } from "@/lib/utils";

/** PickReady's standalone product mark and wordmark, rendered from native UI. */
const ALT = "PickReady";

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
    <span
      aria-hidden="true"
      className="relative block aspect-square shrink-0 overflow-hidden rounded-[20%] bg-white shadow-sm ring-1 ring-black/5"
      style={{ height, width: height }}
    >
      <Image
        src="/icon.png"
        alt=""
        fill
        priority={priority}
        sizes={`${height}px`}
        className="scale-[1.45] object-cover"
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
          Pick<span className="text-violet-600 dark:text-violet-400">Ready</span>
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
