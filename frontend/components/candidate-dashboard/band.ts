/**
 * Column 3 and column 4's styling, and the one rule that is non-negotiable.
 *
 * THE NON-NEGOTIABLE RULE
 * -----------------------
 * The Dashboard Specification calls it out and spec-doc6 §8.1 repeats it:
 * **Pre-Screen Grade renders muted / outline only. No solid fill, no brand
 * colour, regular weight, 11px. Ready Pick Score is the only column allowed to
 * look finished.**
 *
 * The reason is worth restating where the classes live, because the class
 * lists below are what somebody will "improve". If the two render as equally
 * authoritative pills, the dashboard silently reintroduces the original
 * design's bug: a confident-looking verdict shown before the evidence exists.
 * `pre-screen-grade.test.tsx` asserts the rendered element carries no
 * solid-fill and no brand class, so an improvement that adds one fails rather
 * than ships.
 *
 * WHERE THE BAND COLOURS COME FROM (spec-doc6 C30)
 * ------------------------------------------------
 * NOT from the Dashboard document's `#2FD08A / #E0B341 / #EF5D6B / #6B7280`.
 * Those are raw hexes with no theme behind them, and this product's colour
 * lives in tokens that are defined twice, once per theme. The bands map onto
 * the EXISTING four-grade rating ramp, which is already the product's semantic
 * green-amber-red and already contrast-checked in both themes. Two deliberate
 * departures from the document, both recorded:
 *
 *   * "Under Review" is `warning`, NOT red. The token's own comment says why:
 *     a flag is not a rejection, and colouring it like one makes the platform
 *     look as though it had decided. The document groups Under Review with Not
 *     Recommended under one red, which would render "we have not finished
 *     checking" and "we think this person is weak" identically.
 *   * "Pending" is `muted` with INK text, not grey text. Text is never grey in
 *     this product, enforced at the token, and the standing rule wins over the
 *     document's "never pure black" (recorded as C30).
 *
 * COLOUR IS NEVER THE SOLE CARRIER OF MEANING
 * -------------------------------------------
 * Every band ships with a word (`band_label`), a spoken label
 * (`band_screen_reader_label`) and, for the two that carry no score, an icon.
 * Removing colour entirely must leave the row readable.
 */

export const BAND_STRONG = "ready_to_pick_strong";
export const BAND_READY = "ready_to_pick";
export const BAND_RESERVATIONS = "consider_with_reservations";
export const BAND_NOT_RECOMMENDED = "not_recommended";
export const BAND_UNDER_REVIEW = "under_review";
export const BAND_PENDING = "pending_ready_pick_profile";

/**
 * The FILLED chip. Column 4 only.
 *
 * A tinted fill plus a same-hue border plus bold weight, which is what makes
 * it read as decided next to column 3's transparent outline. The pair is the
 * rating ramp's own background/foreground, so it inverts correctly in dark
 * mode instead of becoming light-on-light.
 */
export const BAND_CLASS: Record<string, string> = {
  [BAND_STRONG]: "border-rating-1/30 bg-rating-1-bg text-rating-1",
  [BAND_READY]: "border-rating-2/30 bg-rating-2-bg text-rating-2",
  [BAND_RESERVATIONS]: "border-rating-3/30 bg-rating-3-bg text-rating-3",
  [BAND_NOT_RECOMMENDED]: "border-rating-5/30 bg-rating-5-bg text-rating-5",
  // Held for review, not rejected. See the header.
  [BAND_UNDER_REVIEW]: "border-warning bg-warning text-warning-foreground",
  // Neutral, and the text is ink rather than grey.
  [BAND_PENDING]: "border-border bg-muted text-foreground",
};

/** Bands that carry no number, and therefore no verdict to act on. */
export const BANDS_WITHOUT_A_SCORE = new Set([BAND_UNDER_REVIEW, BAND_PENDING]);

/**
 * Column 3's ONLY styling.
 *
 * Transparent background, hairline border, regular weight, 11px, ink text. No
 * fill, no brand colour, no ramp colour, no bold. A grade is still a signal
 * and still legible; what it is not is a verdict.
 */
export const PRE_SCREEN_CLASS =
  "inline-flex items-center rounded-md border border-border bg-transparent " +
  "px-1.5 py-0.5 text-[11px] font-normal leading-4 text-foreground";

/**
 * Class fragments that would turn column 3 into a verdict.
 *
 * Exported so the component test can assert their ABSENCE rather than
 * restating a list in the test file. Written as fragments because that is how
 * the regression arrives: somebody adds `bg-rating-1-bg` to "make the A stand
 * out", which is exactly the change this rule exists to refuse.
 */
export const FORBIDDEN_ON_PRE_SCREEN = [
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
