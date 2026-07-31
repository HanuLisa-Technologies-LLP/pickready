import { cn } from "@/lib/utils";
import type { RatingWordLabel } from "@/lib/types";

/**
 * The four-grade rating colour ramp (spec §10.2).
 *
 * ONE scale across the whole product since 2026-07-30: the AI Score's four
 * matching parameters, Primary Skills, Secondary Skills, Behavioural
 * Competencies and the Overall grade all resolve to the same four words, so
 * "Matching" reads as the same strength wherever it appears.
 *
 * These are the ONLY colours in the product (claude.md: monochrome elsewhere).
 * They are load-bearing, so colour is never the sole signal: the grade's word
 * is always rendered beside it, which is also what keeps the table usable for a
 * colour-blind reviewer.
 *
 * Contrast: every pairing below clears WCAG 2.1 AA (4.5:1) in both themes, the
 * dark fills carry white text, the light fills carry near-black text.
 */
const BAND_STYLES: Record<RatingWordLabel, string> = {
  "Highly Matching": "border-emerald-800 bg-emerald-800 text-white",
  Matching: "border-emerald-700 bg-emerald-700 text-white",
  "Moderately Matching": "border-amber-600 bg-amber-100 text-amber-950",
  "Not Matching": "border-red-700 bg-red-100 text-red-950",
};

/** Ordered best-to-worst, for legends. */
export const MATCHING_BAND_ORDER: RatingWordLabel[] = [
  "Highly Matching",
  "Matching",
  "Moderately Matching",
  "Not Matching",
];

/** @deprecated One scale now. Alias kept so older imports keep compiling. */
export const ASSESSMENT_BAND_ORDER = MATCHING_BAND_ORDER;

export function bandClassName(label: string | null | undefined): string {
  if (!label) return "border-border bg-muted text-foreground";
  return BAND_STYLES[label as RatingWordLabel] ?? "border-border bg-muted text-foreground";
}

/**
 * The word label itself. Inline-block rather than a Badge so it sits naturally
 * at the start of a comment line ("**Highly Matching**, Strong Python…").
 */
export function RatingLabel({
  label,
  className,
}: {
  label: string | null | undefined;
  className?: string;
}) {
  if (!label) return null;
  return (
    <span
      className={cn(
        "inline-block rounded px-1.5 py-0.5 text-[11px] font-bold leading-tight",
        bandClassName(label),
        className
      )}
    >
      {label}
    </span>
  );
}

/**
 * One "Skills Match: **Highly Matching**, comment" line in the table cell.
 *
 * Renders nothing when there is no comment: an empty labelled row is noise, and
 * the caller already distinguishes "not scored yet" as a separate state.
 */
export function RatedComment({
  title,
  label,
  comment,
}: {
  title: string;
  label?: string | null;
  comment?: string | null;
}) {
  if (!comment) return null;
  return (
    <p className="text-xs leading-5">
      <span className="font-semibold">{title}:</span>{" "}
      <RatingLabel label={label} />{" "}
      <span>{comment}</span>
    </p>
  );
}

/** The four-grade legend shown under a chart or table. Words only. */
export function BandLegend({
  bands = MATCHING_BAND_ORDER,
  className,
}: {
  bands?: readonly (string | null | undefined)[];
  className?: string;
}) {
  return (
    <ul
      className={cn("flex flex-wrap items-center gap-3 text-xs", className)}
      aria-label="Rating legend"
    >
      {bands.filter(Boolean).map((band) => (
        <li key={band} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className={cn("h-2.5 w-2.5 rounded-full border", bandClassName(band))}
          />
          {band}
        </li>
      ))}
    </ul>
  );
}
