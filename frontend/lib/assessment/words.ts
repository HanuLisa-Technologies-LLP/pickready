// Small counts, written out.
//
// The assessment surface is read by someone nervous about their career, and a
// digit on that screen reads as a clock or a score even when it is a count of
// options or a suggested number of minutes. Written to twenty: no format
// offers more options or allocates more minutes than that, and beyond it a
// written number stops being easier to read than a digit.

const WORDS = [
  "zero",
  "one",
  "two",
  "three",
  "four",
  "five",
  "six",
  "seven",
  "eight",
  "nine",
  "ten",
  "eleven",
  "twelve",
  "thirteen",
  "fourteen",
  "fifteen",
  "sixteen",
  "seventeen",
  "eighteen",
  "nineteen",
  "twenty",
];

export function numberInWords(count: number): string {
  return WORDS[count] ?? String(count);
}
