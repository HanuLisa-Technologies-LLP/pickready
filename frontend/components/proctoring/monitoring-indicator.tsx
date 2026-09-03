"use client";

// The persistent indicator (proctoring spec 8.4).
//
// Always visible, small, and it says two things: monitoring is active, and
// how many warnings have been used. The second half is the point of the
// requirement, which is that the candidate is never surprised by warning
// three. The count is the server's number, taken from the last response,
// never a tally this client keeps.
//
// The words are the ones a candidate can act on. Nothing here says strike,
// tier, violation, flag, anomaly, signal, confidence, threshold or severity,
// and there is no colour that grades how the session is going: the dot means
// live, and the sentence means what it says.

export function warningsSentence(used: number, max: number): string {
  if (used <= 0) return "No warnings so far";
  const remaining = Math.max(0, max - used);
  if (remaining === 0) return "You have used all of your warnings";
  if (used === 1) return remaining === 1 ? "One warning used, one left" : "One warning used";
  return remaining === 1 ? "Two warnings used, one left" : `${used} warnings used`;
}

export function MonitoringIndicator({
  warningsUsed,
  maxWarnings,
}: {
  warningsUsed: number;
  maxWarnings: number;
}) {
  return (
    <div
      className="fixed bottom-4 right-4 z-40 flex items-center gap-2 border border-border bg-surface px-3 py-2 text-xs shadow-card"
      role="status"
      aria-live="polite"
    >
      <span aria-hidden className="h-2 w-2 rounded-full bg-teal-600" />
      <span className="font-medium">Monitoring active</span>
      <span aria-hidden className="h-3 w-px bg-border" />
      <span>{warningsSentence(warningsUsed, maxWarnings)}</span>
    </div>
  );
}
