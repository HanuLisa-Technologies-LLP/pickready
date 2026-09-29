"use client";

/**
 * One line saying what the AI is doing, for any workflow that reports activity.
 *
 * PROVENANCE
 * ----------
 * `ai-upgrade-spec-doc.md` "Case 3", sections 21, 22 and 26. There is exactly
 * one of these for the whole product: a developer adding an AI feature declares
 * it in `backend/app/services/activity/workflows.py`, emits its milestones, and
 * this component renders them. Section 20's requirement is that nobody writes a
 * second status component, so this one takes no per-feature configuration
 * beyond the view the hook returns.
 *
 * WHAT IT DELIBERATELY IS NOT
 * ---------------------------
 * No progress bar, because the workflows behind it cannot say how far through
 * they are and a bar that moves on a timer is a lie with a shape. No large
 * loading panel, because the page stays usable while the work runs. No icon
 * carousel and no rotating text: the sentence changes only when the backend
 * reaches a new milestone.
 *
 * COLOUR
 * ------
 * DESIGN.md: navy is structure, teal is evidence. The marker is navy while the
 * step is simply running, and teal only on a line that carries a FINDING the
 * workflow computed, which is the one thing on this row that is corroborated.
 * Spending teal on every line would spend the product's one meaningful colour
 * on a spinner. Teal here is a FILL and a BORDER, never text, because the brand
 * teal measures below AA for body text on white.
 *
 * ACCESSIBILITY
 * -------------
 * The visible sentence is ordinary text. A separate visually-hidden live region
 * announces it, and only when the MILESTONE changes, so a screen reader hears
 * the steps rather than every poll. Every animation is disabled under
 * `prefers-reduced-motion`, and the row holds its height so a longer sentence
 * cannot push the page around under the reader's cursor.
 */
import * as React from "react";

import { cn } from "@/lib/utils";
import type { AiActivityView } from "./use-ai-activity";

export function AiActivityIndicator({
  activity,
  errorMessage,
  className,
}: {
  activity: AiActivityView;
  /**
   * What to say when the operation ended badly. The screen that ran the
   * operation already knows why in the terms the reader cares about, so it
   * says so; this component never renders a raw error, a status code or an
   * exception. Without one, the workflow's own name is used, which is still
   * specific to the task rather than a generic apology.
   */
  errorMessage?: string;
  className?: string;
}) {
  const { state, line, sentence, label, visible } = activity;

  // Announce a milestone, not a poll. The kind changes when the workflow
  // reaches a new step; the sentence can change without that (a step reporting
  // what it found), and reading both aloud would be the aggressive announcing
  // section 26 rules out.
  const [announced, setAnnounced] = React.useState("");
  const announcedKindRef = React.useRef<string>("");
  React.useEffect(() => {
    if (!line) return;
    if (line.kind === announcedKindRef.current) return;
    announcedKindRef.current = line.kind;
    setAnnounced(line.detail || line.text);
  }, [line]);

  const failed = state === "error";
  const cancelled = state === "cancelled";

  const text = failed
    ? errorMessage ||
      (label
        ? `ReadyPick could not finish ${label.toLowerCase()}. Please try again.`
        : "ReadyPick could not finish this. Please try again.")
    : cancelled
      ? "This was stopped before it finished."
      : sentence;

  if (!text) return null;
  if (state === "running" && !visible) return null;
  // A completed operation says so through its own result, not through a line
  // that lingers describing the last step it took.
  if (state === "done" && !line?.terminal) return null;

  // A line the workflow computed a finding for is the corroborated one.
  const evidenced = !failed && !cancelled && Boolean(line?.detail);

  return (
    <div
      className={cn(
        "flex min-h-5 items-start gap-2 text-xs",
        className
      )}
      data-state={state}
      data-source={line?.source ?? "none"}
    >
      <span className="mt-1 shrink-0" aria-hidden="true">
        <span
          className={cn(
            "block h-2 w-2 rounded-full border",
            failed
              ? "border-destructive bg-destructive"
              : evidenced
                ? "border-teal-600 bg-teal-600"
                : "border-navy-600 bg-navy-600/20",
            state === "running" && "animate-pulse motion-reduce:animate-none"
          )}
        />
      </span>
      <span
        // Keyed on the milestone so a new sentence fades in rather than being
        // swapped under the reader. `motion-reduce` removes the movement and
        // keeps the text.
        key={`${line?.operation_id ?? "none"}:${line?.sequence ?? 0}:${state}`}
        className={cn(
          "animate-in fade-in duration-300 motion-reduce:animate-none motion-reduce:duration-0",
          failed && "text-destructive"
        )}
      >
        {text}
      </span>
      <span className="sr-only" role="status" aria-live="polite">
        {failed || cancelled ? text : announced}
      </span>
    </div>
  );
}
