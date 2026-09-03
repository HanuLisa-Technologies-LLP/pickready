// The format's time allocation, as guidance a candidate reads.
//
// "About four minutes" rather than "240 seconds" or a countdown. It is
// navigation, not a limit and not a score: nothing stops the candidate at the
// mark and nothing they read implies it would. Written in words because the
// assessment surface is read by someone nervous about their career, and a
// digit on that screen reads as a clock even when it is not one.

import { numberInWords } from "@/lib/assessment/words";

const MINUTE_SECONDS = 60;

/**
 * "about four minutes", "about a minute", "about half a minute", "about a
 * minute and a half". Rounded to the nearest half minute, which is as precise
 * as guidance needs to be and coarser than anything a candidate could mistake
 * for a timer.
 */
export function timeAllocationPhrase(seconds: number): string {
  const halves = Math.max(1, Math.round((seconds / MINUTE_SECONDS) * 2));
  const whole = Math.floor(halves / 2);
  const half = halves % 2 === 1;
  if (whole === 0) return "about half a minute";
  if (whole === 1) return half ? "about a minute and a half" : "about a minute";
  return half
    ? `about ${numberInWords(whole)} and a half minutes`
    : `about ${numberInWords(whole)} minutes`;
}
