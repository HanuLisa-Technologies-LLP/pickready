"use client";

/**
 * The presentation rules for one AI operation's activity.
 *
 * PROVENANCE
 * ----------
 * `ai-upgrade-spec-doc.md` "Case 3", sections 21, 22, 24, 25 and 29. The copy
 * and the ordering are decided in `backend/app/services/activity/`; what is
 * decided here is when a line appears, how long it stays, and which operation
 * it belongs to.
 *
 * IT IS PUSHED, NOT POLLED, AND THAT IS DELIBERATE
 * -------------------------------------------------
 * The screens that run AI work already poll their operation's status. A hook
 * that opened its own poll would double the request rate for the same bytes,
 * which section 27 rules out, and would let the activity and the run state be
 * read a poll apart from each other so the panel could show a stage as running
 * under a banner that says the run failed. The caller hands over what it has
 * already fetched, and this hook decides what to show.
 *
 * THE ONLY TIMER HERE DELAYS REAL EVENTS. IT NEVER INVENTS ONE
 * -------------------------------------------------------------
 * Section 2 prohibits a timer that advances through a message list. The two
 * timers below do the opposite: they hold a line that the backend has ALREADY
 * produced so the reader can finish it, and they hold the indicator back so a
 * fast operation does not flash. Remove both and the text is still correct;
 * remove the events and there is nothing to show at all.
 *
 * A TERMINAL STATE JUMPS THE QUEUE
 * ---------------------------------
 * A failure or a cancellation is shown at once, never after the minimum
 * lifetime of the line it replaces. Section 25 names the failure being avoided
 * exactly: the user reading "analysing your document" after the request has
 * already failed.
 */
import * as React from "react";

import {
  activitySentence,
  belongsToOperation,
  isNewerLine,
  resolveActivityState,
  type AiActivityLine,
  type AiActivityPayload,
  type AiActivityState,
  type AiTransportState,
} from "@/lib/ai-activity";

/**
 * How long an operation must have been running before the indicator appears.
 *
 * Below this, the work is over before a reader could have read anything, and
 * showing a line would be a flash rather than information. An error is exempt:
 * it appears immediately however fast it arrived.
 */
export const APPEAR_AFTER_MS = 400;

/**
 * The shortest time a line stays before a newer one replaces it.
 *
 * A pipeline can pass three milestones inside a second, and text replaced
 * faster than it can be read is the "rapidly changing text" section 21 rules
 * out. The newest line is held and shown when this elapses, so nothing is
 * skipped and nothing is rushed.
 */
export const MIN_LINE_MS = 900;

export interface AiActivityView {
  /** The operation this view is about. Empty when nothing is being watched. */
  operationId: string;
  state: AiActivityState;
  /** The line currently on screen, which may lag the newest by MIN_LINE_MS. */
  line: AiActivityLine | null;
  /** The sentence to render: the workflow's finding, or the catalogue line. */
  sentence: string;
  /** What the operation is called, from the backend's workflow declaration. */
  label: string;
  /** False while a fast operation is still inside its flicker guard. */
  visible: boolean;
  /** Accept what the caller just fetched. Payloads for other operations are ignored. */
  report: (
    payload: AiActivityPayload | null | undefined,
    transport?: AiTransportState | null
  ) => void;
  /** End the activity because the operation failed outside the reported state. */
  fail: () => void;
  /** End the activity because the operation was cancelled. */
  cancel: () => void;
}

export function useAiActivity(operationId: string): AiActivityView {
  const [state, setState] = React.useState<AiActivityState>("idle");
  const [line, setLine] = React.useState<AiActivityLine | null>(null);
  const [label, setLabel] = React.useState("");
  const [visible, setVisible] = React.useState(false);

  const pendingRef = React.useRef<AiActivityLine | null>(null);
  const shownAtRef = React.useRef(0);
  const firstSeenAtRef = React.useRef(0);
  const timerRef = React.useRef<number | null>(null);
  const appearTimerRef = React.useRef<number | null>(null);

  const clearTimers = React.useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (appearTimerRef.current !== null) {
      window.clearTimeout(appearTimerRef.current);
      appearTimerRef.current = null;
    }
  }, []);

  // A new operation starts from nothing, and leaving the screen ends the old
  // one. Both are section 24's "stale messages appearing after navigation":
  // without this, the last line of a finished run would still be sitting there
  // when the next one begins.
  React.useEffect(() => {
    clearTimers();
    pendingRef.current = null;
    shownAtRef.current = 0;
    firstSeenAtRef.current = 0;
    setState("idle");
    setLine(null);
    setLabel("");
    setVisible(false);
    return clearTimers;
  }, [operationId, clearTimers]);

  const show = React.useCallback((next: AiActivityLine) => {
    pendingRef.current = null;
    shownAtRef.current = Date.now();
    setLine(next);
  }, []);

  const report = React.useCallback<AiActivityView["report"]>(
    (payload, transport) => {
      // Concurrency. A payload stamped with another operation is not merely
      // useless: rendering it would let a slower earlier run repaint over this
      // one, or a finished one hide an active one.
      if (payload && !belongsToOperation(payload, operationId)) return;

      const nextState = resolveActivityState(payload, transport);
      setState(nextState);
      if (payload?.label) setLabel(payload.label);

      if (firstSeenAtRef.current === 0) {
        firstSeenAtRef.current = Date.now();
        appearTimerRef.current = window.setTimeout(() => {
          appearTimerRef.current = null;
          setVisible(true);
        }, APPEAR_AFTER_MS);
      }
      // An error is never held back by the flicker guard.
      if (nextState === "error" || nextState === "cancelled") setVisible(true);

      const candidate = payload?.line ?? null;
      if (!candidate) return;

      setLine((current) => {
        if (!isNewerLine(candidate, current)) return current;

        const terminal =
          candidate.terminal ||
          nextState === "error" ||
          nextState === "cancelled";
        const waited = Date.now() - shownAtRef.current;
        if (terminal || current === null || waited >= MIN_LINE_MS) {
          pendingRef.current = null;
          shownAtRef.current = Date.now();
          if (timerRef.current !== null) {
            window.clearTimeout(timerRef.current);
            timerRef.current = null;
          }
          return candidate;
        }

        // Hold the newest line and show it when the current one has had its
        // turn. Only ever ONE pending line: an intermediate milestone that was
        // overtaken is dropped rather than queued, because a queue would play
        // back a run's history at its own pace long after the run had ended.
        pendingRef.current = candidate;
        if (timerRef.current === null) {
          timerRef.current = window.setTimeout(() => {
            timerRef.current = null;
            const queued = pendingRef.current;
            if (queued) show(queued);
          }, MIN_LINE_MS - waited);
        }
        return current;
      });
    },
    [operationId, show]
  );

  const end = React.useCallback(
    (next: AiActivityState) => {
      clearTimers();
      pendingRef.current = null;
      setState(next);
      setVisible(true);
    },
    [clearTimers]
  );

  const fail = React.useCallback(() => end("error"), [end]);
  const cancel = React.useCallback(() => end("cancelled"), [end]);

  return {
    operationId,
    state,
    line,
    sentence: activitySentence(line),
    label,
    visible,
    report,
    fail,
    cancel,
  };
}
