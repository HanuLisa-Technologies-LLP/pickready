"use client";

// The one autosave indicator every answer component shows.
//
// Says "on this device" deliberately. The draft lives in the browser's local
// storage, not on the server, and a candidate who reads "Saved" and then opens
// the assessment on their phone should not expect to find the text there. The
// server holds an answer only once Send is pressed.

import { Check, Loader2 } from "lucide-react";

import type { AutosaveState } from "@/lib/assessment/contracts";

const COPY: Record<AutosaveState, string | null> = {
  idle: null,
  saving: "Saving on this device",
  saved: "Draft saved on this device",
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
      {copy}
    </p>
  );
}
