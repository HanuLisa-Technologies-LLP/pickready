"use client";

// The system check screen (proctoring spec 8.2).
//
// Six rows, each pass or fail, and EVERY FAILED ROW CARRIES ITS FIX. A
// candidate who fails the check must get clear guidance, not a dead end, so
// the row that failed says what to do about it in the words of somebody who
// has just been refused rather than in the words of the code that refused
// them. Retry re-runs everything.
//
// The Start button appears only when every row has passed, which is what
// "do not let the assessment begin until all pass" has to mean in a screen.
// A slow device is not a failure: performance is measured and recorded, and
// the session runs at a lower frame rate (spec 3.6).

import { Check, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CHECK_LABELS, CHECK_ORDER, type CheckRow } from "@/lib/proctoring/system-check";

export const SYSTEM_CHECK_TITLE = "Checking your setup";

export function SystemCheckScreen({
  rows,
  running,
  error,
  onRetry,
  onStart,
}: {
  rows: CheckRow[] | null;
  running: boolean;
  /** A failure of the check itself, not of a row. */
  error: string | null;
  onRetry: () => void;
  onStart: () => void;
}) {
  const shown: CheckRow[] =
    rows ??
    CHECK_ORDER.map((key) => ({ key, label: CHECK_LABELS[key], passed: null, fix: null }));
  const allPassed = rows !== null && rows.every((row) => row.passed === true);

  return (
    <div className="mx-auto max-w-2xl">
      <Card>
        <CardHeader>
          <CardTitle>{SYSTEM_CHECK_TITLE}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <p className="text-sm leading-6">
            We check your camera, microphone and browser once, before the questions start.
            Nothing is recorded.
          </p>
          <ul className="divide-y divide-border border-y border-border">
            {shown.map((row) => (
              <li key={row.key} className="flex gap-3 py-3">
                <span aria-hidden className="mt-0.5 shrink-0">
                  {row.passed === null ? (
                    running ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <span className="block h-4 w-4 border border-border" />
                    )
                  ) : row.passed ? (
                    <Check className="h-4 w-4 text-teal-700" />
                  ) : (
                    <X className="h-4 w-4 text-destructive" />
                  )}
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-medium">{row.label}</p>
                  <p className="sr-only">
                    {row.passed === null ? "Not checked yet" : row.passed ? "Passed" : "Failed"}
                  </p>
                  {row.fix ? <p className="mt-1 text-sm leading-6">{row.fix}</p> : null}
                </div>
              </li>
            ))}
          </ul>
          {error ? <p className="text-sm leading-6">{error}</p> : null}
          <div className="flex flex-wrap gap-2">
            {allPassed ? (
              <Button size="lg" onClick={onStart}>
                Start the assessment
              </Button>
            ) : (
              <Button size="lg" disabled={running} onClick={onRetry}>
                {running ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    Checking
                  </>
                ) : (
                  "Check again"
                )}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
