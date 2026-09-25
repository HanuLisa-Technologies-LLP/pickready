"use client";

// What a Run showed: each visible sample test's result in words, the
// candidate's own output beside the expected output, and any errors.
//
// Words, never figures: no timing, no memory reading and no score reaches
// this panel, and the count in the summary line is written out (the
// assessment surface's standing rule, `lib/assessment/words.ts`). The output
// shown is the candidate's own program's, against tests they can already
// read in the question; nothing here comes from a hidden test.
//
// A refusal or an outage is shown as the server's sentence, verbatim. The
// candidate's code is never lost by a failed run: it stays in the editor.

import { CheckCircle2, Loader2, XCircle } from "lucide-react";

import type { CodingRunOut, CodingRunTestResult } from "@/lib/assessment/coding";
import type { CodingVisibleTestView } from "@/lib/assessment/contracts";
import { numberInWords } from "@/lib/assessment/words";

export type RunPanelState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "complete"; run: CodingRunOut }
  | { kind: "stopped"; message: string };

/** How a sample test is named on screen: by its position, written out, the
 *  same words above the problem and in this panel. */
export function sampleLabel(position: number): string {
  return `Sample ${numberInWords(position + 1)}`;
}

/** The label for a result: the position of the sample it names. A key the
 *  question does not carry is said to be unknown rather than guessed at. */
export function resultLabel(key: string, samples: CodingVisibleTestView[]): string {
  const position = samples.findIndex((sample) => sample.id === key);
  return position === -1 ? "A sample test" : sampleLabel(position);
}

/** The one-line summary of a completed run, counts written out. */
export function runSummary(tests: CodingRunTestResult[]): string {
  const total = tests.length;
  const passed = tests.filter((test) => test.passed).length;
  if (total === 1) {
    return passed === 1
      ? "Your code passed the sample test."
      : "Your code did not pass the sample test.";
  }
  if (passed === total) return "Your code passed every sample test.";
  if (passed === 0) return "Your code did not pass any of the sample tests.";
  return `Your code passed ${numberInWords(passed)} of the ${numberInWords(total)} sample tests.`;
}

/** The compiler output shared by every test, when there is exactly one. A
 *  program that does not compile fails every test with the same message, and
 *  printing it once per test would bury the only thing worth reading. */
export function sharedCompileOutput(tests: CodingRunTestResult[]): string | null {
  const outputs = new Set(tests.map((test) => test.compile_output));
  if (tests.length === 0 || outputs.size !== 1) return null;
  const [only] = outputs;
  return only.trim().length > 0 ? only : null;
}

export function CodingRunResults({
  state,
  samples,
}: {
  state: RunPanelState;
  /** The question's visible tests, which the results are keyed on. */
  samples: CodingVisibleTestView[];
}) {
  if (state.kind === "idle") return null;

  return (
    <section
      className="space-y-3 border border-border bg-surface p-4"
      aria-live="polite"
      aria-label="Sample test results"
      data-testid="coding-run-results"
      data-state={state.kind}
    >
      {state.kind === "running" ? (
        <p className="flex items-center gap-2 text-sm">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Running your code against the sample tests
        </p>
      ) : state.kind === "stopped" ? (
        <p className="text-sm" role="status" data-testid="coding-run-message">
          {state.message}
        </p>
      ) : state.run.status !== "complete" || state.run.tests.length === 0 ? (
        <p className="text-sm" role="status" data-testid="coding-run-message">
          {state.run.message ??
            "The code runner could not finish this run. Your code is kept; you can run it again."}
        </p>
      ) : (
        <CompletedRun tests={state.run.tests} samples={samples} />
      )}
    </section>
  );
}

function CompletedRun({
  tests,
  samples,
}: {
  tests: CodingRunTestResult[];
  samples: CodingVisibleTestView[];
}) {
  const compiled = sharedCompileOutput(tests);
  return (
    <>
      <p className="text-sm font-medium" data-testid="coding-run-summary">
        {runSummary(tests)}
      </p>
      {compiled !== null ? <OutputBlock label="Compiler output" text={compiled} /> : null}
      <ol className="space-y-3">
        {tests.map((test, index) => (
          <li
            key={`${index}:${test.key}`}
            className="space-y-2 border-t border-border pt-3"
            data-testid="coding-run-test"
            data-passed={test.passed ? "true" : "false"}
          >
            <p className="flex flex-wrap items-center gap-2 text-sm">
              {test.passed ? (
                // Teal is evidence: a passed test is the one thing here that
                // corroborates the code.
                <CheckCircle2 className="h-4 w-4 text-teal-700" aria-hidden="true" />
              ) : (
                <XCircle className="h-4 w-4" aria-hidden="true" />
              )}
              <span className="font-medium">{resultLabel(test.key, samples)}</span>
              <span>{test.result_word}</span>
            </p>
            {compiled === null && test.compile_output.trim().length > 0 ? (
              <OutputBlock label="Compiler output" text={test.compile_output} />
            ) : null}
            <div className="grid gap-2 sm:grid-cols-2">
              <OutputBlock label="Your output" text={test.stdout} />
              <OutputBlock label="Expected output" text={test.expected_stdout} />
            </div>
            {test.stderr.trim().length > 0 ? (
              <OutputBlock label="Errors" text={test.stderr} />
            ) : null}
          </li>
        ))}
      </ol>
    </>
  );
}

function OutputBlock({ label, text }: { label: string; text: string }) {
  return (
    <div className="min-w-0">
      <p className="text-xs font-semibold uppercase tracking-wide">{label}</p>
      {text.length > 0 ? (
        <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words border border-border bg-muted p-2 font-mono text-xs">
          {text}
        </pre>
      ) : (
        <p className="mt-1 text-xs italic">No output.</p>
      )}
    </div>
  );
}
