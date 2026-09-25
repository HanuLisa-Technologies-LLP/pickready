"use client";

// The persistent indicator (proctoring spec 8.4).
//
// Always visible, small, and it says two things: whether monitoring is active
// or the assessment is paused, and how many warnings have been used. The
// second half is the point of the requirement, which is that the candidate is
// never surprised by warning three. The count is the server's number, taken
// from the last response, never a tally this client keeps, and it is spelled
// out like every count on the monitoring screens.
//
// The words are the ones a candidate can act on. Nothing here says strike,
// tier, violation, flag, anomaly, signal, confidence, threshold or severity,
// and there is no colour that grades how the session is going: the dot means
// live, a ring means paused, and the sentence means what it says.

import { countWord } from "@/lib/proctoring/words";

function capitalised(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

export function warningsSentence(used: number, max: number): string {
  if (used <= 0) return "No warnings so far";
  const remaining = Math.max(0, max - used);
  if (remaining === 0) return "You have used all of your warnings";
  const usedPart = `${capitalised(countWord(used))} ${used === 1 ? "warning" : "warnings"} used`;
  return remaining === 1 ? `${usedPart}, one left` : usedPart;
}

export const MONITORING_ACTIVE = "Monitoring active";
export const MONITORING_PAUSED = "Assessment paused";

export function MonitoringIndicator({
  warningsUsed,
  maxWarnings,
  paused = false,
}: {
  warningsUsed: number;
  maxWarnings: number;
  /** The device pause is open. */
  paused?: boolean;
}) {
  return (
    <div
      className="fixed bottom-4 right-4 z-40 flex items-center gap-2 border border-border bg-surface px-3 py-2 text-xs shadow-card"
      role="status"
      aria-live="polite"
    >
      <span
        aria-hidden
        className={
          paused
            ? "h-2 w-2 rounded-full border border-ink"
            : "h-2 w-2 rounded-full bg-teal-600"
        }
      />
      <span className="font-medium">{paused ? MONITORING_PAUSED : MONITORING_ACTIVE}</span>
      <span aria-hidden className="h-3 w-px bg-border" />
      <span>{warningsSentence(warningsUsed, maxWarnings)}</span>
    </div>
  );
}
