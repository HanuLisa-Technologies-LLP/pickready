import { Badge } from "@/components/ui/badge";
import type { Tier } from "@/lib/types";

// Monochrome tier badges — differentiated by weight/fill, not color.
const TIER_STYLES: Record<Tier, { label: string; className: string }> = {
  highly_matching: {
    label: "Highly Matching",
    className: "bg-foreground text-background border-transparent",
  },
  moderately_matching: {
    label: "Moderately Matching",
    className: "bg-muted-foreground/80 text-background border-transparent",
  },
  matching: {
    label: "Matching",
    className: "bg-muted text-foreground border-border",
  },
  not_matching: {
    label: "Not Matching",
    className: "bg-transparent text-muted-foreground border-border border-dashed",
  },
};

export function TierBadge({ tier }: { tier: Tier | null | undefined }) {
  if (!tier) {
    return (
      <Badge variant="outline" className="text-muted-foreground">
        Unrated
      </Badge>
    );
  }
  const style = TIER_STYLES[tier] ?? TIER_STYLES.not_matching;
  return <Badge className={style.className}>{style.label}</Badge>;
}

/**
 * Tier assignment mirror of backend rule (claude.md rule 8): boundaries are
 * inclusive upward, evaluated top-down — exactly 90 is Highly Matching.
 */
export function tierForScore(score: number): Tier {
  if (score >= 90) return "highly_matching";
  if (score >= 70) return "moderately_matching";
  if (score >= 50) return "matching";
  return "not_matching";
}
