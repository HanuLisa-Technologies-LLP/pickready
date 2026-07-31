// Type of Procurement (new spec, 2026-07-28).
//
// Three ways a candidate reaches a job:
//   Applied   they came to PickReady themselves
//   Sourced   they arrived through a third-party link
//   Databank  the recruitment team uploaded them in bulk
//
// This is DISPLAY ONLY. All three types go through identical AI parsing,
// matching and assessment, so nothing in the product may branch on it beyond
// showing this badge and filtering the table.

import { cn } from "@/lib/utils";
import type { CandidateProcurement } from "@/lib/types";

const STYLES: Record<CandidateProcurement, string> = {
  // Neutral outline: procurement is metadata, not a judgement, so it must not
  // compete with the rating colours in the same row.
  applied: "border-border bg-muted",
  sourced: "border-border bg-muted",
  databank: "border-border bg-muted",
};

const FALLBACK_LABELS: Record<CandidateProcurement, string> = {
  applied: "Applied",
  sourced: "Sourced",
  databank: "Databank",
};

export function ProcurementBadge({
  type,
  label,
  className,
}: {
  type: CandidateProcurement;
  /** Server-supplied text. Falls back to a local map if absent. */
  label?: string | null;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-block whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-medium leading-tight",
        STYLES[type] ?? "border-border bg-muted",
        className
      )}
    >
      {label || FALLBACK_LABELS[type] || type}
    </span>
  );
}
