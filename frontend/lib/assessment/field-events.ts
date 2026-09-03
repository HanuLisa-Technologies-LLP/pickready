// Wiring an answer field to the proctoring hooks (proctoring spec 4.5).
//
// One function turns a `ProctoringFieldHooks` into the React handler props a
// field spreads onto itself, so every format attaches the same six hooks the
// same way and a format that forgot one would be visible as a missing spread
// rather than as a subtly different handler.
//
// Paste and drop are refused HERE as well as by the lockdown layer the shell
// installs on the document. The two do different jobs: the lockdown emits the
// session-level blocked-action event, and this hook counts the attempt against
// the ANSWER it was aimed at, which is what the behaviour record carries. Both
// fire on one attempt, deliberately. This stops the ordinary candidate; it
// does not stop a determined one with developer knowledge, and nothing here
// claims otherwise.

import type * as React from "react";

import type { ProctoringFieldHooks } from "@/lib/assessment/contracts";

export function isDeletionKey(key: string): boolean {
  return key === "Backspace" || key === "Delete";
}

/** Ctrl+Enter, or Cmd+Enter on a Mac. The one shortcut the assessment has. */
export function isSubmitShortcut(event: {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
}): boolean {
  return event.key === "Enter" && (event.metaKey || event.ctrlKey);
}

export interface FieldEventProps {
  onFocus: () => void;
  onBlur: () => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLElement>) => void;
  onPaste: (event: React.ClipboardEvent<HTMLElement>) => void;
  onDrop: (event: React.DragEvent<HTMLElement>) => void;
  onDragOver: (event: React.DragEvent<HTMLElement>) => void;
  onScroll: () => void;
}

/**
 * The handler props for a field. `onSubmitShortcut`, when given, is called on
 * Ctrl/Cmd+Enter after the keystroke has been recorded, so the timing of the
 * final key is part of the answer's record like every other key.
 */
export function fieldEventProps(
  hooks: ProctoringFieldHooks,
  onSubmitShortcut?: () => void
): FieldEventProps {
  return {
    onFocus: () => hooks.onFieldFocus(),
    onBlur: () => hooks.onFieldBlur(),
    onKeyDown: (event) => {
      hooks.onKeyDown(event.timeStamp, isDeletionKey(event.key));
      if (onSubmitShortcut && isSubmitShortcut(event)) {
        event.preventDefault();
        onSubmitShortcut();
      }
    },
    onPaste: (event) => {
      event.preventDefault();
      hooks.onBlockedAction();
    },
    onDrop: (event) => {
      event.preventDefault();
      hooks.onBlockedAction();
    },
    onDragOver: (event) => {
      // Without this the browser never fires `drop` on the field, so the
      // attempt would be neither refused nor counted.
      event.preventDefault();
    },
    onScroll: () => hooks.onScroll(),
  };
}
