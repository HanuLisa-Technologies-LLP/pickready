// The clipboard fence around the coding editor (assessment spec 3: copy and
// paste are disabled everywhere, and every attempt is logged).
//
// WHY THE EDITOR NEEDS ITS OWN FENCE. The proctoring lockdown
// (`lib/proctoring/lockdown.ts`) cancels `copy`, `cut`, `paste` and `drop` on
// the document in the capture phase, and that stays the session-level record.
// The editor is different in two ways that make a document listener alone not
// enough:
//
//   1. Monaco reads the keyboard itself. It binds its own clipboard actions to
//      Ctrl/Cmd+C, X and V, and Shift+Insert, and may carry them out through
//      the asynchronous Clipboard API rather than through a DOM clipboard
//      event, so a fence that only watches `paste` events can be walked round
//      by a keystroke. The chords are therefore refused at KEYDOWN, before the
//      browser or the editor acts on them.
//   2. Monaco types through a hidden textarea. A paste that reaches it
//      inserts text through that textarea's `input` event, which no clipboard
//      listener ever sees. `beforeinput` with a paste or drop input type is
//      the last point at which that insertion can still be refused.
//
// Every refused attempt calls `onBlocked` exactly once: a chord refused at
// keydown never produces the clipboard event behind it, so the two listeners
// cannot both report one attempt.
//
// It is installed on the editor's host element in the CAPTURE phase, which is
// before any listener Monaco registers on its own descendants, and it does not
// depend on the editor's internal event routing: the fence is proctoring
// evidence, and a library upgrade that reorders its own dispatch must not be
// able to open it.
//
// This stops the ordinary candidate. It does not stop a determined one with
// developer tools, and nothing here claims otherwise.

import type { BlockedFieldAction } from "@/lib/assessment/contracts";

export type ClipboardAction = Exclude<BlockedFieldAction, "drop">;

interface ChordEvent {
  key: string;
  code?: string;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}

const LETTER_ACTION: Record<string, ClipboardAction> = {
  c: "copy",
  x: "cut",
  v: "paste",
};

const CODE_ACTION: Record<string, ClipboardAction> = {
  KeyC: "copy",
  KeyX: "cut",
  KeyV: "paste",
};

/**
 * The clipboard action a keystroke would perform, or null.
 *
 * Read from `code` (the physical key) as well as `key` (the character it
 * types), because on a non-Latin layout Ctrl plus the physical V key still
 * pastes while `key` is a Cyrillic or Greek letter. Alt is excluded so AltGr
 * combinations, which some layouts need for ordinary characters and which
 * browsers report as Ctrl+Alt, are never swallowed.
 */
export function clipboardChord(event: ChordEvent): ClipboardAction | null {
  const primary = event.ctrlKey || event.metaKey;
  if (primary && !event.altKey) {
    const byKey = LETTER_ACTION[event.key.toLowerCase()];
    if (byKey) return byKey;
    const byCode = event.code ? CODE_ACTION[event.code] : undefined;
    if (byCode) return byCode;
    // Ctrl+Insert is copy on Windows and Linux.
    if (event.key === "Insert" && !event.shiftKey) return "copy";
  }
  if (event.shiftKey && !primary && !event.altKey) {
    // Shift+Insert pastes and Shift+Delete cuts, on Windows and Linux.
    if (event.key === "Insert") return "paste";
    if (event.key === "Delete") return "cut";
  }
  return null;
}

/** The `beforeinput` types that move text into or out of the field through
 *  the clipboard or a drag, and the kind each one is reported as. */
const FOREIGN_INPUT_TYPES: Record<string, BlockedFieldAction> = {
  insertFromPaste: "paste",
  insertFromPasteAsQuotation: "paste",
  insertFromYank: "paste",
  insertFromDrop: "drop",
  deleteByDrag: "drop",
  deleteByCut: "cut",
};

/** The DOM events the fence cancels and reports. */
export const REPORTED_EVENTS = ["copy", "cut", "paste", "drop"] as const;

/** The DOM events the fence cancels without reporting. `dragover` fires
 *  continuously while something is dragged and must be cancelled for the
 *  browser to deliver `drop` at all; the single `drop` is what is reported.
 *  `dragstart` would let a selection be dragged OUT of the editor into
 *  another window. `contextmenu` is the browser's own menu, which carries a
 *  Paste entry once the editor's menu is switched off; the lockdown reports
 *  it at the session level. */
export const SILENT_EVENTS = ["dragover", "dragenter", "dragstart", "contextmenu"] as const;

export interface EditorGuardOptions {
  /** Called once per refused attempt, with its kind. */
  onBlocked(action: BlockedFieldAction): void;
}

/**
 * Install the fence on `host`. Returns the function that removes it.
 *
 * Keystroke RECORDING is not done here. The editor records every keydown
 * (the refused chords included, since pressing them is part of how the answer
 * was produced) before this fence runs, so the order of the two cannot be
 * reversed by a later change to either.
 */
export function installEditorGuard(host: HTMLElement, options: EditorGuardOptions): () => void {
  const refuse = (event: Event) => {
    event.preventDefault();
    event.stopPropagation();
  };

  const onReported = (event: Event) => {
    refuse(event);
    options.onBlocked(event.type as BlockedFieldAction);
  };
  const onSilent = (event: Event) => refuse(event);
  const onBeforeInput = (event: Event) => {
    const kind = FOREIGN_INPUT_TYPES[(event as InputEvent).inputType];
    if (kind === undefined) return;
    refuse(event);
    options.onBlocked(kind);
  };
  const onKeyDown = (event: KeyboardEvent) => {
    const action = clipboardChord(event);
    if (action === null) return;
    refuse(event);
    options.onBlocked(action);
  };

  const capture = { capture: true } as const;
  for (const type of REPORTED_EVENTS) host.addEventListener(type, onReported, capture);
  for (const type of SILENT_EVENTS) host.addEventListener(type, onSilent, capture);
  host.addEventListener("beforeinput", onBeforeInput, capture);
  host.addEventListener("keydown", onKeyDown, capture);

  return () => {
    for (const type of REPORTED_EVENTS) host.removeEventListener(type, onReported, capture);
    for (const type of SILENT_EVENTS) host.removeEventListener(type, onSilent, capture);
    host.removeEventListener("beforeinput", onBeforeInput, capture);
    host.removeEventListener("keydown", onKeyDown, capture);
  };
}
