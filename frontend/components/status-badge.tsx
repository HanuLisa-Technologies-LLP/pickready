import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * The one status chip, used by the pipeline, the job lifecycle and the
 * verification flow.
 *
 * Colour is meaning here, not decoration, so every chip pairs a tinted
 * background with a matching FOREGROUND from the same ramp: a status is never
 * conveyed by a grey wash, and the label always reads at full contrast. The
 * ramp is the shared rating ramp from the design brief, reused so a green in
 * one table means the same thing as a green in the next.
 *
 * A status the map does not know falls back to the neutral chip rather than
 * rendering unstyled, because a new backend stage must never look broken.
 */
const NEUTRAL = "border-border bg-secondary";

const STATUS_STYLES: Record<string, string> = {
  // --- hiring pipeline ---
  shortlisted: "border-transparent bg-rating-2-bg text-rating-2",
  offered: "border-transparent bg-rating-1-bg text-rating-1",
  joined: "border-transparent bg-brand-600 text-white",
  hold: "border-transparent bg-rating-3-bg text-rating-3",
  rejected: "border-transparent bg-rating-5-bg text-rating-5",
  pending: "border-dashed border-border bg-transparent",
  // --- job lifecycle ---
  draft: "border-dashed border-border bg-transparent",
  requested: NEUTRAL,
  recommended: "border-transparent bg-brand-100 text-accent-foreground",
  approved: "border-transparent bg-rating-2-bg text-rating-2",
  ratified: "border-transparent bg-brand-600 text-white",
  published: "border-transparent bg-brand-600 text-white",
  archived: "border-dashed border-border bg-transparent",
  // --- employer verification ---
  sent: "border-transparent bg-brand-100 text-accent-foreground",
  completed: "border-transparent bg-rating-1-bg text-rating-1",
  overridden: "border-transparent bg-rating-3-bg text-rating-3",
  failed: "border-transparent bg-rating-5-bg text-rating-5",
};

export function StatusBadge({
  status,
  className,
}: {
  status: string | null | undefined;
  className?: string;
}) {
  const s = (status ?? "pending").toLowerCase();
  const style = STATUS_STYLES[s] ?? NEUTRAL;
  const label = s
    .split(/[_-]/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
  return (
    <Badge className={cn("font-semibold", style, className)}>{label}</Badge>
  );
}
