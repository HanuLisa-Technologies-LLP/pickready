/**
 * Small counts, spelled out, for the monitoring screens.
 *
 * The monitoring indicator says how many warnings have been used. That is an
 * operational count rather than anything about the candidate's answers, but the proctoring surfaces have always spelled their
 * counts out (the report does, so the words a candidate reads and the words a
 * recruiter reads agree), and a digit on a screen under that rule is the one
 * a sweep eventually has to explain. Past ten nothing on these screens has a
 * count to give; the fallbacks say so in words rather than print a digit.
 */

const COUNTS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];

/** "two". */
export function countWord(n: number): string {
  return COUNTS[n] ?? "more than ten";
}

