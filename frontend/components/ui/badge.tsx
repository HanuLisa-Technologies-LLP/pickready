import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-none border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
  {
    variants: {
      variant: {
        default:
          "border-transparent bg-brand-600 text-white hover:bg-brand-700",
        brand: "border-brand-600/25 bg-brand-100 text-accent-foreground",
        secondary:
          "border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80",
        destructive:
          "border-transparent bg-destructive text-destructive-foreground hover:bg-destructive/90",
        outline: "border-border text-foreground",
        muted: "border-transparent bg-muted text-muted-foreground",

        /* The four-grade rating ramp (spec §10.2): Highly Matching /
           Matching / Moderately Matching / Not Matching. `rating4` is retained
           as a ramp step because other surfaces (tiers, team-review chips)
           still key off five steps; no GRADE maps to it.
           A rating chip carries a WORD ONLY, never a number or a percentage. */
        rating1: "border-transparent bg-rating-1-bg text-rating-1",
        rating2: "border-transparent bg-rating-2-bg text-rating-2",
        rating3: "border-transparent bg-rating-3-bg text-rating-3",
        rating4: "border-transparent bg-rating-4-bg text-rating-4",
        rating5: "border-transparent bg-rating-5-bg text-rating-5",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
);

/** The badge variant for one of the four client-facing grades. */
export type RatingWord =
  | "Highly Matching"
  | "Matching"
  | "Moderately Matching"
  | "Not Matching";

const RATING_VARIANT: Record<RatingWord, BadgeVariant> = {
  "Highly Matching": "rating1",
  Matching: "rating2",
  "Moderately Matching": "rating3",
  "Not Matching": "rating5",
};

type BadgeVariant = NonNullable<
  NonNullable<VariantProps<typeof badgeVariants>["variant"]>
>;

/**
 * Maps a rated word label onto its ramp step. Returns `muted` for anything
 * unrecognised, so an unexpected server value degrades to a neutral chip
 * rather than a misleading colour.
 */
export function ratingVariant(label: string | null | undefined): BadgeVariant {
  if (!label) return "muted";
  return RATING_VARIANT[label.trim() as RatingWord] ?? "muted";
}

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  );
}

export { Badge, badgeVariants };
