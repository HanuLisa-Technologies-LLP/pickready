/**
 * Column 3 and column 4's styling, and the one rule that is non-negotiable.
 *
 * THE NON-NEGOTIABLE RULE
 * -----------------------
 * The Dashboard Specification calls it out and spec-doc6 §8.1 repeats it:
 * **the early signal renders muted / outline only. No solid fill, no brand
 * colour, regular weight, 11px. Column 4 is the only grade column allowed to
 * look finished.** Column 3 is AI Match now, Yukti's reading of the RESUME
 * alone, and the rule transfers to it unchanged: it is exactly the kind of
 * early signal the rule was written for.
 *
 * The reason is worth restating where the classes live, because the class
 * lists below are what somebody will "improve". If the two render as equally
 * authoritative pills, the dashboard silently reintroduces the original
 * design's bug: a confident-looking verdict shown before the evidence exists.
 * `ai-match-cell.test.tsx` asserts the rendered element carries no solid-fill
 * and no brand class, so an improvement that adds one fails rather than ships.
 *
 * NO NUMBER AND NO LETTER (D3, CONTRACT C8)
 * -----------------------------------------
 * Both columns render one of the four grade words, or a status word, chosen
 * by the server. The numeric Vivekium Score and its five-band vocabulary
 * (Ready to Pick, Strong ... Not Recommended) are gone, as is the A / B / C /
 * Hold letter. The browser styles by the STATE the server sends beside the
 * word; it never derives a state from a word or a word from a number.
 *
 * WHERE THE COLOURS COME FROM (spec-doc6 C30)
 * -------------------------------------------
 * The EXISTING four-grade rating ramp, which is already the product's semantic
 * green-amber-red and already contrast-checked in both themes. Two deliberate
 * choices, both recorded:
 *
 *   * "Under Review" is `warning`, NOT red. A flag is not a rejection, and
 *     colouring it like one makes the platform look as though it had decided.
 *   * The two gradeless states are `muted` with INK text, not grey text. Text
 *     is never grey in this product, enforced at the token.
 *
 * COLOUR IS NEVER THE SOLE CARRIER OF MEANING
 * -------------------------------------------
 * Every state ships with a word, a spoken label and, for Under Review, an
 * icon. Removing colour entirely must leave the row readable.
 */

export const STATE_HIGHLY = "highly_matching";
export const STATE_MATCHING = "matching";
export const STATE_MODERATELY = "moderately_matching";
export const STATE_NOT = "not_matching";
export const STATE_NOT_CHECKED = "not_checked";
export const STATE_NOT_ASSESSED = "not_assessed";
export const STATE_UNDER_REVIEW = "under_review";

/**
 * The FILLED chip. Column 4 only.
 *
 * A tinted fill plus a same-hue border plus bold weight, which is what makes
 * it read as decided next to column 3's transparent outline. The pair is the
 * rating ramp's own background/foreground, so it inverts correctly in dark
 * mode instead of becoming light-on-light.
 */
export const GRADE_CLASS: Record<string, string> = {
  [STATE_HIGHLY]: "border-rating-1/30 bg-rating-1-bg text-rating-1",
  [STATE_MATCHING]: "border-rating-2/30 bg-rating-2-bg text-rating-2",
  [STATE_MODERATELY]: "border-rating-3/30 bg-rating-3-bg text-rating-3",
  [STATE_NOT]: "border-rating-5/30 bg-rating-5-bg text-rating-5",
  // Held for review, not rejected. See the header.
  [STATE_UNDER_REVIEW]: "border-warning bg-warning text-warning-foreground",
  // Neutral, and the text is ink rather than grey.
  [STATE_NOT_CHECKED]: "border-border bg-muted text-foreground",
  [STATE_NOT_ASSESSED]: "border-border bg-muted text-foreground",
};

/** States that carry no grade, and therefore no verdict to act on. */
export const STATES_WITHOUT_A_GRADE = new Set([
  STATE_UNDER_REVIEW,
  STATE_NOT_CHECKED,
  STATE_NOT_ASSESSED,
]);

/**
 * Column 3's ONLY styling.
 *
 * Transparent background, hairline border, regular weight, 11px, ink text. No
 * fill, no brand colour, no ramp colour, no bold. A grade is still a signal
 * and still legible; what it is not is a verdict.
 */
export const AI_MATCH_CLASS =
  "inline-flex items-center rounded-md border border-border bg-transparent " +
  "px-1.5 py-0.5 text-[11px] font-normal leading-4 text-foreground";

/**
 * Class fragments that would turn column 3 into a verdict.
 *
 * Exported so the component test can assert their ABSENCE rather than
 * restating a list in the test file. Written as fragments because that is how
 * the regression arrives: somebody adds `bg-rating-1-bg` to "make Highly
 * Matching stand out", which is exactly the change this rule exists to refuse.
 */
export const FORBIDDEN_ON_AI_MATCH = [
  "bg-rating",
  "bg-navy",
  "bg-teal",
  "bg-brand",
  "bg-primary",
  "bg-destructive",
  "bg-warning",
  "bg-accent",
  "text-rating",
  "text-teal",
  "text-navy",
  "text-brand",
  "font-bold",
  "font-semibold",
];

/** The confidence dot. Shape and fill, always beside a word. */
export const CONFIDENCE_DOT_CLASS: Record<string, string> = {
  filled: "bg-current",
  outline: "border border-current bg-transparent",
  grayed: "border border-dashed border-current bg-transparent",
};
