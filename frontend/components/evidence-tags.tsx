import { Check, X } from "lucide-react";

import { cn } from "@/lib/utils";
import type { EvidenceTag } from "@/lib/types";

/**
 * The evidence tags the resume check wrote for one candidate: what the resume
 * shows (positive) and which Must-have skills it does not show (negative).
 *
 * The tags are the server's, verbatim and in the server's order (positives
 * first). This component chooses nothing: no sort, no count, no weighting,
 * and no number of any kind. The caller decides WHICH tags to pass (the row
 * passes the ones the server flagged `shown_in_row`, the Details dialog passes
 * them all).
 *
 * Colour is never the only signal. A positive tag carries a Check in teal-700
 * (teal is the colour of evidence; 700 is the shade that clears AA as text on
 * white), a negative one an X in ink, and each carries an sr-only prefix so a
 * screen reader hears "Evidenced: Python" rather than an icon it cannot see.
 */
export const POSITIVE_PREFIX = "Evidenced:";
export const NEGATIVE_PREFIX = "Not evidenced:";

export function EvidenceTags({
  tags,
  className,
}: {
  tags: readonly EvidenceTag[];
  className?: string;
}) {
  if (tags.length === 0) return null;
  return (
    <ul className={cn("flex flex-wrap gap-1", className)} aria-label="Evidence tags">
      {tags.map((tag) => {
        const positive = tag.polarity === "positive";
        return (
          <li
            key={`${tag.polarity}:${tag.text}`}
            data-polarity={tag.polarity}
            className={cn(
              "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium leading-tight",
              positive
                ? "border-teal-700/40 bg-teal-50 text-foreground"
                : "border-border bg-muted text-foreground",
            )}
          >
            {positive ? (
              <Check className="h-3 w-3 shrink-0 text-teal-700" aria-hidden="true" />
            ) : (
              <X className="h-3 w-3 shrink-0 text-foreground" aria-hidden="true" />
            )}
            <span className="sr-only">{positive ? POSITIVE_PREFIX : NEGATIVE_PREFIX} </span>
            <span>{tag.text}</span>
          </li>
        );
      })}
    </ul>
  );
}
