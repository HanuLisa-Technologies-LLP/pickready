"use client";

// Draft persistence for the answer being written, in two places for two
// different reasons.
//
// ON THE SERVER, BECAUSE THE CLOCK IS THE SERVER'S. When a turn runs out the
// server submits what it holds for that turn (PLAN-p3 section 3.4: a respond
// that arrives after the deadline and its grace is answered from the stored
// draft). So the draft is sent to `PUT /conversations/{id}/draft` no more
// often than every `SERVER_DRAFT_INTERVAL_MS`, and always the LATEST value,
// named by the turn it belongs to. A draft the server refuses because the turn
// has moved on is reported to the player, which re-reads the conversation;
// any other failure is retried at the next interval and said on screen.
//
// IN THIS BROWSER, BECAUSE A RELOAD MUST NOT EMPTY THE BOX. The server keeps
// the draft for expiry but does not hand it back, so local storage is what
// puts the candidate's own words back in front of them after a refresh or a
// tab crash. Keyed by application and by turn, so two open assessments cannot
// overwrite each other and a re-ask never inherits the previous attempt's
// text.

import * as React from "react";

import { ApiError, apiPut } from "@/lib/api";
import type { AnswerPayload, AutosaveState, DraftBody } from "@/lib/assessment/contracts";

/** How long after the last change the local copy is written. Short enough
 *  that a refresh a second after typing keeps the text; long enough that a
 *  fast typist is not serialising a payload per keystroke. */
export const AUTOSAVE_DEBOUNCE_MS = 400;

/** The fastest the server is sent a draft. The route is rate limited at
 *  twenty a minute and this sends at most twelve. */
export const SERVER_DRAFT_INTERVAL_MS = 5000;

const PREFIX = "pickready:assessment-draft";

export function draftKey(linkId: string, turnKey: string): string {
  return `${PREFIX}:${linkId}:${turnKey}`;
}

/** Local storage can be switched off, full, or holding a draft an older
 *  build wrote in another shape. None of those is worth failing an
 *  assessment over, so each is logged and the box simply starts empty; the
 *  server's copy is unaffected. */
function storageProblem(action: string, error: unknown): void {
  console.warn(
    `assessment draft could not be ${action} on this device: ` +
      (error instanceof Error ? error.name : "unknown")
  );
}

export function readDraft(linkId: string, turnKey: string): AnswerPayload | null {
  try {
    const raw = window.localStorage.getItem(draftKey(linkId, turnKey));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return parsed !== null && typeof parsed === "object" ? (parsed as AnswerPayload) : null;
  } catch (error) {
    storageProblem("read", error);
    return null;
  }
}

export function writeDraft(linkId: string, turnKey: string, value: AnswerPayload): void {
  try {
    window.localStorage.setItem(draftKey(linkId, turnKey), JSON.stringify(value));
  } catch (error) {
    storageProblem("written", error);
  }
}

export function clearDraft(linkId: string, turnKey: string): void {
  try {
    window.localStorage.removeItem(draftKey(linkId, turnKey));
  } catch (error) {
    storageProblem("cleared", error);
  }
}

/** The body the server stores for a turn. An empty answer is sent as an
 *  empty prose string whatever the format, which tells the server there is
 *  nothing to submit on the candidate's behalf; a structured shape with
 *  nothing selected would be refused by its validator. */
export function draftBody(turnSeq: number, value: AnswerPayload | null, empty: boolean, prose: boolean): DraftBody {
  if (empty || value === null) return { turn_seq: turnSeq, answer: "" };
  if (prose && "text" in value) return { turn_seq: turnSeq, answer: value.text };
  return { turn_seq: turnSeq, answer_payload: value };
}

export interface AutosaveOptions {
  linkId: string;
  conversationId: string | null;
  /** Null when no turn is answerable. */
  turnKey: string | null;
  turnSeq: number | null;
  value: AnswerPayload | null;
  isEmpty: (value: AnswerPayload | null) => boolean;
  prose: boolean;
  /** False while the session is paused or a submission is in flight: the
   *  server refuses a draft then, and there is nothing new to say. */
  enabled: boolean;
  /** The server refused the draft because the turn is no longer current. */
  onStale: () => void;
}

/**
 * Keeps `value` in local storage and on the server, and reports the state the
 * indicator shows. "saved" means the SERVER holds the value on screen, which
 * is the copy that counts if the time runs out.
 */
