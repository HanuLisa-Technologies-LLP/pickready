// Wiring an answer field to the proctoring hooks (proctoring spec 4.5).
//
// One function turns a `ProctoringFieldHooks` into the React handler props a
// field spreads onto itself, so every format attaches the same hooks the same
// way and a format that forgot one would be visible as a missing spread
// rather than as a subtly different handler.
//
// COPY, CUT, PASTE AND DROP ARE REFUSED IN EVERY ANSWER FIELD (Appendix B
// section 3: "Copy/paste disabled everywhere"). They are refused HERE as well
// as by the lockdown layer the shell installs on the document. The two do
// different jobs: the lockdown emits the session-level blocked-action event,
// and this hook counts the attempt against the ANSWER it was aimed at and
// names its kind, which is what the behaviour record and the report's paste
// sentence carry. This stops the ordinary candidate; it does not stop a
// determined one with developer knowledge, and nothing here claims otherwise.

import type * as React from "react";

import type { BlockedFieldAction, ProctoringFieldHooks } from "@/lib/assessment/contracts";

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
  onCopy: (event: React.ClipboardEvent<HTMLElement>) => void;
  onCut: (event: React.ClipboardEvent<HTMLElement>) => void;
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
  const refuse =
    (kind: BlockedFieldAction) =>
    (event: React.SyntheticEvent<HTMLElement>): void => {
      event.preventDefault();
      hooks.onBlockedAction(kind);
    };
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
    onCopy: refuse("copy"),
    onCut: refuse("cut"),
    onPaste: refuse("paste"),
    onDrop: refuse("drop"),
    onDragOver: (event) => {
      // Without this the browser never fires `drop` on the field, so the
      // attempt would be neither refused nor counted.
      event.preventDefault();
    },
    onScroll: () => hooks.onScroll(),
  };
}
