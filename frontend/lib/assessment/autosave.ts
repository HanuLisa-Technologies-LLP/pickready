"use client";

// Draft persistence for the answer being written.
//
// The page promises "you can close this page and come back", and before the
// question formats that was true of SAVED answers and of one text box. It is
// now true of every format: the draft is the answer payload itself, keyed by
// application AND question, so a refresh, a tab crash or a phone call mid-way
// through a coding question returns the candidate to the code they had, and
// two open assessments cannot overwrite each other.
//
// Debounced rather than written on every keystroke because the write is
// synchronous and a coding answer can be twenty thousand characters; the
// indicator says "Saving" during the gap so the candidate is never looking at
// a state the storage does not yet hold.

import * as React from "react";

import type { AnswerPayload, AutosaveState } from "@/lib/assessment/contracts";

/** How long after the last change the draft is written. Short enough that a
 *  refresh a second after typing keeps the text; long enough that a fast
 *  typist is not serialising a payload per keystroke. */
export const AUTOSAVE_DEBOUNCE_MS = 400;

const PREFIX = "pickready:assessment-draft";

export function draftKey(linkId: string, turnKey: string): string {
  return `${PREFIX}:${linkId}:${turnKey}`;
}

export function readDraft(linkId: string, turnKey: string): AnswerPayload | null {
  try {
    const raw = window.localStorage.getItem(draftKey(linkId, turnKey));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return parsed !== null && typeof parsed === "object" ? (parsed as AnswerPayload) : null;
  } catch {
    // A browser with storage disabled, a full quota, or a draft written by an
    // older build in another shape. Losing draft persistence is not worth
    // failing the assessment over; the field simply starts empty.
    return null;
  }
}

export function writeDraft(linkId: string, turnKey: string, value: AnswerPayload): void {
  try {
    window.localStorage.setItem(draftKey(linkId, turnKey), JSON.stringify(value));
  } catch {
    // See readDraft.
  }
}

export function clearDraft(linkId: string, turnKey: string): void {
  try {
    window.localStorage.removeItem(draftKey(linkId, turnKey));
  } catch {
    // See readDraft.
  }
}

/**
 * Keeps `value` in local storage under (linkId, turnKey) and reports the
 * state the indicator shows.
 *
 * `isEmpty` decides whether there is anything to keep: an empty value removes
 * the draft rather than storing an empty shape, so a cleared field on one
 * device does not resurrect as a blank draft on the next visit. A value that
 * equals what storage already holds (the one just restored, typically) is
 * neither rewritten nor reported as saving.
 */
export function useAutosaveDraft(
  linkId: string,
  turnKey: string | null,
  value: AnswerPayload | null,
  isEmpty: (value: AnswerPayload | null) => boolean
): AutosaveState {
  const [state, setState] = React.useState<AutosaveState>("idle");
  const persisted = React.useRef<string | null>(null);

  // A new turn: what storage holds for it is the baseline, and the indicator
  // starts quiet. Declared before the write effect so it runs first in the
  // same commit.
  React.useEffect(() => {
    if (turnKey === null) {
      persisted.current = null;
    } else {
      const existing = readDraft(linkId, turnKey);
      persisted.current = existing === null ? null : JSON.stringify(existing);
    }
    setState("idle");
  }, [linkId, turnKey]);

  React.useEffect(() => {
    if (turnKey === null) return;
    const serialised = isEmpty(value) ? null : JSON.stringify(value);
    if (serialised === persisted.current) return;
    setState("saving");
    const timer = window.setTimeout(() => {
      if (serialised === null) {
        clearDraft(linkId, turnKey);
      } else {
        writeDraft(linkId, turnKey, value as AnswerPayload);
      }
      persisted.current = serialised;
      setState(serialised === null ? "idle" : "saved");
    }, AUTOSAVE_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [linkId, turnKey, value, isEmpty]);

  return state;
}
