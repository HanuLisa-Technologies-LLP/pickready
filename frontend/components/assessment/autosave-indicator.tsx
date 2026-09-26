"use client";

// The one autosave indicator every answer component shows.
//
// "Draft saved" means the SERVER holds what is on screen. That copy is the one
// that counts: when a question's time runs out the server submits what it
// holds, so the indicator reports the server's copy and not this browser's.
// A draft that has not reached the server yet is said out loud rather than
// shown as saved, because a candidate who reads "saved" stops worrying about
// a copy that does not exist.

import { AlertTriangle, Check, Loader2 } from "lucide-react";

import type { AutosaveState } from "@/lib/assessment/contracts";

const COPY: Record<AutosaveState, string | null> = {
  idle: null,
  saving: "Saving your draft",
  saved: "Draft saved",
  retrying: "Your draft has not reached us yet. Trying again.",
};

export function AutosaveIndicator({ state }: { state: AutosaveState }) {
  const copy = COPY[state];
  return (
    <p
      role="status"
      aria-live="polite"
      data-autosave={state}
      // Reserve the line so the controls beside it do not shift when the text
      // appears; a Send button that moves under the pointer gets missed.
      className="flex min-h-[1.125rem] items-center gap-1 text-xs"
    >
      {state === "saving" ? (
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
      ) : null}
      {state === "saved" ? <Check className="h-3 w-3" aria-hidden="true" /> : null}
      {state === "retrying" ? <AlertTriangle className="h-3 w-3" aria-hidden="true" /> : null}
      {copy}
    </p>
  );
}
