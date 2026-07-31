import { Badge } from "@/components/ui/badge";
import type { Tier } from "@/lib/types";

/**
 * The match tier, rendered as one of the five WORD labels and nothing else.
 *
 * No score, no percentage, no rank reaches a client (CLAUDE.md), so the
 * underlying number never appears here in any form, not even as a tooltip or a
 * bar width. The chip uses the shared rating ramp, and the label alone carries
 * the meaning so it survives being read in greyscale.
 */
const TIER_STYLES: Record<Tier, { label: string; className: string }> = {
  highly_matching: {
    label: "Highly Matching",
    className: "border-transparent bg-rating-1-bg text-rating-1",
  },
  matching: {
    label: "Matching",
    className: "border-transparent bg-rating-2-bg text-rating-2",
  },
  moderately_matching: {
    label: "Moderately Matching",
    className: "border-transparent bg-rating-3-bg text-rating-3",
  },
  not_matching: {
    label: "Not Matching",
    className: "border-transparent bg-rating-5-bg text-rating-5",
  },
};

export function TierBadge({ tier }: { tier: Tier | null | undefined }) {
  if (!tier) return null;
  const style = TIER_STYLES[tier] ?? TIER_STYLES.not_matching;
  return (
    <Badge className={`font-semibold ${style.className}`}>{style.label}</Badge>
  );
}