export function useAutosaveDraft({
  linkId,
  conversationId,
  turnKey,
  turnSeq,
  value,
  isEmpty,
  prose,
  enabled,
  onStale,
}: AutosaveOptions): AutosaveState {
  const [state, setState] = React.useState<AutosaveState>("idle");
  const localCopy = React.useRef<string | null>(null);
  const serverCopy = React.useRef<string | null>(null);
  const lastSentAt = React.useRef(0);
  const inFlight = React.useRef(false);
  const timer = React.useRef<number | null>(null);
  const latest = React.useRef({ value, turnSeq, conversationId, prose, enabled, isEmpty });
  latest.current = { value, turnSeq, conversationId, prose, enabled, isEmpty };
  const onStaleRef = React.useRef(onStale);
  onStaleRef.current = onStale;

  const serialise = React.useCallback(
    (candidate: AnswerPayload | null) =>
      latest.current.isEmpty(candidate) ? "" : JSON.stringify(candidate),
    []
  );

  const cancelTimer = () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  };

  // A new turn: the local copy is the baseline and the server holds nothing
  // for it yet, so the first change (or a restored draft) is sent.
  React.useEffect(() => {
    cancelTimer();
    inFlight.current = false;
    serverCopy.current = turnKey === null ? null : "";
    if (turnKey === null) {
      localCopy.current = null;
    } else {
      const existing = readDraft(linkId, turnKey);
      localCopy.current = existing === null ? null : JSON.stringify(existing);
    }
    setState("idle");
    return cancelTimer;
  }, [linkId, turnKey]);

  // The send and the scheduler call each other, so the send lives in a ref
  // that is refreshed every render and the scheduler is stable.
  const sendRef = React.useRef<() => Promise<void>>(async () => undefined);
  const schedule = React.useCallback(() => {
    if (timer.current !== null || inFlight.current) return;
    const wait = Math.max(
      AUTOSAVE_DEBOUNCE_MS,
      lastSentAt.current + SERVER_DRAFT_INTERVAL_MS - Date.now()
    );
    timer.current = window.setTimeout(() => void sendRef.current(), wait);
  }, []);

  sendRef.current = async () => {
    timer.current = null;
    const current = latest.current;
    if (!current.enabled || current.conversationId === null || current.turnSeq === null) return;
    const serialised = serialise(current.value);
    if (serialised === serverCopy.current) {
      setState(serialised === "" ? "idle" : "saved");
      return;
    }
    inFlight.current = true;
    lastSentAt.current = Date.now();
    const sentFor = current.turnSeq;
    try {
      await apiPut(
        `/api/v2/assessments/conversations/${current.conversationId}/draft`,
        draftBody(sentFor, current.value, serialised === "", current.prose)
      );
      inFlight.current = false;
      if (latest.current.turnSeq !== sentFor) return;
      serverCopy.current = serialised;
      const now = serialise(latest.current.value);
      if (now === serialised) {
        setState(serialised === "" ? "idle" : "saved");
      } else {
        schedule();
      }
    } catch (error) {
      inFlight.current = false;
      if (latest.current.turnSeq !== sentFor) return;
      if (error instanceof ApiError && error.status === 409) {
        // The turn has moved on (answered, expired or paused) since this
        // draft was typed. The player re-reads the server's state.
        onStaleRef.current();
        return;
      }
      console.warn(
        "assessment draft could not reach the server: " +
          (error instanceof Error ? error.message : "unknown")
      );
      setState("retrying");
      schedule();
    }
  };

  // The local copy, debounced.
  React.useEffect(() => {
    if (turnKey === null) return;
    const serialised = isEmpty(value) ? null : JSON.stringify(value);
    if (serialised === localCopy.current) return;
    const local = window.setTimeout(() => {
      if (serialised === null) clearDraft(linkId, turnKey);
      else writeDraft(linkId, turnKey, value as AnswerPayload);
      localCopy.current = serialised;
    }, AUTOSAVE_DEBOUNCE_MS);
    return () => window.clearTimeout(local);
  }, [isEmpty, linkId, turnKey, value]);

  // The server copy, at most once per interval, always the latest value.
  React.useEffect(() => {
    if (turnKey === null || !enabled) return;
    const serialised = serialise(value);
    if (serialised === serverCopy.current) return;
    setState((previous) => (previous === "retrying" ? previous : "saving"));
    schedule();
  }, [enabled, schedule, serialise, turnKey, value]);

  return state;
}
