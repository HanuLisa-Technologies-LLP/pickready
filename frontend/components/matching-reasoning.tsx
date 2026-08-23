"use client";

/**
 * What the AI matching run is doing, shown INLINE on the job page.
 *
 * WHAT THIS REPLACED, AND WHY
 * ---------------------------
 * "Run AI matching" used to open a modal dialog that could not be dismissed
 * (`onEscapeKeyDown` and `onPointerDownOutside` were both preventDefault'd) and
 * showed one sentence -- "Scoring and writing remarks" -- for the entire run.
 * For a pool of forty candidates that is minutes of a spinner over unchanging
 * text, and the rest of the page is taken away from the recruiter while they
 * wait. They cannot read the JD, cannot open another candidate, cannot even
 * close it and come back.
 *
 * This panel sits in the page. The run keeps going if the recruiter scrolls
 * away or opens a resume, and while it runs they can see which step it is on.
 *
 * WHAT IS AND IS NOT SHOWN
 * ------------------------
 * Every line here comes from a FIXED stage vocabulary the backend pipeline
 * emits as it reaches each stage (services/matching_progress). None of it is a
 * model narrating its own reasoning, and that is a deliberate constraint rather
 * than a simplification:
 *
 *   * the prompts behind this run contain a real candidate's resume and a real
 *     client's job description, and a narration quotes its prompt;
 *   * a narration is generated, so it can describe work that never happened --
 *     a convincing progress display is worse than none.
 *
 * The `skipped` status is the part that earns the whole design. When the
 * embedding service is down the run really does fall back to keyword-only
 * ranking, and this panel says so on the semantic row instead of ticking it
 * green. A degraded run that looks complete is the failure this is here to
 * prevent.
 *
 * NO NUMBERS RULE. The counters here are counts of CANDIDATES and CATEGORIES --
 * how many rows are being processed. They are not scores, percentages, ranks or
 * any assessment output, and nothing on this panel grades anybody.
 */
import * as React from "react";
import { AlertTriangle, Check, Loader2, Minus } from "lucide-react";

import { cn } from "@/lib/utils";

export interface MatchingStage {
  key: string;
  label: string;
  detail: string;
  status: "pending" | "active" | "done" | "skipped" | "failed";
}

export interface MatchingProgress {
  stages: MatchingStage[];
  candidate_count: number;
  scored_count: number;
}

export type MatchingRunState = "idle" | "running" | "done" | "error";

function StatusMark({ status }: { status: MatchingStage["status"] }) {
  if (status === "active") {
    return <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />;
  }
  if (status === "done") {
    return <Check className="h-3.5 w-3.5" aria-hidden="true" />;
  }
  if (status === "skipped") {
    return <Minus className="h-3.5 w-3.5" aria-hidden="true" />;
  }
  if (status === "failed") {
    return <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />;
  }
  // Pending: an empty ring, so the reader can see the steps still to come and
  // knows how much is left. That is the thing the old modal could not tell them.
  return (
    <span
      className="block h-3.5 w-3.5 rounded-full border border-current opacity-40"
      aria-hidden="true"
    />
  );
}

const STATUS_WORD: Record<MatchingStage["status"], string> = {
  pending: "not started",
  active: "running",
  done: "finished",
  skipped: "skipped",
  failed: "failed",
};

export function MatchingReasoning({
  state,
  progress,
  message,
  className,
}: {
  state: MatchingRunState;
  progress: MatchingProgress | null;
  /** The terminal sentence: what finished, or what went wrong. */
  message: string;
  className?: string;
}) {
  if (state === "idle" || !progress || progress.stages.length === 0) {
    return message ? (
      <p
        role="status"
        data-state={state}
        className={cn(
          "text-xs leading-5",
          state === "error" && "text-destructive",
          className
        )}
      >
        {message}
      </p>
    ) : null;
  }

  const { stages, candidate_count: total, scored_count: scored } = progress;

  return (
    <section
      aria-label="AI matching progress"
      data-state={state}
      className={cn("rounded-lg border border-border bg-secondary/30 p-4", className)}
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">
          {state === "running"
            ? "AI matching is running"
            : state === "error"
              ? "AI matching stopped"
              : "AI matching finished"}
        </h3>
        {total > 0 ? (
          <p className="text-xs">
            {scored} of {total} candidate{total === 1 ? "" : "s"} processed
          </p>
        ) : null}
      </div>

      {/* aria-live on the list, not on each row: a screen reader should hear
          the step that just changed, not the whole plan re-read every poll. */}
      <ol className="space-y-2" aria-live="polite">
        {stages.map((stage) => (
          <li
            key={stage.key}
            className={cn(
              "flex gap-2.5 text-xs leading-5",
              stage.status === "pending" && "opacity-50"
            )}
          >
            <span className="mt-0.5 shrink-0">
              <StatusMark status={stage.status} />
            </span>
            <span>
              <span className="font-medium">{stage.label}</span>
              <span className="sr-only">, {STATUS_WORD[stage.status]}</span>
              {stage.status === "pending" ? null : (
                <span className="block">{stage.detail}</span>
              )}
            </span>
          </li>
        ))}
      </ol>

      {message ? (
        <p
          role="status"
          className={cn(
            "mt-3 border-t border-border pt-3 text-xs leading-5",
            state === "error" && "text-destructive"
          )}
        >
          {message}
        </p>
      ) : null}
    </section>
  );
}
